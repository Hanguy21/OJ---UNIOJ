import os
import time

from django.core.management import CommandError, call_command
from django.db.models import Q

from judge.management.commands.import_polygon_bulk import Command as PolygonBulkCommand
from judge.management.commands.import_polygon import Command as ImportPolygonCommand, PolygonClient
from judge.models import Problem


class Command(PolygonBulkCommand):
    help = (
        "Like import_polygon_bulk, but only runs import_polygon for problems not already on the "
        "site. Treats a Polygon row as already imported if any Problem matches: legacy numeric "
        "code (= Polygon id), full disambiguated code from import_polygon, the pre-suffix base "
        "slug, suffix _<polygonId>, or the same display name (shortName/name) as Problem.name."
    )

    @staticmethod
    def _already_on_site(item, problem_id):
        """Match how import_polygon resolves code; also catch base-only codes and name matches."""
        pid_str = str(int(problem_id))
        if Problem.objects.filter(Q(code=pid_str) | Q(code__endswith='_' + pid_str)).exists():
            return True
        label = (item.get("shortName") or item.get("name") or "").strip()
        raw_name = (item.get("name") or "").strip()
        raw_short = (item.get("shortName") or "").strip()
        seen = set()
        labels = []
        for cand in (label, raw_name, raw_short):
            c = (cand or "").strip()
            if c and c not in seen:
                seen.add(c)
                labels.append(c)
        codes = set()
        for c in labels:
            codes.add(ImportPolygonCommand.polygon_label_to_problem_code(c, problem_id))
            b = ImportPolygonCommand.polygon_label_to_base_code(c)
            if b:
                codes.add(b)
        if codes and Problem.objects.filter(code__in=list(codes)).exists():
            return True
        for c in labels:
            if Problem.objects.filter(name__iexact=c).exists():
                return True
        return False

    def handle(self, *args, **options):
        api_key = (options.get("api_key") or "").strip() or os.environ.get("POLYGON_API_KEY", "").strip()
        api_secret = (options.get("api_secret") or "").strip() or os.environ.get("POLYGON_API_SECRET", "").strip()
        base_url = (
            (options.get("api_base_url") or "").strip()
            or os.environ.get("POLYGON_API_BASE_URL", "").strip()
            or "https://polygon.codeforces.com/api/"
        )

        if not api_key or not api_secret:
            raise CommandError(
                "POLYGON_API_KEY/POLYGON_API_SECRET are missing. "
                "Run: eval \"$(./scripts/export_polygon_api <api_key> <api_secret>)\""
            )

        client = PolygonClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
        problems = self._fetch_problem_list(
            client,
            include_deleted=options.get("include_deleted", False),
            polygon_owners=options.get("polygon_owners") or [],
            all_key_visible=bool(options.get("polygon_all_key_visible")),
        )
        if not problems:
            self.stdout.write("No problems found from Polygon.")
            return

        missing = []
        skipped = []
        for item in problems:
            problem_id = item["id"]
            if self._already_on_site(item, problem_id):
                skipped.append(item)
            else:
                missing.append(item)

        self.stdout.write(f"Polygon problems matched filters: {len(problems)}")
        self.stdout.write(
            "Already on site (legacy id, import code, base slug, or Problem.name match): %d"
            % len(skipped)
        )
        self.stdout.write(f"Not on site yet (candidates to import): {len(missing)}")
        if not missing:
            self.stdout.write("Nothing new to import.")
            return

        table_lines = self._format_table(missing)
        for line in table_lines:
            self.stdout.write(line)

        total = len(missing)
        self.stdout.write(f"Total new problems to import: {total}.")
        if not options.get("yes"):
            confirm = input(f"Import these {total} new problems? Type YES to continue: ")
            if confirm.strip().upper() != "YES":
                self.stdout.write("Cancelled.")
                return

        success = 0
        failures = []
        for index, item in enumerate(missing, start=1):
            problem_id = item["id"]
            self.stdout.write(f"[{index}/{total}] Importing Polygon problem {problem_id} …")
            try:
                call_command(
                    "import_polygon",
                    str(problem_id),
                    api_key=api_key,
                    api_secret=api_secret,
                    api_base_url=base_url,
                    timeout=options.get("timeout"),
                    poll_interval=options.get("poll_interval"),
                    verify_build=options.get("verify_build"),
                    authors=options.get("authors"),
                    curators=options.get("curators"),
                    non_interactive=True,
                )
                success += 1
            except Exception as exc:
                failures.append(
                    {
                        "problem_id": problem_id,
                        "polygon_name": str(item.get("name", "")),
                        "polygon_owner": str(item.get("owner", "")),
                        "error_log": self._format_import_error(exc),
                    }
                )
                self.stderr.write(f"Failed {problem_id}: {exc}")
            finally:
                delay = max(0, int(options.get("delay") or 0))
                if delay:
                    time.sleep(delay)

        self.stdout.write(f"Imported new: {success}/{total} problems.")
        if failures:
            self.stderr.write("Failures:")
            for row in failures:
                log = row.get("error_log") or ""
                head = log.splitlines()[0] if log else ""
                self.stderr.write(f"- {row['problem_id']}: {head}")
            self._write_import_errors_csv(options.get("errors_csv", ""), failures)
            if not (options.get("errors_csv") or "").strip():
                self.stdout.write("Tip: use --errors-csv /path/to/file.csv to save full error logs next time.")
        elif options.get("errors_csv", "").strip():
            self.stdout.write("(No failures; errors CSV not written.)")
