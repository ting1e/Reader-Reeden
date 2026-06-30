"""daisyUI 主题上下文处理器。

为每个请求注入当前用户选择的 daisyUI 主题名（daisyui_theme）和可选主题列表
（daisyui_themes），供 base.html 的 <html data-theme="..."> 与 header 主题选择器使用。

登录用户从 UserSetting.theme 读取；未登录用户返回 'light'（由前端 localStorage 覆盖）。
"""

DAISYUI_THEMES = [
    'light', 'dark', 'cupcake', 'bumblebee', 'emerald', 'corporate',
    'synthwave', 'retro', 'cyberpunk', 'valentine', 'halloween', 'garden',
    'forest', 'aqua', 'lofi', 'pastel', 'fantasy', 'wireframe', 'black',
    'luxury', 'dracula', 'cmyk', 'autumn', 'business', 'acid', 'lemonade',
    'night', 'coffee', 'winter', 'dim', 'nord', 'sunset', 'caramellatte',
    'silk', 'abyss',
]


def daisyui_theme(request):
    theme = 'light'
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        try:
            from .models import UserSetting
            s = UserSetting.objects.filter(user_id=user.id).first()
            if s and s.theme:
                theme = s.theme
        except Exception:
            pass
    return {'daisyui_theme': theme, 'daisyui_themes': DAISYUI_THEMES}
