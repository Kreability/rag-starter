"""NVIDIA retrieval embeddings must send the right input_type."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.llm import NvidiaEmbeddings


def _embedder():
    embedder = NvidiaEmbeddings(model="nvidia/nv-embedqa-e5-v5", api_key="nvapi-test", base_url="http://x", batch_size=2)
    reply = lambda n: SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2]) for _ in range(n)])  # noqa: E731
    embedder._client = MagicMock()
    embedder._client.embeddings.create.side_effect = lambda input, **kw: reply(len(input))
    embedder._aclient = MagicMock()
    embedder._aclient.embeddings.create = AsyncMock(side_effect=lambda input, **kw: reply(len(input)))
    return embedder


def test_queries_use_query_input_type():
    embedder = _embedder()
    assert embedder.embed_query("hello") == [0.1, 0.2]
    assert embedder._client.embeddings.create.call_args.kwargs["extra_body"]["input_type"] == "query"


def test_documents_use_passage_input_type_and_are_batched():
    embedder = _embedder()
    vectors = embedder.embed_documents(["a", "b", "c"])
    assert len(vectors) == 3
    calls = embedder._client.embeddings.create.call_args_list
    assert len(calls) == 2  # batch_size=2 -> 2 + 1
    assert all(c.kwargs["extra_body"]["input_type"] == "passage" for c in calls)


@pytest.mark.asyncio
async def test_async_query_uses_query_input_type():
    embedder = _embedder()
    await embedder.aembed_query("hello")
    assert embedder._aclient.embeddings.create.call_args.kwargs["extra_body"]["input_type"] == "query"


def test_missing_key_is_a_clear_error():
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        NvidiaEmbeddings(model="m", api_key="", base_url="http://x")


def test_embedder_reuses_the_chat_key_when_it_has_none(monkeypatch):
    from rag import conf, llm

    monkeypatch.setenv("EMBEDDER_PROVIDER", "nvidia")
    monkeypatch.setenv("EMBEDDER_MODEL", "nvidia/nv-embedqa-e5-v5")
    monkeypatch.setenv("EMBEDDER_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "nvapi-shared")
    conf.get_config.cache_clear()
    llm.get_embedder.cache_clear()
    try:
        embedder = llm.get_embedder()
        assert isinstance(embedder, NvidiaEmbeddings)
        assert embedder._client.api_key == "nvapi-shared"
    finally:
        conf.get_config.cache_clear()
        llm.get_embedder.cache_clear()
