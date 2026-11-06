"""Local S3 file observations and the optional stale-progress correction rule."""
import copy
import hashlib
import json
import logging
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone

from ..models import Book, Chapter, S3CorrectionState, UserSetting
from ..utils import get_element_index, read_book_text
from . import volume
from .progress import _parse_progress_time

logger = logging.getLogger('reader')
POSITION_FIELDS = ('sectionIndex', 'paragraphIndex', 'elementIndex', 'readProgress', 'readerEpub')


def content_id(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def position_words(book, data):
    """Absolute text position, not percentage or accumulated reading statistics."""
    if book.format == 'epub':
        from .epub import import_position, read_cache, raw_offset
        chapter, words, _ = import_position(book, data)
        text = read_cache(book)['text']
        raw = chapter.start + raw_offset(text[chapter.start:chapter.end], words)
        return len(text[:raw].replace('\n', ''))
    chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
    internal = volume.progress_from_sync(data, chapters)
    chapter = next(c for c in chapters if c.index == int(internal['sectionIndex']))
    text = read_book_text(book)
    lines = text[chapter.start:chapter.end].split('\n')
    paragraph = max(0, int(internal.get('paragraphIndex', 0)))
    element = max(0, int(internal.get('elementIndex', 0)))
    offset = sum(len(line) for line in lines[:paragraph])
    if paragraph < len(lines):
        line = lines[paragraph]
        # Match the reader's element-index convention, including non-BMP text.
        index, units = 0, 0
        while index < len(line) and units < element:
            units += get_element_index(line[index])
            index += 1
        offset += index
    return len(text[:chapter.start].replace('\n', '')) + offset


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
        defaults={'snapshot': {'data': payload, 'words': position_words(book, payload)}},
    )


def should_correct(previous, current, snapshot):
    if not previous or not snapshot:
        return False
    a = _parse_progress_time({'lastReadTime': previous['time']})
    b = _parse_progress_time({'lastReadTime': current['time']})
    s = _parse_progress_time(snapshot['data'])
    return bool(a and b and s and b - a >= timedelta(minutes=10)
                and abs(current['words'] - previous['words']) <= 2000
                and a < s < b
                and snapshot['words'] > max(previous['words'], current['words']))


def process_file(user_id, filename, baseline=False):
    """Called serially by the watcher; only successful writes consume the quota."""
    setting = UserSetting.objects.filter(user_id=user_id, s3_correction_enabled=True).first()
    if not setting:
        return False
    path = Path(filename)
    if path.suffix != '.json' or path.parent.resolve() != Path(setting.s3_progress_path).resolve():
        return False
    with path.open(encoding='utf-8') as stream:
        data = json.load(stream)
    md5 = path.stem
    if not isinstance(data, dict) or data.get('bookId') != md5:
        return False
    book = Book.objects.filter(md5=md5, local_only=False).first()
    if not book or not data.get('deviceId') or _parse_progress_time(data) is None:
        return False
    state, _ = S3CorrectionState.objects.get_or_create(user_id=user_id, book_md5=md5)
    digest = content_id(data)
    if digest == state.last_content:
        return False
    if digest == state.own_content:
        state.last_content = digest
        state.save(update_fields=['last_content'])
        return False
    device = str(data['deviceId'])
    current = {'time': data['lastReadTime'], 'words': position_words(book, data)}
    previous = state.devices.get(device)
    state.devices[device] = current
    state.last_content = digest
    state.save(update_fields=['devices', 'last_content'])
    if baseline or not should_correct(previous, current, state.snapshot):
        return False
    now = timezone.now()
    count = state.correction_count
    if not state.window_start or now - state.window_start >= timedelta(minutes=5):
        count = 0
    if count >= 2:
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
    logger.info('Corrected S3 progress user=%s book=%s count=%s', user_id, md5, count + 1)
    return True
