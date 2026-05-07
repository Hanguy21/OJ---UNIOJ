"""
Set Problem.group from a CSV: problem_code + Ten_Danh_Muc (matches ProblemGroup.full_name).
"""

import csv
import html
import os
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from judge.models import Problem, ProblemGroup


def normalize_cell(value):
    if value is None:
        return ''
    return unicodedata.normalize('NFC', html.unescape(str(value))).strip()


def _resolve_csv_column(fieldnames, wanted):
    wanted_key = normalize_cell(wanted).lower()
    for h in fieldnames:
        if normalize_cell(h).lower() == wanted_key:
            return h
    return None


class Command(BaseCommand):
    help = (
        'Update each problem\'s group (Category) from CSV columns problem_code and Ten_Danh_Muc. '
        'Ten_Danh_Muc must match an existing ProblemGroup.full_name (case-insensitive).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            help='Path inside site container (e.g. /oj_data/problem_group_classified.csv)',
        )
        parser.add_argument(
            '--encoding',
            default='utf-8-sig',
            help='CSV encoding (default: utf-8-sig)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show planned updates without saving',
        )
        parser.add_argument(
            '--strict',
            action='store_true',
            help='Abort if any problem code or category label is unknown',
        )

    def handle(self, *args, **options):
        csv_path = options['csv_file']
        if not os.path.isfile(csv_path):
            raise CommandError('File not found: %s' % csv_path)

        with open(csv_path, 'r', encoding=options['encoding'], newline='') as fin:
            reader = csv.DictReader(fin)
            if not reader.fieldnames:
                raise CommandError('CSV has no header row')
            col_code = _resolve_csv_column(reader.fieldnames, 'problem_code')
            col_cat = _resolve_csv_column(reader.fieldnames, 'Ten_Danh_Muc')
            if not col_code:
                raise CommandError('Missing column problem_code. Found: %s' % ', '.join(reader.fieldnames))
            if not col_cat:
                raise CommandError('Missing column Ten_Danh_Muc. Found: %s' % ', '.join(reader.fieldnames))

            rows = list(reader)

        groups_by_lower = {}
        for g in ProblemGroup.objects.all():
            key = normalize_cell(g.full_name).lower()
            if key in groups_by_lower:
                self.stdout.write(
                    self.style.WARNING('Duplicate ProblemGroup.full_name (after normalize): %r' % g.full_name)
                )
            groups_by_lower[key] = g

        missing_problem = []
        unknown_category = []
        empty_category = 0
        unchanged = 0
        to_apply = []

        for row in rows:
            code = normalize_cell(row.get(col_code))
            cat = normalize_cell(row.get(col_cat))[:100]
            if not code:
                continue
            if not cat:
                empty_category += 1
                continue

            group = groups_by_lower.get(cat.lower())
            if group is None:
                group = ProblemGroup.objects.filter(full_name__iexact=cat).first()
            if group is None:
                unknown_category.append((code, cat))
                continue

            try:
                problem = Problem.objects.get(code=code)
            except Problem.DoesNotExist:
                missing_problem.append(code)
                continue

            if problem.group_id == group.id:
                unchanged += 1
                continue
            to_apply.append((problem, group))

        if options['strict'] and (missing_problem or unknown_category):
            msg = []
            if missing_problem:
                msg.append('Missing problems: %d (%s…)' % (len(missing_problem), missing_problem[:5]))
            if unknown_category:
                msg.append('Unknown Ten_Danh_Muc: %d (%s…)' % (len(unknown_category), unknown_category[:3]))
            raise CommandError('; '.join(msg))

        if missing_problem:
            self.stdout.write(
                self.style.WARNING('Problems not in DB (%d): %s' % (len(missing_problem), ', '.join(missing_problem[:20])))
            )
            if len(missing_problem) > 20:
                self.stdout.write(self.style.WARNING('… and %d more' % (len(missing_problem) - 20)))

        if unknown_category:
            for code, cat in unknown_category[:25]:
                self.stdout.write(self.style.WARNING('Unknown category %r for problem %r' % (cat, code)))
            if len(unknown_category) > 25:
                self.stdout.write(self.style.WARNING('… %d more unknown categories' % (len(unknown_category) - 25)))

        if empty_category:
            self.stdout.write(self.style.WARNING('Rows skipped (empty Ten_Danh_Muc): %d' % empty_category))

        self.stdout.write(
            'Will update %d problem(s); unchanged %d; skipped unknown/missing as above.'
            % (len(to_apply), unchanged)
        )

        if options['dry_run']:
            for problem, group in to_apply[:50]:
                self.stdout.write('  %s -> %s (%s)' % (problem.code, group.name, group.full_name))
            if len(to_apply) > 50:
                self.stdout.write('  … %d more' % (len(to_apply) - 50))
            self.stdout.write(self.style.WARNING('Dry run — no changes saved.'))
            return

        problems_out = []
        with transaction.atomic():
            for problem, group in to_apply:
                problem.group = group
                problems_out.append(problem)
            Problem.objects.bulk_update(problems_out, ['group'], batch_size=500)

        self.stdout.write(self.style.SUCCESS('Updated group for %d problem(s).' % len(to_apply)))
