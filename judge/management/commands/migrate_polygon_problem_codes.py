import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from judge.management.commands.import_polygon import Command as ImportPolygonCommand, PolygonClient
from judge.models import Problem


class Command(BaseCommand):
    help = (
        "Rename problems imported with legacy numeric Polygon id as code to DMOJ codes derived from "
        "Polygon shortName/name (same rules as import_polygon)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--api-key", default="", help="Polygon API key")
        parser.add_argument("--api-secret", default="", help="Polygon API secret")
        parser.add_argument(
            "--api-base-url",
            default="",
            help="Polygon API base URL (default: https://polygon.codeforces.com/api/)",
        )
        parser.add_argument(
            "--polygon-owner",
            dest="polygon_owners",
            action="append",
            default=[],
            help="Only migrate problems from this Polygon owner (repeatable).",
        )
        parser.add_argument("--yes", action="store_true", help="Skip confirmation")

    def handle(self, *args, **options):
        api_key = (options.get("api_key") or "").strip() or os.environ.get("POLYGON_API_KEY", "").strip()
        api_secret = (options.get("api_secret") or "").strip() or os.environ.get("POLYGON_API_SECRET", "").strip()
        base_url = (
            (options.get("api_base_url") or "").strip()
            or os.environ.get("POLYGON_API_BASE_URL", "").strip()
            or "https://polygon.codeforces.com/api/"
        )
        owners = options.get("polygon_owners") or []

        if not api_key or not api_secret:
            raise CommandError(
                "POLYGON_API_KEY/POLYGON_API_SECRET are missing. "
                "Use --api-key/--api-secret or environment.",
            )
        if not owners:
            raise CommandError("Provide at least one --polygon-owner.")

        client = PolygonClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
        result = client.call_json("problems.list", {"showDeleted": True})
        if isinstance(result, dict):
            for key in ("problems", "items", "data", "result"):
                if isinstance(result.get(key), list):
                    result = result[key]
                    break
        if not isinstance(result, list):
            raise CommandError("Unexpected Polygon problems.list response.")

        owner_set = {o.strip().lower() for o in owners if o.strip()}
        plan = []
        for item in result:
            if not isinstance(item, dict):
                continue
            pid = item.get("id", item.get("problemId"))
            if pid is None or not str(pid).isdigit():
                continue
            pid = int(pid)
            row_owner = ""
            for key in ("owner", "ownerName", "author", "authorName"):
                v = item.get(key)
                if v:
                    row_owner = str(v).strip().lower()
                    break
            if owner_set and row_owner not in owner_set:
                continue
            label = (item.get("shortName") or item.get("name") or "").strip()
            old_code = str(pid)
            prob = Problem.objects.filter(code=old_code).first()
            if prob is None:
                continue
            new_code = ImportPolygonCommand.polygon_label_to_problem_code(label, pid, exclude_pk=prob.pk)
            if new_code == old_code:
                continue
            if Problem.objects.filter(code=new_code).exclude(pk=prob.pk).exists():
                self.stderr.write(
                    self.style.WARNING(f'Skip {pid}: target code "{new_code}" already taken.'),
                )
                continue
            plan.append({"problem": prob, "old_code": old_code, "new_code": new_code, "pid": pid})

        self.stdout.write(f"Problems to rename: {len(plan)}")
        for row in plan:
            self.stdout.write(f"  {row['old_code']} -> {row['new_code']} (Polygon id {row['pid']})")

        if not plan:
            self.stdout.write("Nothing to do.")
            return

        if not options.get("yes"):
            if input("Apply renames? Type YES: ").strip().upper() != "YES":
                self.stdout.write("Cancelled.")
                return

        for row in plan:
            p = row["problem"]
            nc = row["new_code"]
            with transaction.atomic():
                p.code = nc
                p.save(update_fields=["code"])

        self.stdout.write(self.style.SUCCESS(f"Renamed {len(plan)} problem(s)."))
