from django.db import migrations


def set_unicorns_default_group_permissions(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    judge_content_type_ids = ContentType.objects.filter(app_label='judge').values_list('id', flat=True)

    def permissions_for(codenames):
        return Permission.objects.filter(
            codename__in=codenames,
            content_type_id__in=judge_content_type_ids,
        )

    mentor_group, _ = Group.objects.get_or_create(name='uni-mentor')
    student_group, _ = Group.objects.get_or_create(name='uni-student')

    mentor_perm_codenames = [
        'view_roadmap',
        'view_all_submission',
        'spam_submission',
        'resubmit_other',
        'see_private_solution',
        'see_private_problem',
        'see_private_contest',
    ]
    student_perm_codenames = [
        'view_roadmap',
        'spam_submission',
        'see_private_solution',
        'see_private_problem',
        'see_private_contest',
    ]

    mentor_group.permissions.add(*permissions_for(mentor_perm_codenames))
    student_group.permissions.add(*permissions_for(student_perm_codenames))


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0199_roadmap_permissions'),
    ]

    operations = [
        migrations.RunPython(set_unicorns_default_group_permissions, noop_reverse),
    ]
