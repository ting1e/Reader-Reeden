from ..models import Book, Chapter, UserSetting
from ..utils import get_file_md5, to_rel_path, DEFAULT_CHAPTER_RULE
from django.db import transaction
import os
import re
from pathlib import Path
import chardet

from . import volume

DEFAULT_MIN_CHAPTER_LEN = 100
DEFAULT_MAX_CHAPTER_LEN = 100000

CHAPTER_TITLE_MAX_LEN = 256


def _resolve_chapter_rule(book, user):
    """确定分章正则：优先用户自定义规则，其次 book.rule，最后默认规则；并写回 book.rule。"""
    pat = book.rule or DEFAULT_CHAPTER_RULE
    if user and user.is_authenticated:
        setting = UserSetting.objects.filter(user_id=user.id).first()
        if setting and setting.chapter_rule:
            pat = setting.chapter_rule
    book.rule = pat
    return pat


def _resolve_volume_rule(user, rule_choice='main'):
    """返回与所选分章规则配套的分卷正则；留空表示不启用分卷。

    分章规则与分卷规则一一配对：主规则 -> volume_rule，
    备用规则 1 -> volume_rule_2，备用规则 2 -> volume_rule_3。
    若所选备用规则为空而回退到主规则，则同样回退到主分卷规则。
    """
    if not (user and user.is_authenticated):
        return ''
    setting = UserSetting.objects.filter(user_id=user.id).first()
    if not setting:
        return ''
    if rule_choice == 'rule_2' and setting.chapter_rule_2:
        return setting.volume_rule_2 or ''
    if rule_choice == 'rule_3' and setting.chapter_rule_3:
        return setting.volume_rule_3 or ''
    return setting.volume_rule or ''


def _get_chapter_len_limits(user):
    """读取用户的单章字数上下限（低于下限并入上一章、超过上限硬切分章），0 表示不启用。"""
    min_len = DEFAULT_MIN_CHAPTER_LEN
    max_len = DEFAULT_MAX_CHAPTER_LEN
    if user and user.is_authenticated:
        setting = UserSetting.objects.filter(user_id=user.id).first()
        if setting:
            min_len = max(0, setting.chapter_min_len)
            max_len = max(0, setting.chapter_max_len)
    return min_len, max_len


def _merge_small_chapters(spans, min_len):
    """字数低于 min_len 的章节并入上一章（标题丢弃、内容保留）；「前言」无上一章，保持不变。"""
    if min_len <= 0:
        return spans
    merged = []
    for i, span in enumerate(spans):
        title, s, e = span[0], span[1], span[2]
        if i > 0 and merged and e - s < min_len:
            merged[-1][2] = e
        else:
            merged.append(list(span))
    return merged


def _split_large_chapters(spans, max_len):
    """字数超过 max_len 的章节按 max_len 硬切分为多章；首段保留原标题，后续段命名「原标题（k/n）」。"""
    if max_len <= 0:
        return spans
    result = []
    for span in spans:
        title, s, e = span[0], span[1], span[2]
        length = e - s
        if length <= max_len:
            result.append(list(span))
            continue
        n = (length + max_len - 1) // max_len
        base = title[:CHAPTER_TITLE_MAX_LEN]
        for k in range(n):
            ps = s + k * max_len
            pe = min(ps + max_len, e)
            if k == 0:
                result.append([base, ps, pe] + list(span[3:]))
            else:
                suffix = '（%d/%d）' % (k + 1, n)
                result.append([base[:CHAPTER_TITLE_MAX_LEN - len(suffix)] + suffix, ps, pe] + list(span[3:]))
    return result


def _split_into_chapters(book, data, match, url, set_md5, min_len=0, max_len=0, volume_rule=''):
    """按 match 迭代器切分章节、识别分卷、应用单章字数上下限、bulk_create 入库，
    并回填 book 的首/末章、字数等字段。

    分卷规则匹配到的行不会成为章节标题；卷标题只记录在章节的 volume_index /
    volume_title 上，不创建 Chapter，也不改变原始文本和字符偏移。
    min/max 为 0 表示不启用。调用前需保证 book.id 已存在（新建书籍需先 book.save()）；
    须在 transaction.atomic() 内调用。
    """
    wc = len(data)
    book.word_count = wc

    markers = volume.find_volume_markers(data, volume_rule)
    volume_line_starts = {marker['line_start'] for marker in markers}

    spans = []  # [title, start, end, volume_index, volume_title]
    offset = 0
    chpt_name = '前言'
    first_matched_title = None
    marker_pos = 0
    current_volume = None
    pending_volume = (None, '')  # 当前正在累积的章节所属卷
    volume_seq = {}  # 卷标题匹配位置 -> 连续卷序号（只给实际有章节的卷编号）

    for chpt in match:
        tit_st = chpt.span()[0]
        # 卷标题所在行不能同时被识别为章节。
        line_start = data.rfind('\n', 0, tit_st) + 1
        if line_start in volume_line_starts:
            continue

        while marker_pos < len(markers) and markers[marker_pos]['pos'] < tit_st:
            current_volume = markers[marker_pos]
            marker_pos += 1
        if current_volume is not None:
            key = current_volume['pos']
            if key not in volume_seq:
                volume_seq[key] = len(volume_seq)
            vol_index = volume_seq[key]
            vol_title = current_volume['title']
        else:
            vol_index, vol_title = None, ''

        if offset == 0:
            first_matched_title = str(chpt.group())
            book.intro = data[:min(tit_st, 512)]
            spans.append([chpt_name, offset, tit_st, vol_index, vol_title])
            offset = tit_st
            chpt_name = first_matched_title
        else:
            spans.append([chpt_name, offset, tit_st] + list(pending_volume))
            offset = tit_st
            chpt_name = str(chpt.group())
        pending_volume = (vol_index, vol_title)
    spans.append([chpt_name, offset, wc] + list(pending_volume))

    book.first_chapter_title = chpt_name if first_matched_title is None else first_matched_title

    spans = _merge_small_chapters(spans, min_len)
    spans = _split_large_chapters(spans, max_len)

    chapters_to_create = [
        Chapter(title=title, book_id=book.id, book_url=to_rel_path(url), index=i, start=s, end=e,
                volume_index=vi, volume_title=vt)
        for i, (title, s, e, vi, vt) in enumerate(spans)
    ]
    Chapter.objects.bulk_create(chapters_to_create)
    created = list(Chapter.objects.filter(book_id=book.id).order_by('index'))

    book.first_chapter_id = created[0].id
    book.last_chapter_title = created[-1].title
    book.last_chapter_id = created[-1].id
    book.total_chapter_num = len(created) - 1
    if set_md5:
        book.md5 = get_file_md5(url)
    book.save()
    return True


