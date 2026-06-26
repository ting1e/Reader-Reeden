from django.http import HttpResponse, JsonResponse
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

def get_upload_dir():
    d = os.path.join(BASE_DIR, 'local', 'upload')
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

def get_books_progress(user, books):
    """批量返回 {book_id: 进度百分比(0-100)}，基于用户的最新阅读记录。"""
    result = {}
    if not books:
        return result
    for b in books:
        result[b.id] = 0
    if not user.is_authenticated:
        return result
    book_ids = [b.id for b in books]
    records = UserBookRecord.objects.filter(
        user_id=user.id, book_id__in=book_ids
    ).order_by('-read_time')
    latest = {}
    for r in records:
        if r.book_id not in latest:
            latest[r.book_id] = r
    if not latest:
        return result
    book_map = {b.id: b for b in books}
    for book_id, rec in latest.items():
        book = book_map.get(book_id)
        if book is not None:
            book.read_time = rec.read_time
        try:
            progress_val = round(float(rec.progress), 2)
        except (TypeError, ValueError):
            progress_val = 0
        result[book_id] = min(max(0, progress_val), 100)
    return result

def save_progress_json(book, chapter, words_read):
    if getattr(book, 'local_only', False):
        return
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

def _parse_progress_time(obj):
    """从进度 dict 中解析 lastReadTime 为 naive UTC datetime，失败返回 None。"""
    if not obj:
        return None
    t = obj.get("lastReadTime")
    if not t:
        return None
    try:
        return make_naive_utc(datetime.datetime.fromisoformat(str(t).replace('Z', '+00:00')))
    except Exception:
        return None

def _get_s3_client(cfg):
    import boto3
    from botocore.config import Config
    s3_config = Config(
        request_checksum_calculation='WHEN_REQUIRED',
        response_checksum_validation='WHEN_REQUIRED'
    )

    return boto3.client(
        's3',
        aws_access_key_id=cfg['access_key'],
        aws_secret_access_key=cfg['secret_key'],
        region_name=cfg['region'],
        endpoint_url=cfg['endpoint'],
        config=s3_config  # 注入兼容性配置
    )

