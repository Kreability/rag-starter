"""Retrieval pipeline tests.

These cover the ported logic that is easy to break silently: summary expansion,
deduplication, pruning, and the tenant filter.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.documents import Document

from rag.graph import build_context, document_to_citation, format_history
from rag.retrieval import _early_prune, _expand_summaries, _remove_duplicates


def make_document(doc_id: str, *, content_type="TEXT", related=None, score=0.9, content="text"):
    return Document(
        page_content=content,
        metadata={
            "id": doc_id,
            "type": content_type,
            "related": related or [],
            "score": score,
            "document_id": "doc-1",
            "document_name": "manual.pdf",
        },
    )


class TestRemoveDuplicates:
    def test_keeps_first_occurrence_only(self):
        documents = [make_document("a"), make_document("b"), make_document("a")]
        result = _remove_duplicates(documents)
        assert [d.metadata["id"] for d in result] == ["a", "b"]

    def test_empty_input(self):
        assert _remove_duplicates([]) == []


class TestExpandSummaries:
    def test_summary_is_replaced_by_related_chunks(self):
        summary = make_document("s1", content_type="SUMMARY", related=["c1", "c2"])
        underlying = [make_document("c1"), make_document("c2")]

        with patch("rag.retrieval.get_documents_by_ids", return_value=underlying) as fetch:
            result = _expand_summaries([summary])

        fetch.assert_called_once()
        assert set(fetch.call_args[0][0]) == {"c1", "c2"}
        # The summary itself must never survive as a citation.
        assert all(d.metadata["type"] != "SUMMARY" for d in result)
        assert {d.metadata["id"] for d in result} == {"c1", "c2"}

    def test_does_not_refetch_already_present_chunks(self):
        summary = make_document("s1", content_type="SUMMARY", related=["c1"])
        present = make_document("c1")
        with patch("rag.retrieval.get_documents_by_ids") as fetch:
            result = _expand_summaries([summary, present])
        fetch.assert_not_called()
        assert [d.metadata["id"] for d in result] == ["c1"]

    def test_passthrough_without_summaries(self):
        documents = [make_document("a"), make_document("b")]
        assert _expand_summaries(documents) == documents

    def test_expansion_failure_is_survivable(self):
        """A vector DB hiccup during expansion must not fail the query."""
        summary = make_document("s1", content_type="SUMMARY", related=["c1"])
        with patch("rag.retrieval.get_documents_by_ids", side_effect=RuntimeError("qdrant down")):
            result = _expand_summaries([summary, make_document("a")])
        assert [d.metadata["id"] for d in result] == ["a"]


class TestEarlyPrune:
    def test_prunes_to_total_k_by_score(self):
        documents = [make_document(str(i), score=i / 100) for i in range(30)]
        result = _early_prune(documents)
        assert len(result) == 10
        # Highest scores survive.
        assert result[0].metadata["score"] > result[-1].metadata["score"]

    def test_no_prune_when_under_cap(self):
        documents = [make_document("a"), make_document("b")]
        assert _early_prune(documents) == documents


class TestGraphHelpers:
    def test_format_history_respects_limit(self):
        messages = [
            {"role": "user", "content": f"q{i}"} if i % 2 == 0 else {"role": "assistant", "content": f"a{i}"}
            for i in range(10)
        ]
        formatted = format_history(messages)
        # Default limit is 4 messages.
        assert len(formatted.splitlines()) == 4

    def test_format_history_empty(self):
        assert format_history([]) == ""

    def test_citation_shape(self):
        citation = document_to_citation(make_document("a"))
        assert set(citation) == {
            "page_content",
            "type",
            "document_id",
            "document_name",
            "document_url",
            "page",
            "score",
        }

    def test_build_context_numbers_sources(self):
        context = build_context([make_document("a", content="alpha"), make_document("b", content="beta")])
        assert "[1]" in context and "[2]" in context
        assert "alpha" in context and "beta" in context


class TestRetrieveScoping:
    """Tenant isolation must be enforced in the query, not after it."""

    async def test_every_search_carries_owner_filter(self):
        captured: list[dict] = []

        async def fake_search(query, *, k, score_threshold, filter_kwargs=None):
            captured.append(filter_kwargs)
            return []

        with (
            patch("rag.retrieval.collection_available", return_value=True),
            patch("rag.retrieval.asearch", side_effect=fake_search),
        ):
            from rag.retrieval import retrieve

            await retrieve("question", owner_id=42)

        assert captured, "no searches were issued"
        assert all(f["owner_id"] == 42 for f in captured)
        # One search per content type.
        assert {f["type"] for f in captured} == {"TEXT", "TABLE", "SUMMARY", "IMAGE"}

    async def test_document_scope_is_applied(self):
        captured: list[dict] = []

        async def fake_search(query, *, k, score_threshold, filter_kwargs=None):
            captured.append(filter_kwargs)
            return []

        with (
            patch("rag.retrieval.collection_available", return_value=True),
            patch("rag.retrieval.asearch", side_effect=fake_search),
        ):
            from rag.retrieval import retrieve

            await retrieve("question", owner_id=7, document_id="doc-9")

        assert all(f["document_id"] == "doc-9" for f in captured)

    async def test_empty_collection_raises(self):
        with patch("rag.retrieval.collection_available", return_value=False):
            from rag.retrieval import NoOrEmptyCollectionError, retrieve

            with pytest.raises(NoOrEmptyCollectionError):
                await retrieve("question", owner_id=1)

    async def test_one_failing_quark_does_not_sink_the_query(self):
        async def flaky(query, *, k, score_threshold, filter_kwargs=None):
            if filter_kwargs["type"] == "TABLE":
                raise RuntimeError("index corrupt")
            return [make_document(f"{filter_kwargs['type']}-1")]

        with (
            patch("rag.retrieval.collection_available", return_value=True),
            patch("rag.retrieval.asearch", side_effect=flaky),
        ):
            from rag.retrieval import retrieve

            result = await retrieve("question", owner_id=1)

        types = {d.metadata["id"] for d in result}
        assert "TEXT-1" in types
        assert "TABLE-1" not in types
