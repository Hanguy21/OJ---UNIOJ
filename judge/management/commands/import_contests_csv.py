import csv
import html
import os
import unicodedata
from collections import defaultdict
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from judge.models import Contest, ContestProblem, Organization, Problem, Profile


def write_error_report(error_rows, report_path):
    if not error_rows:
        return None

    headers = [
        'level',
        'error_type',
        'contest_id',
        'contest_name',
        'contest_key',
        'csv_line',
        'problem_name',
        'message',
    ]

    root, ext = os.path.splitext(report_path)
    ext = ext.lower()
    if ext != '.xlsx':
        report_path = root + '.xlsx'

    try:
        from openpyxl import Workbook
    except ImportError:
        csv_path = root + '.csv'
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as fout:
            writer = csv.DictWriter(fout, fieldnames=headers)
            writer.writeheader()
            writer.writerows(error_rows)
        return csv_path, 'csv'

    wb = Workbook()
    ws = wb.active
    ws.title = 'import_errors'
    ws.append(headers)
    for row in error_rows:
        ws.append([row.get(h, '') for h in headers])
    wb.save(report_path)
    return report_path, 'xlsx'


def normalize_cell(value):
    if value is None:
        return ''
    return unicodedata.normalize('NFC', html.unescape(str(value))).strip()


def normalize_spaces(s):
    return ' '.join(s.split())


def slugify_contest_key(contest_name, contest_id):
    """Build ^[a-z0-9_]+$ key within 32 chars; suffix contest_id avoids name collisions."""
    cid = ''.join(c for c in str(contest_id) if c.isalnum())
    suffix = ('_' + cid) if cid else ''
    contest_name = normalize_cell(contest_name)
    if not contest_name:
        return ('c' + cid)[:32] if cid else 'contest_import'
    nfkd = unicodedata.normalize('NFKD', contest_name)
    base = ''.join(ch for ch in nfkd if not unicodedata.combining(ch))
    key = ''.join(ch.lower() if ch.isalnum() else '_' for ch in base)
    while '__' in key:
        key = key.replace('__', '_')
    key = key.strip('_')
    if not key:
        key = 'c'
    room = max(1, 32 - len(suffix))
    key = (key[:room].rstrip('_') or 'c') + suffix
    return key[:32]


def apply_organization_key_prefix(key, org_slug):
    prefix = ''.join(x for x in org_slug.lower() if x.isalpha()) + '_'
    if key.startswith(prefix):
        return key[:32]
    combined = (prefix + key)[:32]
    if len(combined) < len(prefix):
        raise CommandError(
            'Contest key would exceed 32 characters after organization prefix %r; '
            'use a shorter backup contest name or import without --organization-slug.'
            % prefix,
        )
    return combined


def find_problem_by_display_name(raw_name, match_mode):
    name = normalize_spaces(normalize_cell(raw_name))
    if not name:
        return None, 'empty problem_name'

    qs = Problem.objects.all()
    p = qs.filter(name=name).first()
    if p:
        return p, None
    p = qs.filter(name__iexact=name).first()
    if p:
        return p, None

    if match_mode == 'strict':
        return None, 'no exact/iexact match for Problem.name'

    candidates = list(qs.filter(name__icontains=name))
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        codes = ', '.join(sorted(c.code for c in candidates[:8]))
        more = '…' if len(candidates) > 8 else ''
        return None, 'ambiguous icontains match (%d problems: %s%s)' % (len(candidates), codes, more)

    return None, 'no matching Problem.name'


