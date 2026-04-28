from django.db import migrations


def assign_editorial_permission_to_uni_mentor(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    mentor_group, _ = Group.objects.get_or_create(name='uni-mentor')
    editorial_perm = Permission.objects.filter(
        content_type__app_label='judge',
        codename='edit_problem_editorial',
    ).first()
    if editorial_perm is None:
        return

    mentor_group.permissions.add(editorial_perm)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0206_editorial_edit_permission_for_unimentor'),
    ]

    operations = [
        migrations.RunPython(assign_editorial_permission_to_uni_mentor, noop_reverse),
    ]
