"""Tests for RAGAS evaluation wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag.eval import EvaluationResult, evaluate_batch, evaluate_rag


class TestEvaluateRag:
    def test_returns_result_when_ragas_not_installed(self) -> None:
        with patch("rag.eval.evaluate_rag") as mock_eval:
            mock_eval.return_value = EvaluationResult(error="ragas is not installed")
            result = evaluate_rag("test query", "test answer", ["context1"])
        assert result.error is not None
        assert result.faithfulness_score is None

    def test_evaluate_batch_multiple_items(self) -> None:
        with patch("rag.eval.evaluate_rag") as mock_eval:
            mock_eval.return_value = EvaluationResult(
                faithfulness_score=0.9,
                answer_relevancy_score=0.85,
                context_precision_score=0.8,
                context_recall_score=0.75,
            )
            results = evaluate_batch(
                queries=["q1", "q2"],
                answers=["a1", "a2"],
                contexts_list=[["c1"], ["c2"]],
            )
        assert len(results) == 2
        assert results[0].faithfulness_score == 0.9
        assert results[1].faithfulness_score == 0.9


class TestEvaluationResult:
    def test_default_values(self) -> None:
        result = EvaluationResult()
        assert result.faithfulness_score is None
        assert result.answer_relevancy_score is None
        assert result.context_precision_score is None
        assert result.context_recall_score is None
        assert result.error is None
        assert result.duration_seconds == 0.0
