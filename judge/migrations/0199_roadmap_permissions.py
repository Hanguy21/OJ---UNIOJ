from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0198_set_admin_as_unicorns_org_admin'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='roadmaplevel',
            options={
                'ordering': ('order', 'level'),
                'permissions': (
                    ('view_roadmap', 'View roadmap'),
                    ('roadmap_level_edit_mode', 'Use roadmap level edit mode'),
                ),
                'verbose_name': 'roadmap level',
                'verbose_name_plural': 'roadmap levels',
            },
        ),
    ]
