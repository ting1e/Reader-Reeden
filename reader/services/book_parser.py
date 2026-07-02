from ..models import Book, Chapter, UserSetting
from ..utils import get_file_md5, to_rel_path, DEFAULT_CHAPTER_RULE
from django.db import transaction
import os
import re
from pathlib import Path
import chardet


def _resolve_chapter_rule(book, user):
    """确定分章正则：优先用户自定义规则，其次 book.rule，最后默认规则；并写回 book.rule。"""
    pat = book.rule or DEFAULT_CHAPTER_RULE
    if user and user.is_authenticated:
        setting = UserSetting.objects.filter(user_id=user.id).first()
        if setting and setting.chapter_rule:
            pat = setting.chapter_rule
    book.rule = pat
    return pat


def _split_into_chapters(book, data, match, url, set_md5):
    """按 match 迭代器切分章节、bulk_create 入库，并回填 book 的首/末章、字数等字段。

    调用前需保证 book.id 已存在（新建书籍需先 book.save()）；须在 transaction.atomic() 内调用。
    """
    wc = len(data)
    book.word_count = wc

    chapters_to_create = []
    offset = 0
    total_ch_num = 0
    chpt_name = '前言'
    has_match = False
    for chpt in match:
        has_match = True
        tit_st = chpt.span()[0]
        if offset == 0:
            book.first_chapter_title = chpt.group()
            book.intro = data[:min(tit_st, 512)]
            chapters_to_create.append(Chapter(title=chpt_name, book_id=book.id, book_url=to_rel_path(url), index=total_ch_num, start=offset, end=tit_st))
            offset = tit_st
            chpt_name = str(chpt.group())
        else:
            chapters_to_create.append(Chapter(title=chpt_name, book_id=book.id, book_url=to_rel_path(url), index=total_ch_num, start=offset, end=tit_st))
            offset = tit_st
            chpt_name = str(chpt.group())

        total_ch_num += 1

    if not has_match:
        chapters_to_create.append(Chapter(title=chpt_name, book_id=book.id, book_url=to_rel_path(url), index=0, start=0, end=wc))
        Chapter.objects.bulk_create(chapters_to_create)
        created = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        book.first_chapter_title = chpt_name
        book.first_chapter_id = created[0].id
        book.last_chapter_title = chpt_name
        book.last_chapter_id = created[0].id
        book.total_chapter_num = 0
        if set_md5:
            book.md5 = get_file_md5(url)
        book.save()
        return True

    chapters_to_create.append(Chapter(title=chpt_name, book_id=book.id, book_url=to_rel_path(url), index=total_ch_num, start=offset, end=wc))
    Chapter.objects.bulk_create(chapters_to_create)
    created = list(Chapter.objects.filter(book_id=book.id).order_by('index'))

    book.first_chapter_id = created[0].id
    book.last_chapter_title = chpt_name
    book.last_chapter_id = created[-1].id
    book.total_chapter_num = total_ch_num
    if set_md5:
        book.md5 = get_file_md5(url)
    book.save()
    return True


def handle_local_book(request, url, local_only=False):
    if Path(url).suffix.lower() != '.txt':
        return False
    file_name = os.path.basename(url).replace('.txt', '')
    book = Book(book_url=to_rel_path(url))
    book.name = file_name
    book.file_name = os.path.basename(url)
    book.local = True
    book.local_only = local_only
    if request.user.is_authenticated:
        book.uploader = request.user.id

    charset = 'utf-8'
    with open(url, 'rb') as f:
        charset = chardet.detect(f.read(5000))["encoding"]
    book.charset = charset

    with open(url, 'r', encoding=charset) as f:
        data = f.read()
        pat = _resolve_chapter_rule(book, request.user)
        match = re.compile(pat, re.MULTILINE).finditer(data)
        with transaction.atomic():
            book.save()
            return _split_into_chapters(book, data, match, url, set_md5=True)

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
        with transaction.atomic():
            Chapter.objects.filter(book_id=book.id).delete()
            return _split_into_chapters(book, data, match, url, set_md5=False)
