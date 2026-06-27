import os
import logging

from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from urllib.parse import quote

from ..utils import FONT_EXTENSIONS, get_fonts_dir, get_local_fonts
from ..services.s3 import get_s3_config, _get_s3_client

logger = logging.getLogger('reader')


def _font_admin_redirect(message, kind='err'):
    from django.urls import reverse
    return redirect(reverse('reader:font_admin') + '?%s=%s' % (kind, quote(message)))


@login_required(login_url='reader:index')
def font_admin(request):
    """字体管理：列出 S3 字体库和本地字体"""
    s3_fonts = []
    s3_error = None
    cfg = get_s3_config(request.user)
    if not cfg:
        s3_error = '未配置 S3，请在个人设置中填写 S3 连接信息'
    else:
        target_prefix = cfg['prefix'] + 'fonts/'
        try:
            client = _get_s3_client(cfg)
            response = client.list_objects_v2(Bucket=cfg['bucket'], Prefix=target_prefix)
            local_names = {f['file_name'] for f in get_local_fonts()}
            if 'Contents' in response:
                for obj in response['Contents']:
                    if obj['Key'] == target_prefix:
                        continue
                    filename = obj['Key'][len(target_prefix):]
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in FONT_EXTENSIONS:
                        continue
                    s3_fonts.append({
                        'name': filename,
                        'in_local': filename in local_names,
                    })
        except Exception as e:
            logger.exception("font_admin: S3 list error")
            s3_error = str(e)

    local_fonts = get_local_fonts()
    return render(request, 'font_admin.html', {
        's3_fonts': s3_fonts,
        'local_fonts': local_fonts,
        's3_error': s3_error,
        'msg': request.GET.get('msg', ''),
        'err': request.GET.get('err', ''),
    })


@login_required(login_url='reader:index')
def font_download(request):
    """从 S3 下载字体到 local/fonts/"""
    if request.method != 'POST':
        return redirect('reader:font_admin')
    name = os.path.basename(request.POST.get('name', ''))
    if not name or os.path.splitext(name)[1].lower() not in FONT_EXTENSIONS:
        return _font_admin_redirect('无效的字体文件名')
    cfg = get_s3_config(request.user)
    if not cfg:
        return _font_admin_redirect('S3 未配置')
    s3_key = f"{cfg['prefix']}fonts/{name}"
    local_path = os.path.join(get_fonts_dir(), name)
    try:
        client = _get_s3_client(cfg)
        client.download_file(cfg['bucket'], s3_key, local_path)
    except Exception as e:
        logger.exception("font_download error")
        return _font_admin_redirect(f'下载失败: {e}')
    return _font_admin_redirect(f'{name} 已下载', kind='msg')


@login_required(login_url='reader:index')
def font_del(request, name):
    """删除本地字体文件"""
    if request.method != 'POST':
        return redirect('reader:font_admin')
    name = os.path.basename(name)
    if not name or os.path.splitext(name)[1].lower() not in FONT_EXTENSIONS:
        return _font_admin_redirect('无效的字体文件名')
    local_path = os.path.join(get_fonts_dir(), name)
    try:
        if os.path.exists(local_path):
            os.remove(local_path)
        else:
            return _font_admin_redirect('文件不存在')
    except Exception as e:
        logger.exception("font_del error")
        return _font_admin_redirect(f'删除失败: {e}')
    return _font_admin_redirect(f'{name} 已删除', kind='msg')


def font_file(request, name):
    """供 @font-face 加载的字体文件服务"""
    name = os.path.basename(name)
    ext = os.path.splitext(name)[1].lower()
    if ext not in FONT_EXTENSIONS:
        raise Http404
    local_path = os.path.join(get_fonts_dir(), name)
    if not os.path.exists(local_path):
        raise Http404
    content_types = {
        '.ttf': 'font/ttf',
        '.otf': 'font/otf',
        '.woff': 'font/woff',
        '.woff2': 'font/woff2',
    }
    return FileResponse(
        open(local_path, 'rb'),
        content_type=content_types[ext],
    )
