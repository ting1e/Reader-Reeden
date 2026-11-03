"""Read-only restoration; opening an EPUB never publishes a new cloud position."""
import json
import logging
import os
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.utils import timezone

from ..models import Chapter, UserBookRecord
from ..utils import get_progress_dir
from .epub import import_position
from .progress import _parse_progress_time, calculate_read_progress, make_naive_utc
from .s3 import get_s3_config, _get_s3_client

logger = logging.getLogger('reader')


def _kosync_record(request, book):
    from . import kosync
    if not kosync.kosync_active(request.user):
        return None
    try:
        record = kosync.book_record(request.user, book)
        if record is None:
            return None
        chapter, words, exact, position = kosync.restore_epub_progress(
            request.user, book, record)
        record.restored = (chapter, words, exact, position)
        return record
    except Exception:
        logger.exception('KoSync EPUB progress restore failed')
        return None


def restore_epub_progress(request, book):
    default = (book.first_chapter_id, 0, False)
    if not request.user.is_authenticated:
        return default
    from . import kosync
    kosync_mode = kosync.user_sync_mode(request.user) == 'kosync'
    kosync_enabled = kosync.server_enabled()
    record = UserBookRecord.objects.filter(user_id=request.user.id, book_id=book.id).order_by('-read_time').first()
    candidates = []
    if record:
        chapter = Chapter.objects.filter(id=record.chapter_id, book_id=book.id).first()
        if chapter:
            candidates.append((make_naive_utc(record.read_time), 'db', record))
    path = os.path.join(get_progress_dir(request.user.id), book.md5 + '.json')
    if not book.local_only:
        try:
            with open(path, encoding='utf-8') as stream:
                local = json.load(stream)
            timestamp = _parse_progress_time(local)
            if timestamp and local.get('bookId') == book.md5 and local.get('schemaVersion') == 1:
                candidates.append((timestamp, 'file', local))
        except (OSError, ValueError, TypeError):
            pass
    if kosync_mode and kosync_enabled:
        remote = _kosync_record(request, book)
        if remote is not None:
            candidates.append((make_naive_utc(remote.updated_at), 'kosync', remote))
    if not kosync_mode and not book.local_only:
        cfg = get_s3_config(request.user)
        if cfg:
            try:
                response = _get_s3_client(cfg).get_object(
                    Bucket=cfg['bucket'], Key=cfg['prefix'] + 'book_progress/' + book.md5 + '.json',
                )
                with response['Body'] as stream:
                    remote = json.loads(stream.read(1024 * 1024))
                timestamp = _parse_progress_time(remote)
                if timestamp and remote.get('bookId') == book.md5 and remote.get('schemaVersion') == 1:
                    candidates.append((timestamp, 'remote', remote))
            except Exception as exc:
                code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
                if code not in ('NoSuchKey', '404', 'NotFound'):
                    logger.warning('EPUB remote progress unavailable; keeping local progress', exc_info=True)
    if not candidates:
        return default
    timestamp, source, selected = max(candidates, key=lambda entry: entry[0])
    if source == 'db':
        chapter = Chapter.objects.filter(id=selected.chapter_id, book_id=book.id).first()
        if chapter is None:
            return default
        return selected.chapter_id, selected.words_read, False
    if source == 'kosync':
        chapter, words, exact, position = selected.restored
        if not exact:
            # Never upgrade an approximate restoration into a newer exact/cloud record.
            return chapter.id, words, True
        selected = {
            'schemaVersion': 1, 'bookId': book.md5, **position,
            'lastReadTime': timestamp.replace(tzinfo=datetime_timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
    else:
        chapter, words, exact = import_position(book, selected)
        if not exact:
            # Never upgrade an approximate restoration into a newer exact/cloud record.
            return chapter.id, words, True
    if source in ('remote', 'kosync'):
        from .epub import write_cache
        write_cache(path, selected)
    location = selected.get('readerEpub', {}).get('location', {}) if isinstance(selected.get('readerEpub'), dict) else {}
    own = selected.get('readerEpub', {})
    if not isinstance(own, dict) or own.get('position') != {k: selected.get(k) for k in ('sectionIndex', 'paragraphIndex', 'elementIndex')}:
        location = {}
    if not location and type(selected.get('sectionIndex')) is int:
        location = {'section': selected['sectionIndex']}
    UserBookRecord.objects.update_or_create(
        user_id=request.user.id, book_id=book.id,
        defaults={'chapter_id': chapter.id, 'words_read': words,
                  'progress': calculate_read_progress(book, chapter, words) / 100,
                  'epub_location': location,
                  'read_time': (timestamp.replace(tzinfo=datetime_timezone.utc) if settings.USE_TZ else
                                timezone.make_naive(timestamp.replace(tzinfo=datetime_timezone.utc)))},
    )
    return chapter.id, words, False
