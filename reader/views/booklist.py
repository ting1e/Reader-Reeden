import logging
import os

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.db.models import Q as models_Q

from ..models import BookList, BookListItem, Book
from ..utils import (
    get_accessible_books, can_admin_booklist, can_view_booklist, can_access_book,
    fmt_file_size, get_local_books_dir, link_external_booklist_items,
)
from ..services.s3 import get_s3_config, _get_s3_client
from ..services import book_parser

logger = logging.getLogger('reader')


def booklist_list(request):
    """书单浏览：列出公开书单和用户自己的书单（只读）。

    未登录用户仅可查看公开书单，且不显示新建按钮。
    """
    user = request.user
    if getattr(user, 'is_superuser', False):
        booklists = list(BookList.objects.all().order_by('-updated_time'))
    elif getattr(user, 'is_authenticated', False):
        booklists = list(
            BookList.objects.filter(
                models_Q(user_id=user.id) | models_Q(is_public=True)
            ).order_by('-updated_time')
        )
    else:
        booklists = list(BookList.objects.filter(is_public=True).order_by('-updated_time'))

    booklist_ids = [bl.id for bl in booklists]
    item_counts = {}
    for item in BookListItem.objects.filter(book_list_id__in=booklist_ids):
        item_counts[item.book_list_id] = item_counts.get(item.book_list_id, 0) + 1
    for bl in booklists:
        bl.item_count = item_counts.get(bl.id, 0)

    return render(request, 'booklist_admin.html', {
        'booklist_list': booklists,
        'can_create': bool(getattr(user, 'is_authenticated', False)),
        'can_delete': False,
    })


@login_required(login_url='reader:index')
def booklist_admin(request):
    """书单管理：列出当前用户创建的书单（超管可看全部）"""
    if request.user.is_superuser:
        booklists = list(BookList.objects.all().order_by('-updated_time'))
    else:
        booklists = list(BookList.objects.filter(user_id=request.user.id).order_by('-updated_time'))

    booklist_ids = [bl.id for bl in booklists]
    item_counts = {}
    for item in BookListItem.objects.filter(book_list_id__in=booklist_ids):
        item_counts[item.book_list_id] = item_counts.get(item.book_list_id, 0) + 1
    for bl in booklists:
        bl.item_count = item_counts.get(bl.id, 0)

    return render(request, 'booklist_admin.html', {
        'booklist_list': booklists,
        'can_create': True,
        'can_delete': True,
    })


@login_required(login_url='reader:index')
def booklist_create(request):
    """创建书单（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': '名称不能为空'})
    if len(name) > 128:
        return JsonResponse({'success': False, 'error': '名称不能超过 128 字符'})
    description = (request.POST.get('description') or '').strip()
    bl = BookList.objects.create(
        name=name,
        description=description,
        user_id=request.user.id,
        is_public=False,
    )
    return JsonResponse({'success': True, 'id': bl.id, 'name': bl.name})


def booklist_detail(request, pk):
    """书单详情页：单页查看 + 内联编辑。

    未登录用户可查看公开书单（只读）；私有书单重定向到书单浏览页。
    """
    bl = get_object_or_404(BookList, id=pk)
    if not can_view_booklist(bl, request.user):
        return redirect('reader:booklist_list')

    items = list(BookListItem.objects.filter(book_list_id=pk).order_by('sort_order', 'added_time'))
    book_ids = [it.book_id for it in items if it.book_id > 0]
    book_map = {b.id: b for b in Book.objects.filter(id__in=book_ids)}
    for it in items:
        it.book_obj = book_map.get(it.book_id) if it.book_id > 0 else None

    can_edit = can_admin_booklist(bl, request.user)
    accessible_books = get_accessible_books(request.user) if can_edit else []

    return render(request, 'booklist_detail.html', {
        'booklist': bl,
        'items': items,
        'accessible_books': accessible_books,
        'can_edit': can_edit,
    })


@login_required(login_url='reader:index')
def booklist_edit(request, pk):
    """编辑书单名称/简介（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        return JsonResponse({'success': False, 'error': '无权限'})

    name = (request.POST.get('name') or '').strip()
    description = request.POST.get('description', None)

    if name:
        if len(name) > 128:
            return JsonResponse({'success': False, 'error': '名称不能超过 128 字符'})
        bl.name = name
    if description is not None:
        bl.description = description.strip()
    bl.save()
    return JsonResponse({'success': True, 'name': bl.name, 'description': bl.description})


