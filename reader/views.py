from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from .models import *
from django.views import generic
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth import authenticate,login,logout
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string

from . import form_book

class BookListView(generic.ListView):
    template_name = 'book_list.html'
    context_object_name = 'book_list'

    def get_queryset(self):
        """Return the last five published questions."""
        if self.request.user.is_authenticated:
            return Book.objects.filter(share = False) | Book.objects.filter(uploader = self.request.user.id)
        else:
            return Book.objects.filter(share = True)
        


def BookView(request):
    """打开书籍并恢复阅读进度，支持 ?book_id=X&chapter_id=X&offset=Y 查询参数。

    GET  view/?book_id=X         — 打开书籍，自动恢复阅读进度
    POST view/                   — 表单提交（章节导航、保存进度、关键词搜索）
    """
    # ---- POST: 保存阅读进度 (AJAX) ----
    if request.method == 'POST' and 'words' in request.POST \
            and 'book_id' in request.POST and 'chapter_id' in request.POST:
        if not request.user.is_authenticated:
            return HttpResponse('not login')
        _book_id = request.POST.get('book_id')
        _chapter_id = request.POST.get('chapter_id')
        words_read = request.POST.get('words')
        records = UserBookRecord.objects.filter(user_id=request.user.id, book_id=_book_id).order_by('-read_time')
        if len(records) == 0:
            UserBookRecord(
                user_id=request.user.id,
                book_id=_book_id,
                chapter_id=_chapter_id,
                words_read=int(words_read)
            ).save()
        else:
            records[0].chapter_id = _chapter_id
            records[0].words_read = int(words_read)
            records[0].save()
        return HttpResponse('success')

    # ---- POST: 关键词搜索 (AJAX) ----
    if request.method == 'POST' and 'kwd' in request.POST:
        _book_id = request.POST.get('book_id')
        _chapter_id = request.POST.get('chapter_id')
        if _book_id and _chapter_id:
            return keyword_search(request, int(_book_id), int(_chapter_id), str(request.POST['kwd']))
        return HttpResponse('')

    # ---- 确定 book_id：POST 表单
    chapter_id = None
    book_id = None
    offset = 0
    if request.method == 'POST' and 'book_id' in request.POST:
        book_id = int(request.POST.get('book_id'))
        _ch = request.POST.get('chapter_id')
        if _ch:
            chapter_id = int(_ch)
        _off = request.POST.get('words_read')
        if _off:
            offset = int(_off)

    if not book_id:
        return HttpResponse('get')

    # ---- 权限检查 ----
    _book = get_object_or_404(Book, id=book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser == 1):
        return redirect('reader:index')
    

    # POST: chapter_id 已在上面从表单获取；未指定则使用阅读记录或第一章
    if not chapter_id and request.user.is_authenticated and 0==offset:
        last = UserBookRecord.objects.filter(
            user_id=request.user.id, book_id=book_id
        ).order_by('-read_time')
        if last.exists():
            chapter_id = last[0].chapter_id
            offset = last[0].words_read

    if not chapter_id:
        chapter_id = _book.first_chapter_id

    # ---- 渲染书籍阅读页面 ----
    chapter_ids = list(Chapter.objects.filter(book_id=book_id).values_list('id', flat=True))
    cur_chpt = get_object_or_404(Chapter, pk=chapter_id)

    with open(_book.book_url, 'r', encoding=_book.charset) as f:
        content = f.read()[cur_chpt.start:cur_chpt.end]
        content = content.split('\n')

    chapter_view = render_to_string('chapter_view.html', {
        'chapter_title': cur_chpt.title,
        'content_lines': content
    })

    context = {
        'chapter_title': cur_chpt.title,
        'chapter_ids': chapter_ids,
        'content_lines': content,
        'progess': 20,
        'book_id': book_id,
        'chapter_id': cur_chpt.id,
        'chapter_view': chapter_view,
        'last_words': offset,
    }
    if request.user.is_authenticated:
        context['user_setting'], _ = UserSetting.objects.get_or_create(
            user_id=request.user.id,
            defaults={'font_size': 16, 'read_bg': '#fff'}
        )

    return render(request, 'book_view.html', context)


@login_required(login_url='reader:index')
def chapter_content(request, chapter_id):
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    _book = get_object_or_404(Book, id=chapter.book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser == 1):
        return JsonResponse({'success': False, 'error': 'no permission'})
    with open(_book.book_url, 'r', encoding=_book.charset) as f:
        content = f.read()[chapter.start:chapter.end]
        content = content.split('\n')
    chapter_view = render_to_string('chapter_view.html', {
        'chapter_title': chapter.title,
        'content_lines': content
    })
    return JsonResponse({
        'success': True,
        'chapter_id': chapter.id,
        'book_id': chapter.book_id,
        'title': chapter.title,
        'chapter_view': chapter_view,
    })



class ChapterListView(generic.ListView):
    template_name = 'chapter_list.html'
    context_object_name = 'chapter_list'

    def get_queryset(self):
        _book =  get_object_or_404(Book,id = self.kwargs['pk'])
        if _book.uploader == 0:
            return Chapter.objects.filter(book_id=self.kwargs['pk'])
        if self.request.user.is_authenticated and self.request.user.id == _book.uploader :
            return Chapter.objects.filter(book_id=self.kwargs['pk'])
        return Chapter.objects.none()

        
        

