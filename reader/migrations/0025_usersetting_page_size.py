from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0024_alter_readstat_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='page_width',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='page_height',
            field=models.IntegerField(default=0),
        ),
    ]
