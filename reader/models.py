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

class UserSetting(models.Model):
    user_id = models.IntegerField()
    font_size = models.IntegerField(default=16)
    read_bg = models.CharField(max_length=256,default='#fff')

class UserBookMark(models.Model):
    user_id = models.IntegerField()
    book_id = models.IntegerField()
    chapter_id = models.IntegerField()
    chapter_title = models.CharField(max_length=256, default='')
    words_read = models.IntegerField()
    content = models.TextField()
    add_time = models.DateTimeField(default = timezone.now)

