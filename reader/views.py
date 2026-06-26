from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from .models import *
from django.views import generic
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth import authenticate,login,logout
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils import timezone

from . import form_book

import os
import json
import datetime
import hashlib
import uuid

from django.conf import settings

BASE_DIR = str(settings.BASE_DIR)

def get_progress_dir():
    d = os.path.join(BASE_DIR, 'local', 'book_progress')
    os.makedirs(d, exist_ok=True)
    return d

def get_local_books_dir():
    d = os.path.join(BASE_DIR, 'local', 'books')
    os.makedirs(d, exist_ok=True)
    return d

def get_s3_config(user):
    if not user.is_authenticated:
        return None
    setting = UserSetting.objects.filter(user_id=user.id).first()
    if not setting:
        return None
    try:
        s3_dict = json.loads(setting.s3_setting)
        if isinstance(s3_dict, str):
            s3_dict = json.loads(s3_dict)
    except Exception:
        return None
    prefix = s3_dict.get('prefix', '')
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    return {
        'access_key': s3_dict.get('accessKeyId'),
        'secret_key': s3_dict.get('secretAccessKey'),
        'region': s3_dict.get('region'),
        'endpoint': s3_dict.get('endpoint'),
        'bucket': s3_dict.get('bucket'),
        'prefix': prefix,
    }

def make_naive_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        try:
            from django.conf import settings
            from django.utils import timezone
            if not getattr(settings, 'USE_TZ', False):
                dt = timezone.make_aware(dt)
        except Exception:
            pass
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt

def get_file_md5(file_path):
    with open(file_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest().upper()

def get_element_index(text):
    idx = 0
    for c in text:
        if ord(c) > 127:
            idx += 2
        else:
            idx += 1
    return idx

def get_device_id():
    node = uuid.getnode()
    return str(uuid.UUID(int=node))

def calculate_read_progress(book, chapter, words_read):
    try:
        chapter_list = Chapter.objects.filter(book_id=book.id).order_by('index')
        all_chars = book.word_count
        if all_chars <= 0:
            return 0
        read = 0.0
        for ch in chapter_list:
            if chapter.id == ch.id:
                break
            read += ch.end - ch.start
        
        try:
            current_words = int(words_read)
        except (TypeError, ValueError):
            current_words = 0
            
        progress_val = int(((read + current_words) / all_chars) * 10000)
        return min(max(0, progress_val), 10000)
    except Exception:
        return 0

def save_progress_json(book, chapter, words_read):
    try:
        md5_val = get_file_md5(book.book_url)
    except Exception as e:
        print(f"Error calculating MD5: {e}")
        return

    device_id = get_device_id()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_utc.isoformat().replace('+00:00', 'Z')
    
    from django.utils import timezone
    dj_now = timezone.now()
    if timezone.is_aware(dj_now):
        dj_now = timezone.localtime(dj_now)
    current_date_str = dj_now.strftime('%Y-%m-%d')
    
    try:
        with open(book.book_url, 'r', encoding=book.charset) as f:
            content = f.read()[chapter.start:chapter.end]
            content_lines = content.split('\n')
    except Exception as e:
        print(f"Error reading book file: {e}")
        return

    try:
        target_words = int(words_read)
    except (TypeError, ValueError):
        target_words = 0
    accumulated = 0
    paragraph_index = 0
    element_index = 0

    for idx, line in enumerate(content_lines):
        line_len = len(line)
        if accumulated + line_len >= target_words:
            paragraph_index = idx
            element_offset = target_words - accumulated
            element_index = get_element_index(line[:element_offset])
            break
        accumulated += line_len
    else:
        if content_lines:
            paragraph_index = len(content_lines) - 1
            element_index = get_element_index(content_lines[-1])

    temp_dir = get_progress_dir()
    json_path = os.path.join(temp_dir, f"{md5_val}.json")
    
    old_data = None
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except Exception:
            pass

    # todayStats 保持原来的内容不变
    if old_data and "todayStats" in old_data:
        stats = old_data["todayStats"]
    else:
        stats = {
            "date": current_date_str,
            "devices": {
                device_id: {
                    "readSeconds": 0,
                    "wordCount": 0,
                    "hourly": {}
                }
            }
        }

    progress_val = calculate_read_progress(book, chapter, words_read)

    new_data = {
        "schemaVersion": 1,
        "bookId": md5_val,
        "sectionIndex": chapter.index,
        "paragraphIndex": paragraph_index,
        "elementIndex": element_index,
        "readProgress": progress_val,
        "lastReadTime": now_iso,
        "deviceId": device_id,
        "todayStats": stats,
        "chapterId": chapter.id,
        "wordsRead": target_words
    }

    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing progress JSON: {e}")

class BookListView(generic.ListView):
    template_name = 'book_list.html'
    context_object_name = 'book_list'

    def get_queryset(self):
        """Return the last five published questions."""
        if self.request.user.is_authenticated:
            return Book.objects.filter(share = False) | Book.objects.filter(uploader = self.request.user.id)
        else:
            return Book.objects.filter(share = True)
        
class BookListRemoteView(generic.ListView):
    template_name = 'book_list_remote.html'
    context_object_name = 'book_list_remote'

    def get_queryset(self):
        remote_files = []
        if self.request.user.is_authenticated:
            settings = UserSetting.objects.filter(user_id=self.request.user.id)
            if len(settings) > 0:
                s3_str = settings[0].s3_setting
                try:
                    s3_dict = json.loads(s3_str)
                    if isinstance(s3_dict, str):
                        s3_dict = json.loads(s3_dict)
                    
                    import boto3
                    s3_client = boto3.client(
                        's3',
                        aws_access_key_id=s3_dict.get('accessKeyId'),
                        aws_secret_access_key=s3_dict.get('secretAccessKey'),
                        region_name=s3_dict.get('region'),
                        endpoint_url=s3_dict.get('endpoint')
                    )
                    
                    bucket = s3_dict.get('bucket')
                    prefix = s3_dict.get('prefix', '')
                    if prefix and not prefix.endswith('/'):
                        prefix += '/'
                    target_prefix = prefix + 'books/'
                    
                    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=target_prefix)
                    if 'Contents' in response:
                        local_books_set = set(Book.objects.values_list('file_name', flat=True))
                        for obj in response['Contents']:
                            if obj['Key'] != target_prefix:
                                filename = obj['Key'][len(target_prefix):]
                                is_in_db = filename in local_books_set
                                remote_files.append({'name': filename, 'in_db': is_in_db})
                    
                except Exception as e:
                    print("Error parsing S3 settings or connecting to S3:", e)
        return remote_files


