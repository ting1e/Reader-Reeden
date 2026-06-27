from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0014_usersetting_typography'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usersetting',
            name='line_height',
            field=models.FloatField(default=1.2),
        ),
    ]
