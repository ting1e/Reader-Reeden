import datetime
import json
import logging
import os
from zoneinfo import ZoneInfo

from django.conf import settings as dj_settings
from django.utils import timezone

from ..models import UserBookRecord
from ..utils import get_progress_dir, get_file_md5, get_element_index, get_device_id

logger = logging.getLogger('reader')


def make_naive_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        try:
            from django.conf import settings as dj_settings
            if not getattr(dj_settings, 'USE_TZ', False):
                dt = timezone.make_aware(dt)
        except Exception:
            logger.warning("make_naive_utc: failed to make aware", exc_info=True)
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def calculate_read_progress(book, chapter, words_read):
    """计算阅读进度，返回 0-10000 的整数（百分比 × 100）。

    words_read 来自前端 $(p).text().length 累加，不含换行符；
    book.word_count 为全文件原始字符数，含换行符。
    为保证单位一致，读取当前章节内容统计换行符数，
    将 text-only 偏移按比例换算为原始字符偏移。
    """
    all_chars = book.word_count
    if all_chars <= 0:
        return 0

    try:
        current_words = int(words_read)
    except (TypeError, ValueError):
        current_words = 0

    chapter_raw = chapter.end - chapter.start
    if chapter_raw <= 0:
        return min(max(0, int(chapter.start / all_chars * 10000)), 10000)

    # 统计当前章节换行符数，用于 text-only → raw 比例换算
    try:
        with open(book.abs_path(), 'r', encoding=book.charset) as f:
            content = f.read()[chapter.start:chapter.end]
        newline_count = content.count('\n')
    except Exception:
        logger.exception("calculate_read_progress: error reading chapter content")
        newline_count = 0

    chapter_text_len = chapter_raw - newline_count
    if chapter_text_len > 0:
        current_raw = min(int(current_words * chapter_raw / chapter_text_len), chapter_raw)
    else:
        current_raw = min(current_words, chapter_raw)

    progress_val = int(((chapter.start + current_raw) / all_chars) * 10000)
    return min(max(0, progress_val), 10000)


def get_books_progress(user, books):
    """批量返回 {book_id: 进度百分比(0-100)}，基于用户的最新阅读记录。"""
    result = {}
    if not books:
        return result
    for b in books:
        result[b.id] = 0
    if not user.is_authenticated:
        return result
    book_ids = [b.id for b in books]
    records = UserBookRecord.objects.filter(
        user_id=user.id, book_id__in=book_ids
    ).order_by('-read_time')
    latest = {}
    for r in records:
        if r.book_id not in latest:
            latest[r.book_id] = r
    if not latest:
        return result
    book_map = {b.id: b for b in books}
    for book_id, rec in latest.items():
        book = book_map.get(book_id)
        if book is not None:
            book.read_time = rec.read_time
        try:
            progress_val = round(float(rec.progress), 2)
        except (TypeError, ValueError):
            progress_val = 0
        result[book_id] = min(max(0, progress_val), 100)
    return result


def save_progress_json(book, chapter, words_read):
    if getattr(book, 'local_only', False):
        return
    try:
        md5_val = book.md5 or get_file_md5(book.abs_path())
    except Exception:
        logger.exception("Error calculating MD5")
        return

    device_id = get_device_id()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_utc.isoformat().replace('+00:00', 'Z')

    # 显式使用 Django TIME_ZONE 设置计算本地时间，不依赖容器系统时区
    local_tz = ZoneInfo(dj_settings.TIME_ZONE)
    local_now = datetime.datetime.now(local_tz)
    current_date_str = local_now.strftime('%Y-%m-%d')

    try:
        with open(book.abs_path(), 'r', encoding=book.charset) as f:
            content = f.read()[chapter.start:chapter.end]
            content_lines = content.split('\n')
    except Exception:
        logger.exception("Error reading book file")
        return

    try:
        target_words = int(words_read)
    except (TypeError, ValueError):
        target_words = 0
    accumulated = 0
    paragraph_index = 0
    element_index = 0

    for idx, line in enumerate(content_lines):
        line_len = len(line)
        if accumulated + line_len >= target_words:
            paragraph_index = idx
            element_offset = target_words - accumulated
            element_index = get_element_index(line[:element_offset])
            break
        accumulated += line_len
    else:
        if content_lines:
            paragraph_index = len(content_lines) - 1
            element_index = get_element_index(content_lines[-1])

    temp_dir = get_progress_dir()
    json_path = os.path.join(temp_dir, f"{md5_val}.json")

    old_data = None
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except Exception:
            logger.warning("Error loading old progress JSON", exc_info=True)

    progress_val = calculate_read_progress(book, chapter, words_read)

    delta_seconds = 0
    delta_words = 0
    if old_data:
        old_time = _parse_progress_time(old_data)
        if old_time:
            old_dt = old_time.replace(tzinfo=datetime.timezone.utc)
            delta_seconds = int((now_utc - old_dt).total_seconds())
            delta_seconds = min(max(0, delta_seconds), 600)
        old_progress = old_data.get('readProgress', 0)
        if progress_val > old_progress and book.word_count > 0:
            delta_words = int((progress_val - old_progress) / 10000.0 * book.word_count)

    if old_data and isinstance(old_data.get('todayStats'), dict):
        stats = old_data['todayStats']
    else:
        stats = {}
    if stats.get('date') != current_date_str:
        stats = {'date': current_date_str, 'devices': {}}
    dev = stats.setdefault('devices', {}).setdefault(device_id, {
        'readSeconds': 0, 'wordCount': 0, 'hourly': {},
    })
    dev['readSeconds'] = dev.get('readSeconds', 0) + delta_seconds
    dev['wordCount'] = dev.get('wordCount', 0) + delta_words
    current_hour = str(local_now.hour)
    hourly = dev.setdefault('hourly', {})
    h = hourly.setdefault(current_hour, {'readSeconds': 0, 'wordCount': 0})
    h['readSeconds'] = h.get('readSeconds', 0) + delta_seconds
    h['wordCount'] = h.get('wordCount', 0) + delta_words

    new_data = {
        "schemaVersion": 1,
        "bookId": md5_val,
        "sectionIndex": chapter.index,
        "paragraphIndex": paragraph_index,
        "elementIndex": element_index,
        "readProgress": progress_val,
        "lastReadTime": now_iso,
        "deviceId": device_id,
        "todayStats": stats,
    }

    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False)
    except Exception:
        logger.exception("Error writing progress JSON")


def _parse_progress_time(obj):
    """从进度 dict 中解析 lastReadTime 为 naive UTC datetime，失败返回 None。"""
    if not obj:
        return None
    t = obj.get("lastReadTime")
    if not t:
        return None
    try:
        return make_naive_utc(datetime.datetime.fromisoformat(str(t).replace('Z', '+00:00')))
    except Exception:
        return None