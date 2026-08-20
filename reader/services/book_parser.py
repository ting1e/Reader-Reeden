from ..models import Book, Chapter, UserSetting
from ..utils import get_file_md5, to_rel_path, DEFAULT_CHAPTER_RULE
from django.db import transaction
import os
import re
from pathlib import Path
import chardet

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
    for i, (title, s, e) in enumerate(spans):
        if i > 0 and merged and e - s < min_len:
            merged[-1][2] = e
        else:
            merged.append([title, s, e])
    return merged


def _split_large_chapters(spans, max_len):
    """字数超过 max_len 的章节按 max_len 硬切分为多章；首段保留原标题，后续段命名「原标题（k/n）」。"""
    if max_len <= 0:
        return spans
    result = []
    for title, s, e in spans:
        length = e - s
        if length <= max_len:
            result.append([title, s, e])
            continue
        n = (length + max_len - 1) // max_len
        base = title[:CHAPTER_TITLE_MAX_LEN]
        for k in range(n):
            ps = s + k * max_len
            pe = min(ps + max_len, e)
            if k == 0:
                result.append([base, ps, pe])
            else:
                suffix = '（%d/%d）' % (k + 1, n)
                result.append([base[:CHAPTER_TITLE_MAX_LEN - len(suffix)] + suffix, ps, pe])
    return result


def _split_into_chapters(book, data, match, url, set_md5, min_len=0, max_len=0):
    """按 match 迭代器切分章节、应用单章字数上下限、bulk_create 入库，并回填 book 的首/末章、字数等字段。

    先合并低于 min_len 的章节到上一章，再对超过 max_len 的章节硬切分章（min/max 为 0 表示不启用）。
    调用前需保证 book.id 已存在（新建书籍需先 book.save()）；须在 transaction.atomic() 内调用。
    """
    wc = len(data)
    book.word_count = wc

    spans = []
    offset = 0
    chpt_name = '前言'
    first_matched_title = None
    for chpt in match:
        tit_st = chpt.span()[0]
        if offset == 0:
            first_matched_title = str(chpt.group())
            book.intro = data[:min(tit_st, 512)]
            spans.append([chpt_name, offset, tit_st])
            offset = tit_st
            chpt_name = first_matched_title
        else:
            spans.append([chpt_name, offset, tit_st])
            offset = tit_st
            chpt_name = str(chpt.group())
    spans.append([chpt_name, offset, wc])

    book.first_chapter_title = chpt_name if first_matched_title is None else first_matched_title

    spans = _merge_small_chapters(spans, min_len)
    spans = _split_large_chapters(spans, max_len)

    chapters_to_create = [
        Chapter(title=title, book_id=book.id, book_url=to_rel_path(url), index=i, start=s, end=e)
        for i, (title, s, e) in enumerate(spans)
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
        min_len, max_len = _get_chapter_len_limits(request.user)
        match = re.compile(pat, re.MULTILINE).finditer(data)
        with transaction.atomic():
            book.save()
            return _split_into_chapters(book, data, match, url, set_md5=True, min_len=min_len, max_len=max_len)

    return False


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
        with transaction.atomic():
            Chapter.objects.filter(book_id=book.id).delete()
            return _split_into_chapters(book, data, match, url, set_md5=False, min_len=min_len, max_len=max_len)