class BookmarkListView(generic.ListView):
    template_name = 'bookmark_list.html'
    context_object_name = 'bookmark_list'

    def get_queryset(self):
        if self.request.user.is_authenticated and self.request.user.id == self.kwargs['user_id']:
            return UserBookMark.objects.filter(book_id=self.kwargs['book_id'],user_id =self.kwargs['user_id']).order_by('-add_time')
        else:
            return UserBookMark.objects.none()

class ChapterDetailView(generic.DetailView):
    # template_name = 'chapter_detail.html'
    # model: Content

    def get_queryset(self):
        # return Content.objects.filter(pk=self.kwargs['pk'])
        return

class IndexView(BookListView):
    template_name = 'book_list.html'


def progress(book_id, chapter_id):
    chapter_list = Chapter.objects.filter(book_id=book_id).order_by('index')
    book = Book.objects.filter(id=book_id)[0]
    all_chars = book.word_count
    read = 0.0
    for ch in chapter_list:
        if chapter_id == ch.id:
            break
        read += ch.end - ch.start
    return read / all_chars * 100

def login_auth(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(username=username, password=password)
        setting = UserSetting.objects.filter(user_id = user.id)
        if len(setting) == 0:
            UserSetting(user_id = user.id).save()
        if user is not None:
            login(request, user)
            return HttpResponse('success')  
        else:
            return HttpResponse('请输出正确的用户名或密码')  

    return HttpResponse('fk off')  


def logout_view(request):
    logout(request)
    return redirect('reader:book_list')
    # Redirect to a success page.

def book_del(request,pk):
    _book = get_object_or_404(Book,id = pk)
    if request.user.is_superuser or request.user.id == _book.uploader:
        chapter_list = Chapter.objects.filter(book_id = pk)
        for i in chapter_list:
            Content.objects.filter(id=i.content_id).delete()
        chapter_list.delete()
        UserBookRecord.objects.filter(book_id = pk).delete()
        Book.objects.filter(id = pk).delete()
    return redirect('reader:book_admin')


class search_item:
    def __init__(self,book,chapter,cont,off,title,index):
        self.book_pk = book
        self.chapter_pk = chapter
        self.content = cont
        self.offset = off
        self.chapter_title = title
        self.chapter_index = index

def keyword_search(request,book_pk,chapter_pk,kwd):
    _book = get_object_or_404(Book, id=book_pk)
    chapter_list = Chapter.objects.filter(book_id=book_pk).order_by('index')
    search_list = []
    with open(_book.book_url, 'r', encoding=_book.charset) as f:
        raw = f.read()
    for chapter in chapter_list:
        content_lines = raw[chapter.start:chapter.end].split('\n')
        cnt = 0
        content_cnt = [0,]
        for i in content_lines:
            cnt += len(i)
            content_cnt.append(cnt)

        for i in range(len(content_lines)):
            if content_lines[i].find(kwd) != -1:
                search_list.append(search_item(book_pk,chapter.id,content_lines[i],content_cnt[i],chapter.title,chapter.index))
    return render(request, 'search.html', {'list': search_list, 'chapter_pk': chapter_pk, 'book_pk': book_pk})

@login_required(login_url='reader:index')
def update_setting(request):
    if request.method == 'POST':
       
        settings = UserSetting.objects.filter(user_id = request.user.id)
        if len(settings) == 0:
            setting = UserSetting(user_id = request.user.id,font_size = request.POST['font_size'],read_bg = request.POST['read_bg'])
            setting.save()
        else:
            settings[0].font_size = request.POST['font_size']
            settings[0].read_bg = request.POST['read_bg']
            settings[0].save()
        return HttpResponse('ok')  
    return HttpResponse('not login')  

@login_required(login_url='reader:index')
def bookmark_save(request):
    if not request.user.is_authenticated:
        return HttpResponse('not login')  
    
    if request.method == 'POST':
        user_bookmark = UserBookMark(user_id = request.user.id,book_id = request.POST['book_id'],chapter_id = request.POST['chapter_id'],
            chapter_title = request.POST.get('chapter_title', ''), words_read = request.POST['words_read'],content =request.POST['content'] )
        user_bookmark.save()
        return HttpResponse('ok')  

    
def chapter_list_ajax(request, book_id):
    """AJAX 返回书籍的章节列表 HTML，仅在用户打开目录时请求"""
    _book = get_object_or_404(Book, id=book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser == 1):
        return JsonResponse({'success': False, 'error': 'no permission'})
    chapter_list = Chapter.objects.filter(book_id=book_id)
    chapter_ids = list(chapter_list.values_list('id', flat=True))
    html = render_to_string('chapter_list.html', {
        'chapter_list': chapter_list,
        'chapter_id': request.GET.get('chapter_id'),
    }, request=request)
    return JsonResponse({
        'success': True,
        'chapter_ids': chapter_ids,
        'html': html,
    })


def ret_null(request):
    return HttpResponse('')


def test_requset(request):
    form_book.handle_local_book(request,'temp/《遮天》（精校版全本）作者：辰东.txt')
    return HttpResponse('done')