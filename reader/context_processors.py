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

THEME_LABELS = {
    'light': '明亮',
    'dark': '深色',
    'cupcake': '纸杯蛋糕',
    'bumblebee': '大黄蜂',
    'emerald': '翡翠绿',
    'corporate': '商务',
    'synthwave': '合成波',
    'retro': '复古',
    'cyberpunk': '赛博朋克',
    'valentine': '情人节',
    'halloween': '万圣节',
    'garden': '花园',
    'forest': '森林',
    'aqua': '水蓝',
    'lofi': '低保真',
    'pastel': '粉彩',
    'fantasy': '幻想',
    'wireframe': '线框',
    'black': '纯黑',
    'luxury': '奢华',
    'dracula': '德古拉',
    'cmyk': 'CMYK',
    'autumn': '秋日',
    'business': '商业',
    'acid': '酸性',
    'lemonade': '柠檬水',
    'night': '夜晚',
    'coffee': '咖啡',
    'winter': '冬日',
    'dim': '朦胧',
    'nord': '北欧',
    'sunset': '日落',
    'caramellatte': '焦糖拿铁',
    'silk': '丝绸',
    'abyss': '深渊',
}


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
    return {
        'daisyui_theme': theme,
        'daisyui_themes': [(k, THEME_LABELS.get(k, k)) for k in DAISYUI_THEMES],
        'theme_labels': THEME_LABELS,
    }
