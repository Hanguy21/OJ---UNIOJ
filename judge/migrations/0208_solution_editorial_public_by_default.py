from django.db import migrations, models


def set_existing_solution_public(apps, schema_editor):
    Solution = apps.get_model('judge', 'Solution')
    Solution.objects.filter(is_public=False).update(is_public=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0207_assign_editorial_permission_unimentor'),
    ]

    operations = [
        migrations.AlterField(
            model_name='solution',
            name='is_public',
            field=models.BooleanField(default=True, verbose_name='public visibility'),
        ),
        migrations.RunPython(set_existing_solution_public, noop_reverse),
    ]
