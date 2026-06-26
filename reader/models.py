import math
from django.db import models
import re
import django.utils.timezone as timezone
from matplotlib.pyplot import cla, title
# from numpy import mat
import chardet



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

    first_chapter_title = models.CharField(max_length=64)
    first_chapter_id = models.IntegerField(default=0)
    last_chapter_title = models.CharField(max_length=64)
    last_chapter_id = models.IntegerField(default=0)


    uploader = models.IntegerField(default=0)
    upload_time = models.DateTimeField(default = timezone.now)
    share = models.BooleanField (default = False)
    charset = models.CharField(max_length=64)

    md5 = models.CharField(max_length=32,default='')
    local = models.BooleanField(default=False)
    local_only = models.BooleanField(default=False)

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
    s3_setting = models.CharField(max_length=4096,default='"{\"accessKeyId\":\"YOUR_ACCESS_KEY\",\"secretAccessKey\":\"YOUR_SECRET_KEY\",\"region\":\"us-east-1\",\"endpoint\":\"https://s3.us-east-1.amazonaws.com\",\"bucket\":\"YOUR_BUCKET_NAME\",\"prefix\":\"YOUR_FOLDER_NAME/\"}"')
    chapter_rule = models.CharField(max_length=1024, default=r'^[ 　\t]{0,4}(?:序章|楔子|正文(?!完|结)|终章|后记|尾声|番外|第\s{0,4}[\d〇零一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+?\s{0,4}(?:章|折|节(?!课)|卷|集(?![合和])|部(?![分赛游])|篇(?!张))).{0,30}$')

class UserBookMark(models.Model):
    user_id = models.IntegerField()
    book_id = models.IntegerField()
    chapter_id = models.IntegerField()
    chapter_title = models.CharField(max_length=256, default='')
    words_read = models.IntegerField()
    content = models.TextField()
    add_time = models.DateTimeField(default = timezone.now)

