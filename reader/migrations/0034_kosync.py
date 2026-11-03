from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0033_chapter_volume_and_usersetting_volume_rule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='progress_sync_mode',
            field=models.CharField(default='json', max_length=16),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='kosync_checksum_method',
            field=models.CharField(default='binary', max_length=16),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='kosync_auth_hash',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.CreateModel(
            name='KosyncProgress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document', models.CharField(max_length=128)),
                ('progress', models.TextField()),
                ('percentage', models.FloatField(default=0)),
                ('device', models.CharField(blank=True, default='', max_length=128)),
                ('device_id', models.CharField(blank=True, default='', max_length=128)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'document'), name='unique_kosync_progress'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SystemSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kosync_enabled', models.BooleanField(default=False)),
            ],
        ),
    ]
