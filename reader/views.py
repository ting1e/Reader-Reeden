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
        


@login_required(login_url='reader:index')        
def BookView(request):
    if request.method == 'POST':
        book_id = request.POST.get('book_id')  # 获取表单提交的 book_id
        chapter_id = request.POST.get('chapter_id') 
        print(chapter_id)
        _book = get_object_or_404(Book,id = int(book_id))
        if _book.share == True or _book.uploader == request.user.id or request.user.is_superuser == 1:
            pass
        else:
            return redirect('reader:index')
        # book = Book.objects.get(id=book_id)  # 根据 ID 获取书籍对象
        if chapter_id==None:

            chapter_list = Chapter.objects.filter(book_id = book_id)
            chapter_ids = list(chapter_list.values_list('id', flat=True))
            cur_chpt = chapter_list[0]
            offset = 0
            with open(_book.book_url,'r',encoding=_book.charset) as f:
                content = f.read()[cur_chpt.start:cur_chpt.end]
                # print(content)
                content = content.split('\n')

                chapter_view = render_to_string('chapter_view.html',{'chapter_title':cur_chpt.title,'content_lines':content})
                return render(request, 'book_view.html', {'chapter_title':cur_chpt.title,'chapter_list':chapter_list,'chapter_ids':chapter_ids,
                                                          'content_lines':content,'progess':20,
                                                          'book_id':book_id,'chapter_id':cur_chpt.id,'chapter_view':chapter_view,'last_words':0})
        else:
            cur_chpt = get_object_or_404(Chapter,pk = chapter_id)
            chapter_list = Chapter.objects.filter(book_id = book_id)
            chapter_ids = list(chapter_list.values_list('id', flat=True))
            with open(_book.book_url,'r',encoding=_book.charset) as f:
                content = f.read()[cur_chpt.start:cur_chpt.end]
                # print(content)
                content = content.split('\n')

                chapter_view = render_to_string('chapter_view.html',{'chapter_title':cur_chpt.title,'content_lines':content})
                return render(request, 'book_view.html', {'chapter_title':cur_chpt.title,'chapter_list':chapter_list,'chapter_ids':chapter_ids,
                                                          'content_lines':content,'progess':20,
                                                          'book_id':book_id,'chapter_id':cur_chpt.id,'chapter_view':chapter_view,'last_words':0})
            

    return HttpResponse('get')


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

# def book_reader(request,book_pk):
#     if  request.method == 'POST':
#         if not request.user.is_authenticated:
#             return HttpResponse('required login')  
        
        
#     _book = get_object_or_404(Book,id = int(book_pk))
#     if _book.share != True and _book.uploader != request.user.id:
#         return redirect('reader:index')

#     chapter_list = Chapter.objects.filter(book_id = book_pk)

#     cur_chpt = chapter_list[len(chapter_list)-1]
#     offset = 0
#     with open(_book.book_url,'r',encoding=_book.charset) as f:
#         # f.seek(cur_chpt.start+2 )
#         content = f.read()[cur_chpt.start :cur_chpt.end]
#         # print(content)
#         content = content.split('\n')
#         return render(request, 'book_reader.html', {'chapter_title':cur_chpt.title,'chapter_list':chapter_list,'content_lines':content,'progess':20})


@login_required(login_url='reader:index')
def upload_file(request):
    if  request.method == 'POST':
        handle_uploaded_file(request)
        return render(request, 'upload_file.html', {'notice':"succeed"})
    return render(request, 'upload_file.html')




@login_required(login_url='reader:index')
def BookAdminView(request):
    if request.user.is_superuser == 1:
        books = Book.objects.all()
    else:
        books = Book.objects.filter(uploader = request.user.id)
    return render(request, 'book_admin.html', {'book_list': books})


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

class IndexView(generic.TemplateView):
    template_name = 'index.html'


def book(request,pk):
    if request.user.is_authenticated:
        last = UserBookRecord.objects.filter(user_id = request.user.id , book_id = pk).order_by('-read_time')
        if len(last) > 0:
            last = last[0]
            return redirect('reader:book_reader',pk,last.chapter_id)
    _book =  get_object_or_404(Book,id = pk)
    if _book.share == True or _book.uploader==request.user.id:
        return redirect('reader:book_reader',book_pk=pk,chapter_pk=_book.first_chapter_id)
    return redirect('reader:index')


@login_required(login_url='reader:index')
def book_reader_offset(request,book_pk,chapter_pk,offset):
    _book = get_object_or_404(Book,id = book_pk)
    if _book.uploader == 0 or _book.uploader == request.user.id:
        chapter_list = Chapter.objects.filter(book_id = book_pk)
        chapter_list = Chapter.objects.filter(book_id = book_pk)
        chapter = get_object_or_404(Chapter,pk = chapter_pk)
        content = 'NULL'
        with open(_book.book_url,'r',encoding=_book.charset) as f:
            content = f.read()[chapter.start :chapter.end]
        content_lines = content.split('\n')
        if len(content_lines) == 1:
            content_lines = content_lines[0].split(' ',1)
        user_setting = get_object_or_404(UserSetting,user_id = request.user.id)


        return render(request, 'book_reader.html', 
                    {'chapter_list': chapter_list,'chapter_title':content_lines[0],'content_lines':content_lines[1:],
                'last_words':offset,'user_setting':user_setting})

    return redirect('reader:index')


