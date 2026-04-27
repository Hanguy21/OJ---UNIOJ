from django.db import migrations


def create_unicorns_defaults(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Organization = apps.get_model('judge', 'Organization')

    Group.objects.get_or_create(name='uni-mentor')

    Organization.objects.get_or_create(
        slug='unicorns-edu',
        defaults={
            'name': 'Unicorns Edu',
            'short_name': 'UNICORNS',
            'about': 'Default organization for Unicorns Edu students and mentors.',
            'is_open': False,
            'is_unlisted': True,
        },
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0195_uni_student_profile_and_group'),
    ]

    operations = [
        migrations.RunPython(create_unicorns_defaults, noop_reverse),
    ]
