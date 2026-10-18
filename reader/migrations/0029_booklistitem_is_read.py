from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0028_usersetting_chapter_len_limits'),
    ]

    operations = [
        migrations.AddField(
            model_name='booklistitem',
            name='is_read',
            field=models.BooleanField(default=False),
        ),
    ]
