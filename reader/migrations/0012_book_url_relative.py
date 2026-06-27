import os

from django.conf import settings
from django.db import migrations


def to_relative(apps, schema_editor):
    base_dir = str(settings.BASE_DIR)
    Book = apps.get_model('reader', 'Book')
    Chapter = apps.get_model('reader', 'Chapter')
    for book in Book.objects.iterator():
        if book.book_url and os.path.isabs(book.book_url):
            book.book_url = os.path.relpath(book.book_url, base_dir)
            book.save(update_fields=['book_url'])
    for ch in Chapter.objects.iterator():
        if ch.book_url and os.path.isabs(ch.book_url):
            ch.book_url = os.path.relpath(ch.book_url, base_dir)
            ch.save(update_fields=['book_url'])


def to_absolute(apps, schema_editor):
    base_dir = str(settings.BASE_DIR)
    Book = apps.get_model('reader', 'Book')
    Chapter = apps.get_model('reader', 'Chapter')
    for book in Book.objects.iterator():
        if book.book_url and not os.path.isabs(book.book_url):
            book.book_url = os.path.join(base_dir, book.book_url)
            book.save(update_fields=['book_url'])
    for ch in Chapter.objects.iterator():
        if ch.book_url and not os.path.isabs(ch.book_url):
            ch.book_url = os.path.join(base_dir, ch.book_url)
            ch.save(update_fields=['book_url'])


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0011_book_local_only'),
    ]

    operations = [
        migrations.RunPython(to_relative, reverse_code=to_absolute),
    ]
