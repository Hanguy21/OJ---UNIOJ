# Ensures auth.Group used by organization creation exists (GROUP_PERMISSION_FOR_ORG_ADMIN).

from django.db import migrations


def create_org_admin_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Org Admin')


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0193_roadmap_levels'),
    ]

    operations = [
        migrations.RunPython(create_org_admin_group, noop_reverse),
    ]