def handle_local_book(request, url, local_only=False):
    if Path(url).suffix.lower() == '.epub':
        return _handle_epub(request, url, local_only)
    if Path(url).suffix.lower() != '.txt':
        return False
    file_name = os.path.splitext(os.path.basename(url))[0]
    book = Book(book_url=to_rel_path(url))
    book.name = file_name
    book.file_name = os.path.basename(url)
    book.local = True
    book.local_only = local_only
    if request.user.is_authenticated:
        book.uploader = request.user.id

    charset = 'utf-8'
    with open(url, 'rb') as f:
        charset = chardet.detect(f.read(5000))["encoding"] or 'utf-8'
    book.charset = charset

    with open(url, 'r', encoding=charset) as f:
        data = f.read()
        pat = _resolve_chapter_rule(book, request.user)
        volume_rule = _resolve_volume_rule(request.user)
        min_len, max_len = _get_chapter_len_limits(request.user)
        match = re.compile(pat, re.MULTILINE).finditer(data)
        with transaction.atomic():
            book.save()
            return _split_into_chapters(book, data, match, url, set_md5=True, min_len=min_len,
                                        max_len=max_len, volume_rule=volume_rule)

    return False


def _handle_epub(request, url, local_only):
    from .epub import parse_epub, write_cache
    import uuid
    data = parse_epub(url)
    cache_path = str(Path(url).parent / (uuid.uuid4().hex + '.reader.json'))
    book = Book(
        book_url=to_rel_path(url), file_name=os.path.basename(url),
        name=(data['title'] or Path(url).stem)[:64], author=data['author'][:64],
        intro=data['text'][:512], format='epub', content_path=to_rel_path(cache_path),
        charset='utf-8', local=True, local_only=local_only,
        uploader=request.user.id if request.user.is_authenticated else 0,
        md5=get_file_md5(url), word_count=len(data['text']),
    )
    try:
        write_cache(cache_path, data)
        with transaction.atomic():
            book.save()
            Chapter.objects.bulk_create([
                Chapter(book_id=book.id, book_url=book.book_url, index=index,
                        title=ch['title'], start=ch['start'], end=ch['end'])
                for index, ch in enumerate(data['chapters'])
            ])
            chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
            book.first_chapter_id, book.last_chapter_id = chapters[0].id, chapters[-1].id
            book.first_chapter_title, book.last_chapter_title = chapters[0].title, chapters[-1].title
            book.total_chapter_num = len(chapters)
            book.save()
    except Exception:
        if os.path.exists(cache_path):
            os.remove(cache_path)
        raise
    return True


def rechapter_book(book, user=None, rule_choice='main'):
    """对已存在的书籍重新分章，保留 book id，删除旧章节并重建。

    rule_choice: 'main'（主规则）、'rule_2'（备用规则1）、'rule_3'（备用规则2）
    """
    url = book.abs_path()
    if not url or Path(url).suffix.lower() != '.txt':
        return False

    charset = book.charset or 'utf-8'
    with open(url, 'r', encoding=charset) as f:
        data = f.read()
        if rule_choice != 'main' and user and user.is_authenticated:
            setting = UserSetting.objects.filter(user_id=user.id).first()
            if rule_choice == 'rule_2' and setting and setting.chapter_rule_2:
                pat = setting.chapter_rule_2
            elif rule_choice == 'rule_3' and setting and setting.chapter_rule_3:
                pat = setting.chapter_rule_3
            else:
                pat = _resolve_chapter_rule(book, user)
        else:
            pat = _resolve_chapter_rule(book, user)
        match = re.compile(pat, re.MULTILINE).finditer(data)
        min_len, max_len = _get_chapter_len_limits(user)
        volume_rule = _resolve_volume_rule(user, rule_choice)
        with transaction.atomic():
            Chapter.objects.filter(book_id=book.id).delete()
            return _split_into_chapters(book, data, match, url, set_md5=False, min_len=min_len,
                                        max_len=max_len, volume_rule=volume_rule)
