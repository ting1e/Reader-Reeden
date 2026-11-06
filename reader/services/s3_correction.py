"""Local S3 file observations and the optional stale-progress correction rule."""
import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone

from ..models import Book, Chapter, S3CorrectionState, UserSetting
from . import volume
from .progress import _parse_progress_time

logger = logging.getLogger('reader.s3_progress')
POSITION_FIELDS = ('sectionIndex', 'paragraphIndex', 'elementIndex', 'readProgress', 'readerEpub')


def content_id(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def capture_snapshot(user_id, book, data):
    if not UserSetting.objects.filter(user_id=user_id, s3_correction_enabled=True).exists():
        return
    if book.local_only:
        return
    payload = copy.deepcopy(data)
    if book.format != 'epub':
        payload = volume.progress_to_sync(payload, list(
            Chapter.objects.filter(book_id=book.id).order_by('index')))
    S3CorrectionState.objects.update_or_create(
        user_id=user_id, book_md5=payload['bookId'],
        defaults={'snapshot': {'data': payload}},
    )


def should_correct(modified_at, data, snapshot, delay_minutes=5):
    if not modified_at or not snapshot:
        return False
    modified = _parse_progress_time({'lastReadTime': modified_at.isoformat()})
    read = _parse_progress_time(data)
    s = _parse_progress_time(snapshot['data'])
    return bool(modified and read and s
                and modified - read > timedelta(minutes=delay_minutes) and s > read)


def process_file(user_id, filename):
    """Called serially by the watcher; only successful writes consume the quota."""
    setting = UserSetting.objects.filter(user_id=user_id, s3_correction_enabled=True).first()
    if not setting:
        return False
    path = Path(filename)
    if not setting.s3_progress_path or path.suffix != '.json' or path.parent.resolve() != Path(setting.s3_progress_path).resolve():
        return False
    with path.open(encoding='utf-8') as stream:
        data = json.load(stream)
    md5 = path.stem
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, datetime_timezone.utc)
    return process_data(setting, md5, data, modified_at=modified_at)


def process_data(setting, md5, data, modified_at=None):
    user_id = setting.user_id
    if not UserSetting.objects.filter(pk=setting.pk, s3_correction_enabled=True).exists():
        return False
    if not isinstance(data, dict) or data.get('bookId') != md5:
        return False
    book = Book.objects.filter(md5=md5, local_only=False).first()
    if not book or not data.get('deviceId') or _parse_progress_time(data) is None:
        return False
    state, _ = S3CorrectionState.objects.get_or_create(user_id=user_id, book_md5=md5)
    # Identical JSON uploaded again can have a new modification time.
    digest = content_id({'data': data, 'modified': modified_at.isoformat() if modified_at else None})
    if digest == state.last_content:
        return False
    if content_id(data) == state.own_content:
        state.last_content = digest
        state.save(update_fields=['last_content'])
        return False
    device = str(data['deviceId'])
    state.last_content = digest
    state.save(update_fields=['last_content'])
    eligible = should_correct(modified_at, data, state.snapshot, setting.s3_upload_delay_minutes)
    logger.info('S3 correction observation user=%s book=%s device=%s modified=%s read=%s server_read=%s delay_minutes=%s eligible=%s',
                user_id, md5, device, modified_at, data['lastReadTime'],
                state.snapshot.get('data', {}).get('lastReadTime'), setting.s3_upload_delay_minutes, eligible)
    if not eligible:
        return False
    now = timezone.now()
    count = state.correction_count
    if not state.window_start or now - state.window_start >= timedelta(minutes=setting.s3_correction_window_minutes):
        count = 0
    if count >= setting.s3_correction_limit:
        logger.info('S3 correction skipped: quota user=%s book=%s count=%s limit=%s',
                    user_id, md5, count, setting.s3_correction_limit)
        return False
    from .s3 import _get_s3_client, get_s3_config
    cfg = get_s3_config(get_user_model().objects.get(pk=user_id))
    if not cfg:
        return False
    corrected = copy.deepcopy(data)
    source = state.snapshot['data']
    for key in POSITION_FIELDS:
        corrected.pop(key, None)
        if key in source:
            corrected[key] = source[key]
    corrected['deviceId'] = source['deviceId']
    corrected['lastReadTime'] = now.isoformat().replace('+00:00', 'Z')
    body = json.dumps(corrected, ensure_ascii=False).encode('utf-8')
    # No ETag concurrency policy: this feature deliberately follows the agreed rule.
    _get_s3_client(cfg).put_object(
        Bucket=cfg['bucket'], Key=f"{cfg['prefix']}book_progress/{md5}.json",
        Body=body, ContentLength=len(body),
    )
    state.own_content = content_id(corrected)
    if count == 0:
        state.window_start = now
    state.correction_count = count + 1
    state.save(update_fields=['own_content', 'window_start', 'correction_count'])
    logger.info('Corrected S3 progress user=%s book=%s device=%s modified=%s device_read=%s server_read=%s old_position=%s new_position=%s written_read=%s count=%s limit=%s window_minutes=%s',
                user_id, md5, device, modified_at, data['lastReadTime'], source['lastReadTime'],
                [data.get(k) for k in POSITION_FIELDS[:4]],
                [corrected.get(k) for k in POSITION_FIELDS[:4]], corrected['lastReadTime'],
                count + 1, setting.s3_correction_limit, setting.s3_correction_window_minutes)
    return True
