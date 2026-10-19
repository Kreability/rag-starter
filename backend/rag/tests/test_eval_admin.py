"""Launching and executing evaluation runs from the Django admin."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from api.models import Organization
from rag.models import EvaluationRun
from rag.tasks import run_evaluation_task


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Acme", slug="acme-evals")


@pytest.fixture
def admin_client(org, db):
    user = get_user_model().objects.create_superuser(
        username="root@example.com", email="root@example.com", password="pw", organization=org
    )
    client = Client()
    client.force_login(user)
    return client


def _row(case_id="c1", passed=True):
    return {"id": case_id, "question": "q", "passed": passed, "failures": [] if passed else ["missing 'x'"],
            "scores": {"faithfulness": 0.9}, "retrieval_hit": True, "seconds": 1.2, "answer": "a"}


def test_add_page_launches_a_queued_run(admin_client, org, django_capture_on_commit_callbacks):
    with patch("rag.admin.run_evaluation_task.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(
            "/admin/rag/evaluationrun/add/", {"organization": str(org.pk), "use_ragas": "on"}
        )
    assert response.status_code == 302
    run = EvaluationRun.objects.get()
    assert run.status == EvaluationRun.State.QUEUED and run.use_ragas
    assert run.triggered_by is not None
    delay.assert_called_once_with(str(run.pk))


def test_changelist_and_detail_render(admin_client, org):
    run = EvaluationRun.objects.create(
        organization=org, status="DONE", total_cases=1, passed_cases=1,
        results=[_row()], summary={"mean_faithfulness": 0.9, "retrieval_hit_rate": 1.0},
    )
    assert admin_client.get("/admin/rag/evaluationrun/").status_code == 200
    detail = admin_client.get(f"/admin/rag/evaluationrun/{run.pk}/change/")
    assert detail.status_code == 200
    assert b"PASS" in detail.content and b"c1" in detail.content


def test_rerun_action_queues_a_new_run(admin_client, org, django_capture_on_commit_callbacks):
    run = EvaluationRun.objects.create(organization=org, use_ragas=False)
    with patch("rag.admin.run_evaluation_task.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        admin_client.post("/admin/rag/evaluationrun/", {"action": "rerun", "_selected_action": [str(run.pk)]})
    assert EvaluationRun.objects.count() == 2
    assert delay.call_count == 1


def test_task_runs_cases_and_stores_summary(org):
    run = EvaluationRun.objects.create(organization=org, use_ragas=False)
    cases = [{"id": "c1", "question": "q1"}, {"id": "c2", "question": "q2"}]
    outcomes = iter([_row("c1", True), _row("c2", False)])
    with patch("rag.evalrun.load_cases", return_value=cases), \
         patch("rag.evalrun.run_case", side_effect=lambda *a, **k: next(outcomes)):
        run_evaluation_task(str(run.pk))
    run.refresh_from_db()
    assert run.status == "DONE"
    assert (run.passed_cases, run.total_cases) == (1, 2)
    assert run.summary["total"] == 2 and run.finished_at is not None


def test_task_records_failure_instead_of_crashing(org):
    run = EvaluationRun.objects.create(organization=org, use_ragas=False)
    with patch("rag.evalrun.load_cases", side_effect=FileNotFoundError("Golden file not found: /x")):
        run_evaluation_task(str(run.pk))
    run.refresh_from_db()
    assert run.status == "FAILED" and "Golden file not found" in run.error