class Command(BaseCommand):
    help = (
        'Import contests from a backup CSV (columns: contest_id, contest_name, problem_index, problem_name). '
        'Problems are resolved against Problem.name using problem_name.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            help='Path to CSV inside the site container (e.g. /oj_data/contest.csv)',
        )
        parser.add_argument(
            '--author',
            required=True,
            help='Username of the contest author (must exist)',
        )
        parser.add_argument(
            '--encoding',
            default='utf-8-sig',
            help='CSV file encoding (default: utf-8-sig)',
        )
        parser.add_argument(
            '--start',
            help='Contest start time (ISO-like), default: now',
        )
        parser.add_argument(
            '--end',
            help='Contest end time (ISO-like), default: --start + duration',
        )
        parser.add_argument(
            '--duration-days',
            type=float,
            default=14.0,
            help='If --end omitted, length after start in days (default: 14)',
        )
        parser.add_argument(
            '--points',
            type=int,
            default=100,
            help='Points per contest problem (default: 100)',
        )
        parser.add_argument(
            '--visible',
            action='store_true',
            help='Set contest is_visible=True',
        )
        parser.add_argument(
            '--match-mode',
            choices=('strict', 'relaxed'),
            default='strict',
            help='Problem matching: strict = exact/iexact on Problem.name; relaxed = also unique icontains',
        )
        parser.add_argument(
            '--organization-slug',
            default='',
            help='If set, contest key gets org prefix (same rule as organization contest form) '
                 'and organization is attached with is_organization_private=True',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse CSV and resolve problems but do not write contests',
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip contests whose key already exists',
        )
        parser.add_argument(
            '--replace-problems',
            action='store_true',
            help='If contest key exists, delete its ContestProblem rows and re-import problem list',
        )
        parser.add_argument(
            '--error-report',
            default='',
            help='Path to Excel error report (.xlsx). '
                 'Default: ./contest_import_errors_<timestamp>.xlsx',
        )

    def handle(self, *args, **options):
        if options['skip_existing'] and options['replace_problems']:
            raise CommandError('Use only one of --skip-existing or --replace-problems')

        csv_path = options['csv_file']
        try:
            fin = open(csv_path, 'r', encoding=options['encoding'], newline='')
        except OSError as ex:
            raise CommandError('Cannot open CSV: %s' % ex)

        author_user = User.objects.filter(username=options['author']).first()
        if not author_user:
            raise CommandError('No user with username %r' % options['author'])
        author_profile = Profile.objects.filter(user=author_user).first()
        if not author_profile:
            raise CommandError('User %r has no profile' % options['author'])

        org = None
        org_slug = (options['organization_slug'] or '').strip()
        if org_slug:
            org = Organization.objects.filter(slug=org_slug).first()
            if not org:
                raise CommandError('No organization with slug %r' % org_slug)

        start = options['start']
        end = options['end']
        if start:
            start_time = parse_datetime(start.strip())
            if not start_time:
                raise CommandError('Could not parse --start %r' % start)
            if timezone.is_naive(start_time):
                start_time = timezone.make_aware(start_time, timezone.get_current_timezone())
        else:
            start_time = timezone.now()

        if end:
            end_time = parse_datetime(end.strip())
            if not end_time:
                raise CommandError('Could not parse --end %r' % end)
            if timezone.is_naive(end_time):
                end_time = timezone.make_aware(end_time, timezone.get_current_timezone())
        else:
            end_time = start_time + timedelta(days=options['duration_days'])

        if end_time <= start_time:
            raise CommandError('end time must be after start time')

        with fin:
            reader = csv.DictReader(fin)
            required = {'contest_id', 'contest_name', 'problem_name'}
            fields = set(reader.fieldnames or [])
            missing = required - fields
            if missing:
                raise CommandError('Missing CSV columns: %s' % ', '.join(sorted(missing)))

            rows_by_contest = defaultdict(list)
            order_seen = defaultdict(int)
            parse_errors = []

            for idx, row in enumerate(reader, start=2):
                cid = normalize_cell(row.get('contest_id'))
                cname = normalize_cell(row.get('contest_name'))
                pname = normalize_cell(row.get('problem_name'))
                if not cid or not cname:
                    self.stdout.write(self.style.WARNING('[line %d] skip: missing contest_id/contest_name' % idx))
                    parse_errors.append(
                        {
                            'level': 'ERROR',
                            'error_type': 'missing_contest_fields',
                            'contest_id': cid,
                            'contest_name': cname,
                            'contest_key': '',
                            'csv_line': idx,
                            'problem_name': pname,
                            'message': 'missing contest_id/contest_name',
                        },
                    )
                    continue
                if not pname:
                    continue
                key_tuple = (cid, cname)
                order_seen[key_tuple] += 1
                rows_by_contest[key_tuple].append(
                    {
                        'line': idx,
                        'problem_name': pname,
                        'order': order_seen[key_tuple],
                    },
                )

        if not rows_by_contest:
            raise CommandError('No contest rows with problems found in CSV')

        stats = {'contests': 0, 'skipped': 0, 'problems_added': 0, 'errors': len(parse_errors)}
        error_rows = list(parse_errors)

        def log_error(error_type, message, contest_id='', contest_name='', contest_key='', csv_line='', problem_name=''):
            error_rows.append(
                {
                    'level': 'ERROR',
                    'error_type': error_type,
                    'contest_id': contest_id,
                    'contest_name': normalize_cell(contest_name),
                    'contest_key': contest_key,
                    'csv_line': csv_line,
                    'problem_name': normalize_cell(problem_name),
                    'message': message,
                },
            )

        for (cid, cname), problems in rows_by_contest.items():
            key = slugify_contest_key(cname, cid)
            if org_slug:
                key = apply_organization_key_prefix(key, org_slug)

            existing = Contest.objects.filter(key=key).first()
            if existing:
                if options['skip_existing']:
                    self.stdout.write(self.style.WARNING('Skip existing contest key=%s' % key))
                    stats['skipped'] += 1
                    continue
                if not options['replace_problems']:
                    stats['errors'] += 1
                    msg = 'Contest key %r already exists (use --skip-existing or --replace-problems)' % key
                    self.stdout.write(
                        self.style.ERROR(
                            msg,
                        ),
                    )
                    log_error(
                        error_type='contest_exists',
                        message=msg,
                        contest_id=cid,
                        contest_name=cname,
                        contest_key=key,
                    )
                    continue

            resolutions = []
            resolve_failed = False
            for p in problems:
                prob, err = find_problem_by_display_name(p['problem_name'], options['match_mode'])
                if err:
                    resolve_failed = True
                    stats['errors'] += 1
                    msg = '%s — problem_name=%r' % (err, p['problem_name'][:120])
                    self.stdout.write(
                        self.style.ERROR(
                            '[contest %s / %s line %s] %s — problem_name=%r'
                            % (cid, key, p['line'], err, p['problem_name'][:120]),
                        ),
                    )
                    log_error(
                        error_type='problem_not_resolved',
                        message=msg,
                        contest_id=cid,
                        contest_name=cname,
                        contest_key=key,
                        csv_line=p['line'],
                        problem_name=p['problem_name'],
                    )
                    continue
                resolutions.append((p['order'], prob))

            if resolve_failed:
                self.stdout.write(
                    self.style.ERROR('Contest %s (%s): skipped (unresolved problems)' % (key, cname)),
                )
                continue

            seen_problem_ids = set()
            deduped = []
            for order, prob in resolutions:
                if prob.id in seen_problem_ids:
                    self.stdout.write(
                        self.style.WARNING(
                            'Contest %s: skip duplicate problem %s (%s)' % (key, prob.code, prob.name),
                        ),
                    )
                    continue
                seen_problem_ids.add(prob.id)
                deduped.append((order, prob))

            resolutions = deduped

            if not resolutions:
                self.stdout.write(self.style.WARNING('Contest %s: no problems left after dedupe, skipped' % key))
                continue

            display_name = normalize_cell(cname)[:100]
            if len(normalize_cell(cname)) > 100:
                self.stdout.write(
                    self.style.WARNING(
                        'Contest name truncated to 100 chars for key=%s' % key,
                    ),
                )

            if options['dry_run']:
                self.stdout.write(
                    '[dry-run] %s (%s) → %d problems: %s'
                    % (
                        key,
                        display_name,
                        len(resolutions),
                        ', '.join(pr.code for _, pr in resolutions[:12]),
                    )
                    + (' …' if len(resolutions) > 12 else ''),
                )
                stats['contests'] += 1
                stats['problems_added'] += len(resolutions)
                continue

            try:
                with transaction.atomic():
                    if existing and options['replace_problems']:
                        contest = existing
                        contest.contest_problems.all().delete()
                    else:
                        contest = Contest.objects.create(
                            key=key,
                            name=display_name,
                            start_time=start_time,
                            end_time=end_time,
                            is_visible=options['visible'],
                            is_organization_private=bool(org),
                        )
                        contest.authors.add(author_profile)
                        if org:
                            contest.organizations.add(org)

                    for order, prob in resolutions:
                        ContestProblem.objects.create(
                            contest=contest,
                            problem=prob,
                            points=options['points'],
                            partial=True,
                            order=order,
                        )
            except Exception as ex:
                stats['errors'] += 1
                msg = 'Contest %s: DB error %s' % (key, ex)
                self.stdout.write(self.style.ERROR(msg))
                log_error(
                    error_type='db_error',
                    message=str(ex),
                    contest_id=cid,
                    contest_name=cname,
                    contest_key=key,
                )
                continue

            action = 'updated problems for' if existing and options['replace_problems'] else 'created'
            self.stdout.write(self.style.SUCCESS('%s contest %s (%d problems)' % (action, key, len(resolutions))))
            stats['contests'] += 1
            stats['problems_added'] += len(resolutions)

        self.stdout.write(
            'Done: contests=%d problems=%d skipped=%d errors=%d'
            % (stats['contests'], stats['problems_added'], stats['skipped'], stats['errors']),
        )
        if error_rows:
            report_path = options['error_report']
            if not report_path:
                report_path = 'contest_import_errors_%s.xlsx' % timezone.now().strftime('%Y%m%d_%H%M%S')
            report_result = write_error_report(error_rows, report_path)
            if report_result is None:
                return
            final_path, report_type = report_result
            if report_type == 'xlsx':
                self.stdout.write(self.style.WARNING('Error report written: %s' % final_path))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        'openpyxl not available, wrote CSV error report instead: %s' % final_path,
                    ),
                )
