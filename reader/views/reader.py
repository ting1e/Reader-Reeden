import os
import json
import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils import timezone

from ..models import Book, Chapter, UserBookRecord
from ..utils import get_file_md5, get_progress_dir, get_local_fonts, can_access_book, get_or_create_user_setting, get_element_index
from ..services.progress import (
    calculate_read_progress, save_progress_json,
    _parse_progress_time, make_naive_utc,
)
from ..services.s3 import get_s3_config, _get_s3_client, sync_progress_to_s3

logger = logging.getLogger('reader')

USER_SETTING_DEFAULTS = {'font_size': 16, 'read_bg': '#fff', 'read_mode': 'page'}


def BookView(request):
    """打开书籍并恢复阅读进度，支持 ?book_id=X&chapter_id=X&offset=Y 查询参数。

    GET  view/?book_id=X         — 打开书籍，自动恢复阅读进度
    POST view/                   — 表单提交（章节导航、保存进度、关键词搜索）
    """
    # ---- 解析 book_id：POST 表单 or GET 参数（统一在最前面解析，供后续权限校验与各分支使用）----
    book_id = None
    if request.method == 'POST' and 'book_id' in request.POST:
        try:
            book_id = int(request.POST.get('book_id'))
        except (TypeError, ValueError):
            book_id = None
    elif request.method == 'GET' and 'book_id' in request.GET:
        try:
            book_id = int(request.GET.get('book_id'))
        except (TypeError, ValueError):
            book_id = None

    if not book_id:
        raise Http404

    # ---- 权限检查（所有分支共用，防止越权访问他人私有书籍）----
    _book = get_object_or_404(Book, id=book_id)
    if not can_access_book(_book, request.user):
        return redirect('reader:index')

    # ---- POST: 保存阅读进度 (AJAX) ----
    if request.method == 'POST' and 'words' in request.POST and 'chapter_id' in request.POST:
        if not request.user.is_authenticated:
            return HttpResponse('not login')
        _chapter_id = request.POST.get('chapter_id')
        words_read = request.POST.get('words')
        try:
            int(words_read)
        except (TypeError, ValueError):
            return HttpResponse('invalid words')
        try:
            chapter = Chapter.objects.get(id=_chapter_id)
        except Chapter.DoesNotExist:
            chapter = None
        progress_val = 0
        if chapter:
            progress_val = calculate_read_progress(_book, chapter, words_read) / 100.0
        UserBookRecord.objects.update_or_create(
            user_id=request.user.id,
            book_id=book_id,
            defaults={
                'chapter_id': _chapter_id,
                'words_read': int(words_read),
                'progress': progress_val,
                'read_time': timezone.now(),
            },
        )

        try:
            if chapter:
                save_progress_json(_book, chapter, words_read)
                sync_progress_to_s3(request, _book)
        except Exception:
            logger.exception("Failed to save progress JSON")

        return HttpResponse('success')

    # ---- POST: 关键词搜索 (AJAX) ----
    if request.method == 'POST' and 'kwd' in request.POST:
        if not request.user.is_authenticated:
            return HttpResponse('not login')
        _chapter_id = request.POST.get('chapter_id')
        if _chapter_id:
            try:
                _chapter_id_int = int(_chapter_id)
            except (TypeError, ValueError):
                return HttpResponse('invalid chapter_id')
            return keyword_search(request, book_id, _chapter_id_int, str(request.POST['kwd']))
        return HttpResponse('')

    # ---- 确定 chapter_id / offset：POST 表单 or GET 参数 ----
    chapter_id = None
    offset = 0
    if request.method == 'POST' and 'book_id' in request.POST:
        _ch = request.POST.get('chapter_id')
        if _ch:
            try:
                chapter_id = int(_ch)
            except (TypeError, ValueError):
                pass
        _off = request.POST.get('words_read')
        if _off:
            try:
                offset = int(_off)
            except (TypeError, ValueError):
                pass
    elif request.method == 'GET' and 'book_id' in request.GET:
        _ch = request.GET.get('chapter_id')
        if _ch:
            try:
                chapter_id = int(_ch)
            except (TypeError, ValueError):
                pass
        _off = request.GET.get('offset')
        if _off:
            try:
                offset = int(_off)
            except (TypeError, ValueError):
                pass

    # POST: chapter_id 已在上面从表单获取；未指定则使用阅读记录或第一章
    if not chapter_id and request.user.is_authenticated and 0 == offset:
        db_record = None
        last = UserBookRecord.objects.filter(
            user_id=request.user.id, book_id=book_id
        ).order_by('-read_time')
        if last.exists():
            db_record = last[0]

        file_record = None
        md5_val = None
        if not getattr(_book, 'local_only', False):
            try:
                md5_val = _book.md5 or get_file_md5(_book.abs_path())
                json_path = os.path.join(get_progress_dir(), f"{md5_val}.json")
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as f:
                        file_record = json.load(f)
            except Exception:
                logger.exception("Error reading progress file on open")

        # 从 S3 获取远程进度
        remote_record = None
        if not getattr(_book, 'local_only', False):
            cfg = get_s3_config(request.user)
            if cfg and md5_val:
                s3_key = f"{cfg['prefix']}book_progress/{md5_val}.json"
                try:
                    client = _get_s3_client(cfg)
                    resp = client.get_object(Bucket=cfg['bucket'], Key=s3_key)
                    remote_record = json.load(resp['Body'])
                except Exception:
                    logger.debug("S3 remote progress fetch error", exc_info=True)
                    remote_record = None

        # 比较三个来源的时间：本地文件、数据库、S3 远程
        file_time = _parse_progress_time(file_record)
        db_time = make_naive_utc(db_record.read_time) if db_record else None
        remote_time = _parse_progress_time(remote_record)

        # 选出最新的来源
        best_record = None
        best_source = None
        times = [
            (file_time, 'file', file_record),
            (db_time, 'db', db_record),
            (remote_time, 'remote', remote_record),
        ]
        for t, source, rec in times:
            if t is None or rec is None:
                continue
            if best_record is None or t > best_record[0]:
                best_record = (t, source, rec)

        # 默认使用第一章
        if best_record is None:
            chapter_id = _book.first_chapter_id
            offset = 0
        else:
            best_source = best_record[1]
            if best_source == 'db':
                chapter_id = best_record[2].chapter_id
                offset = best_record[2].words_read
            else:
                # file 或 remote：使用 sectionIndex/paragraphIndex/elementIndex
                rec = best_record[2]
                if "sectionIndex" in rec:
                    section_index = int(rec["sectionIndex"])
                    cur_chapter = Chapter.objects.filter(book_id=book_id, index=section_index).first()
                    if cur_chapter:
                        chapter_id = cur_chapter.id
                        paragraph_index = rec.get("paragraphIndex", 0)
                        element_index = rec.get("elementIndex", 0)

                        with open(_book.abs_path(), 'r', encoding=_book.charset) as f:
                            content = f.read()[cur_chapter.start:cur_chapter.end]
                            content_lines = content.split('\n')

                        accumulated = 0
                        for idx in range(min(paragraph_index, len(content_lines))):
                            accumulated += len(content_lines[idx])

                        element_offset = 0
                        if paragraph_index < len(content_lines):
                            current_line = content_lines[paragraph_index]
                            element_offset = 0
                            for c in current_line:
                                if get_element_index(current_line[:element_offset]) >= element_index:
                                    break
                                element_offset += 1
                        offset = accumulated + element_offset
                    else:
                        chapter_id = _book.first_chapter_id
                        offset = 0
                else:
                    chapter_id = _book.first_chapter_id
                    offset = 0

            # 同步到本地文件、数据库、S3（确保三者一致）
            ch_obj = Chapter.objects.get(id=chapter_id) if chapter_id else None
            if ch_obj:
                save_progress_json(_book, ch_obj, offset)
            sync_progress_val = calculate_read_progress(_book, ch_obj, offset) / 100.0 if ch_obj else 0
            if db_record:
                db_record.chapter_id = chapter_id
                db_record.words_read = offset
                db_record.progress = sync_progress_val
                db_record.read_time = timezone.now()
                db_record.save()
            else:
                UserBookRecord(
                    user_id=request.user.id,
                    book_id=book_id,
                    chapter_id=chapter_id,
                    words_read=offset,
                    progress=sync_progress_val,
                    read_time=timezone.now()
                ).save()
            # 如果选中的不是 remote，则上传到 S3；如果选中 remote，本地和 DB 已经同步
            if best_source != 'remote':
                sync_progress_to_s3(request, _book)

    if not chapter_id:
        chapter_id = _book.first_chapter_id

    # ---- 渲染书籍阅读页面 ----
    chapter_ids = list(Chapter.objects.filter(book_id=book_id).values_list('id', flat=True))
    cur_chpt = get_object_or_404(Chapter, pk=chapter_id)

    with open(_book.abs_path(), 'r', encoding=_book.charset) as f:
        content = f.read()[cur_chpt.start:cur_chpt.end]
        content = content.split('\n')

    # 跳过内容中与章节标题重复的第一行（标题已由 <h3> 显示）
    display_lines = content
    if display_lines and display_lines[0].strip() == cur_chpt.title.strip():
        display_lines = display_lines[1:]

    chapter_view = render_to_string('chapter_view.html', {
        'chapter_title': cur_chpt.title,
        'content_lines': display_lines
    })

    context = {
        'chapter_title': cur_chpt.title,
        'chapter_ids': chapter_ids,
        'content_lines': display_lines,
        'book_id': book_id,
        'chapter_id': cur_chpt.id,
        'chapter_view': chapter_view,
        'last_words': offset,
    }
    if request.user.is_authenticated:
        context['user_setting'] = get_or_create_user_setting(request.user)

    context['local_fonts'] = get_local_fonts()

    return render(request, 'book_view.html', context)


