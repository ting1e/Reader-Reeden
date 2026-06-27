"""首次运行创建管理员视图。

GET  渲染创建管理员表单
POST 校验并创建超级管理员，创建 UserSetting，刷新中间件缓存，
     跳转到首页（reader:index）让用户用新账号手动登录（不自动登录）。

guard：管理员已存在时访问 /setup/ 重定向回首页，防止重复创建。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_protect

from ..utils import get_or_create_user_setting
from ..middleware import reset_first_run_cache

User = get_user_model()


@csrf_protect
def setup_admin(request):
    # 管理员已存在 → 重定向首页（guard，防止重复创建 / 复用入口）
    try:
        superuser_exists = User.objects.filter(is_superuser=True).exists()
    except (OperationalError, ProgrammingError):
        # auth_user 表不存在（未运行 migrate）
        return render(request, 'setup_admin.html', {
            'error': '数据库未初始化，请先在命令行运行：python manage.py migrate',
        })

    if superuser_exists:
        return redirect('reader:index')

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        password2 = request.POST.get('password2') or ''
        errors = []

        if not username:
            errors.append('请输入用户名')
        if not password:
            errors.append('请输入密码')
        if password != password2:
            errors.append('两次输入的密码不一致')

        if not errors:
            try:
                validate_password(password, user=User(username=username))
            except ValidationError as e:
                errors.extend(e.messages)

        if not errors:
            try:
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    is_superuser=True,
                    is_staff=True,
                )
            except Exception as e:
                errors.append(f'创建失败：{e}')

        if not errors:
            get_or_create_user_setting(user)
            reset_first_run_cache()
            # 不自动登录，跳转到首页让用户用新账号手动登录
            return redirect('reader:index')

        return render(request, 'setup_admin.html', {'error': '；'.join(errors)})

    return render(request, 'setup_admin.html')
