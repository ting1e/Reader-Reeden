from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0008_usersetting_chapter_rule'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='read_mode',
            field=models.CharField(default='page', max_length=16),
        ),
    ]
