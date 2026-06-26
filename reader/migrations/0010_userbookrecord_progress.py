from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0009_usersetting_read_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='userbookrecord',
            name='progress',
            field=models.FloatField(default=0),
        ),
    ]
