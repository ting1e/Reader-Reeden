import re
import logging
from urllib.parse import quote

from django.http import HttpResponse
from django.urls import reverse
from django.shortcuts import redirect, render
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required

from ..models import UserSetting
from ..utils import get_or_create_user_setting, parse_s3_json

logger = logging.getLogger('reader')

DAISYUI_THEMES = [
    'light', 'dark', 'cupcake', 'bumblebee', 'emerald', 'corporate',
    'synthwave', 'retro', 'cyberpunk', 'valentine', 'halloween', 'garden',
    'forest', 'aqua', 'lofi', 'pastel', 'fantasy', 'wireframe', 'black',
    'luxury', 'dracula', 'cmyk', 'autumn', 'business', 'acid', 'lemonade',
    'night', 'coffee', 'winter', 'dim', 'nord', 'sunset', 'caramellatte',
    'silk', 'abyss',
]


def _settings_redirect(message, kind='err'):
    """跳转回个人设置页并附带 URL 编码后的 err/msg 提示。"""
    return redirect(reverse('reader:user_settings') + '?%s=%s' % (kind, quote(message)))


@login_required(login_url='reader:index')
def user_settings(request):
    """个人设置页面：S3 配置、分章规则、修改密码"""
    setting = get_or_create_user_setting(request.user)
    return render(request, 'user_settings.html', {
        'setting': setting,
        'msg': request.GET.get('msg', ''),
        'err': request.GET.get('err', ''),
    })


@login_required(login_url='reader:index')
def user_settings_s3(request):
    """保存 S3 配置"""
    if request.method != 'POST':
        return redirect('reader:user_settings')
    s3_raw = request.POST.get('s3_setting', '').strip()
    if not s3_raw:
        return _settings_redirect('S3配置不能为空')
    try:
        parsed = parse_s3_json(s3_raw)
        required = ['accessKeyId', 'secretAccessKey', 'endpoint', 'bucket']
        missing = [k for k in required if not parsed.get(k)]
        if missing:
            return _settings_redirect('S3配置缺少字段: ' + ','.join(missing))
    except Exception as e:
        logger.warning("user_settings_s3: JSON parse error", exc_info=True)
        return _settings_redirect('S3配置JSON格式错误: ' + str(e)[:80])
    setting = get_or_create_user_setting(request.user)
    setting.s3_setting = s3_raw
    setting.save()
    return _settings_redirect('S3配置已保存', kind='msg')


@login_required(login_url='reader:index')
def user_settings_rule(request):
    """保存分章规则"""
    if request.method != 'POST':
        return redirect('reader:user_settings')
    rule = request.POST.get('chapter_rule', '').strip()
    rule_2 = request.POST.get('chapter_rule_2', '').strip()
    rule_3 = request.POST.get('chapter_rule_3', '').strip()
    if not rule:
        return _settings_redirect('主分章规则不能为空')
    for label, r in [('主规则', rule), ('备用规则1', rule_2), ('备用规则2', rule_3)]:
        if r:
            try:
                re.compile(r)
            except re.error as e:
                return _settings_redirect('%s正则表达式错误: %s' % (label, str(e)[:80]))
    setting = get_or_create_user_setting(request.user)
    setting.chapter_rule = rule
    setting.chapter_rule_2 = rule_2
    setting.chapter_rule_3 = rule_3
    setting.save()
    return _settings_redirect('分章规则已保存', kind='msg')


@login_required(login_url='reader:index')
def user_settings_password(request):
    """修改密码"""
    if request.method != 'POST':
        return redirect('reader:user_settings')
    old_pwd = request.POST.get('old_password', '')
    new_pwd = request.POST.get('new_password', '')
    confirm_pwd = request.POST.get('confirm_password', '')
    if not request.user.check_password(old_pwd):
        return _settings_redirect('旧密码不正确')
    if not new_pwd:
        return _settings_redirect('新密码不能为空')
    if new_pwd != confirm_pwd:
        return _settings_redirect('两次输入的新密码不一致')
    request.user.set_password(new_pwd)
    request.user.save()
    update_session_auth_hash(request, request.user)
    return _settings_redirect('密码已修改', kind='msg')


@login_required(login_url='reader:index')
def update_setting(request):
    if request.method != 'POST':
        return HttpResponse('not login')
    try:
        font_size = int(request.POST.get('font_size', ''))
    except (TypeError, ValueError):
        return HttpResponse('invalid font_size')
    if not 1 <= font_size <= 1000:
        return HttpResponse('invalid font_size')
    read_bg = (request.POST.get('read_bg') or '').strip() or '#fff'
    if not re.match(r'^(#[0-9a-fA-F]{3,8}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)|var\(--[a-z][-a-z0-9]*\))$', read_bg):
        return HttpResponse('invalid read_bg')
    read_mode = request.POST.get('read_mode', 'page') or 'page'
    if read_mode not in ('page', 'slide'):
        return HttpResponse('invalid read_mode')
    font_family = (request.POST.get('font_family') or '').strip()
    font_color = (request.POST.get('font_color') or '').strip()
    if font_color and not re.match(r'^#[0-9a-fA-F]{3,8}$', font_color):
        return HttpResponse('invalid font_color')
    try:
        letter_spacing = int(request.POST.get('letter_spacing', 0) or 0)
    except (TypeError, ValueError):
        return HttpResponse('invalid letter_spacing')
    if not -10 <= letter_spacing <= 50:
        return HttpResponse('invalid letter_spacing')
    try:
        line_height = float(request.POST.get('line_height', 0) or 0)
    except (TypeError, ValueError):
        return HttpResponse('invalid line_height')
    if not 0 <= line_height <= 10:
        return HttpResponse('invalid line_height')
    font_weight = (request.POST.get('font_weight') or '').strip()
    if font_weight and font_weight not in ('100','200','300','400','500','600','700','800','900','normal','bold','lighter','bolder'):
        return HttpResponse('invalid font_weight')
    UserSetting.objects.update_or_create(
        user_id=request.user.id,
        defaults={
            'font_size': font_size,
            'read_bg': read_bg,
            'read_mode': read_mode,
            'font_family': font_family,
            'font_color': font_color,
            'letter_spacing': letter_spacing,
            'line_height': line_height,
            'font_weight': font_weight,
        },
    )
    return HttpResponse('ok')


@login_required(login_url='reader:index')
def set_theme(request):
    """保存用户选择的 daisyUI 主题。"""
    if request.method != 'POST':
        return HttpResponse('method not allowed')
    theme = (request.POST.get('theme') or '').strip()
    if theme not in DAISYUI_THEMES:
        return HttpResponse('invalid theme')
    setting = get_or_create_user_setting(request.user)
    setting.theme = theme
    setting.save()
    return HttpResponse('ok')
