from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0027_booklistitem_remote_file_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='chapter_min_len',
            field=models.IntegerField(default=100),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='chapter_max_len',
            field=models.IntegerField(default=100000),
        ),
    ]
