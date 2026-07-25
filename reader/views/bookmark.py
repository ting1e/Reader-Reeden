from django.http import HttpResponse, JsonResponse
from django.views import generic
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required

from ..models import Book, UserBookMark
from ..utils import can_access_book


class BookmarkListView(generic.ListView):
    template_name = '_bookmark_list.html'
    context_object_name = 'bookmark_list'

    def get_queryset(self):
        # 只返回当前登录用户本人的书签，user_id 不再取自 URL
        if self.request.user.is_authenticated:
            return UserBookMark.objects.filter(
                book_id=self.kwargs['book_id'], user_id=self.request.user.id
            ).order_by('-add_time')
        return UserBookMark.objects.none()


def bookmark_list_legacy_redirect(request, user_id, book_id):
    """旧版 bookmark_list URL（含 user_id）301 重定向到新 URL。"""
    return redirect('reader:bookmark_list', book_id=book_id, permanent=True)


@login_required(login_url='reader:index')
def bookmark_admin(request):
    """书签管理：列出当前用户的所有书签"""
    marks = list(UserBookMark.objects.filter(user_id=request.user.id).order_by('-add_time'))
    book_ids = [m.book_id for m in marks]
    book_map = {b.id: b for b in Book.objects.filter(id__in=book_ids)}
    for m in marks:
        m.book_obj = book_map.get(m.book_id)
    return render(request, 'bookmark_admin.html', {'bookmark_list': marks})


@login_required(login_url='reader:index')
def bookmark_del(request, pk):
    """删除单条书签（AJAX，返回 JSON）"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'}, status=405)
    mark = get_object_or_404(UserBookMark, pk=pk)
    if not (request.user.is_superuser or request.user.id == mark.user_id):
        return JsonResponse({'success': False, 'error': '无权限'})
    mark.delete()
    return JsonResponse({'success': True})


@login_required(login_url='reader:index')
def bookmark_save(request):
    if request.method != 'POST':
        return HttpResponse('method not allowed')

    book_id = request.POST.get('book_id')
    chapter_id = request.POST.get('chapter_id')
    if not book_id or not chapter_id:
        return HttpResponse('invalid params')

    _book = get_object_or_404(Book, id=book_id)
    if not can_access_book(_book, request.user):
        return HttpResponse('no permission')

    try:
        words_read = int(request.POST.get('words_read', 0) or 0)
    except (TypeError, ValueError):
        words_read = 0

    user_bookmark = UserBookMark(
        user_id=request.user.id,
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_title=request.POST.get('chapter_title', ''),
        words_read=words_read,
        content=request.POST.get('content', ''),
    )
    user_bookmark.save()
    return HttpResponse('ok')
