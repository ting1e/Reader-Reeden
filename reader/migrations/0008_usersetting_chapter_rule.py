from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0007_book_file_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersetting',
            name='chapter_rule',
            field=models.CharField(default=r'^[ 　\t]{0,4}(?:序章|楔子|正文(?!完|结)|终章|后记|尾声|番外|第\s{0,4}[\d〇零一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟廿卅]+?\s{0,4}(?:章|折|节(?!课)|卷|集(?![合和])|部(?![分赛游])|篇(?!张))).{0,30}$', max_length=1024),
        ),
    ]