def sync_progress_to_s3(request, book):
    """若本地进度比 S3 上的新（或 S3 无进度），则上传本地进度到 S3。"""
    if getattr(book, 'local_only', False):
        return
    cfg = get_s3_config(request.user)
    if not cfg or not book or not getattr(book, 'book_url', None):
        return
    try:
        md5_val = get_file_md5(book.book_url)
    except Exception as e:
        print(f"sync_progress_to_s3 MD5 error: {e}")
        return
    local_path = os.path.join(get_progress_dir(), f'{md5_val}.json')
    if not os.path.exists(local_path):
        return
    try:
        with open(local_path, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
    except Exception:
        local_data = None
    local_time = _parse_progress_time(local_data)
    if local_time is None:
        return
    s3_key = f"{cfg['prefix']}book_progress/{md5_val}.json"
    try:
        client = _get_s3_client(cfg)
        try:
            resp = client.get_object(Bucket=cfg['bucket'], Key=s3_key)
            remote_data = json.load(resp['Body'])
            remote_time = _parse_progress_time(remote_data)
        except Exception as e:
            remote_time = None
        if remote_time is None or local_time > remote_time:
            with open(local_path, 'rb') as pf:
                body = pf.read()
            client.put_object(Bucket=cfg['bucket'], Key=s3_key, Body=body, ContentLength=len(body))
    except Exception as e:
        print(f"sync_progress_to_s3 upload error: {e}")

class BookListView(generic.ListView):
    template_name = 'book_list.html'
    context_object_name = 'book_list'

    def get_queryset(self):
        """Return the last five published questions."""
        if self.request.user.is_authenticated:
            return Book.objects.filter(share = False) | Book.objects.filter(uploader = self.request.user.id)
        else:
            return Book.objects.filter(share = True)

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
                print("Error parsing S3 settings or connecting to S3:", e)
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

        # 先把远端进度读到临时内存，与本地进度按时间对比，保留较新的版本
        try:
            resp = s3_client.get_object(Bucket=bucket, Key=progress_key)
            remote_data = json.load(resp['Body'])
        except Exception as e:
            print(f"Remote progress not found or fetch error: {e}")
            remote_data = None

        remote_time = _parse_progress_time(remote_data)
        local_data = None
        if os.path.exists(local_progress_path):
            try:
                with open(local_progress_path, 'r', encoding='utf-8') as lf:
                    local_data = json.load(lf)
            except Exception:
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
            except Exception as e:
                print(f"Error writing remote progress to local: {e}")
    except Exception as e:
        print(f"Error syncing progress from S3: {e}")

    return redirect(f"{reverse('reader:book_view')}?book_id={book.id}")


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
        return HttpResponse('get')

    # ---- 权限检查（所有分支共用，防止越权访问他人私有书籍）----
    _book = get_object_or_404(Book, id=book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser):
        return redirect('reader:index')

    # ---- POST: 保存阅读进度 (AJAX) ----
    if request.method == 'POST' and 'words' in request.POST and 'chapter_id' in request.POST:
        if not request.user.is_authenticated:
            return HttpResponse('not login')
        _chapter_id = request.POST.get('chapter_id')
        words_read = request.POST.get('words')
        try:
            chapter = Chapter.objects.get(id=_chapter_id)
        except Chapter.DoesNotExist:
            chapter = None
        progress_val = 0
        if chapter:
            progress_val = calculate_read_progress(_book, chapter, words_read) / 100.0
        records = UserBookRecord.objects.filter(user_id=request.user.id, book_id=book_id).order_by('-read_time')
        if len(records) == 0:
            UserBookRecord(
                user_id=request.user.id,
                book_id=book_id,
                chapter_id=_chapter_id,
                words_read=int(words_read),
                progress=progress_val
            ).save()
        else:
            records[0].chapter_id = _chapter_id
            records[0].words_read = int(words_read)
            records[0].progress = progress_val
            records[0].read_time = timezone.now()
            records[0].save()

        try:
            if chapter:
                save_progress_json(_book, chapter, words_read)
                sync_progress_to_s3(request, _book)
        except Exception as e:
            print(f"Failed to save progress JSON: {e}")

        return HttpResponse('success')

    # ---- POST: 关键词搜索 (AJAX) ----
    if request.method == 'POST' and 'kwd' in request.POST:
        _chapter_id = request.POST.get('chapter_id')
        if _chapter_id:
            return keyword_search(request, book_id, int(_chapter_id), str(request.POST['kwd']))
        return HttpResponse('')

    # ---- 确定 chapter_id / offset：POST 表单 or GET 参数 ----
    chapter_id = None
    offset = 0
    if request.method == 'POST' and 'book_id' in request.POST:
        _ch = request.POST.get('chapter_id')
        if _ch:
            chapter_id = int(_ch)
        _off = request.POST.get('words_read')
        if _off:
            offset = int(_off)
    elif request.method == 'GET' and 'book_id' in request.GET:
        _ch = request.GET.get('chapter_id')
        if _ch:
            chapter_id = int(_ch)
        _off = request.GET.get('offset')
        if _off:
            offset = int(_off)

    # POST: chapter_id 已在上面从表单获取；未指定则使用阅读记录或第一章
    if not chapter_id and request.user.is_authenticated and 0==offset:
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
                md5_val = get_file_md5(_book.book_url)
                json_path = os.path.join(get_progress_dir(), f"{md5_val}.json")
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as f:
                        file_record = json.load(f)
            except Exception as e:
                print("Error reading progress file on open:", e)

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

    with open(_book.book_url, 'r', encoding=_book.charset) as f:
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
        'progess': 20,
        'book_id': book_id,
        'chapter_id': cur_chpt.id,
        'chapter_view': chapter_view,
        'last_words': offset,
    }
    if request.user.is_authenticated:
        context['user_setting'], _ = UserSetting.objects.get_or_create(
            user_id=request.user.id,
            defaults={'font_size': 16, 'read_bg': '#fff', 'read_mode': 'page'}
        )

    return render(request, 'book_view.html', context)