@login_required(login_url='reader:index')
def open_remote_book(request):
    """点击远程书籍：已在本地则直接打开，否则从 S3 下载到 local/books、
    下载进度到 local/book_progress、入库分章后打开。"""
    book_name = request.GET.get('name', '') if request.method == 'GET' else request.POST.get('name', '')
    if not book_name:
        return redirect('reader:book_list_remote')

    existing = Book.objects.filter(file_name=book_name).first()
    if existing:
        return redirect(f"{reverse('reader:book_view')}?book_id={existing.id}")

    cfg = get_s3_config(request.user)
    if not cfg:
        return HttpResponse('S3 未配置')

    import boto3
    s3_client = boto3.client(
        's3',
        aws_access_key_id=cfg['access_key'],
        aws_secret_access_key=cfg['secret_key'],
        region_name=cfg['region'],
        endpoint_url=cfg['endpoint'],
    )
    bucket = cfg['bucket']
    prefix = cfg['prefix']
    s3_key = f'{prefix}books/{book_name}'

    local_path = os.path.join(get_local_books_dir(), book_name)
    try:
        s3_client.download_file(bucket, s3_key, local_path)
    except Exception as e:
        print(f"Error downloading book from S3: {e}")
        return HttpResponse(f'下载失败: {e}')

    result = form_book.handle_local_book(request, local_path)
    if result != 'true':
        return HttpResponse('分章失败')

    book = Book.objects.filter(file_name=book_name).first()
    if not book:
        return HttpResponse('入库失败')

    try:
        md5_val = get_file_md5(local_path)
        progress_key = f'{prefix}book_progress/{md5_val}.json'
        local_progress_path = os.path.join(get_progress_dir(), f'{md5_val}.json')
        s3_client.download_file(bucket, progress_key, local_progress_path)
    except Exception as e:
        print(f"Error downloading progress from S3: {e}")

    return redirect(f"{reverse('reader:book_view')}?book_id={book.id}")


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
            records[0].read_time = timezone.now()
            records[0].save()

        try:
            book = Book.objects.get(id=_book_id)
            chapter = Chapter.objects.get(id=_chapter_id)
            save_progress_json(book, chapter, words_read)
        except Exception as e:
            print(f"Failed to save progress JSON: {e}")

        return HttpResponse('success')

    # ---- POST: 关键词搜索 (AJAX) ----
    if request.method == 'POST' and 'kwd' in request.POST:
        _book_id = request.POST.get('book_id')
        _chapter_id = request.POST.get('chapter_id')
        if _book_id and _chapter_id:
            return keyword_search(request, int(_book_id), int(_chapter_id), str(request.POST['kwd']))
        return HttpResponse('')

    # ---- 确定 book_id：POST 表单 or GET 参数
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
    elif request.method == 'GET' and 'book_id' in request.GET:
        book_id = int(request.GET.get('book_id'))
        _ch = request.GET.get('chapter_id')
        if _ch:
            chapter_id = int(_ch)
        _off = request.GET.get('offset')
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
        db_record = None
        last = UserBookRecord.objects.filter(
            user_id=request.user.id, book_id=book_id
        ).order_by('-read_time')
        if last.exists():
            db_record = last[0]

        file_record = None
        try:
            md5_val = get_file_md5(_book.book_url)
            json_path = os.path.join(get_progress_dir(), f"{md5_val}.json")
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    file_record = json.load(f)
        except Exception as e:
            print("Error reading progress file on open:", e)

        use_file = False
        if file_record and db_record:
            try:
                file_time_str = file_record.get("lastReadTime")
                if file_time_str:
                    file_time = datetime.datetime.fromisoformat(file_time_str.replace('Z', '+00:00'))
                    file_time = make_naive_utc(file_time)
                    db_time = make_naive_utc(db_record.read_time)
                    print(file_time, db_time)
                    if file_time > db_time:
                        use_file = True
            except Exception:
                pass
        elif file_record and not db_record:
            use_file = True

        # 默认优先使用数据库记录，否则使用第一章
        if db_record:
            chapter_id = db_record.chapter_id
            offset = db_record.words_read
        else:
            chapter_id = _book.first_chapter_id
            offset = 0

        if use_file and "sectionIndex" in file_record:
            section_index = int(file_record["sectionIndex"])
            cur_chapter = Chapter.objects.filter(book_id=book_id, index=section_index).first()
            if cur_chapter:
                chapter_id = cur_chapter.id
                paragraph_index = file_record.get("paragraphIndex", 0)
                element_index = file_record.get("elementIndex", 0)
                
                with open(_book.book_url, 'r', encoding=_book.charset) as f:
                    content = f.read()[cur_chapter.start:cur_chapter.end]
                    content_lines = content.split('\n')
                
                accumulated = 0
                for idx in range(min(paragraph_index, len(content_lines))):
                    accumulated += len(content_lines[idx])
                
                element_offset = 0
                if paragraph_index < len(content_lines):
                    current_line = content_lines[paragraph_index]
                    idx_val = 0
                    for c in current_line:
                        if idx_val >= element_index:
                            break
                        if ord(c) > 127:
                            idx_val += 2
                        else:
                            idx_val += 1
                        element_offset += 1
                offset = accumulated + element_offset

            # 同步更新/新增数据库进度记录
            if db_record:
                db_record.chapter_id = chapter_id
                db_record.words_read = offset
                db_record.read_time = timezone.now()
                db_record.save()
            else:
                UserBookRecord(
                    user_id=request.user.id,
                    book_id=book_id,
                    chapter_id=chapter_id,
                    words_read=offset,
                    read_time=timezone.now()
                ).save()
        elif not use_file and db_record:
            # 既然数据库更新，就把最新进度同步到 md5.json
            ch_obj = Chapter.objects.get(id=chapter_id)
            save_progress_json(_book, ch_obj, offset)
                
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

