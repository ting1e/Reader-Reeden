from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0012_book_url_relative'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='font_family',
            field=models.CharField(max_length=256, default=''),
        ),
    ]
