from django.db import migrations, models

from reader.utils import DEFAULT_CHAPTER_RULE


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0016_usersetting_theme'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='chapter_rule_2',
            field=models.CharField(max_length=1024, default=''),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='chapter_rule_3',
            field=models.CharField(max_length=1024, default=''),
        ),
    ]
