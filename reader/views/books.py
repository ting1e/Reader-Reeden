import os
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views import generic
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.utils.text import get_valid_filename

from ..models import Book, Chapter, UserBookRecord, UserBookMark
from ..utils import get_file_md5, get_local_books_dir, get_upload_dir, get_progress_dir, can_admin_book
from ..services.progress import (
    get_books_progress, _parse_progress_time,
)
from ..services.s3 import get_s3_config, _get_s3_client
from ..services import book_parser

logger = logging.getLogger('reader')


def fmt_file_size(n):
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        s = f'{n / 1024:.1f}'.rstrip('0').rstrip('.')
        return f'{s} KB'
    if n < 1024 * 1024 * 1024:
        s = f'{n / 1024 / 1024:.1f}'.rstrip('0').rstrip('.')
        return f'{s} MB'
    s = f'{n / 1024 / 1024 / 1024:.2f}'.rstrip('0').rstrip('.')
    return f'{s} GB'


class BookListView(generic.ListView):
    template_name = 'book_list.html'
    context_object_name = 'book_list'

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Book.objects.all().order_by('-upload_time')
        if self.request.user.is_authenticated:
            return Book.objects.filter(share=True) | Book.objects.filter(uploader=self.request.user.id)
        return Book.objects.filter(share=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        books = list(context['book_list'])
        progress = get_books_progress(self.request.user, books)
        for book in books:
            book.progress_value = progress.get(book.id, 0)
            if not hasattr(book, 'read_time') or book.read_time is None:
                book.read_time = 0
            book.file_ext = os.path.splitext(book.file_name)[1].lstrip('.').upper()
            try:
                fsize = os.path.getsize(book.abs_path())
                book.file_size = fsize
                book.file_size_display = fmt_file_size(fsize)
            except Exception:
                book.file_size = 0
                book.file_size_display = ''
        context['book_list'] = books
        return context


class BookListRemoteView(generic.ListView):
    template_name = 'book_list_remote.html'
    context_object_name = 'book_list_remote'

    def get_queryset(self):
        remote_files = []
        if not self.request.user.is_authenticated:
            self.s3_error = '未登录，请先登录'
            return remote_files
        cfg = get_s3_config(self.request.user)
        if cfg:
            target_prefix = cfg['prefix'] + 'books/'
            try:
                client = _get_s3_client(cfg)
                response = client.list_objects_v2(Bucket=cfg['bucket'], Prefix=target_prefix)
                if 'Contents' in response:
                    local_books_set = set(Book.objects.values_list('file_name', flat=True))
                    for obj in response['Contents']:
                        if obj['Key'] != target_prefix:
                            filename = obj['Key'][len(target_prefix):]
                            last_modified = obj.get('LastModified')
                            remote_files.append({
                                'name': filename,
                                'in_db': filename in local_books_set,
                                'size': obj.get('Size', 0),
                                'size_display': fmt_file_size(obj.get('Size', 0)),
                                'last_modified': last_modified.isoformat() if last_modified else '',
                            })
            except Exception as e:
                logger.exception("BookListRemoteView: S3 list error")
                self.s3_error = str(e)
            else:
                self.s3_error = None
        else:
            self.s3_error = '未配置 S3，请在设置中填写 S3 连接信息'
        in_db_names = [f['name'] for f in remote_files if f.get('in_db')]
        name_progress = {}
        if in_db_names:
            db_books = list(Book.objects.filter(file_name__in=in_db_names))
            progress = get_books_progress(self.request.user, db_books)
            name_to_id = {b.file_name: b.id for b in db_books}
            for name in in_db_names:
                bid = name_to_id.get(name)
                if bid is not None:
                    name_progress[name] = progress.get(bid, 0)
        for f in remote_files:
            f['progress'] = name_progress.get(f['name'], 0)
        return remote_files

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['s3_error'] = getattr(self, 's3_error', None)
        return context


class IndexView(BookListView):
    template_name = 'book_list.html'


@login_required(login_url='reader:index')
def open_remote_book(request):
    """点击远程书籍：已在本地则直接打开，否则从 S3 下载到 local/books、
    下载进度到 local/book_progress、入库分章后打开。"""
    if request.method != 'POST':
        return redirect('reader:book_list_remote')
    book_name = request.POST.get('name', '')
    # 路径穿越防护：仅保留文件名，剥离任何目录部分
    book_name = os.path.basename(book_name)
    if not book_name or book_name in ('.', '..'):
        return redirect('reader:book_list_remote')

    existing = Book.objects.filter(file_name=book_name).first()
    if existing:
        return redirect(f"{reverse('reader:book_view')}?book_id={existing.id}")

    cfg = get_s3_config(request.user)
    if not cfg:
        return HttpResponse('S3 未配置')

    s3_client = _get_s3_client(cfg)
    bucket = cfg['bucket']
    prefix = cfg['prefix']
    s3_key = f'{prefix}books/{book_name}'

    local_path = os.path.join(get_local_books_dir(), book_name)
    try:
        s3_client.download_file(bucket, s3_key, local_path)
    except Exception as e:
        logger.exception("Error downloading book from S3")
        return HttpResponse(f'下载失败: {e}')

    result = book_parser.handle_local_book(request, local_path)
    if not result:
        return HttpResponse('分章失败')

    book = Book.objects.filter(file_name=book_name).first()
    if not book:
        return HttpResponse('入库失败')

    try:
        md5_val = book.md5 or get_file_md5(local_path)
        progress_key = f'{prefix}book_progress/{md5_val}.json'
        local_progress_path = os.path.join(get_progress_dir(), f'{md5_val}.json')

        # 先把远端进度读到临时内存，与本地进度按时间对比，保留较新的版本
        try:
            resp = s3_client.get_object(Bucket=bucket, Key=progress_key)
            remote_data = json.load(resp['Body'])
        except Exception:
            logger.debug("Remote progress not found or fetch error", exc_info=True)
            remote_data = None

        remote_time = _parse_progress_time(remote_data)
        local_data = None
        if os.path.exists(local_progress_path):
            try:
                with open(local_progress_path, 'r', encoding='utf-8') as lf:
                    local_data = json.load(lf)
            except Exception:
                logger.warning("Error loading local progress", exc_info=True)
                local_data = None
        local_time = _parse_progress_time(local_data)

        # 本地较新：把本地进度回传到 S3；远端较新或本地无：使用远端覆盖本地
        if remote_time is None and local_time is not None:
            with open(local_progress_path, 'rb') as pf:
                body = pf.read()
            s3_client.put_object(Bucket=bucket, Key=progress_key, Body=body, ContentLength=len(body))
        elif remote_time is not None and (local_time is None or remote_time > local_time):
            try:
                with open(local_progress_path, 'w', encoding='utf-8') as lf:
                    json.dump(remote_data, lf, ensure_ascii=False)
            except Exception:
                logger.exception("Error writing remote progress to local")
    except Exception:
        logger.exception("Error syncing progress from S3")

    return redirect(f"{reverse('reader:book_view')}?book_id={book.id}")


@login_required(login_url='reader:index')
def book_admin(request):
    """书籍管理：列出所有本地书籍"""
    if request.user.is_superuser:
        book_list = list(Book.objects.all().order_by('-upload_time'))
    else:
        book_list = list(Book.objects.filter(uploader=request.user.id).order_by('-upload_time'))
    progress = get_books_progress(request.user, book_list)
    for book in book_list:
        book.progress_value = progress.get(book.id, 0)
    return render(request, 'book_admin.html', {'book_list': book_list})


@login_required(login_url='reader:index')
def book_local_del(request, pk):
    """删除本地书籍：只删除本地文件、进度、数据库记录，不触碰 S3"""
    _book = get_object_or_404(Book, id=pk)
    if not can_admin_book(_book, request.user):
        return redirect('reader:book_admin')

    try:
        md5_val = _book.md5 or get_file_md5(_book.abs_path())
    except Exception:
        logger.exception("book_local_del: MD5 error")
        md5_val = None

    try:
        if os.path.exists(_book.abs_path()):
            os.remove(_book.abs_path())
    except Exception:
        logger.exception("Error deleting local book file")

    if md5_val:
        progress_path = os.path.join(get_progress_dir(), f'{md5_val}.json')
        try:
            if os.path.exists(progress_path):
                os.remove(progress_path)
        except Exception:
            logger.exception("Error deleting progress file")

    Chapter.objects.filter(book_id=pk).delete()
    UserBookRecord.objects.filter(book_id=pk).delete()
    UserBookMark.objects.filter(book_id=pk).delete()
    Book.objects.filter(id=pk).delete()

    return redirect('reader:book_admin')


@login_required(login_url='reader:index')
def book_rechapter(request, pk):
    """重新分章：删除旧章节并重建，根据 progress 重算阅读记录和书签的章节定位。

    words_read 在系统中是 text-only 偏移（不含换行符，相对章首）。
    重新分章时需要：旧章内 text-only → 全书 raw → 新章内 raw → 新章内 text-only。
    """
    _book = get_object_or_404(Book, id=pk)
    if not can_admin_book(_book, request.user):
        return redirect('reader:book_admin')

    # 1. 在 rechapter 删除旧章节之前，捕获旧章节元数据
    old_chapters = list(Chapter.objects.filter(book_id=pk).order_by('index'))
    old_meta = {}  # chapter_id -> {start, raw, text_len}
    try:
        with open(_book.abs_path(), 'r', encoding=_book.charset) as f:
            file_data = f.read()
    except Exception:
        logger.exception("book_rechapter: error reading book file")
        file_data = None
    for oc in old_chapters:
        raw = oc.end - oc.start
        if file_data is not None:
            nl = file_data[oc.start:oc.end].count('\n')
        else:
            nl = 0
        old_meta[oc.id] = {'start': oc.start, 'raw': raw, 'text_len': max(1, raw - nl)}

    # 2. 执行重新分章（删除旧章节、创建新章节）
    rule_choice = request.POST.get('rule_choice', 'main')
    result = book_parser.rechapter_book(_book, request.user, rule_choice=rule_choice)
    if not result:
        return HttpResponse('重新分章失败')

    new_chapters = list(Chapter.objects.filter(book_id=pk).order_by('index'))
    if not new_chapters:
        return redirect('reader:book_admin')

    # 3. 构建新章节元数据
    new_meta = {}  # chapter_id -> {raw, text_len}
    for ch in new_chapters:
        raw = ch.end - ch.start
        if file_data is not None:
            nl = file_data[ch.start:ch.end].count('\n')
        else:
            nl = 0
        new_meta[ch.id] = {'raw': raw, 'text_len': max(1, raw - nl)}

    total_chars = _book.word_count
    if total_chars <= 0:
        total_chars = sum(ch.end - ch.start for ch in new_chapters)

    def offset_to_chapter(offset):
        """根据全书 raw 字符偏移量，找到对应的新章节和章内 raw 偏移。"""
        for ch in new_chapters:
            if offset < ch.end:
                in_chapter = max(0, offset - ch.start)
                return ch, in_chapter
        return new_chapters[-1], max(0, new_chapters[-1].end - new_chapters[-1].start)

    def raw_to_text(in_chapter_raw, ch):
        """章内 raw 偏移 → text-only 偏移（按章节 raw/text 比例反算）。"""
        nm = new_meta.get(ch.id)
        if nm and nm['raw'] > 0:
            return round(in_chapter_raw * nm['text_len'] / nm['raw'])
        return in_chapter_raw

    # 4. 根据 progress 重算阅读记录（progress 是全书百分比，保留不动）
    records = UserBookRecord.objects.filter(book_id=pk)
    for rec in records:
        try:
            progress_val = float(rec.progress)
        except (TypeError, ValueError):
            progress_val = 0
        target_offset = int(progress_val / 100.0 * total_chars)
        ch, in_chapter_raw = offset_to_chapter(target_offset)
        rec.chapter_id = ch.id
        rec.words_read = raw_to_text(in_chapter_raw, ch)
        # rec.progress 保留原值（重新分章不改变阅读进度百分比）
        rec.save()

    # 5. 重算书签的章节定位
    #    书签 words_read 是旧章内 text-only 偏移，需还原为全书 raw 偏移再映射
    bookmarks = UserBookMark.objects.filter(book_id=pk)
    for bm in bookmarks:
        oc_meta = old_meta.get(bm.chapter_id)
        if oc_meta and oc_meta['text_len'] > 0:
            # 旧章内 text-only → 旧章内 raw → 全书 raw
            in_old_raw = round(bm.words_read * oc_meta['raw'] / oc_meta['text_len'])
            full_raw = oc_meta['start'] + in_old_raw
        else:
            # 旧章节未知（已损坏），回退到全书起始
            full_raw = 0
        ch, in_chapter_raw = offset_to_chapter(full_raw)
        bm.chapter_id = ch.id
        bm.words_read = raw_to_text(in_chapter_raw, ch)
        bm.chapter_title = ch.title
        bm.save()

    return redirect('reader:book_admin')


@login_required(login_url='reader:index')
def book_share_toggle(request, pk):
    """切换书籍的共享状态"""
    if request.method != 'POST':
        return redirect('reader:book_admin')
    _book = get_object_or_404(Book, id=pk)
    if not can_admin_book(_book, request.user):
        return redirect('reader:book_admin')
    _book.share = not _book.share
    _book.save()
    return redirect('reader:book_admin')


@login_required(login_url='reader:index')
def book_rename(request, pk):
    """编辑书籍名称（仅展示用，不影响文件路径/进度/分章）。"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    _book = get_object_or_404(Book, id=pk)
    if not can_admin_book(_book, request.user):
        return JsonResponse({'success': False, 'error': 'no permission'})
    new_name = (request.POST.get('name') or '').strip()
    if not new_name:
        return JsonResponse({'success': False, 'error': '名称不能为空'})
    if len(new_name) > 64:
        return JsonResponse({'success': False, 'error': '名称不能超过 64 字符'})
    _book.name = new_name
    _book.save(update_fields=['name'])
    return JsonResponse({'success': True, 'name': _book.name})


@login_required(login_url='reader:index')
def upload_file(request):
    """上传书籍：保存到 local/upload，分章入库，标记 local_only=True。

    前端逐文件 POST（FormData，字段名 file），后端逐文件处理并返回 JSON，
    便于上传页展示每个文件的成功/失败状态。
    """
    if request.method == 'POST' and request.FILES.get('file'):
        f = request.FILES['file']
        if f.size > 100 * 1024 * 1024:
            return JsonResponse({'success': False, 'name': f.name, 'error': '文件大小超过 100MB 限制'})
        if not f.name.endswith('.txt'):
            return JsonResponse({'success': False, 'name': f.name, 'error': '仅支持 .txt 文件'})
        # 路径穿越防护：剥离目录部分并清洗危险字符
        try:
            safe_name = get_valid_filename(os.path.basename(f.name))
        except Exception:
            return JsonResponse({'success': False, 'name': f.name, 'error': '文件名非法'})
        if not safe_name or not safe_name.endswith('.txt') or safe_name == '.txt':
            return JsonResponse({'success': False, 'name': f.name, 'error': '仅支持 .txt 文件'})
        # 防止同名覆盖：已存在同名书籍则拒绝，避免覆盖文件并产生悬挂引用
        if Book.objects.filter(file_name=safe_name).exists():
            return JsonResponse({'success': False, 'name': safe_name, 'error': '同名书籍已存在，请先删除原书再上传'})
        upload_dir = get_upload_dir()
        local_path = os.path.join(upload_dir, safe_name)
        try:
            with open(local_path, 'wb') as dest:
                for chunk in f.chunks():
                    dest.write(chunk)
            result = book_parser.handle_local_book(request, local_path, local_only=True)
        except Exception as e:
            logger.exception("upload_file: error processing %s", safe_name)
            return JsonResponse({'success': False, 'name': safe_name, 'error': f'处理失败: {e}'})
        if result:
            return JsonResponse({'success': True, 'name': safe_name})
        return JsonResponse({'success': False, 'name': safe_name, 'error': '分章失败'})
    return render(request, 'upload_file.html')

