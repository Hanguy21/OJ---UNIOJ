"""
Suggest Problem.group (Category) from problem code, name, and source using heuristics.

Output CSV for human review; does not modify the database.
Rules reference ProblemGroup.name slugs created by import_problem_groups_csv; unknown slugs are skipped.
"""

import csv
import re
import unicodedata

from django.core.management.base import BaseCommand

from judge.models import Problem, ProblemGroup


def _fold_ascii(s):
    if not s:
        return ''
    nfkd = unicodedata.normalize('NFKD', str(s))
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _haystack(problem):
    parts = [problem.code or '', problem.name or '', problem.source or '']
    raw = ' '.join(parts)
    # Fold accents + keep raw lowercase so regex can hit both
    return _fold_ascii(raw) + '\n' + raw.lower()


# (score, regex_pattern, problem_group_slug, human_readable_rule_id)
# Higher score wins. On tie, earlier rule in this list wins (enumerated at runtime).
RULES = [
    # Educational rounds must beat generic codeforces.com
    (96, r'educational\s+(codeforces|round)|\bedu\b.*\b(round|division)\b.*cf|^[^\n]{0,40}\bedu\d+\b', 'educational_codeforc', 'educational_cf'),
    (94, r'codeforces\.com|\bcodeforces\b|(^|[^a-z0-9])cf\d{3,}\b|\bcf\s*(div|round|global)', 'codeforces', 'codeforces'),
    (90, r'\batcoder\.jp\b|\batcoder\b|\babc\d{3}\b|\barc\d{3}\b|\bagc\d{3}\b', 'atcoder', 'atcoder'),
    (90, r'\busaco\b|usaco\.org', 'usaco', 'usaco'),
    (90, r'\bcoci\b|croatian\s+open', 'coci', 'coci'),
    (88, r'\bioi\b|informatics\s+olympiad(?!\s+asia)', 'international_olympi', 'ioi'),
    (88, r'\bapio\b|asia[\s-]?pacific\s+informatics', 'asia_pacific_informa', 'apio'),
    (86, r'\bicpc\b|collegiate\s+programming|world\s+finals?\s+icpc', 'icpc', 'icpc'),
    (84, r'\bvnoi\s*cup\b', 'vnoi_cup', 'vnoi_cup'),
    (82, r'\bvnoi\b|vietnam\s+olympiad\s+informatics', 'hsg_quoc_gia', 'vnoi_national'),
    (82, r'\bhsg\s*(qg|quoc\s+gia|national)|hoc\s+sinh\s+gioi\s+quoc\s+gia', 'hsg_quoc_gia', 'hsg_quoc_gia'),
    (78, r'\bhsg\b.*(tinh|tp\.|thanh\s+pho|city|provincial)|hoc\s+sinh\s+gioi\s+tinh', 'hsg_tinhthanh_pho', 'hsg_province'),
    (79, r'\bhsg\b.*(thcs|thpt)|\bhsg\b.*\d{4}\s*-\s*\d{4}', 'hsg_tinhthanh_pho', 'hsg_school_year'),
    (77, r'\bhsg\b', 'hsg_tinhthanh_pho', 'hsg_generic'),
    (80, r'\bhanoi\b|\bhnoi\b|ha\s+noi\s+olympic', 'hnoi', 'hnoi'),
    (80, r'\bduyen\s+hai|dhb\b|bac\s+bo\b.*chuyen', 'duyen_hai_bac_bo', 'duyen_hai_bac_bo'),
    (80, r'hung\s+vuong|trai\s+he\s+hung', 'trai_he_hung_vuong', 'hung_vuong'),
    (80, r'olympic\s*30\s*/\s*4|\b304\b.*olympic|30\s*/\s*4', 'olympic_304', 'olympic_304'),
    (80, r'\bkhtn\b|chuyen\s+khoa\s+hoc\s+tu\s+nhiên|chuyen\s+khtn', 'olympic_chuyen_khtn', 'khtn'),
    (78, r'tin\s+hoc\s+tre|thi\s+tre\b', 'tin_hoc_tre', 'tin_hoc_tre'),
    (82, r'\btst\b|team\s+selection\s+test', 'team_selection_test', 'tst_official'),
    (76, r'tst\s+(training|luyen|practice)|doi\s+tuyen.*tap\s+luyen', 'tst_training', 'tst_training'),
    (82, r'\blop\s*10\b.*chuyen|tuyen\s+sinh\s+10|vao\s+10\b.*chuyen', 'tuyen_sinh_lop_10_ch', 'grade10_specialized'),
    (80, r'\bolympic\s+sinh\s+vien\b|\bolp\b.*tin|\boisp\b', 'olympic_sinh_vien', 'uni_olympiad'),
    (82, r'\bbedao\b', 'bedao_contest', 'bedao'),
    (78, r'\bfree\s+contest\b', 'free_contest', 'free_contest'),
    (82, r'\bgspvh\b|phan\s+vu\s+hao', 'gspvh', 'gspvh'),
    (82, r'\bviettel\b.*(programming|lap\s+trinh)|vpc\b', 'viettel_programming_', 'viettel'),
    (80, r'\bdytechlab\b', 'dytechlab', 'dytechlab'),
    (76, r'\bmock\b|\bthi\s+thu\b|\bmocktest\b', 'thi_thu_mock_test', 'mock'),
    (74, r'\bchuyen\s+de\b|\btopic\b|\bluyen\s+tap\b.*(dp|graph|geom)|\bpractice\b.*topic', 'luyen_tap_chuyen_e', 'topic_practice'),
]


