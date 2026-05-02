import csv

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from judge.models import Language, Profile
from judge.views.user import get_unicorns_organization


class Command(BaseCommand):
    help = 'Import Unicorns Edu student accounts from CSV'

    def add_arguments(self, parser):
        parser.add_argument(
            'input',
            help='Path to CSV file (expected columns: Tên đăng nhập, Mật khẩu, Tên)',
        )
        parser.add_argument(
            '--encoding',
            default='utf-8-sig',
            help='CSV encoding (default: utf-8-sig)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and validate rows without writing to database',
        )

    def handle(self, *args, **options):
        input_path = options['input']
        encoding = options['encoding']
        dry_run = options['dry_run']

        try:
            fin = open(input_path, 'r', encoding=encoding, newline='')
        except OSError as ex:
            raise CommandError(f'Cannot open CSV file: {ex}')

        with fin:
            reader = csv.DictReader(fin)
            required_columns = {'Tên đăng nhập', 'Mật khẩu', 'Tên'}
            actual_columns = set(reader.fieldnames or [])
            missing = required_columns - actual_columns
            if missing:
                raise CommandError(
                    'Missing required CSV columns: %s' % ', '.join(sorted(missing)),
                )

            group_name = getattr(settings, 'GROUP_UNI_STUDENT', 'uni-student')
            group, _ = Group.objects.get_or_create(name=group_name)
            org = get_unicorns_organization()
            default_language = Language.get_default_language()

            total = 0
            created = 0
            updated_membership = 0
            skipped = 0
            errors = 0

            for idx, row in enumerate(reader, start=2):
                total += 1
                username = (row.get('Tên đăng nhập') or '').strip()
                password = (row.get('Mật khẩu') or '').strip()
                display_name = (row.get('Tên') or '').strip()

                if not username or not password or not display_name:
                    errors += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'[line {idx}] Missing username/password/display name, skipped.',
                        ),
                    )
                    continue

                user = User.objects.filter(username__iexact=username).first()
                if user:
                    if dry_run:
                        skipped += 1
                        self.stdout.write(f'[line {idx}] DRY-RUN existing username: {user.username}')
                        continue
                    with transaction.atomic():
                        user.groups.add(group)
                        profile = user.profile
                        profile.organizations.add(org)
                    updated_membership += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f'[line {idx}] Existing user "{user.username}", added role/org only.',
                        ),
                    )
                    continue

                if dry_run:
                    created += 1
                    self.stdout.write(f'[line {idx}] DRY-RUN create "{username}"')
                    continue

                with transaction.atomic():
                    new_user = User(
                        username=username,
                        first_name=display_name[:150],
                        is_active=True,
                    )
                    new_user.set_password(password)
                    new_user.save()

                    profile = Profile(
                        user=new_user,
                        language=default_language,
                        username_display_override=display_name[:100],
                        uni_student_profile_completed=False,
                    )
                    profile.save()
                    new_user.groups.add(group)
                    profile.organizations.add(org)

                created += 1

        mode = 'DRY-RUN' if dry_run else 'IMPORT'
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'[{mode}] Completed'))
        self.stdout.write(f'  Total rows: {total}')
        self.stdout.write(f'  Created users: {created}')
        self.stdout.write(f'  Existing users updated (role/org): {updated_membership}')
        self.stdout.write(f'  Existing users skipped: {skipped}')
        self.stdout.write(f'  Rows with errors: {errors}')
