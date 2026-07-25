"""首次运行检测中间件。

当系统中尚无超级管理员（is_superuser=True 的用户不存在）时，把除 /setup/、
静态资源、admin 登录页之外的所有请求强制重定向到 /setup/ 创建管理员，
保证在管理员创建完成前无法访问任何业务页面。

判定条件与 setup 视图保持一致（均查 is_superuser），避免出现
"有普通用户但无超管" 时 /setup/ 对未登录者开放的安全空档。

模块级 `_users_exist` 缓存使正常运行期零 DB 查询：一旦确认存在超管即置 True，
后续请求直接放行。`setup` 视图创建管理员后调用 `reset_first_run_cache()` 刷新缓存。
注意：False（无超管）状态不缓存——若运维通过 CLI/shell 创建超管，
下一个请求即可感知，无需重启进程。
"""

from django.shortcuts import redirect
from django.db.utils import OperationalError, ProgrammingError

# 缓存：None=未知/无超管（不缓存），True=已存在超管
_users_exist = None


def reset_first_run_cache():
    """setup 视图创建管理员后调用，使中间件放行后续请求。"""
    global _users_exist
    _users_exist = True


class FirstRunMiddleware:
    """首次运行（无超级管理员）时强制重定向到 /setup/。"""

    SETUP_URL_NAME = 'reader:setup_admin'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _users_exist

        if _users_exist is None:
            try:
                from django.contrib.auth import get_user_model
                has_superuser = get_user_model().objects.filter(is_superuser=True).exists()
            except (OperationalError, ProgrammingError):
                # auth_user 表不存在（未运行 migrate），重定向到 setup 页
                return self._redirect_to_setup(request)
            except Exception:
                # DB 临时故障：放行请求，下次重新检查
                return self.get_response(request)
            # 仅缓存 True（已初始化）；False 不缓存，
            # 使 CLI/shell 创建超管后无需重启即可生效（多 worker 下也一致）
            if has_superuser:
                _users_exist = True

        if _users_exist:
            return self.get_response(request)

        # 首次运行：放行 setup 页自身、静态资源、admin 登录页
        path = request.path_info
        if (path == '/setup/' or path.startswith('/static/')
                or path == '/admin/login/'):
            return self.get_response(request)

        return self._redirect_to_setup(request)

    def _redirect_to_setup(self, request):
        try:
            return redirect(self.SETUP_URL_NAME)
        except Exception:
            # URL 反解失败（极早期启动阶段），回退到硬编码路径
            return redirect('/setup/')
