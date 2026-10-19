"""Run the golden set through the live pipeline and persist the scores.

    python manage.py run_eval --organization <slug-or-uuid>
    python manage.py run_eval --organization acme --no-ragas --fail-below 0.8

The same runner powers "Evaluation runs" in the Django admin.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from api.models import Organization
from rag.evalrun import METRICS, golden_path, load_cases, run_case, summarize


class Command(BaseCommand):
    help = "Run the golden set against the live pipeline and save RAGAS reports."

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True, help="Organization slug or UUID.")
        parser.add_argument("--golden", default=None, help="Path to a golden YAML file.")
        parser.add_argument("--no-ragas", action="store_true", help="Skip RAGAS; run deterministic checks only.")
        parser.add_argument("--case", action="append", default=[], help="Only run this case id (repeatable).")
        parser.add_argument("--fail-below", type=float, default=None,
                            help="Exit non-zero if the pass rate or mean faithfulness is below this value (0-1).")
        parser.add_argument("--json", dest="json_out", default=None, help="Also write the results to this JSON file.")

    def handle(self, *args, **options):
        organization = self._organization(options["organization"])
        try:
            cases = load_cases(Path(options["golden"]) if options["golden"] else golden_path(), options["case"])
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        if not cases:
            raise CommandError("No golden cases to run.")

        rows = [run_case(case, organization, use_ragas=not options["no_ragas"]) for case in cases]
        summary = summarize(rows)
        self._report(rows, summary)
        if options["json_out"]:
            Path(options["json_out"]).write_text(json.dumps({"summary": summary, "cases": rows}, indent=2, default=str))

        threshold = options["fail_below"]
        if threshold is not None:
            pass_rate = summary["passed"] / summary["total"]
            faith = summary["mean_faithfulness"]
            if pass_rate < threshold or (faith is not None and faith < threshold):
                raise CommandError(
                    f"Quality gate failed (pass rate {pass_rate:.2f}, "
                    f"faithfulness {faith if faith is not None else 'n/a'}, threshold {threshold})."
                )

    def _organization(self, ref: str) -> Organization:
        try:
            return Organization.objects.get(slug=ref)
        except Organization.DoesNotExist:
            pass
        try:
            return Organization.objects.get(pk=ref)
        except (Organization.DoesNotExist, ValidationError, ValueError) as exc:
            raise CommandError(f"Organization {ref!r} not found (use its slug or UUID).") from exc

    def _report(self, rows: list[dict], summary: dict) -> None:
        self.stdout.write("")
        for row in rows:
            mark = self.style.SUCCESS("PASS") if row["passed"] else self.style.ERROR("FAIL")
            scores = " ".join(f"{k[:5]}={v:.2f}" for k, v in row["scores"].items() if v is not None)
            self.stdout.write(f"{mark} {row['id']:<32} {row['seconds']:>6}s  {scores}")
            for failure in row["failures"]:
                self.stdout.write(f"       - {failure}")
        self.stdout.write(f"\n{summary['passed']}/{summary['total']} cases passed")
        if summary["retrieval_hit_rate"] is not None:
            self.stdout.write(f"retrieval hit rate: {summary['retrieval_hit_rate']:.2f}")
        for metric in METRICS:
            value = summary[f"mean_{metric}"]
            if value is not None:
                self.stdout.write(f"mean {metric}: {value:.2f}")
