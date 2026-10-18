from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reader', '0031_book_epub_content')]
    operations = [
        migrations.AddField(
            model_name='userbookrecord', name='epub_location',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='userbookmark', name='epub_location',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
