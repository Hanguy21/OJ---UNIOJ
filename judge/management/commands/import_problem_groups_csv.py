import csv
import os
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from judge.models import ProblemGroup


def _resolve_csv_column(fieldnames, wanted):
    wanted_key = _normalize_header(wanted).lower()
    for h in fieldnames:
        if _normalize_header(h).lower() == wanted_key:
            return h
    return None


def _normalize_header(s):
    if s is None:
        return ''
    return unicodedata.normalize('NFC', str(s)).strip()


def _slug_base_from_full_name(full_name):
    base = slugify(full_name).replace('-', '_')
    if not base:
        base = 'group'
    return base[:20]


def _allocate_group_name(base):
    candidate = base
    counter = 2
    while ProblemGroup.objects.filter(name=candidate).exists():
        suffix = f'_{counter}'
        candidate = f'{base[: max(1, 20 - len(suffix))]}{suffix}'
        counter += 1
    return candidate


class Command(BaseCommand):
    help = (
        'Create ProblemGroup rows from a CSV column (default: Ten_Danh_Muc). '
        'These appear under Category on the problem list (Problem.group). '
        'Slug rules: name ≤20 chars, unique, same pattern as Polygon problem-type creation.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            help='Path inside the site container (e.g. /oj_data/danh_muc_ky_thi_lap_trinh.csv)',
        )
        parser.add_argument(
            '--column',
            default='Ten_Danh_Muc',
            help='Header for display name / full_name (default: Ten_Danh_Muc)',
        )
        parser.add_argument(
            '--encoding',
            default='utf-8-sig',
            help='CSV encoding (default: utf-8-sig)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List rows that would be created; do not write to the database',
        )

    def handle(self, *args, **options):
        csv_path = options['csv_file']
        column = options['column']
        if not os.path.isfile(csv_path):
            raise CommandError('File not found: %s' % csv_path)

        with open(csv_path, 'r', encoding=options['encoding'], newline='') as fin:
            reader = csv.DictReader(fin)
            if not reader.fieldnames:
                raise CommandError('CSV has no header row')
            col_key = _resolve_csv_column(reader.fieldnames, column)
            if not col_key:
                raise CommandError(
                    'Column %r not in CSV. Found: %s' % (column, ', '.join(reader.fieldnames))
                )

        created = 0
        skipped = 0
        with open(csv_path, 'r', encoding=options['encoding'], newline='') as fin:
            reader = csv.DictReader(fin)
            for row in reader:
                raw = row.get(col_key)
                full_name = _normalize_header(raw)[:100]
                if not full_name:
                    continue
                existing = ProblemGroup.objects.filter(full_name__iexact=full_name).first()
                if existing:
                    skipped += 1
                    if options['dry_run']:
                        self.stdout.write(f'[skip exists] {full_name!r} -> {existing.name}')
                    continue

                base = _slug_base_from_full_name(full_name)
                name = _allocate_group_name(base)
                if options['dry_run']:
                    self.stdout.write(f'[would create] {name!r} / {full_name!r}')
                    created += 1
                    continue

                ProblemGroup.objects.create(name=name, full_name=full_name)
                created += 1
                self.stdout.write(self.style.SUCCESS(f'Created group {name!r} — {full_name}'))

        if options['dry_run']:
            self.stdout.write(
                f'Dry run: would create {created} group(s), skip {skipped} already present.'
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Done: created {created} group(s), skipped {skipped} already present.')
            )
