from django.db import migrations


def add_editorial_edit_perm_to_uni_mentor(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    mentor_group, _ = Group.objects.get_or_create(name='uni-mentor')
    solution_content_type = ContentType.objects.filter(app_label='judge', model='solution').first()
    if solution_content_type is None:
        return

    editorial_perm = Permission.objects.filter(
        content_type=solution_content_type,
        codename='edit_problem_editorial',
    ).first()
    if editorial_perm is None:
        return

    mentor_group.permissions.add(editorial_perm)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0205_ensure_about_flatpage'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='solution',
            options={
                'permissions': (
                    ('see_private_solution', 'See hidden solutions'),
                    ('edit_problem_editorial', 'Edit problem editorial content'),
                ),
                'verbose_name': 'solution',
                'verbose_name_plural': 'solutions',
            },
        ),
        migrations.RunPython(add_editorial_edit_perm_to_uni_mentor, noop_reverse),
    ]
