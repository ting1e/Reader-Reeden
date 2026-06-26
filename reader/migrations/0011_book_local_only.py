from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0010_userbookrecord_progress'),
    ]

    operations = [
        migrations.AddField(
            model_name='book',
            name='local_only',
            field=models.BooleanField(default=False),
        ),
    ]
