"""Tests for the golden-set runner's deterministic checks and persistence."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from api.models import Organization
from rag.eval import EvaluationResult
from rag.evalrun import check_case, retrieval_hit
from rag.models import Document, EvaluationReport, Status


@pytest.fixture
def organization_factory():
    def create(name: str = "Acme Corporation"):
        return Organization.objects.create(
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{Organization.objects.count()}",
        )

    return create


def _result(answer="RAG is retrieval augmented generation.", sources=("guide.txt",)):
    return {
        "answer": answer,
        "citations": [{"document_name": s, "page_content": "ctx", "score": 0.9} for s in sources],
        "confidence": "high",
        "finish_reason": "stop",
    }


class TestCheckCase:
    def test_passes_when_all_rules_hold(self):
        case = {"must_contain": ["rag"], "must_not_contain": ["unknown"], "expected_sources": ["guide.txt"]}
        assert check_case(case, _result()) == []

    def test_reports_missing_and_forbidden_phrases(self):
        failures = check_case({"must_contain": ["tungsten"], "must_not_contain": ["rag"]}, _result())
        assert len(failures) == 2

    def test_expected_source_must_be_cited(self):
        failures = check_case({"expected_sources": ["other.pdf"]}, _result())
        assert "other.pdf" in failures[0]

    def test_refusal_case_fails_when_answer_is_cited(self):
        assert check_case({"expect_refusal": True}, _result())
        assert check_case({"expect_refusal": True}, _result(sources=())) == []


@pytest.mark.parametrize("reason", ["RetrievalError", "NoOrEmptyCollectionError", "GenerationError"])
def test_broken_pipeline_never_counts_as_a_refusal(reason):
    broken = {**_result(sources=()), "finish_reason": reason}
    failures = check_case({"expect_refusal": True}, broken)
    assert failures and "pipeline error" in failures[0]


def test_no_documents_found_is_a_legitimate_refusal():
    refusal = {**_result(sources=()), "finish_reason": "No documents found"}
    assert check_case({"expect_refusal": True}, refusal) == []


def test_retrieval_hit():
    assert retrieval_hit({}, _result()) is None
    assert retrieval_hit({"expected_sources": ["guide.txt"]}, _result()) is True
    assert retrieval_hit({"expected_sources": ["x"]}, _result()) is False


@pytest.mark.django_db
def test_run_eval_saves_reports(organization_factory, tmp_path):
    organization = organization_factory()
    document = Document.objects.create(organization=organization, name="guide.txt", status=Status.READY)
    golden = tmp_path / "golden.yaml"
    golden.write_text(
        "- id: c1\n  question: What is RAG?\n  document: guide.txt\n"
        "  expected_sources: [guide.txt]\n  must_contain: [rag]\n  min_faithfulness: 0.8\n"
    )

    async def fake_answer(*args, **kwargs):
        return _result()

    scored = EvaluationResult(faithfulness_score=0.95, answer_relevancy_score=0.9,
                              context_precision_score=0.8, context_recall_score=0.7)
    with patch("rag.evalrun.answer", fake_answer), \
         patch("rag.evalrun.evaluate_rag", return_value=scored):
        call_command("run_eval", organization=organization.slug, golden=str(golden), fail_below=0.8)

    report = EvaluationReport.objects.get(document=document)
    assert report.faithfulness_score == 0.95
    assert report.metadata["case_id"] == "c1" and report.metadata["passed"] is True


@pytest.mark.django_db
def test_run_eval_quality_gate_fails_below_threshold(organization_factory, tmp_path):
    organization = organization_factory()
    golden = tmp_path / "golden.yaml"
    golden.write_text("- id: c1\n  question: Q?\n  must_contain: [tungsten]\n")

    async def fake_answer(*args, **kwargs):
        return _result()

    with patch("rag.evalrun.answer", fake_answer), pytest.raises(CommandError):
        call_command("run_eval", organization=organization.slug, golden=str(golden),
                     no_ragas=True, fail_below=0.9)


@pytest.mark.django_db
def test_run_eval_unknown_organization():
    with pytest.raises(CommandError):
        call_command("run_eval", organization="nope")


@pytest.mark.django_db
def test_report_attaches_to_cited_document_when_case_names_none(organization_factory, tmp_path):
    organization = organization_factory()
    cited = Document.objects.create(organization=organization, name="cited.txt", status=Status.READY)
    golden = tmp_path / "golden.yaml"
    golden.write_text("- id: no-doc\n  question: What is RAG?\n  must_contain: [rag]\n")

    async def fake_answer(*args, **kwargs):
        result = _result(sources=("cited.txt",))
        result["citations"][0]["document_id"] = str(cited.id)
        return result

    scored = EvaluationResult(faithfulness_score=0.9)
    with patch("rag.evalrun.answer", fake_answer), patch("rag.evalrun.evaluate_rag", return_value=scored):
        call_command("run_eval", organization=organization.slug, golden=str(golden))

    assert EvaluationReport.objects.get().document == cited


def test_judge_gets_a_larger_token_budget(monkeypatch):
    from types import SimpleNamespace

    from rag.eval import _judge_model

    class Model:
        max_tokens = 4096

        def model_copy(self, update):
            clone = Model()
            clone.max_tokens = update["max_tokens"]
            return clone

    monkeypatch.delenv("EVAL_JUDGE_MAX_TOKENS", raising=False)
    assert _judge_model(Model()).max_tokens == 8192
    monkeypatch.setenv("EVAL_JUDGE_MAX_TOKENS", "9000")
    assert _judge_model(Model()).max_tokens == 9000
    stand_in = SimpleNamespace()  # models without model_copy pass through untouched
    assert _judge_model(stand_in) is stand_in


def test_run_config_raises_the_default_judge_timeout(monkeypatch):
    import sys
    from types import ModuleType

    from rag.eval import _run_config_kwargs

    module = ModuleType("ragas.run_config")
    module.RunConfig = lambda timeout: {"timeout": timeout}
    monkeypatch.setitem(sys.modules, "ragas.run_config", module)
    monkeypatch.delenv("EVAL_JUDGE_TIMEOUT", raising=False)
    assert _run_config_kwargs() == {"run_config": {"timeout": 600}}
    monkeypatch.setenv("EVAL_JUDGE_TIMEOUT", "900")
    assert _run_config_kwargs() == {"run_config": {"timeout": 900}}


def test_run_config_is_skipped_when_ragas_is_unavailable(monkeypatch):
    import sys

    from rag.eval import _run_config_kwargs

    monkeypatch.setitem(sys.modules, "ragas.run_config", None)  # makes the import raise ImportError
    assert _run_config_kwargs() == {}
