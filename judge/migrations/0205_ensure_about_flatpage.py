from django.conf import settings
from django.db import migrations


ABOUT_CONTENT = """
## Tiếng Việt

Đây là trang giới thiệu UNIOJ.

## English

This is the UNIOJ about page.
""".strip()


def ensure_about_flatpage(apps, schema_editor):
    FlatPage = apps.get_model('flatpages', 'FlatPage')
    Site = apps.get_model('sites', 'Site')

    # Prefer configured SITE_ID, then fallback to all existing sites.
    target_sites = list(Site.objects.filter(id=getattr(settings, 'SITE_ID', None)))
    if not target_sites:
        target_sites = list(Site.objects.all())

    about_page, _created = FlatPage.objects.get_or_create(
        url='/about/',
        defaults={
            'title': 'About',
            'content': ABOUT_CONTENT,
            'template_name': '',
            'registration_required': False,
        },
    )

    # Keep content/title non-empty even if an old broken row exists.
    updated_fields = []
    if not about_page.title:
        about_page.title = 'About'
        updated_fields.append('title')
    if not about_page.content:
        about_page.content = ABOUT_CONTENT
        updated_fields.append('content')
    if updated_fields:
        about_page.save(update_fields=updated_fields)

    if target_sites:
        about_page.sites.add(*target_sites)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0204_hide_aggregate_level_contests'),
        ('flatpages', '0001_initial'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(ensure_about_flatpage, noop_reverse),
    ]
