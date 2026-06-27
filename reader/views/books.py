import os
import json
import logging

from django.http import HttpResponse
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
                            remote_files.append({'name': filename, 'in_db': filename in local_books_set})
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
    """重新分章：删除旧章节并重建，根据 progress 重算阅读记录和书签的章节定位。"""
    _book = get_object_or_404(Book, id=pk)
    if not can_admin_book(_book, request.user):
        return redirect('reader:book_admin')

    result = book_parser.rechapter_book(_book, request.user)
    if not result:
        return HttpResponse('重新分章失败')

    new_chapters = list(Chapter.objects.filter(book_id=pk).order_by('index'))
    if not new_chapters:
        return redirect('reader:book_admin')

    total_chars = _book.word_count
    if total_chars <= 0:
        total_chars = sum(ch.end - ch.start for ch in new_chapters)

    def offset_to_chapter(offset):
        """根据全书字符偏移量，找到对应的新章节和章内偏移。"""
        for ch in new_chapters:
            if offset < ch.end:
                in_chapter = max(0, offset - ch.start)
                return ch, in_chapter
        return new_chapters[-1], max(0, new_chapters[-1].end - new_chapters[-1].start)

    # 根据 progress 重算阅读记录
    records = UserBookRecord.objects.filter(book_id=pk)
    for rec in records:
        try:
            progress_val = float(rec.progress)
        except (TypeError, ValueError):
            progress_val = 0
        target_offset = int(progress_val / 100.0 * total_chars)
        ch, in_chapter = offset_to_chapter(target_offset)
        rec.chapter_id = ch.id
        rec.words_read = in_chapter
        rec.save()

    # 根据 progress 重算书签的章节定位
    bookmarks = UserBookMark.objects.filter(book_id=pk)
    for bm in bookmarks:
        try:
            progress_val = float(bm.words_read) / float(total_chars) * 100 if total_chars > 0 else 0
        except (TypeError, ValueError):
            progress_val = 0
        # 书签的 words_read 存的是全书偏移量，重新映射到新章节
        target_offset = bm.words_read
        ch, in_chapter = offset_to_chapter(target_offset)
        bm.chapter_id = ch.id
        bm.words_read = in_chapter
        # 更新章节标题
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
def upload_file(request):
    """上传书籍：保存到 local/upload，分章入库，标记 local_only=True"""
    if request.method == 'POST' and request.FILES.get('file'):
        f = request.FILES['file']
        if f.size > 100 * 1024 * 1024:
            return HttpResponse('文件大小超过 100MB 限制')
        if not f.name.endswith('.txt'):
            return HttpResponse('仅支持 .txt 文件')
        # 路径穿越防护：剥离目录部分并清洗危险字符
        safe_name = get_valid_filename(os.path.basename(f.name))
        if not safe_name or not safe_name.endswith('.txt'):
            return HttpResponse('仅支持 .txt 文件')
        upload_dir = get_upload_dir()
        local_path = os.path.join(upload_dir, safe_name)
        with open(local_path, 'wb') as dest:
            for chunk in f.chunks():
                dest.write(chunk)
        result = book_parser.handle_local_book(request, local_path, local_only=True)
        if result:
            return HttpResponse('success')
        return HttpResponse('分章失败')
    return render(request, 'upload_file.html')