def progress(book_id,chapter_id):
    chapter_list = Chapter.objects.filter(book_id = book_id).order_by('index')
    book = Book.objects.filter(id=book_id)[0]
    all = book.word_count
    read = 0.0
    
    for ch in chapter_list:
        if chapter_id == ch.id:
            break
        read+=ch.end - ch.start
    return read/all *100 
    
# def book_reader(request,book_pk,chapter_pk):
#     if  request.method == 'POST':
#         if not request.user.is_authenticated:
#             return HttpResponse('required login')  
        
#         if 'words' in request.POST:
#             records = UserBookRecord.objects.filter(user_id =request.user.id, book_id =book_pk)
#             if len(records) == 0:
#                 UserBookRecord(user_id =request.user.id, book_id =book_pk, chapter_id = chapter_pk, words_read = int(request.POST['words'])).save()
#             else:
#                 records[0].chapter_id = chapter_pk
#                 records[0].words_read = int(request.POST['words'])
#                 records[0].save()

#             return HttpResponse('success')  
#         if 'kwd' in request.POST:
#             return keyword_search(request,book_pk,chapter_pk,str(request.POST['kwd']))
#     _book = get_object_or_404(Book,id = int(book_pk))
#     if _book.share!=True and _book.uploader != request.user.id:
#         return redirect('reader:index')

#     chapter_list = Chapter.objects.filter(book_id = book_pk)
#     chapter = get_object_or_404(Chapter,pk = chapter_pk)

#     content = 'NULL'
#     with open(_book.book_url,'r',encoding=_book.charset) as f:
#         content = f.read()[chapter.start :chapter.end]
        
#     if content[:len(chapter.title)] == chapter.title:
#         content = content[len(chapter.title):]
#     content = content.strip().split('\n')
#     # return render(request, 'book_reader.html', {'chapter_title':chapter.title,'chapter_list':chapter_list,'content_lines':content,'progess':20})

#     # print(request.META['HTTP_REFERER'])
#     # if request.user.is_authenticated and 'HTTP_REFERER' in request.META and ('book_list' in request.META['HTTP_REFERER'] or 'bookmark_href' in request.META['HTTP_REFERER'])  :
#     #     print(request.META['HTTP_REFERER'])
#     #     last = UserBookRecord.objects.filter(user_id = request.user.id , book_id = book_pk).order_by('-read_time')
#     #     user_setting = get_object_or_404(UserSetting,user_id = request.user.id)
#     #     if len(last) > 0:
#     #         last = last[0]
#     #         return render(request, 'book_reader.html', 
#     #             {'chapter_title':chapter.title,'chapter_list':chapter_list, 'content_lines':content,
#     #             'last_words':last.words_read,'user_setting':user_setting,'progess':progress(book_pk,chapter_pk)})

#     if request.user.is_authenticated :
#         user_setting = get_object_or_404(UserSetting,user_id = request.user.id)
#         return render(request, 'book_reader.html', {'chapter_title':chapter.title,'chapter_list':chapter_list,'content_lines':content,
#             'user_setting':user_setting,'progess':progress(book_pk,chapter_pk),'last_words':0})
#     else:
#         return render(request, 'book_reader.html', {'chapter_title':chapter.title,'chapter_list':chapter_list,'content_lines':content,'progess':progress(book_pk,chapter_pk),'last_words':0})

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
    def __init__(self,book,chapter,cont,off):
        self.book_pk = book
        self.chapter_pk = chapter
        self.content = cont
        self.offset = off

def keyword_search(request,book_pk,chapter_pk,kwd):
    chapter_list = Chapter.objects.filter(book_id = book_pk)
    search_list = []
    for chapter in chapter_list:
        con = get_object_or_404(Content,pk = chapter.content_id)
        content_lines = con.content.split('\n')
        cnt = 0
        content_cnt = [0,]
        for i in content_lines:
            cnt += len(i)
            content_cnt.append(cnt)

        for i in range(len(content_lines)):
            if content_lines[i].find(kwd) != -1:
                search_list.append(search_item(book_pk,chapter.id,content_lines[i],content_cnt[i]))
    return render(request, 'search.html', {'list': search_list})

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
            words_read = request.POST['words_read'],content =request.POST['content'] )
        user_bookmark.save()
        return HttpResponse('ok')  

    
def ret_null(request):
    return HttpResponse('')


def test_requset(request):
    form_book.handle_local_book(request,'temp/《遮天》（精校版全本）作者：辰东.txt')
    return HttpResponse('done')