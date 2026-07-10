import os
import shutil

from django.conf import settings
from django.db import migrations


def _get_first_superuser_id(apps):
    User = apps.get_model('auth', 'User')
    su = User.objects.filter(is_superuser=True).order_by('id').first()
    return su.id if su else 1


def _resolve_target_user(book, fallback_user_id):
    """返回书籍应归属的 user_id。"""
    if book.uploader:
        return book.uploader
    return fallback_user_id


def _move_file(src, dst):
    """将 src 移动到 dst，自动创建目标目录，跳过不存在的源文件。"""
    if not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.move(src, dst)
        return True
    except Exception:
        # 若目标已存在（同名），覆盖
        try:
            if os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)
            return True
        except Exception:
            return False


def _copy_file(src, dst):
    """将 src 复制到 dst，自动创建目标目录。"""
    if not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def _rel(base_dir, abs_path):
    return os.path.relpath(abs_path, base_dir)


def migrate_forward(apps, schema_editor):
    base_dir = str(settings.BASE_DIR)
    local_dir = os.path.join(base_dir, 'local')
    Book = apps.get_model('reader', 'Book')
    Chapter = apps.get_model('reader', 'Chapter')
    UserSetting = apps.get_model('reader', 'UserSetting')

    fallback_user_id = _get_first_superuser_id(apps)

    # 1. 迁移书籍文件（books/ 和 upload/）并更新 book_url
    for book in Book.objects.iterator():
        target_uid = _resolve_target_user(book, fallback_user_id)
        if not book.book_url:
            continue
        if os.path.isabs(book.book_url):
            abs_path = book.book_url
        else:
            abs_path = os.path.join(base_dir, book.book_url)

        if not os.path.exists(abs_path):
            continue

        file_name = os.path.basename(abs_path)
        if getattr(book, 'local_only', False):
            new_dir = os.path.join(local_dir, str(target_uid), 'upload')
        else:
            new_dir = os.path.join(local_dir, str(target_uid), 'books')
        new_abs = os.path.join(new_dir, file_name)

        if abs_path == new_abs:
            continue
        if not _move_file(abs_path, new_abs):
            continue

        new_rel = _rel(base_dir, new_abs)
        old_rel = book.book_url
        book.book_url = new_rel
        book.save(update_fields=['book_url'])

        # 同步更新 Chapter.book_url
        for ch in Chapter.objects.filter(book_url=old_rel):
            ch.book_url = new_rel
            ch.save(update_fields=['book_url'])

    # 2. 迁移进度文件 book_progress/{md5}.json -> local/{uid}/book_progress/{md5}.json
    old_progress_dir = os.path.join(local_dir, 'book_progress')
    if os.path.isdir(old_progress_dir):
        # 建立 md5 -> target_uid 映射
        md5_to_uid = {}
        for book in Book.objects.iterator():
            if book.md5:
                md5_to_uid[book.md5] = _resolve_target_user(book, fallback_user_id)
        for fn in os.listdir(old_progress_dir):
            if not fn.endswith('.json'):
                continue
            md5_val = fn[:-5]  # 去掉 .json
            uid = md5_to_uid.get(md5_val, fallback_user_id)
            src = os.path.join(old_progress_dir, fn)
            dst_dir = os.path.join(local_dir, str(uid), 'book_progress')
            dst = os.path.join(dst_dir, fn)
            _move_file(src, dst)
        # 清理旧空目录
        try:
            if os.path.isdir(old_progress_dir) and not os.listdir(old_progress_dir):
                os.rmdir(old_progress_dir)
        except Exception:
            pass

    # 3. 迁移字体：复制到每个 UserSetting 用户的目录
    old_fonts_dir = os.path.join(local_dir, 'fonts')
    if os.path.isdir(old_fonts_dir) and os.listdir(old_fonts_dir):
        user_ids = list(UserSetting.objects.values_list('user_id', flat=True))
        if not user_ids:
            user_ids = [fallback_user_id]
        font_files = []
        for fn in os.listdir(old_fonts_dir):
            src = os.path.join(old_fonts_dir, fn)
            if os.path.isfile(src):
                font_files.append(fn)
        for uid in user_ids:
            for fn in font_files:
                src = os.path.join(old_fonts_dir, fn)
                dst = os.path.join(local_dir, str(uid), 'fonts', fn)
                _copy_file(src, dst)
        # 删除旧的共享字体目录
        try:
            shutil.rmtree(old_fonts_dir)
        except Exception:
            pass

    # 4. 清理旧的空目录 books/ 和 upload/（文件已移走）
    for old_sub in ('books', 'upload'):
        old_path = os.path.join(local_dir, old_sub)
        try:
            if os.path.isdir(old_path) and not os.listdir(old_path):
                os.rmdir(old_path)
        except Exception:
            pass


