"""RAGAS evaluation wrapper.

Provides a thin, optional wrapper around the `ragas` library so the rest of
the codebase never imports it directly. When the `evaluation` extra is not
installed, all metrics return None and the caller can decide how to degrade.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Single RAGAS evaluation result."""

    faithfulness_score: float | None = None
    answer_relevancy_score: float | None = None
    context_precision_score: float | None = None
    context_recall_score: float | None = None
    duration_seconds: float = 0.0
    error: str | None = None


def evaluate_rag(
    query: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None = None,
) -> EvaluationResult:
    """Run RAGAS evaluation for a single query/answer/context tuple.

    Returns an EvaluationResult with scores or an error field if evaluation
    failed or ragas is not installed.
    """
    start = time.perf_counter()
    result = EvaluationResult()

    try:
        from ragas import evaluate
        from ragas.metrics import (
            Faithfulness,
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
        )
        from datasets import Dataset
    except ImportError:
        result.error = "ragas is not installed. Install with: uv sync --extra evaluation"
        result.duration_seconds = time.perf_counter() - start
        return result

    try:
        dataset = Dataset.from_list([
            {
                "question": query,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": ground_truth or "",
            }
        ])

        eval_result = evaluate(
            dataset=dataset,
            metrics=[
                Faithfulness(),
                AnswerRelevancy(),
                ContextPrecision(),
                ContextRecall(),
            ],
        )

        result.faithfulness_score = float(eval_result["faithfulness"][0]) if "faithfulness" in eval_result.column_names else None
        result.answer_relevancy_score = float(eval_result["answer_relevancy"][0]) if "answer_relevancy" in eval_result.column_names else None
        result.context_precision_score = float(eval_result["context_precision"][0]) if "context_precision" in eval_result.column_names else None
        result.context_recall_score = float(eval_result["context_recall"][0]) if "context_recall" in eval_result.column_names else None
    except Exception:
        logger.exception("RAGAS evaluation failed.")
        result.error = "evaluation_failed"

    result.duration_seconds = time.perf_counter() - start
    return result


def evaluate_batch(
    queries: list[str],
    answers: list[str],
    contexts_list: list[list[str]],
    ground_truths: list[str] | None = None,
) -> list[EvaluationResult]:
    """Evaluate a batch of RAG triples."""
    results = []
    ground_truths = ground_truths or [""] * len(queries)
    for query, answer, contexts, gt in zip(queries, answers, contexts_list, ground_truths):
        results.append(evaluate_rag(query, answer, contexts, gt))
    return results
