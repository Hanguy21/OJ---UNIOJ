from django.db import migrations


def set_admin_as_unicorns_org_admin(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Group = apps.get_model('auth', 'Group')
    Organization = apps.get_model('judge', 'Organization')

    admin_user = User.objects.filter(username='admin').first()
    if not admin_user:
        return

    org, _ = Organization.objects.get_or_create(
        slug='unicorns-edu',
        defaults={
            'name': 'Unicorns Edu',
            'short_name': 'UNICORNS',
            'about': 'Default organization for Unicorns Edu students and mentors.',
            'is_open': False,
            'is_unlisted': True,
        },
    )

    try:
        admin_profile = admin_user.profile
    except Exception:
        return

    org.admins.add(admin_profile)
    org.members.add(admin_profile)
    org_admin_group, _ = Group.objects.get_or_create(name='Org Admin')
    admin_user.groups.add(org_admin_group)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0197_sync_unicorns_org_members'),
    ]

    operations = [
        migrations.RunPython(set_admin_as_unicorns_org_admin, noop_reverse),
    ]