def migrate_backward(apps, schema_editor):
    """反向迁移：将 per-user 目录下的文件移回全局目录，恢复 book_url。"""
    base_dir = str(settings.BASE_DIR)
    local_dir = os.path.join(base_dir, 'local')
    Book = apps.get_model('reader', 'Book')
    Chapter = apps.get_model('reader', 'Chapter')
    UserSetting = apps.get_model('reader', 'UserSetting')

    fallback_user_id = _get_first_superuser_id(apps)

    # 1. 恢复书籍文件到全局 books/ 或 upload/
    global_books_dir = os.path.join(local_dir, 'books')
    global_upload_dir = os.path.join(local_dir, 'upload')
    os.makedirs(global_books_dir, exist_ok=True)
    os.makedirs(global_upload_dir, exist_ok=True)

    for book in Book.objects.iterator():
        if not book.book_url:
            continue
        abs_path = os.path.join(base_dir, book.book_url) if not os.path.isabs(book.book_url) else book.book_url
        if not os.path.exists(abs_path):
            continue
        file_name = os.path.basename(abs_path)
        if getattr(book, 'local_only', False):
            new_abs = os.path.join(global_upload_dir, file_name)
        else:
            new_abs = os.path.join(global_books_dir, file_name)
        if abs_path == new_abs:
            continue
        if not _move_file(abs_path, new_abs):
            continue
        old_rel = book.book_url
        new_rel = _rel(base_dir, new_abs)
        book.book_url = new_rel
        book.save(update_fields=['book_url'])
        for ch in Chapter.objects.filter(book_url=old_rel):
            ch.book_url = new_rel
            ch.save(update_fields=['book_url'])

    # 2. 恢复进度文件到全局 book_progress/
    global_progress_dir = os.path.join(local_dir, 'book_progress')
    os.makedirs(global_progress_dir, exist_ok=True)
    md5_to_uid = {}
    for book in Book.objects.iterator():
        if book.md5:
            md5_to_uid[book.md5] = _resolve_target_user(book, fallback_user_id)
    for md5_val, uid in md5_to_uid.items():
        src = os.path.join(local_dir, str(uid), 'book_progress', f'{md5_val}.json')
        dst = os.path.join(global_progress_dir, f'{md5_val}.json')
        _move_file(src, dst)

    # 3. 恢复字体到全局 fonts/（取第一个用户的字体集合）
    global_fonts_dir = os.path.join(local_dir, 'fonts')
    os.makedirs(global_fonts_dir, exist_ok=True)
    uid = fallback_user_id
    user_fonts_dir = os.path.join(local_dir, str(uid), 'fonts')
    if os.path.isdir(user_fonts_dir):
        for fn in os.listdir(user_fonts_dir):
            src = os.path.join(user_fonts_dir, fn)
            if os.path.isfile(src):
                _move_file(src, os.path.join(global_fonts_dir, fn))


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0017_usersetting_chapter_rules'),
    ]

    operations = [
        migrations.RunPython(migrate_forward, reverse_code=migrate_backward),
    ]
