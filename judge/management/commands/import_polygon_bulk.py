import os
import time
from datetime import datetime, timezone as dt_timezone

from django.core.management import BaseCommand, CommandError, call_command

from judge.management.commands.import_polygon import PolygonClient


class Command(BaseCommand):
    help = "List Polygon problems, confirm count, and import them sequentially"

    def add_arguments(self, parser):
        parser.add_argument("--api-key", default="", help="Polygon API key (overrides environment)")
        parser.add_argument("--api-secret", default="", help="Polygon API secret (overrides environment)")
        parser.add_argument("--api-base-url", default="", help="Polygon API base URL (overrides environment)")
        parser.add_argument("--authors", nargs="*", default=[], help="Author usernames for imported problems")
        parser.add_argument("--curators", nargs="*", default=[], help="Curator usernames for imported problems")
        parser.add_argument(
            "--polygon-owner",
            dest="polygon_owners",
            action="append",
            default=[],
            help="Only import Polygon problems whose owner matches this username. Can be repeated.",
        )
        parser.add_argument("--timeout", type=int, default=600, help="Package build timeout in seconds")
        parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval in seconds")
        parser.add_argument("--verify-build", action="store_true", help="Enable Polygon verify when building package")
        parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
        parser.add_argument("--include-deleted", action="store_true", help="Include deleted problems")
        parser.add_argument("--delay", type=int, default=2, help="Seconds to wait between imports")

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
        )
        if not problems:
            self.stdout.write("No problems found.")
            return

        table_lines = self._format_table(problems)
        for line in table_lines:
            self.stdout.write(line)

        total = len(problems)
        self.stdout.write(f"Total: {total} problems.")
        if not options.get("yes"):
            confirm = input(f"Import {total} problems? Type YES to continue: ")
            if confirm.strip().upper() != "YES":
                self.stdout.write("Cancelled.")
                return

        success = 0
        failures = []
        for item in problems:
            problem_id = item["id"]
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
                failures.append((problem_id, str(exc)))
                self.stderr.write(f"Failed {problem_id}: {exc}")
            finally:
                delay = max(0, int(options.get("delay") or 0))
                if delay:
                    time.sleep(delay)

        self.stdout.write(f"Imported: {success}/{total} problems.")
        if failures:
            self.stderr.write("Failures:")
            for problem_id, message in failures:
                self.stderr.write(f"- {problem_id}: {message}")

    def _fetch_problem_list(self, client, include_deleted=False, polygon_owners=None):
        result = client.call_json("problems.list", {"showDeleted": True})
        if isinstance(result, dict):
            for key in ("problems", "items", "data", "result"):
                if isinstance(result.get(key), list):
                    result = result[key]
                    break
        if not isinstance(result, list):
            raise CommandError(f"Unexpected Polygon response shape: {type(result).__name__}")

        owner_filter = {str(owner).strip().lower() for owner in (polygon_owners or []) if str(owner).strip()}
        items = []
        for item in result:
            if not isinstance(item, dict):
                continue
            if not include_deleted and self._is_deleted(item):
                continue
            problem_id = item.get("id", item.get("problemId"))
            name = item.get("name", item.get("title", ""))
            owner = self._extract_owner(item)
            if owner_filter and owner.lower() not in owner_filter:
                continue
            date_value = self._format_date(self._extract_date_value(item))
            if problem_id is None:
                continue
            items.append({"id": problem_id, "name": name, "owner": owner, "date": date_value})

        items.sort(key=lambda entry: self._sort_key(entry.get("id")))
        return items

    @staticmethod
    def _extract_owner(item):
        for key in ("owner", "ownerName", "author", "authorName"):
            value = item.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _is_deleted(item):
        return bool(item.get("deleted") or item.get("isDeleted") or item.get("is_deleted"))

    @staticmethod
    def _extract_date_value(item):
        for key in (
            "creationTimeSeconds",
            "createTimeSeconds",
            "creationTime",
            "createTime",
            "updateTimeSeconds",
            "updateTime",
            "modifiedTimeSeconds",
            "modifiedTime",
        ):
            if key in item:
                return item.get(key)
        return None

    @staticmethod
    def _format_date(raw):
        if raw is None:
            return ""
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
            value = int(raw)
            if value > 10**12:
                value //= 1000
            return datetime.fromtimestamp(value, tz=dt_timezone.utc).date().isoformat()
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed.date().isoformat()
            except ValueError:
                return raw
        return ""

    @staticmethod
    def _sort_key(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 10**18

    def _format_table(self, items):
        header = ["ID", "Name", "Owner", "Date"]
        rows = [
            [
                str(item.get("id", "")),
                str(item.get("name", "")),
                str(item.get("owner", "")),
                str(item.get("date", "")),
            ]
            for item in items
        ]
        widths = [len(value) for value in header]
        for row in rows:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], len(value))

        def pad(value, width):
            return value.ljust(width)

        lines = [
            " | ".join(pad(value, widths[index]) for index, value in enumerate(header)),
            "-+-".join("-" * width for width in widths),
        ]
        for row in rows:
            lines.append(" | ".join(pad(value, widths[index]) for index, value in enumerate(row)))
        return lines
