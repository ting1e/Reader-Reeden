import os
import logging

from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required

from ..utils import FONT_EXTENSIONS, get_fonts_dir, get_local_fonts, fmt_file_size
from ..services.s3 import get_s3_config, _get_s3_client

logger = logging.getLogger('reader')


@login_required(login_url='reader:index')
def font_admin(request):
    """字体管理：列出云端字体库和本地字体"""
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
            local_names = {f['file_name'] for f in get_local_fonts(request.user.id)}
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
                        'size': obj.get('Size', 0),
                        'size_display': fmt_file_size(obj.get('Size', 0)),
                    })
        except Exception as e:
            logger.exception("font_admin: S3 list error")
            s3_error = f'云端字体库列表获取失败: {e}'

    local_fonts = get_local_fonts(request.user.id)
    for f in local_fonts:
        f['size_display'] = fmt_file_size(f.get('size', 0))
    return render(request, 'font_admin.html', {
        's3_fonts': s3_fonts,
        'local_fonts': local_fonts,
        's3_error': s3_error,
    })


@login_required(login_url='reader:index')
def font_download(request):
    """从 S3 下载字体到 local/fonts/（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return redirect('reader:font_admin')
    name = os.path.basename(request.POST.get('name', ''))
    if not name or os.path.splitext(name)[1].lower() not in FONT_EXTENSIONS:
        return JsonResponse({'success': False, 'error': '无效的字体文件名'})
    cfg = get_s3_config(request.user)
    if not cfg:
        return JsonResponse({'success': False, 'error': 'S3 未配置'})
    s3_key = f"{cfg['prefix']}fonts/{name}"
    local_path = os.path.join(get_fonts_dir(request.user.id), name)
    try:
        client = _get_s3_client(cfg)
        client.download_file(cfg['bucket'], s3_key, local_path)
    except Exception as e:
        logger.exception("font_download error")
        return JsonResponse({'success': False, 'error': f'字体下载失败: {e}'})
    try:
        size = os.path.getsize(local_path)
    except OSError:
        size = 0
    return JsonResponse({
        'success': True,
        'name': name,
        'size': size,
        'size_display': fmt_file_size(size),
    })


@login_required(login_url='reader:index')
def font_del(request, name):
    """删除本地字体文件（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return redirect('reader:font_admin')
    name = os.path.basename(name)
    if not name or os.path.splitext(name)[1].lower() not in FONT_EXTENSIONS:
        return JsonResponse({'success': False, 'error': '无效的字体文件名'})
    local_path = os.path.join(get_fonts_dir(request.user.id), name)
    try:
        if os.path.exists(local_path):
            os.remove(local_path)
        else:
            return JsonResponse({'success': False, 'error': '文件不存在'})
    except Exception as e:
        logger.exception("font_del error")
        return JsonResponse({'success': False, 'error': f'删除失败: {e}'})
    return JsonResponse({'success': True, 'name': name})


@login_required(login_url='reader:index')
def font_file(request, name):
    """供 @font-face 加载的字体文件服务"""
    name = os.path.basename(name)
    ext = os.path.splitext(name)[1].lower()
    if ext not in FONT_EXTENSIONS:
        raise Http404
    local_path = os.path.join(get_fonts_dir(request.user.id), name)
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
