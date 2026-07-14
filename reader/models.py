from django.db import models
import django.utils.timezone as timezone

from .utils import DEFAULT_CHAPTER_RULE



# Create your models here.
class Book(models.Model):
    book_url = models.CharField(max_length=256)
    name = models.CharField(max_length=64)
    file_name = models.CharField(max_length=256,default='')
    author = models.CharField(max_length=64)
    book_grp = models.IntegerField(default=0)
    intro  = models.CharField(max_length=512)
    rule = models.CharField(max_length=256)
    word_count = models.IntegerField(default=0)
    total_chapter_num = models.IntegerField(default=0)

    first_chapter_title = models.CharField(max_length=256)
    first_chapter_id = models.IntegerField(default=0)
    last_chapter_title = models.CharField(max_length=256)
    last_chapter_id = models.IntegerField(default=0)


    uploader = models.IntegerField(default=0)
    upload_time = models.DateTimeField(default = timezone.now)
    share = models.BooleanField (default = False)
    charset = models.CharField(max_length=64)

    md5 = models.CharField(max_length=32,default='')
    local = models.BooleanField(default=False)
    local_only = models.BooleanField(default=False)

    def abs_path(self):
        """返回书籍文件的绝对路径（book_url 存的是项目相对路径）。"""
        from .utils import resolve_book_path
        return resolve_book_path(self.book_url)

class BookGroup(models.Model):
    group_name = models.CharField(max_length=64)

class Chapter(models.Model):
    url = models.CharField(max_length=32)
    title = models.CharField(max_length=256)
    book_id = models.IntegerField()
    book_url = models.CharField(max_length=256)
    index = models.IntegerField()
    start = models.IntegerField()
    end = models.IntegerField()


class UserBookRecord(models.Model):
    user_id = models.IntegerField()
    book_id = models.IntegerField()
    chapter_id = models.IntegerField()
    read_time = models.DateTimeField(default = timezone.now)
    words_read = models.IntegerField()
    progress = models.FloatField(default=0)

class UserSetting(models.Model):
    user_id = models.IntegerField()
    font_size = models.IntegerField(default=16)
    read_bg = models.CharField(max_length=256,default='#fff')
    read_mode = models.CharField(max_length=16, default='page')
    font_family = models.CharField(max_length=256, default='')
    font_color = models.CharField(max_length=64, default='')
    letter_spacing = models.IntegerField(default=0)
    line_height = models.FloatField(default=1.2)
    font_weight = models.CharField(max_length=16, default='')
    page_width = models.IntegerField(default=0)
    page_height = models.IntegerField(default=0)
    theme = models.CharField(max_length=32, default='light')
    s3_setting = models.CharField(max_length=4096,default='"{\"accessKeyId\":\"YOUR_ACCESS_KEY\",\"secretAccessKey\":\"YOUR_SECRET_KEY\",\"region\":\"us-east-1\",\"endpoint\":\"https://s3.us-east-1.amazonaws.com\",\"bucket\":\"YOUR_BUCKET_NAME\",\"prefix\":\"YOUR_FOLDER_NAME/\"}"')
    chapter_rule = models.CharField(max_length=1024, default=DEFAULT_CHAPTER_RULE)
    chapter_rule_2 = models.CharField(max_length=1024, default='')
    chapter_rule_3 = models.CharField(max_length=1024, default='')

class UserBookMark(models.Model):
    user_id = models.IntegerField()
    book_id = models.IntegerField()
    chapter_id = models.IntegerField()
    chapter_title = models.CharField(max_length=256, default='')
    words_read = models.IntegerField()
    content = models.TextField()
    add_time = models.DateTimeField(default = timezone.now)


class BookList(models.Model):
    name = models.CharField(max_length=128)
    description = models.TextField(default='')
    user_id = models.IntegerField()
    is_public = models.BooleanField(default=False)
    created_time = models.DateTimeField(default=timezone.now)
    updated_time = models.DateTimeField(auto_now=True)


class BookListItem(models.Model):
    book_list_id = models.IntegerField()
    book_id = models.IntegerField(default=0)
    manual_name = models.CharField(max_length=128, default='')
    manual_author = models.CharField(max_length=64, default='')
    rating = models.IntegerField(default=0)
    review = models.TextField(default='')
    sort_order = models.IntegerField(default=0)
    added_time = models.DateTimeField(default=timezone.now)


class ReadStat(models.Model):
    # 注意：禁止为本表添加任何唯一约束（如 unique_together / UniqueConstraint）。
    # 历史上曾有过 (user_id, book_id, date) 的 unique_together，已在 migration 0024 移除。
    # upsert 逻辑（_upsert_readstat）依赖 "update 命中则累加，否则 create" 的模式，
    # 若加回唯一约束，并发保存会产生 IntegrityError 而非幂等累加。
    # 以后也不得添加唯一约束 —— 如需去重请另建去重任务/迁移，勿改表约束。
    user_id = models.IntegerField()
    book_id = models.IntegerField(default=0)
    book_name = models.CharField(max_length=128, default='')
    date = models.CharField(max_length=10)
    read_seconds = models.IntegerField(default=0)
    word_count = models.IntegerField(default=0)
    last_save_time = models.DateTimeField(null=True, blank=True)
    last_progress = models.IntegerField(default=0)

