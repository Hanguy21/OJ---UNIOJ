import hashlib
import json
import os
import random
import re
import shutil
import string
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from lxml import etree as ET

from judge.management.commands import import_polygon_package as polygon_import
from judge.models import Language, Problem, ProblemData, ProblemGroup, ProblemTestCase, \
    ProblemTranslation, ProblemType, Profile, Solution
from judge.models.problem import ProblemTestcaseAccess
from judge.utils.problem_data import ProblemDataCompiler


class PolygonClient:
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/") + "/"
        self.session = requests.Session()

    @staticmethod
    def _flatten_params(params):
        pairs = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            if isinstance(value, (list, tuple)):
                for item in value:
                    pairs.append((str(key), str(item)))
            else:
                pairs.append((str(key), str(value)))
        return pairs

    def _signed_payload(self, method, params):
        pairs = self._flatten_params(params)
        pairs.append(("apiKey", self.api_key))
        pairs.append(("time", str(int(time.time()))))

        encoded = [(k.encode("utf-8"), v.encode("utf-8")) for k, v in pairs]
        sorted_pairs = sorted(encoded)
        args_part = b"&".join(k + b"=" + v for k, v in sorted_pairs)

        rand = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        rand_bytes = rand.encode("utf-8")
        sig_source = rand_bytes + b"/" + method.encode("utf-8") + b"?" + args_part + b"#" + self.api_secret.encode(
            "utf-8"
        )
        api_sig = rand + hashlib.sha512(sig_source).hexdigest()
        encoded.append((b"apiSig", api_sig.encode("utf-8")))
        return encoded

    def call_json(self, method, params):
        resp = self.session.post(self.base_url + method, files=self._signed_payload(method, params), timeout=60)
        if resp.status_code not in (200, 400):
            raise CommandError(f"Polygon API returned HTTP {resp.status_code} for {method}")
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise CommandError(f"Polygon API returned invalid JSON for {method}") from exc
        status = data.get("status")
        if status != "OK":
            comment = data.get("comment", "Unknown Polygon API error")
            raise CommandError(f"{method} failed: {comment}")
        return data.get("result")

    def call_raw(self, method, params):
        return self.session.post(self.base_url + method, files=self._signed_payload(method, params), timeout=120)