@login_required(login_url='reader:index')
def book_admin(request):
    """书籍管理：列出所有本地书籍"""
    if request.user.is_superuser:
        book_list = Book.objects.all().order_by('-upload_time')
    else:
        book_list = Book.objects.filter(uploader=request.user.id).order_by('-upload_time')
    return render(request, 'book_admin.html', {'book_list': book_list})


@login_required(login_url='reader:index')
def book_local_del(request, pk):
    """删除本地书籍：只删除本地文件、进度、数据库记录，不触碰 S3"""
    _book = get_object_or_404(Book, id=pk)
    if not (request.user.is_superuser or request.user.id == _book.uploader):
        return redirect('reader:book_admin')

    try:
        md5_val = get_file_md5(_book.book_url)
    except Exception:
        md5_val = None

    try:
        if os.path.exists(_book.book_url):
            os.remove(_book.book_url)
    except Exception as e:
        print(f"Error deleting local book file: {e}")

    if md5_val:
        progress_path = os.path.join(get_progress_dir(), f'{md5_val}.json')
        try:
            if os.path.exists(progress_path):
                os.remove(progress_path)
        except Exception as e:
            print(f"Error deleting progress file: {e}")

    Chapter.objects.filter(book_id=pk).delete()
    UserBookRecord.objects.filter(book_id=pk).delete()
    UserBookMark.objects.filter(book_id=pk).delete()
    Book.objects.filter(id=pk).delete()

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


def ret_null(request):
    return HttpResponse('')


def test_requset(request):
    form_book.handle_local_book(request,'/home/ubuntu/Reader-Reeden/temp/《诛仙》（校对版全本）作者：萧鼎.txt')
    return HttpResponse('done')