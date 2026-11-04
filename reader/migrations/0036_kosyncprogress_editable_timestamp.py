from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0035_kosync_password_version'),
    ]

    operations = [
        migrations.AlterField(
            model_name='kosyncprogress',
            name='updated_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
