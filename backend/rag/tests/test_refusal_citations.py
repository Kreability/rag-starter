"""A refusal must not be shown with citations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.documents import Document

from rag.graph import REFUSAL_PHRASE, _generate_node, astream_answer, is_refusal


def _docs(n=3):
    return [
        Document(
            page_content=f"chunk {i}",
            metadata={"id": f"id{i}", "document_id": "d", "document_name": "book.pdf", "page": "1", "type": "TEXT"},
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        (REFUSAL_PHRASE, True),
        ("I can’t answer that from the information I have.", True),  # curly apostrophe
        ("  i can't answer that from the information i have.  ", True),
        ("The token bucket refills at a fixed rate.", False),
        # A long real answer that merely mentions the phrase is not a refusal.
        ("Here is a long, detailed answer. " * 20 + REFUSAL_PHRASE, False),
        ("", False),
    ],
)
def test_is_refusal(text, expected):
    assert is_refusal(text) is expected


class _Chain:
    def __init__(self, text):
        self.text = text

    async def ainvoke(self, *args, **kwargs):
        return SimpleNamespace(content=self.text)

    async def astream(self, *args, **kwargs):
        for part in (self.text[: len(self.text) // 2], self.text[len(self.text) // 2 :]):
            yield SimpleNamespace(content=part)


class _Prompt:
    def __init__(self, text):
        self.text = text

    def __or__(self, _model):
        return _Chain(self.text)


@pytest.mark.asyncio
async def test_generate_node_drops_citations_on_refusal():
    with patch("rag.graph.get_answer_prompt", return_value=_Prompt(REFUSAL_PHRASE)), \
         patch("rag.graph.get_chat_model"), patch("rag.graph._record_query_usage"):
        result = await _generate_node({"question": "q", "documents": _docs()})
    assert result["citations"] == []
    assert result["answer_text"] == REFUSAL_PHRASE


@pytest.mark.asyncio
async def test_generate_node_keeps_citations_on_real_answer():
    with patch("rag.graph.get_answer_prompt", return_value=_Prompt("Tokens refill at a fixed rate.")), \
         patch("rag.graph.get_chat_model"), patch("rag.graph._record_query_usage"):
        result = await _generate_node({"question": "q", "documents": _docs()})
    assert len(result["citations"]) == 3


async def _stream(answer_text):
    async def fake_retrieve(state):
        return {"documents": _docs()}

    async def fake_node(state, config=None):
        return {"language": "en", "rephrased_question": "q"}

    with patch("rag.graph.get_answer_prompt", return_value=_Prompt(answer_text)), \
         patch("rag.graph.get_chat_model"), patch("rag.graph._record_query_usage"), \
         patch("rag.graph._retrieve_node", fake_retrieve), \
         patch("rag.graph._determine_language_node", fake_node), \
         patch("rag.graph._rephrase_node", fake_node):
        return [event async for event in astream_answer("what is x", organization_id="org")]


@pytest.mark.asyncio
async def test_stream_retracts_citations_after_a_refusal():
    events = await _stream(REFUSAL_PHRASE)
    citation_events = [e for e in events if e["type"] == "citations"]
    assert len(citation_events[0]["citations"]) == 3  # sent up front for fast rendering
    assert citation_events[-1]["citations"] == []     # then retracted
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_stream_keeps_citations_on_a_real_answer():
    events = await _stream("Tokens refill at a fixed rate over time.")
    citation_events = [e for e in events if e["type"] == "citations"]
    assert len(citation_events) == 1 and len(citation_events[0]["citations"]) == 3
