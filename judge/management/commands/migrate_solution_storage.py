from django.core.management.base import BaseCommand

from judge.models import Solution


class Command(BaseCommand):
    help = 'Move solution source content from DB to problem data files.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be migrated without writing files.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        moved = 0
        skipped = 0

        for solution in Solution.objects.select_related('problem').all():
            content = (solution.get_content_text() or '').strip()
            if not content:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] would migrate problem={solution.problem.code}")
                moved += 1
                continue

            solution.save_content_text(content)
            moved += 1

        self.stdout.write(self.style.SUCCESS(
            f"Completed. migrated={moved}, skipped_empty={skipped}, dry_run={dry_run}"
        ))