class Command(BaseCommand):
    help = "Download and import a Polygon problem by URL/ID"

    def add_arguments(self, parser):
        parser.add_argument("problem", help="Polygon problem URL or problem ID")
        parser.add_argument("--authors", nargs="*", default=[], help="Author usernames for imported problem")
        parser.add_argument("--curators", nargs="*", default=[], help="Curator usernames for imported problem")
        parser.add_argument("--api-key", default="", help="Polygon API key (overrides environment)")
        parser.add_argument("--api-secret", default="", help="Polygon API secret (overrides environment)")
        parser.add_argument("--api-base-url", default="", help="Polygon API base URL (overrides environment)")
        parser.add_argument("--timeout", type=int, default=600, help="Package build timeout in seconds")
        parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval in seconds")
        parser.add_argument("--verify-build", action="store_true", help="Enable Polygon verify when building package")

    def _log_progress(self, percent, message):
        self.stdout.write(f"[{int(percent)}%] {message}")

    def _classify_polygon_error(self, message):
        lowered = (message or "").lower()
        auth_markers = (
            "invalid apikey",
            "api key",
            "apisig",
            "signature",
            "access denied",
            "permission",
            "unauthorized",
            "not authorized",
            "authentication",
        )
        if any(marker in lowered for marker in auth_markers):
            return "auth"
        return "other"

    def handle(self, *args, **options):
        problem_ref = options["problem"].strip()
        problem_id = self._extract_problem_id(problem_ref)

        api_key = (options.get("api_key") or "").strip() or os.environ.get("POLYGON_API_KEY", "").strip()
        api_secret = (options.get("api_secret") or "").strip() or os.environ.get("POLYGON_API_SECRET", "").strip()
        base_url = (
            (options.get("api_base_url") or "").strip()
            or os.environ.get("POLYGON_API_BASE_URL", "https://polygon.codeforces.com/api/").strip()
        )

        if not api_key or not api_secret:
            raise CommandError(
                "POLYGON_API_KEY/POLYGON_API_SECRET are missing. "
                "Run: eval \"$(./scripts/export_polygon_api <api_key> <api_secret>)\""
            )

        client = PolygonClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
        if problem_id is None:
            problem_id = self._resolve_problem_id(client, problem_ref)
        if problem_id is None:
            self.stdout.write(self.style.ERROR(f"Invalid Polygon problem input: {problem_ref}"))
            raise CommandError(
                "Cannot resolve Polygon problem ID from input. "
                "Please provide a numeric problem ID or a URL that can be resolved via Polygon API."
            )

        polygon_root = Path(getattr(settings, 'POLYGON_PACKAGE_ROOT', '/polygon_package'))
        polygon_root.mkdir(parents=True, exist_ok=True)
        package_path = polygon_root / f"{problem_id}$linux.zip"
        problem_code = str(problem_id)

        self.stdout.write(f"Polygon problem_id: {problem_id}")
        self.stdout.write(f"DMOJ problem code: {problem_code}")
        self.stdout.write(f"Output package path: {package_path}")
        self._log_progress(3, "Validating Polygon API credentials and problem ID")
        try:
            owner_name, problem_name, exists = self._fetch_problem_identity(client, problem_id)
        except CommandError as exc:
            raw_error = str(exc)
            if self._classify_polygon_error(raw_error) == "auth":
                error_message = "Polygon API authentication failed. Please check API key/secret."
            else:
                error_message = f"Cannot validate Polygon problem {problem_id}: {raw_error}"
            self.stdout.write(self.style.ERROR(error_message))
            self._log_progress(100, "Import failed")
            raise CommandError(error_message) from exc

        if not exists:
            error_message = f"Polygon problem ID {problem_id} does not exist or is inaccessible."
            self.stdout.write(self.style.ERROR(error_message))
            self._log_progress(100, "Import failed")
            raise CommandError(error_message)

        self.stdout.write(
            f"Polygon account: {owner_name or 'unknown'} | Problem: {problem_id} - {problem_name or 'unknown'}"
        )
        self._log_progress(5, "Parsed input and initialized import context")

        self._log_progress(10, "Fetching Polygon tags")
        tags = self._fetch_tags(client, problem_id)
        if tags:
            self.stdout.write(f"Fetched tags: {', '.join(tags)}")
        else:
            self.stdout.write("No tags found on Polygon")
        self._log_progress(18, "Tags fetched")

        self._log_progress(22, "Checking/downloading Linux package")
        self._download_linux_package(
            client=client,
            problem_id=problem_id,
            output_path=package_path,
            timeout=options["timeout"],
            poll_interval=options["poll_interval"],
            verify=options["verify_build"],
        )

        existing_problem = Problem.objects.filter(code=problem_code).first()
        if existing_problem is None:
            self.stdout.write("Problem does not exist. Creating new problem from package...")
            self._log_progress(72, "Creating problem in DMOJ")
            self._import_new_problem(
                package_path=package_path,
                problem_code=problem_code,
                authors=options["authors"],
                curators=options["curators"],
            )
        else:
            self.stdout.write("Problem already exists. Updating existing problem data...")
            self._log_progress(72, "Updating existing problem in DMOJ")
            self._update_existing_problem(
                existing_problem=existing_problem,
                package_path=package_path,
                problem_code=problem_code,
                authors=options["authors"],
                curators=options["curators"],
            )

        if tags:
            self._apply_tags_to_problem_types(problem_code, tags)
            self.stdout.write(self.style.SUCCESS("Applied Polygon tags to problem types"))
            self._log_progress(92, "Applied Polygon tags to problem types")

        self._log_progress(100, "Import completed")
        self.stdout.write(self.style.SUCCESS(f"Imported Polygon problem {problem_id} successfully"))

    def _extract_problem_id(self, problem_ref):
        if problem_ref.isdigit():
            return int(problem_ref)

        parsed = urlparse(problem_ref)
        query = parse_qs(parsed.query)
        for key in ("problemId", "id"):
            if key in query and query[key]:
                value = query[key][0]
                if str(value).isdigit():
                    return int(value)

        patterns = [
            r"/problems?/(\d+)",
            r"/problem/(\d+)",
            r"/p/(\d+)",
            r"(\d+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, problem_ref)
            if match:
                return int(match.group(1))
        return None

    def _resolve_problem_id(self, client, problem_ref):
        parsed = urlparse(problem_ref)
        if not parsed.netloc:
            return None

        # Expected shared Polygon URL form:
        # /<share-token>/<owner>/<problem-slug>
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) < 2:
            return None
        owner = parts[-2].strip()
        slug = parts[-1].strip()
        if not owner or not slug:
            return None

        # First try direct lookup by owner.
        try:
            result = client.call_json("problems.list", {"owner": owner, "showDeleted": True})
        except CommandError:
            result = []

        if isinstance(result, list):
            slug_lower = slug.lower()
            for item in result:
                if not isinstance(item, dict):
                    continue
                candidates = [
                    item.get("shortName"),
                    item.get("name"),
                    item.get("url"),
                    item.get("title"),
                ]
                for value in candidates:
                    if not value:
                        continue
                    if str(value).strip().lower() == slug_lower:
                        problem_id = item.get("id")
                        if str(problem_id).isdigit():
                            return int(problem_id)
                    # Be tolerant with URLs/titles that may include separators.
                    normalized = slugify(str(value)).replace("-", "").lower()
                    if normalized and normalized == slugify(slug).replace("-", "").lower():
                        problem_id = item.get("id")
                        if str(problem_id).isdigit():
                            return int(problem_id)

        return None

    def _fetch_tags(self, client, problem_id):
        result = client.call_json("problem.viewTags", {"problemId": problem_id})
        if isinstance(result, list):
            return [str(tag).strip() for tag in result if str(tag).strip()]
        if isinstance(result, str):
            return [tag.strip() for tag in result.split(",") if tag.strip()]
        if isinstance(result, dict):
            tags = result.get("tags", [])
            if isinstance(tags, list):
                return [str(tag).strip() for tag in tags if str(tag).strip()]
        return []

    def _fetch_problem_identity(self, client, problem_id):
        result = client.call_json("problems.list", {"id": problem_id, "showDeleted": True})
        if isinstance(result, list) and result:
            problem_data = result[0] if isinstance(result[0], dict) else {}
            return problem_data.get("owner"), problem_data.get("name"), True
        return None, None, False

    def _download_linux_package(self, client, problem_id, output_path, timeout, poll_interval, verify):
        deadline = time.time() + timeout
        last_message = "No response"
        build_requested = False

        while time.time() < deadline:
            latest_package = self._latest_ready_package(client, problem_id)
            if latest_package is None and not build_requested:
                self._request_package_build(client, problem_id, verify=verify)
                build_requested = True

            # Avoid downloading before there is a READY package id.
            if latest_package is None:
                last_message = "No READY package yet"
                self.stdout.write(f"Package not ready yet: {last_message}. Retrying in {poll_interval}s...")
                self._log_progress(35, "Waiting for package to become READY")
                time.sleep(poll_interval)
                continue

            download_params = {
                "problemId": problem_id,
                "packageId": latest_package["id"],
                "type": "linux",
            }

            resp = client.call_raw("problem.package", download_params)
            if resp.status_code in (500, 502, 503, 504):
                # Polygon may transiently return 5xx while package artifacts are still being prepared.
                if not build_requested:
                    self._request_package_build(client, problem_id, verify=verify)
                    build_requested = True
                last_message = f"HTTP {resp.status_code} from problem.package"
                self.stdout.write(f"Package not ready yet: {last_message}. Retrying in {poll_interval}s...")
                self._log_progress(40, "Package artifact not ready yet")
                time.sleep(poll_interval)
                continue
            if resp.status_code not in (200, 400):
                raise CommandError(f"problem.package returned HTTP {resp.status_code}")

            data = resp.content
            if data.startswith(b"PK\x03\x04"):
                output_path.write_bytes(data)
                self.stdout.write(self.style.SUCCESS(f"Downloaded Linux package to {output_path}"))
                self._log_progress(60, "Downloaded Linux package")
                return

            message, retryable = self._parse_package_response(resp.text)
            last_message = message
            if not build_requested and "package not found" in message.lower():
                self._request_package_build(client, problem_id, verify=verify)
                build_requested = True
            self.stdout.write(f"Package not ready yet: {message}. Retrying in {poll_interval}s...")
            self._log_progress(45, "Polling package status")
            if not retryable:
                raise CommandError(f"Cannot download package: {message}")
            time.sleep(poll_interval)

        raise CommandError(f"Timed out waiting for package build. Last response: {last_message}")

    def _latest_ready_package(self, client, problem_id):
        packages = client.call_json("problem.packages", {"problemId": problem_id}) or []
        if not isinstance(packages, list):
            return None
        ready_packages = [
            p for p in packages
            if isinstance(p, dict) and str(p.get("state", "")).upper() == "READY" and p.get("id") is not None
        ]
        if not ready_packages:
            return None
        return max(ready_packages, key=lambda p: int(p.get("id", 0)))

    def _request_package_build(self, client, problem_id, verify):
        try:
            client.call_json("problem.buildPackage", {"problemId": problem_id, "full": True, "verify": verify})
            self.stdout.write("Requested Polygon to build FULL package (linux included).")
            self._log_progress(30, "Triggered package build on Polygon")
        except CommandError as exc:
            message = str(exc).lower()
            if "already" in message and "build" in message:
                self.stdout.write("Polygon is already building package. Continue polling...")
                self._log_progress(30, "Polygon is already building package")
                return
            raise

    def _parse_package_response(self, raw_text):
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            # Polygon/Cloudflare can intermittently return HTML pages while artifacts
            # are propagating. Treat this as transient and keep polling.
            return "Unexpected non-JSON response from Polygon", True

        status = payload.get("status")
        if status == "OK":
            result = payload.get("result")
            if isinstance(result, str) and result:
                if result.startswith("http://") or result.startswith("https://"):
                    return "Received URL instead of binary package", False
            return "Build in progress", True

        comment = payload.get("comment", "Unknown Polygon error")
        lowered = comment.lower()
        # Polygon may return "Package not found" while linux package is still being generated.
        # Treat this as a transient state and keep polling until timeout.
        if "package not found" in lowered:
            return comment, True

        non_retryable = (
            "invalid apikey",
            "signature",
            "permission",
            "access denied",
        )
        if any(part in lowered for part in non_retryable):
            return comment, False
        return comment, True

    def _import_new_problem(self, package_path, problem_code, authors, curators):
        from django.core.management import call_command

        call_command(
            "import_polygon_package",
            str(package_path),
            problem_code,
            authors=authors,
            curators=curators,
        )

    def _resolve_profiles(self, usernames):
        profiles = []
        for username in usernames:
            try:
                profiles.append(Profile.objects.get(user__username=username))
            except Profile.DoesNotExist:
                raise CommandError(f"user {username} does not exist")
        return profiles

    @transaction.atomic
    def _update_existing_problem(self, existing_problem, package_path, problem_code, authors, curators):
        if not shutil.which("pandoc"):
            raise CommandError("pandoc not installed")
        if polygon_import.pandoc_get_version() < (3, 0, 0):
            raise CommandError("pandoc version must be at least 3.0.0")

        package = zipfile.ZipFile(str(package_path), "r")
        if "problem.xml" not in package.namelist():
            raise CommandError("problem.xml not found")
        root = ET.fromstring(package.read("problem.xml"))

        problem_meta = {
            "image_cache": {},
            "code": problem_code,
            "tmp_dir": tempfile.TemporaryDirectory(),
            "authors": self._resolve_profiles(authors) if authors else list(existing_problem.authors.all()),
            "curators": self._resolve_profiles(curators) if curators else list(existing_problem.curators.all()),
        }

        try:
            polygon_import.parse_assets(problem_meta, root, package)
            polygon_import.parse_tests(problem_meta, root, package)
            polygon_import.parse_statements(problem_meta, root, package)
            polygon_import.parse_reference_solution(problem_meta, package)
            self._apply_problem_update(existing_problem, problem_meta)
        finally:
            problem_meta["tmp_dir"].cleanup()

    def _apply_problem_update(self, problem, problem_meta):
        problem.name = problem_meta["name"]
        problem.time_limit = problem_meta["time_limit"]
        problem.memory_limit = problem_meta["memory_limit"]
        problem.description = problem_meta["description"]
        problem.partial = problem_meta["partial"]
        problem.testcase_visibility_mode = ProblemTestcaseAccess.ALWAYS
        if problem.group_id is None:
            problem.group = ProblemGroup.objects.order_by("id").first()
        problem.save()

        problem.allowed_languages.set(Language.objects.filter(include_in_problem=True))
        problem.authors.set(problem_meta["authors"])
        problem.curators.set(problem_meta["curators"])

        ProblemTranslation.objects.filter(problem=problem).delete()
        for tran in problem_meta["translations"]:
            ProblemTranslation.objects.create(
                problem=problem,
                language=tran["language"],
                name=tran["name"],
                description=tran["description"],
            )

        solution_content = ""
        model_solution_code = (problem_meta.get("model_solution_code") or "").strip()
        if model_solution_code:
            solution_content = "```cpp\n" + model_solution_code + "\n```"
        else:
            tutorial = (problem_meta.get("tutorial") or "").strip()
            if tutorial:
                solution_content = tutorial

        if solution_content:
            solution, _ = Solution.objects.update_or_create(
                problem=problem,
                defaults={
                    "is_public": False,
                    "content": "",
                    "publish_on": timezone.now(),
                    "solution_language_key": "CPP17",
                },
            )
            solution.save_content_text(solution_content)
            solution.authors.set(problem_meta["authors"])
        elif Solution.objects.filter(problem=problem).exists():
            self.stdout.write("No reference solution source found in Polygon package; keeping existing solution.")

        with open(problem_meta["zipfile"], "rb") as f:
            problem_data, _ = ProblemData.objects.get_or_create(problem=problem)
            problem_data.zipfile.save(os.path.basename(problem_meta["zipfile"]), File(f), save=False)
            problem_data.grader = problem_meta["grader"]
            problem_data.checker = problem_meta["checker"]
            problem_data.grader_args = json.dumps(problem_meta["grader_args"])

            if "checker_args" in problem_meta:
                problem_data.checker_args = json.dumps(problem_meta["checker_args"])
            else:
                problem_data.checker_args = ""

            if "custom_checker" in problem_meta:
                # Must not wrap a file handle that closes before model.save() reads it.
                with open(problem_meta["custom_checker"], "rb") as checker_file:
                    checker_bytes = checker_file.read()
                problem_data.custom_checker.save(
                    os.path.basename(problem_meta["custom_checker"]),
                    ContentFile(checker_bytes),
                    save=False,
                )
            else:
                if problem_data.custom_checker:
                    problem_data.custom_checker.delete(save=False)
                problem_data.custom_checker = None

            if "custom_grader" in problem_meta:
                with open(problem_meta["custom_grader"], "rb") as grader_file:
                    grader_bytes = grader_file.read()
                problem_data.custom_grader.save(
                    os.path.basename(problem_meta["custom_grader"]),
                    ContentFile(grader_bytes),
                    save=False,
                )
            else:
                if problem_data.custom_grader:
                    problem_data.custom_grader.delete(save=False)
                problem_data.custom_grader = None

            problem_data.save()

        ProblemTestCase.objects.filter(dataset=problem).delete()
        order = 0
        for batch in problem_meta["batches"].values():
            if len(batch["cases"]) == 0:
                continue
            order += 1
            ProblemTestCase.objects.create(dataset=problem, order=order, type="S", points=batch["points"], is_pretest=False)
            for case_index in batch["cases"]:
                order += 1
                case_data = problem_meta["cases_data"][case_index]
                ProblemTestCase.objects.create(
                    dataset=problem,
                    order=order,
                    type="C",
                    input_file=case_data["input_file"],
                    output_file=case_data["output_file"],
                    is_pretest=False,
                )
            order += 1
            ProblemTestCase.objects.create(dataset=problem, order=order, type="E", is_pretest=False)

        for case_index in problem_meta["normal_cases"]:
            order += 1
            case_data = problem_meta["cases_data"][case_index]
            ProblemTestCase.objects.create(
                dataset=problem,
                order=order,
                type="C",
                input_file=case_data["input_file"],
                output_file=case_data["output_file"],
                points=case_data["points"],
                is_pretest=False,
            )

        ProblemDataCompiler.generate(
            problem=problem,
            data=problem.data_files,
            cases=problem.cases.order_by("order"),
            files=zipfile.ZipFile(problem_meta["zipfile"]).namelist(),
        )

    def _apply_tags_to_problem_types(self, problem_code, tags):
        problem = Problem.objects.get(code=problem_code)
        mapped_types = []
        for tag in tags:
            pt = self._get_or_create_problem_type(tag)
            mapped_types.append(pt)

        if mapped_types:
            problem.types.set(mapped_types)
        rating = self._extract_rating_from_tags(tags)
        if rating is not None:
            problem.points = rating
            problem.save(update_fields=["points"])
            self.stdout.write(f"Applied problem rating from Polygon tags: {rating}")

    def _extract_rating_from_tags(self, tags):
        for tag in tags:
            value = str(tag or "").strip()
            if not value:
                continue
            star_match = re.search(r"\*(\d{3,4})\b", value)
            if star_match:
                return float(int(star_match.group(1)))
            plain_match = re.fullmatch(r"(\d{3,4})", value)
            if plain_match:
                return float(int(plain_match.group(1)))
        return None

    def _get_or_create_problem_type(self, tag):
        tag = tag.strip()
        existing = ProblemType.objects.filter(full_name__iexact=tag).first()
        if existing:
            return existing

        base = slugify(tag).replace("-", "_")
        if not base:
            base = "polygon"
        base = base[:20]

        candidate = base
        counter = 2
        while ProblemType.objects.filter(name=candidate).exists():
            suffix = f"_{counter}"
            candidate = f"{base[: max(1, 20 - len(suffix))]}{suffix}"
            counter += 1

        return ProblemType.objects.create(name=candidate, full_name=tag[:100])
