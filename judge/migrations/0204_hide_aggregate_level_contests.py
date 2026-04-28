from django.db import migrations


def hide_aggregate_level_contests(apps, schema_editor):
    Contest = apps.get_model('judge', 'Contest')

    for contest in Contest.objects.filter(name__contains=' - Luyện tập tổng hợp').only('id', 'name', 'is_visible'):
        name = (contest.name or '').strip()
        if not name.startswith('Level '):
            continue
        if ' - Luyện tập tổng hợp' not in name:
            continue
        suffix = name[len('Level '):].split(' - Luyện tập tổng hợp', 1)[0].strip()
        if not suffix.isdigit():
            continue
        if contest.is_visible:
            contest.is_visible = False
            contest.save(update_fields=['is_visible'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0203_solution_language_key'),
    ]

    operations = [
        migrations.RunPython(hide_aggregate_level_contests, noop_reverse),
    ]