@login_required(login_url='reader:index')
def booklist_delete(request, pk):
    """删除书单（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        return JsonResponse({'success': False, 'error': '无权限'})
    name = bl.name
    BookListItem.objects.filter(book_list_id=pk).delete()
    bl.delete()
    return JsonResponse({'success': True, 'name': name})


@login_required(login_url='reader:index')
def booklist_share_toggle(request, pk):
    """切换书单公开/私有状态

    详情页通过 AJAX 调用（返回 JSON），管理页通过 form POST 调用（返回 redirect）。
    """
    if request.method != 'POST':
        return redirect('reader:booklist_admin')
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': '无权限'})
        return redirect('reader:booklist_admin')
    bl.is_public = not bl.is_public
    bl.save(update_fields=['is_public'])
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'is_public': bl.is_public})
    return redirect('reader:booklist_admin')


@login_required(login_url='reader:index')
def booklist_add_book(request, pk):
    """向书单添加书籍（AJAX，返回 JSON）

    两种模式：
    - book_id > 0：从书库添加
    - manual_name + manual_author：手动添加外部书籍
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        return JsonResponse({'success': False, 'error': '无权限'})

    book_id = request.POST.get('book_id', '').strip()
    manual_name = (request.POST.get('manual_name') or '').strip()
    manual_author = (request.POST.get('manual_author') or '').strip()
    remote_file_name = (request.POST.get('remote_file_name') or '').strip()
    review = (request.POST.get('review') or '').strip()
    rating_raw = (request.POST.get('rating') or '').strip()
    try:
        rating = int(rating_raw) if rating_raw else 0
        if not 0 <= rating <= 5:
            rating = 0
    except (TypeError, ValueError):
        rating = 0

    if book_id:
        try:
            book_id = int(book_id)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': '无效的书籍 ID'})
        book = Book.objects.filter(id=book_id).first()
        if not book:
            return JsonResponse({'success': False, 'error': '书籍不存在'})
        if not can_access_book(book, request.user):
            return JsonResponse({'success': False, 'error': '无权限访问该书'})
        if BookListItem.objects.filter(book_list_id=pk, book_id=book_id).exists():
            return JsonResponse({'success': False, 'error': '该书已在书单中'})
        # 去重：若同书单已存在同名外部占位条目，转为真实书籍而非新建第二条
        names = set()
        for raw in (book.name, book.file_name):
            if not raw:
                continue
            text = str(raw).strip()
            if not text:
                continue
            names.add(text)
            stem, _ = os.path.splitext(text)
            if stem:
                names.add(stem)
        name_q = models_Q()
        for n in names:
            name_q |= models_Q(manual_name__iexact=n)
        existing_external = BookListItem.objects.filter(
            book_list_id=pk, book_id=0
        ).filter(name_q).first()
        if existing_external:
            # 保留外部条目原 rating/review，除非新提交有非零/非空值
            new_rating = rating if rating else existing_external.rating
            new_review = review if review else existing_external.review
            existing_external.book_id = book_id
            existing_external.manual_name = ''
            existing_external.manual_author = ''
            existing_external.remote_file_name = ''
            existing_external.rating = new_rating
            existing_external.review = new_review
            existing_external.save(update_fields=[
                'book_id', 'manual_name', 'manual_author',
                'remote_file_name', 'rating', 'review',
            ])
            item = existing_external
        else:
            max_order = 0
            last = BookListItem.objects.filter(book_list_id=pk).order_by('-sort_order').first()
            if last:
                max_order = last.sort_order
            item = BookListItem.objects.create(
                book_list_id=pk,
                book_id=book_id,
                rating=rating,
                review=review,
                sort_order=max_order + 1,
            )
    elif manual_name:
        if len(manual_name) > 128:
            return JsonResponse({'success': False, 'error': '书名不能超过 128 字符'})
        existing = BookListItem.objects.filter(
            book_list_id=pk, book_id=0, manual_name__iexact=manual_name
        )
        if existing.exists():
            return JsonResponse({'success': False, 'error': '该书已在书单中'})
        max_order = 0
        last = BookListItem.objects.filter(book_list_id=pk).order_by('-sort_order').first()
        if last:
            max_order = last.sort_order
        item = BookListItem.objects.create(
            book_list_id=pk,
            book_id=0,
            manual_name=manual_name,
            manual_author=manual_author,
            remote_file_name=remote_file_name,
            rating=rating,
            review=review,
            sort_order=max_order + 1,
        )
    else:
        return JsonResponse({'success': False, 'error': '请提供书籍或填写书名'})

    item_count = BookListItem.objects.filter(book_list_id=pk).count()
    return JsonResponse({
        'success': True,
        'item_id': item.id,
        'item_count': item_count,
    })


@login_required(login_url='reader:index')
def booklist_update_item(request, pk, item_id):
    """更新书单条目的评分/短评（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        return JsonResponse({'success': False, 'error': '无权限'})
    item = get_object_or_404(BookListItem, id=item_id, book_list_id=pk)

    if 'rating' in request.POST:
        try:
            rating = int(request.POST.get('rating'))
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': '无效的评分'})
        if not 0 <= rating <= 5:
            return JsonResponse({'success': False, 'error': '评分范围 0-5'})
        item.rating = rating
        item.save(update_fields=['rating'])
        return JsonResponse({'success': True, 'rating': item.rating})

    if 'review' in request.POST:
        review = (request.POST.get('review') or '').strip()
        item.review = review
        item.save(update_fields=['review'])
        return JsonResponse({'success': True, 'review': item.review})

    return JsonResponse({'success': False, 'error': '无更新字段'})


@login_required(login_url='reader:index')
def booklist_remove_item(request, pk, item_id):
    """从书单移除某条目（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        return JsonResponse({'success': False, 'error': '无权限'})
    item = get_object_or_404(BookListItem, id=item_id, book_list_id=pk)
    item.delete()
    item_count = BookListItem.objects.filter(book_list_id=pk).count()
    return JsonResponse({'success': True, 'item_count': item_count})