def _fallback_uncategorized(groups_by_name):
    for key in ('uncategorized', 'Uncategorized'):
        if key in groups_by_name:
            return groups_by_name[key]
    for g in groups_by_name.values():
        fn = _fold_ascii(g.full_name or '')
        if 'chua phan loai' in fn or 'uncategor' in fn:
            return g
    return None


class Command(BaseCommand):
    help = (
        'Suggest Problem.group from code, name, and source (heuristic). '
        'Writes CSV for review; does not update problems.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            '-o',
            default='',
            help='Write CSV to this path (default: stdout)',
        )
        parser.add_argument(
            '--only-unmatched',
            action='store_true',
            help='Only rows where no rule matched (or score below --min-score)',
        )
        parser.add_argument(
            '--min-score',
            type=int,
            default=0,
            help='Ignore suggestions below this score (default: 0)',
        )
        parser.add_argument(
            '--codes',
            nargs='*',
            default=[],
            help='Optional problem codes to analyze (default: all problems)',
        )

    def handle(self, *args, **options):
        groups = {g.name: g for g in ProblemGroup.objects.all()}
        active = []
        missing_slugs = set()
        for i, (score, pattern, slug, rid) in enumerate(RULES):
            g = groups.get(slug)
            if not g:
                missing_slugs.add(slug)
                continue
            active.append((i, score, re.compile(pattern, re.I), g, rid))
        if missing_slugs:
            self.stdout.write(
                self.style.WARNING(
                    'Skipping %d rule(s): ProblemGroup slug not in DB: %s'
                    % (len(missing_slugs), ', '.join(sorted(missing_slugs)))
                )
            )

        qs = Problem.objects.all().select_related('group').only(
            'id', 'code', 'name', 'source', 'group_id'
        )
        if options['codes']:
            qs = qs.filter(code__in=options['codes'])

        rows = []
        for p in qs.order_by('code'):
            hay = _haystack(p)
            matches = []
            for rule_index, score, cre, group, rid in active:
                if score < options['min_score']:
                    continue
                if cre.search(hay):
                    matches.append((score, -rule_index, group, rid))

            current = p.group
            if matches:
                score, _neg_i, suggested, rid = max(matches, key=lambda t: (t[0], t[1]))
                status = 'keep' if suggested.id == current.id else 'change'
            else:
                unc = _fallback_uncategorized(groups)
                suggested = unc
                score = 0
                rid = 'fallback_uncategorized' if unc else 'no_match'
                status = (
                    'unmatched'
                    if not unc
                    else ('keep' if current.id == unc.id else 'fallback')
                )

            if options['only_unmatched'] and matches:
                continue

            rows.append({
                'problem_code': p.code,
                'problem_name': p.name,
                'source': p.source or '',
                'current_group_slug': current.name,
                'current_group_label': current.full_name,
                'suggested_group_slug': suggested.name if suggested else '',
                'suggested_group_label': suggested.full_name if suggested else '',
                'score': str(score),
                'rule_id': rid,
                'status': status,
            })

        fieldnames = [
            'problem_code',
            'problem_name',
            'source',
            'current_group_slug',
            'current_group_label',
            'suggested_group_slug',
            'suggested_group_label',
            'score',
            'rule_id',
            'status',
        ]
        out_path = options['output']
        if out_path:
            with open(out_path, 'w', encoding='utf-8-sig', newline='') as fout:
                w = csv.DictWriter(fout, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            self.stdout.write(self.style.SUCCESS('Wrote %d rows to %s' % (len(rows), out_path)))
        else:
            w = csv.DictWriter(self.stdout, fieldnames=fieldnames)
            w.writeheader()
            for row in rows:
                w.writerow(row)

        # Short summary for operators
        n_change = sum(1 for r in rows if r['status'] == 'change')
        n_keep = sum(1 for r in rows if r['status'] == 'keep')
        n_un = sum(1 for r in rows if r['status'] in ('unmatched', 'fallback'))
        self.stdout.write(
            'Summary: total=%d, suggest_change=%d, keep=%d, unmatched_or_fallback=%d'
            % (len(rows), n_change, n_keep, n_un)
        )
