from django.db import migrations, models


def copy_read_status(apps, schema_editor):
    items = apps.get_model('reader', 'BookListItem').objects.using(schema_editor.connection.alias)
    items.filter(is_read=True).update(read_status='read')


def restore_is_read(apps, schema_editor):
    items = apps.get_model('reader', 'BookListItem').objects.using(schema_editor.connection.alias)
    items.filter(read_status='read').update(is_read=True)


class Migration(migrations.Migration):

    dependencies = [
        ('reader', '0029_booklistitem_is_read'),
    ]

    operations = [
        migrations.AddField(
            model_name='booklistitem',
            name='read_status',
            field=models.CharField(
                choices=[('unread', '未读'), ('read', '已读'), ('abandoned', '弃读')],
                default='unread', max_length=16,
            ),
        ),
        migrations.RunPython(copy_read_status, restore_is_read),
        migrations.RemoveField(model_name='booklistitem', name='is_read'),
    ]