@login_required(login_url='reader:index')
def booklist_remote_books(request):
    """AJAX：列出 S3 远程书库中的 .txt 文件（已下载的带 book_id 供直接加入书单）"""
    cfg = get_s3_config(request.user)
    if not cfg:
        return JsonResponse({'success': False, 'error': '未配置 S3'})
    try:
        client = _get_s3_client(cfg)
        target_prefix = cfg['prefix'] + 'books/'
        response = client.list_objects_v2(Bucket=cfg['bucket'], Prefix=target_prefix)
        # 先收集所有远程 .txt 文件（不再排除已在本地的）
        remote_files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                if obj['Key'] == target_prefix:
                    continue
                filename = obj['Key'][len(target_prefix):]
                if not filename.endswith('.txt'):
                    continue
                remote_files.append((filename, obj.get('Size', 0)))
        # 批量查本地书并按访问权限过滤，构建 filename -> book_id 映射
        name_to_book_id = {}
        if remote_files:
            names = [f[0] for f in remote_files]
            user = request.user
            qs = Book.objects.filter(file_name__in=names)
            if getattr(user, 'is_superuser', False):
                local_books = qs
            elif getattr(user, 'is_authenticated', False):
                local_books = qs.filter(models_Q(share=True) | models_Q(uploader=user.id))
            else:
                local_books = qs.filter(share=True)
            for b in local_books:
                # 同名多本取其一（按访问权限过滤后理论上不应发生）
                if b.file_name not in name_to_book_id:
                    name_to_book_id[b.file_name] = b.id
        result = []
        for filename, size in remote_files:
            book_id = name_to_book_id.get(filename)
            result.append({
                'name': filename,
                'size_display': fmt_file_size(size),
                'in_db': book_id is not None,
                'book_id': book_id,
            })
        return JsonResponse({'success': True, 'books': result})
    except Exception as e:
        logger.exception("booklist_remote_books: S3 list error")
        return JsonResponse({'success': False, 'error': f'远程书库列表获取失败: {e}'})


@login_required(login_url='reader:index')
def booklist_download_remote(request, pk, item_id):
    """AJAX：下载书单中外部条目对应的远程书籍，入库后自动关联，返回 JSON。

    仅对标记了 remote_file_name 的外部条目（book_id=0）可用；下载成功后
    link_external_booklist_items 会把条目转为真实书籍，前端 reload 即见「阅读」按钮。
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'})
    bl = get_object_or_404(BookList, id=pk)
    if not can_admin_booklist(bl, request.user):
        return JsonResponse({'success': False, 'error': '无权限'})
    item = get_object_or_404(BookListItem, id=item_id, book_list_id=pk)
    if item.book_id != 0 or not item.remote_file_name:
        return JsonResponse({'success': False, 'error': '该条目不可下载'})

    # 路径穿越防护：仅保留文件名
    book_name = os.path.basename(item.remote_file_name)
    if not book_name or book_name in ('.', '..'):
        return JsonResponse({'success': False, 'error': '无效的文件名'})

    # 已在本地书库：直接关联，无需重复下载
    existing = Book.objects.filter(file_name=book_name).first()
    if existing:
        if can_access_book(existing, request.user):
            link_external_booklist_items(request.user, existing)
            return JsonResponse({'success': True, 'already_in_db': True})
        # 同名书存在但无权访问（可能为他人的私有书），不重复下载以避免产生重复记录
        return JsonResponse({'success': False, 'error': '本地已存在同名书籍但你无权访问'})

    cfg = get_s3_config(request.user)
    if not cfg:
        return JsonResponse({'success': False, 'error': '未配置 S3'})

    s3_client = _get_s3_client(cfg)
    bucket = cfg['bucket']
    prefix = cfg['prefix']
    s3_key = f'{prefix}books/{book_name}'

    local_path = os.path.join(get_local_books_dir(request.user.id), book_name)
    try:
        s3_client.download_file(bucket, s3_key, local_path)
    except Exception as e:
        logger.exception("booklist_download_remote: S3 download error")
        # 清理失败的本地文件
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except OSError:
            pass
        return JsonResponse({'success': False, 'error': f'远程书籍下载失败: {e}'})

    result = book_parser.handle_local_book(request, local_path)
    if not result:
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except OSError:
            pass
        return JsonResponse({'success': False, 'error': '分章失败'})

    book = Book.objects.filter(file_name=book_name).first()
    if not book:
        return JsonResponse({'success': False, 'error': '入库失败'})

    # 关联当前用户书单中匹配的外部条目（含本条目）
    link_external_booklist_items(request.user, book)
    return JsonResponse({'success': True})
