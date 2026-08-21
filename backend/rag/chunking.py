"""Chunking and metadata assembly.

Ported from `admin_api_lib/impl/chunker/text_chunker.py`. Uses structure-aware
splitting when the content has markdown headers, falling back to the recursive
character splitter for unstructured text. Tables are never split.
"""

from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from rag.conf import get_config
from rag.extract import Piece

logger = logging.getLogger(__name__)

_MARKDOWN_HEADERS = [
    ("#", "Header 1"),
    ("##", "Header 2"),
    ("###", "Header 3"),
]

_RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def get_splitter() -> RecursiveCharacterTextSplitter:
    settings = get_config().chunker
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.max_size,
        chunk_overlap=settings.overlap,
        length_function=len,
        separators=list(_RECURSIVE_SEPARATORS),
    )


def _looks_like_markdown(text: str) -> bool:
    lines = text.splitlines()
    for line in lines[:30]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return True
    return False


def _heading_path(metadata: dict[str, Any]) -> str:
    parts = [metadata[key] for key in ("Header 1", "Header 2", "Header 3") if key in metadata]
    return " > ".join(parts) if parts else ""


def _chunk_with_structure(content: str, settings) -> list[tuple[str, dict]]:
    if _looks_like_markdown(content):
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=list(_MARKDOWN_HEADERS),
            strip_headers=False,
        )
        sections = md_splitter.split_text(content)

        result: list[tuple[str, dict]] = []
        for doc in sections:
            text = doc.page_content.strip()
            if not text:
                continue

            path = _heading_path(doc.metadata)
            extra = {"section_path": path} if path else {}

            if len(text) > settings.max_size:
                sub_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=settings.max_size,
                    chunk_overlap=settings.overlap,
                    length_function=len,
                    separators=list(_RECURSIVE_SEPARATORS),
                )
                for sub in sub_splitter.split_text(text):
                    result.append((sub, extra))
            else:
                result.append((text, extra))
        return result

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.max_size,
        chunk_overlap=settings.overlap,
        length_function=len,
        separators=list(_RECURSIVE_SEPARATORS),
    )
    return [(text, {}) for text in splitter.split_text(content)]


def pieces_to_documents(
    pieces: list[Piece],
    *,
    document_id: str,
    owner_id: int,
    document_name: str,
    storage_key: str = "",
    document_url: str = "",
) -> list[Document]:
    """Chunk extracted pieces into LangChain documents carrying full metadata.

    Tables are never split: a half table is worse than a long one.
    Markdown-like content is split on headers first, then recursively for
    oversized sections, preserving the heading hierarchy as `section_path`.
    """
    settings = get_config().chunker
    documents: list[Document] = []

    for piece in pieces:
        base_metadata = {
            "document_id": document_id,
            "owner_id": owner_id,
            "document_name": document_name,
            "storage_key": storage_key,
            "document_url": document_url,
            "type": piece.content_type,
            "page": piece.page or "",
            "related": [],
        }

        if piece.content_type == "TABLE":
            if not any(character.isalnum() for character in piece.content):
                continue
            chunks = [(piece.content, {})]
        else:
            chunks = _chunk_with_structure(piece.content, settings)

        for position, (text, extra) in enumerate(chunks):
            text = text.strip()
            if not text:
                continue
            metadata = dict(base_metadata)
            metadata.update(extra)
            metadata["id"] = sha256(
                f"{document_id}:{piece.content_type}:{piece.page}:{position}:{text}".encode()
            ).hexdigest()
            documents.append(Document(page_content=text, metadata=metadata))

    logger.info("Chunked %d pieces into %d documents.", len(pieces), len(documents))
    return documents
