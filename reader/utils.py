import os
import hashlib
import logging
import json
import uuid

from django.conf import settings

BASE_DIR = str(settings.BASE_DIR)
logger = logging.getLogger('reader')

DEFAULT_CHAPTER_RULE = r'^[ 　\t]{0,4}(?:序章|楔子|正文(?!完|结)|终章|后记|尾声|番外|第\s{0,4}[\d〇零一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+?\s{0,4}(?:章|折|节(?!课)|卷|集(?![合和])|部(?![分赛游])|篇(?!张))).{0,30}$'


def get_progress_dir():
    d = os.path.join(BASE_DIR, 'local', 'book_progress')
    os.makedirs(d, exist_ok=True)
    return d


def get_local_books_dir():
    d = os.path.join(BASE_DIR, 'local', 'books')
    os.makedirs(d, exist_ok=True)
    return d


def get_upload_dir():
    d = os.path.join(BASE_DIR, 'local', 'upload')
    os.makedirs(d, exist_ok=True)
    return d


def to_rel_path(abs_path):
    """将 BASE_DIR 下的绝对路径转为项目相对路径。"""
    return os.path.relpath(abs_path, BASE_DIR)


def resolve_book_path(book_url):
    """将存储的 book_url（项目相对路径）解析为绝对路径；已是绝对路径则原样返回。"""
    if os.path.isabs(book_url):
        return book_url
    return os.path.join(BASE_DIR, book_url)


FONT_EXTENSIONS = {
    '.ttf': 'truetype',
    '.otf': 'opentype',
    '.woff': 'woff',
    '.woff2': 'woff2',
}


def get_fonts_dir():
    d = os.path.join(BASE_DIR, 'local', 'fonts')
    os.makedirs(d, exist_ok=True)
    return d


def get_local_fonts():
    """扫描 local/fonts/，返回 [{name, file_name, ext, format}, ...]"""
    fonts_dir = get_fonts_dir()
    result = []
    for fn in sorted(os.listdir(fonts_dir)):
        ext = os.path.splitext(fn)[1].lower()
        if ext in FONT_EXTENSIONS:
            result.append({
                'name': os.path.splitext(fn)[0],
                'file_name': fn,
                'ext': ext,
                'format': FONT_EXTENSIONS[ext],
            })
    return result


def get_file_md5(file_path):
    with open(file_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest().upper()


def get_element_index(text):
    idx = 0
    for c in text:
        if ord(c) > 127:
            idx += 2
        else:
            idx += 1
    return idx


def get_device_id():
    import uuid
    device_id_file = os.path.join(BASE_DIR, 'local', '.device_id')
    if os.path.exists(device_id_file):
        try:
            with open(device_id_file, 'r') as f:
                return f.read().strip()
        except Exception:
            logger.exception("get_device_id: error reading device_id file")
    device_id = str(uuid.uuid4())
    try:
        os.makedirs(os.path.dirname(device_id_file), exist_ok=True)
        with open(device_id_file, 'w') as f:
            f.write(device_id)
    except Exception:
        logger.exception("get_device_id: error writing device_id file")
    return device_id


def can_access_book(book, user):
    """检查用户是否有权阅读该书：共享书、自己上传的、或超级管理员。"""
    return book.share or book.uploader == user.id or user.is_superuser


def can_admin_book(book, user):
    """检查用户是否有权管理该书（删除/重新分章）：超级管理员或上传者本人。"""
    return user.is_superuser or user.id == book.uploader


def get_or_create_user_setting(user):
    """获取或创建用户设置，统一默认值。"""
    from .models import UserSetting
    return UserSetting.objects.get_or_create(
        user_id=user.id,
        defaults={'font_size': 16, 'read_bg': '#fff', 'read_mode': 'page', 'line_height': 1.2, 'theme': 'light'},
    )[0]


def parse_s3_json(raw):
    """解析 S3 配置 JSON 字符串，兼容双重编码格式。"""
    parsed = json.loads(raw)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    return parsed