@login_required(login_url='reader:index')
def chapter_content(request, chapter_id):
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    _book = get_object_or_404(Book, id=chapter.book_id)
    if not can_access_book(_book, request.user):
        return JsonResponse({'success': False, 'error': 'no permission'})
    with open(_book.abs_path(), 'r', encoding=_book.charset) as f:
        content = f.read()[chapter.start:chapter.end]
        content = content.split('\n')
    display_lines = content
    if display_lines and display_lines[0].strip() == chapter.title.strip():
        display_lines = display_lines[1:]
    chapter_view = render_to_string('chapter_view.html', {
        'chapter_title': chapter.title,
        'content_lines': display_lines
    })
    return JsonResponse({
        'success': True,
        'chapter_id': chapter.id,
        'book_id': chapter.book_id,
        'title': chapter.title,
        'chapter_view': chapter_view,
    })


class search_item:
    def __init__(self, book, chapter, cont, off, title, index):
        self.book_pk = book
        self.chapter_pk = chapter
        self.content = cont
        self.offset = off
        self.chapter_title = title
        self.chapter_index = index


def keyword_search(request, book_pk, chapter_pk, kwd):
    _book = get_object_or_404(Book, id=book_pk)
    chapter_list = Chapter.objects.filter(book_id=book_pk).order_by('index')
    search_list = []
    with open(_book.abs_path(), 'r', encoding=_book.charset) as f:
        raw = f.read()
    for chapter in chapter_list:
        content_lines = raw[chapter.start:chapter.end].split('\n')
        cnt = 0
        content_cnt = [0, ]
        for i in content_lines:
            cnt += len(i)
            content_cnt.append(cnt)

        for i in range(len(content_lines)):
            if content_lines[i].find(kwd) != -1:
                search_list.append(search_item(book_pk, chapter.id, content_lines[i], content_cnt[i], chapter.title, chapter.index))
    return render(request, 'search.html', {'list': search_list, 'chapter_pk': chapter_pk, 'book_pk': book_pk})


def chapter_list_ajax(request, book_id):
    """AJAX 返回书籍的章节列表 HTML，仅在用户打开目录时请求"""
    _book = get_object_or_404(Book, id=book_id)
    if not can_access_book(_book, request.user):
        return JsonResponse({'success': False, 'error': 'no permission'})
    chapter_list = Chapter.objects.filter(book_id=book_id)
    chapter_ids = list(chapter_list.values_list('id', flat=True))
    chapter_id_param = request.GET.get('chapter_id')
    try:
        chapter_id_param = int(chapter_id_param)
    except (TypeError, ValueError):
        chapter_id_param = None
    html = render_to_string('chapter_list.html', {
        'chapter_list': chapter_list,
        'chapter_id': chapter_id_param,
    }, request=request)
    return JsonResponse({
        'success': True,
        'chapter_ids': chapter_ids,
        'html': html,
    })
