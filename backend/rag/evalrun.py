"""Golden-set evaluation runner, shared by `manage.py run_eval` and the admin.

Runs each case through the real chat graph, applies the deterministic checks,
optionally scores with RAGAS, and persists an `EvaluationReport` per case that
targets a document.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import yaml

from rag.eval import evaluate_rag
from rag.graph import answer
from rag.models import Document, EvaluationReport

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "tests" / "golden" / "set.yaml"
METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def golden_path() -> Path:
    """The golden file: `EVAL_GOLDEN_PATH` if set, otherwise the one shipped in the repo."""
    return Path(os.environ.get("EVAL_GOLDEN_PATH") or DEFAULT_GOLDEN)


def load_cases(path: Path | None = None, only: list[str] | None = None) -> list[dict]:
    path = Path(path) if path else golden_path()
    if not path.exists():
        raise FileNotFoundError(f"Golden file not found: {path}")
    cases = yaml.safe_load(path.read_text()) or []
    if only:
        cases = [case for case in cases if case["id"] in only]
    return cases


# Finish reasons that mean the pipeline broke, as opposed to "nothing relevant
# was found" (a legitimate refusal). A broken pipeline must never count as one:
# an embedder mismatch used to make every query look like a correct refusal.
PIPELINE_ERRORS = {"RetrievalError", "NoOrEmptyCollectionError", "GenerationError"}


def check_case(case: dict, result: dict) -> list[str]:
    """Deterministic checks for one case; returns the list of failures."""
    failures: list[str] = []
    reason = result.get("finish_reason", "")
    if any(error in reason for error in PIPELINE_ERRORS):
        failures.append(f"pipeline error ({reason}); check the api/worker logs and model configuration")
    text = result["answer"].lower()
    for phrase in case.get("must_contain", []):
        if phrase.lower() not in text:
            failures.append(f"missing {phrase!r}")
    for phrase in case.get("must_not_contain", []):
        if phrase.lower() in text:
            failures.append(f"forbidden {phrase!r} present")

    expected = case.get("expected_sources", [])
    if expected:
        cited = {c.get("document_name", "") for c in result["citations"]}
        missing = [name for name in expected if name not in cited]
        if missing:
            failures.append(f"expected source(s) not cited: {', '.join(missing)}")
    if case.get("expect_refusal") and result["citations"]:
        failures.append("expected a refusal but the answer was cited")
    return failures


def retrieval_hit(case: dict, result: dict) -> bool | None:
    """Did retrieval surface an expected source? None when the case has none."""
    expected = case.get("expected_sources", [])
    if not expected:
        return None
    cited = {c.get("document_name", "") for c in result["citations"]}
    return any(name in cited for name in expected)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _cited_document(result: dict, organization) -> Document | None:
    for citation in result["citations"]:
        document_id = citation.get("document_id")
        if document_id:
            return Document.objects.filter(pk=document_id, organization=organization).first()
    return None


def run_case(case: dict, organization, *, use_ragas: bool) -> dict:
    started = time.perf_counter()
    document = None
    if case.get("document"):
        document = Document.objects.filter(organization=organization, name=case["document"]).first()
        if document is None:
            return {
                "id": case["id"],
                "question": case["question"],
                "passed": False,
                "failures": [f"document {case['document']!r} not found in organization"],
                "scores": {},
                "retrieval_hit": None,
                "seconds": 0.0,
                "answer": "",
            }

    result = asyncio.run(
        answer(
            case["question"],
            organization_id=str(organization.id),
            is_org_admin=True,
            document_id=str(document.id) if document else None,
        )
    )
    failures = check_case(case, result)

    scores: dict[str, float | None] = {}
    error = None
    if use_ragas and result["citations"]:
        evaluation = evaluate_rag(
            case["question"],
            result["answer"],
            [c["page_content"] for c in result["citations"]],
            case.get("ground_truth"),
        )
        error = evaluation.error
        scores = {
            "faithfulness": evaluation.faithfulness_score,
            "answer_relevancy": evaluation.answer_relevancy_score,
            "context_precision": evaluation.context_precision_score,
            "context_recall": evaluation.context_recall_score,
        }
        minimum = case.get("min_faithfulness")
        if minimum is not None and scores["faithfulness"] is not None and scores["faithfulness"] < minimum:
            failures.append(f"faithfulness {scores['faithfulness']:.2f} < {minimum}")
        # Cases that do not name a document are attached to the one the answer
        # cited most, so every scored case still shows up under Evaluation Reports.
        report_document = document or _cited_document(result, organization)
        if report_document is not None and not error:
            EvaluationReport.objects.create(
                document=report_document,
                query=case["question"],
                answer=result["answer"],
                faithfulness_score=scores["faithfulness"],
                answer_relevancy_score=scores["answer_relevancy"],
                context_precision_score=scores["context_precision"],
                context_recall_score=scores["context_recall"],
                chunks_retrieved=len(result["citations"]),
                evaluation_duration_seconds=evaluation.duration_seconds,
                metadata={"case_id": case["id"], "passed": not failures, "failures": failures},
            )

    return {
        "id": case["id"],
        "question": case["question"],
        "passed": not failures,
        "failures": failures + ([f"ragas: {error}"] if error else []),
        "scores": scores,
        "retrieval_hit": retrieval_hit(case, result),
        "seconds": round(time.perf_counter() - started, 2),
        "answer": result["answer"],
    }


def summarize(rows: list[dict]) -> dict:
    hits = [r["retrieval_hit"] for r in rows if r["retrieval_hit"] is not None]
    summary = {
        "total": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "retrieval_hit_rate": (sum(hits) / len(hits)) if hits else None,
    }
    for metric in METRICS:
        summary[f"mean_{metric}"] = mean(
            [r["scores"][metric] for r in rows if r["scores"].get(metric) is not None]
        )
    return summary
