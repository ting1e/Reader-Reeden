from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0032_userbookrecord_epub_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='volume_rule',
            field=models.CharField(default='', max_length=1024),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='volume_rule_2',
            field=models.CharField(default='', max_length=1024),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='volume_rule_3',
            field=models.CharField(default='', max_length=1024),
        ),
        migrations.AddField(
            model_name='chapter',
            name='volume_index',
            field=models.IntegerField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='chapter',
            name='volume_title',
            field=models.CharField(blank=True, default='', max_length=256),
        ),
    ]