@login_required(login_url='reader:index')
def chapter_content(request, chapter_id):
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    _book = get_object_or_404(Book, id=chapter.book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': 'no permission'})
    with open(_book.book_url, 'r', encoding=_book.charset) as f:
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



class BookmarkListView(generic.ListView):
    template_name = 'bookmark_list.html'
    context_object_name = 'bookmark_list'

    def get_queryset(self):
        if self.request.user.is_authenticated and self.request.user.id == self.kwargs['user_id']:
            return UserBookMark.objects.filter(book_id=self.kwargs['book_id'],user_id =self.kwargs['user_id']).order_by('-add_time')
        else:
            return UserBookMark.objects.none()

class IndexView(BookListView):
    template_name = 'book_list.html'


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
    """删除单条书签"""
    mark = get_object_or_404(UserBookMark, pk=pk)
    if not (request.user.is_superuser or request.user.id == mark.user_id):
        return redirect('reader:bookmark_admin')
    mark.delete()
    return redirect('reader:bookmark_admin')


def login_auth(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        if not username or not password:
            return HttpResponse('请输出正确的用户名或密码')
        user = authenticate(username=username, password=password)
        if user is None:
            return HttpResponse('请输出正确的用户名或密码')
        UserSetting.objects.get_or_create(user_id=user.id, defaults={'font_size': 16, 'read_bg': '#fff', 'read_mode': 'page'})
        login(request, user)
        return HttpResponse('success')

    return HttpResponse('fk off')  


def logout_view(request):
    logout(request)
    return redirect('reader:book_list')
    # Redirect to a success page.

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


@login_required(login_url='reader:index')
def book_rechapter(request, pk):
    """重新分章：删除旧章节并重建，根据 progress 重算阅读记录和书签的章节定位。"""
    _book = get_object_or_404(Book, id=pk)
    if not (request.user.is_superuser or request.user.id == _book.uploader):
        return redirect('reader:book_admin')

    result = form_book.rechapter_book(_book, request.user)
    if result != 'true':
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
def upload_file(request):
    """上传书籍：保存到 local/upload，分章入库，标记 local_only=True"""
    if request.method == 'POST' and request.FILES.get('file'):
        f = request.FILES['file']
        if not f.name.endswith('.txt'):
            return HttpResponse('仅支持 .txt 文件')
        upload_dir = get_upload_dir()
        local_path = os.path.join(upload_dir, f.name)
        with open(local_path, 'wb') as dest:
            for chunk in f.chunks():
                dest.write(chunk)
        result = form_book.handle_local_book(request, local_path, local_only=True)
        if result == 'true':
            return HttpResponse('success')
        return HttpResponse('分章失败')
    return render(request, 'upload_file.html')


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
    if request.method != 'POST':
        return HttpResponse('not login')
    UserSetting.objects.update_or_create(
        user_id=request.user.id,
        defaults={
            'font_size': request.POST.get('font_size'),
            'read_bg': request.POST.get('read_bg'),
            'read_mode': request.POST.get('read_mode', 'page'),
        },
    )
    return HttpResponse('ok')  

@login_required(login_url='reader:index')
def bookmark_save(request):
    if request.method != 'POST':
        return HttpResponse('method not allowed')

    book_id = request.POST.get('book_id')
    chapter_id = request.POST.get('chapter_id')
    if not book_id or not chapter_id:
        return HttpResponse('invalid params')

    _book = get_object_or_404(Book, id=book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser):
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

    
def chapter_list_ajax(request, book_id):
    """AJAX 返回书籍的章节列表 HTML，仅在用户打开目录时请求"""
    _book = get_object_or_404(Book, id=book_id)
    if not (_book.share == True or _book.uploader == request.user.id or request.user.is_superuser):
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