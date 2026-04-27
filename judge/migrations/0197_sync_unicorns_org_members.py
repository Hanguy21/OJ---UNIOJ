from django.db import migrations


def sync_unicorns_org_members(apps, schema_editor):
    Organization = apps.get_model('judge', 'Organization')
    Profile = apps.get_model('judge', 'Profile')

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

    role_profiles = Profile.objects.filter(
        user__groups__name__in=('uni-student', 'uni-mentor'),
    ).distinct()
    org.members.add(*role_profiles)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0196_unicorns_edu_org_and_mentor_group'),
    ]

    operations = [
        migrations.RunPython(sync_unicorns_org_members, noop_reverse),
    ]
