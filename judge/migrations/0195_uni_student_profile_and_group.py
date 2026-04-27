from django.db import migrations, models


def create_uni_student_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='uni-student')


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0194_org_admin_auth_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='uni_student_profile_completed',
            field=models.BooleanField(
                default=True,
                help_text='When false, users in the uni-student group are prompted to finish their profile after login.',
                verbose_name='Unicorns student profile completed',
            ),
        ),
        migrations.RunPython(create_uni_student_group, noop_reverse),
    ]
