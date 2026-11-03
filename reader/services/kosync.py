"""KoSync server storage and authentication helpers."""
import hashlib
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction

from ..models import KosyncProgress, SystemSetting, UserSetting
from .kosync_document import book_document_id

logger = logging.getLogger('reader')

VALID_SYNC_MODES = ('json', 'kosync')
VALID_CHECKSUM_METHODS = ('binary', 'filename')


def md5_hex(password):
    return hashlib.md5(password.encode('utf-8')).hexdigest()


def password_version(user):
    return hashlib.sha256((user.password or '').encode('utf-8')).hexdigest()


def set_auth_hash(user, password):
    """Store make_password(MD5(password)); never keep the plain password."""
    setting, _ = UserSetting.objects.get_or_create(user_id=user.id)
    setting.kosync_auth_hash = make_password(md5_hex(password))
    setting.kosync_password_version = password_version(user)
    setting.save(update_fields=['kosync_auth_hash', 'kosync_password_version'])
    return setting


def clear_auth_hash(user):
    UserSetting.objects.filter(user_id=user.id).update(
        kosync_auth_hash='', kosync_password_version='')


def get_user_setting(user):
    return UserSetting.objects.filter(user_id=user.id).first()


def verified_user(request):
    username = (request.headers.get('x-auth-user') or '').strip()
    key = (request.headers.get('x-auth-key') or '').strip()
    if not username or not key:
        return None
    user = get_user_model().objects.filter(username=username, is_active=True).first()
    if user is None:
        return None
    setting = get_user_setting(user)
    if setting is None or not setting.kosync_auth_hash:
        return None
    if not check_password(key, setting.kosync_auth_hash):
        return None
    current = password_version(user)
    if not setting.kosync_password_version or setting.kosync_password_version != current:
        return None
    return user


def server_enabled():
    return SystemSetting.load().kosync_enabled


def user_sync_mode(user):
    if not getattr(user, 'is_authenticated', False):
        return 'json'
    setting = get_user_setting(user)
    return setting.progress_sync_mode if setting else 'json'


def kosync_active(user):
    return user_sync_mode(user) == 'kosync' and server_enabled()


def checksum_method(user):
    setting = get_user_setting(user)
    method = setting.kosync_checksum_method if setting else 'binary'
    return method if method in VALID_CHECKSUM_METHODS else 'binary'


def serialize_progress(record):
    if record is None:
        return {}
    return {
        'document': record.document,
        'progress': record.progress,
        'percentage': record.percentage,
        'device': record.device,
        'device_id': record.device_id,
        'timestamp': int(record.updated_at.timestamp()),
    }


def get_progress(user, document):
    return KosyncProgress.objects.filter(user=user, document=document).first()


def update_api_progress(user, document, progress, percentage, device='', device_id='',
                        metadata=None):
    def apply(record):
        record.progress = progress
        record.percentage = percentage
        record.device = device
        record.device_id = device_id
        if metadata is not None:
            record.metadata = metadata
        record.save()
        return record

    try:
        with transaction.atomic():
            record = (KosyncProgress.objects.select_for_update()
                      .filter(user=user, document=document).first())
            if record is not None:
                return apply(record)
            return KosyncProgress.objects.create(
                user=user, document=document, progress=progress, percentage=percentage,
                device=device, device_id=device_id, metadata=metadata or {},
            )
    except IntegrityError:
        # Another device created the row between SELECT and INSERT.
        record = KosyncProgress.objects.filter(user=user, document=document).first()
        if record is None:
            raise
        return apply(record)


def book_record(user, book):
    document = book_document_id(book, checksum_method(user))
    return get_progress(user, document)


def _position_for_book(book, chapter, words, location=None):
    from .kosync_position import epub_to_koreader_progress, txt_to_koreader_progress
    if book.format == 'epub':
        return epub_to_koreader_progress(book, chapter, words, location)
    return txt_to_koreader_progress(book, chapter, words)


def update_book_progress(user, book, chapter, words, location=None):
    """Push the current internal reader position to KoSync (best effort).

    No server-side time throttle: local DB/cache already uses its own debounce,
    and dropping an update could lose the final position on page close.
    """
    if not kosync_active(user):
        return None
    try:
        position = _position_for_book(book, chapter, words, location)
        document = book_document_id(book, checksum_method(user))
    except Exception:
        logger.exception('KoSync position conversion failed')
        return None
    record = KosyncProgress.objects.filter(user=user, document=document).first()
    if record is not None:
        same = (record.progress == position['progress']
                and abs(record.percentage - position['percentage']) < 1e-9)
        if same:
            return record
    metadata = {'title': book.name, 'author': book.author}
    return update_api_progress(
        user, document, position['progress'], position['percentage'],
        device='Reader-Reeden', device_id='reader-reeden', metadata=metadata,
    )


def restore_txt_progress(user, book, record):
    """Convert a KoSync record to (chapter, words, exact)."""
    from .kosync_position import koreader_progress_to_txt
    return koreader_progress_to_txt(book, record.progress, record.percentage)


def restore_epub_progress(user, book, record):
    """Convert a KoSync record to (chapter, words, exact, position_record)."""
    from .kosync_position import koreader_progress_to_epub
    return koreader_progress_to_epub(book, record.progress, record.percentage)
