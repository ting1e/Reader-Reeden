from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0013_usersetting_font_family'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='font_color',
            field=models.CharField(max_length=64, default=''),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='letter_spacing',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='line_height',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='usersetting',
            name='font_weight',
            field=models.CharField(max_length=16, default=''),
        ),
    ]
