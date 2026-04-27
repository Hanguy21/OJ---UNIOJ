from django.db import migrations


def add_quick_submit_perm_to_uni_mentor(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    mentor_group, _ = Group.objects.get_or_create(name='uni-mentor')
    problem_content_type = ContentType.objects.filter(app_label='judge', model='problem').first()
    if problem_content_type is None:
        return

    quick_submit_perm = Permission.objects.filter(
        content_type=problem_content_type,
        codename='quick_submit_problem',
    ).first()
    if quick_submit_perm is None:
        return

    mentor_group.permissions.add(quick_submit_perm)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0200_unicorns_default_group_permissions'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='problem',
            options={
                'permissions': (
                    ('see_private_problem', 'See hidden problems'),
                    ('edit_own_problem', 'Edit own problems'),
                    ('create_organization_problem', 'Create organization problem'),
                    ('edit_all_problem', 'Edit all problems'),
                    ('edit_public_problem', 'Edit all public problems'),
                    ('suggest_new_problem', 'Suggest new problem'),
                    ('quick_submit_problem', 'Quick submit problem'),
                    ('problem_full_markup', 'Edit problems with full markup'),
                    ('clone_problem', 'Clone problem'),
                    ('upload_file_statement', 'Upload file-type statement'),
                    ('change_public_visibility', 'Change is_public field'),
                    ('change_manually_managed', 'Change is_manually_managed field'),
                    ('see_organization_problem', 'See organization-private problems'),
                ),
                'verbose_name': 'problem',
                'verbose_name_plural': 'problems',
            },
        ),
        migrations.RunPython(add_quick_submit_perm_to_uni_mentor, noop_reverse),
    ]
