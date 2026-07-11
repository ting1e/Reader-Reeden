import os
import hashlib
import logging
import json
import uuid

from django.conf import settings
from django.db.models import Q

BASE_DIR = str(settings.BASE_DIR)
logger = logging.getLogger('reader')

DEFAULT_CHAPTER_RULE = r'^[ 　\t]{0,4}(?:序章|楔子|正文(?!完|结)|终章|后记|尾声|番外|第\s{0,4}[\d〇零一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+?\s{0,4}(?:章|折|节(?!课)|卷|集(?![合和])|部(?![分赛游])|篇(?!张))).{0,30}$'


def get_progress_dir(user_id):
    """返回指定用户的进度目录：local/{user_id}/book_progress/"""
    d = os.path.join(BASE_DIR, 'local', str(user_id), 'book_progress')
    os.makedirs(d, exist_ok=True)
    return d


def get_local_books_dir(user_id):
    """返回指定用户的 S3 下载书籍目录：local/{user_id}/books/"""
    d = os.path.join(BASE_DIR, 'local', str(user_id), 'books')
    os.makedirs(d, exist_ok=True)
    return d


def get_upload_dir(user_id):
    """返回指定用户的本地上传目录：local/{user_id}/upload/"""
    d = os.path.join(BASE_DIR, 'local', str(user_id), 'upload')
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


def fmt_file_size(n):
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        s = f'{n / 1024:.1f}'.rstrip('0').rstrip('.')
        return f'{s} KB'
    if n < 1024 * 1024 * 1024:
        s = f'{n / 1024 / 1024:.1f}'.rstrip('0').rstrip('.')
        return f'{s} MB'
    s = f'{n / 1024 / 1024 / 1024:.2f}'.rstrip('0').rstrip('.')
    return f'{s} GB'


def get_fonts_dir(user_id):
    """返回指定用户的字体目录：local/{user_id}/fonts/"""
    d = os.path.join(BASE_DIR, 'local', str(user_id), 'fonts')
    os.makedirs(d, exist_ok=True)
    return d


def get_local_fonts(user_id):
    """扫描指定用户的字体目录，返回 [{name, file_name, ext, format, size}, ...]

    user_id 为 falsy（None/0/匿名）时返回空列表。
    """
    if not user_id:
        return []
    fonts_dir = get_fonts_dir(user_id)
    result = []
    for fn in sorted(os.listdir(fonts_dir)):
        ext = os.path.splitext(fn)[1].lower()
        if ext in FONT_EXTENSIONS:
            full = os.path.join(fonts_dir, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            result.append({
                'name': os.path.splitext(fn)[0],
                'file_name': fn,
                'ext': ext,
                'format': FONT_EXTENSIONS[ext],
                'size': size,
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


def get_accessible_books(user):
    """返回用户可访问的书籍 queryset（共享书、自己上传的、超管全部）。"""
    from .models import Book
    if user.is_superuser:
        return Book.objects.all()
    if user.is_authenticated:
        return Book.objects.filter(share=True) | Book.objects.filter(uploader=user.id)
    return Book.objects.filter(share=True)


def can_admin_booklist(booklist, user):
    """检查用户是否有权管理书单（编辑/删除/添加/移除）：超级管理员或创建者本人。"""
    return user.is_superuser or user.id == booklist.user_id


def can_view_booklist(booklist, user):
    """检查用户是否有权查看书单：公开书单、自己创建的、或超级管理员。"""
    return booklist.is_public or user.is_superuser or user.id == booklist.user_id


def link_external_booklist_items(user, book):
    """将当前用户书单中匹配的外部条目关联到已入库书籍。

    仅更新 user 自己创建的书单；按 book.name / file_name 及其去扩展名形式匹配 manual_name。
    返回更新行数。
    """
    if not user or not getattr(user, 'is_authenticated', False) or not book:
        return 0

    names = set()
    for raw in (getattr(book, 'name', None), getattr(book, 'file_name', None)):
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        names.add(text)
        stem, _ = os.path.splitext(text)
        if stem:
            names.add(stem)
    if not names:
        return 0

    from .models import BookList, BookListItem

    my_list_ids = list(BookList.objects.filter(user_id=user.id).values_list('id', flat=True))
    if not my_list_ids:
        return 0

    name_q = Q()
    for n in names:
        name_q |= Q(manual_name__iexact=n)

    return BookListItem.objects.filter(
        book_list_id__in=my_list_ids,
        book_id=0,
    ).filter(name_q).update(
        book_id=book.id,
        manual_name='',
        manual_author='',
    )


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
