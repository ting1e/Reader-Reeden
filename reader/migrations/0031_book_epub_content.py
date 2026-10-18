from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reader', '0030_booklistitem_read_status')]

    operations = [
        migrations.AddField(
            model_name='book', name='format',
            field=models.CharField(default='txt', max_length=8),
        ),
        migrations.AddField(
            model_name='book', name='content_path',
            field=models.CharField(default='', max_length=512),
        ),
    ]
