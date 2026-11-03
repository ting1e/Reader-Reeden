from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0034_kosync'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='kosync_password_version',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
