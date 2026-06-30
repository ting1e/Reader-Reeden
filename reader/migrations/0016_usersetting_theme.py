from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0015_usersetting_line_height_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='theme',
            field=models.CharField(max_length=32, default='light'),
        ),
    ]
