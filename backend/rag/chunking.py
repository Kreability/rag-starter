"""Chunking and metadata assembly.

Ported from `admin_api_lib/impl/chunker/text_chunker.py`. Uses the recursive
splitter deliberately: `CharacterTextSplitter` ignores `chunk_size`
(langchain-ai/langchain#10410), which upstream also calls out.
"""

from __future__ import annotations

import logging
from hashlib import sha256

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.conf import get_config
from rag.extract import Piece

logger = logging.getLogger(__name__)


def get_splitter() -> RecursiveCharacterTextSplitter:
    settings = get_config().chunker
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.max_size,
        chunk_overlap=settings.overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def pieces_to_documents(
    pieces: list[Piece], *, document_id: str, owner_id: int, document_name: str, document_url: str
) -> list[Document]:
    """Chunk extracted pieces into LangChain documents carrying full metadata.

    Tables are never split: a half table is worse than a long one.
    """
    splitter = get_splitter()
    documents: list[Document] = []

    for piece in pieces:
        base_metadata = {
            "document_id": document_id,
            "owner_id": owner_id,
            "document_name": document_name,
            "document_url": piece.metadata.get("document_url", document_url),
            "type": piece.content_type,
            "page": piece.page or "",
            "related": [],
            **{k: v for k, v in piece.metadata.items() if k != "document_url"},
        }

        if piece.content_type == "TABLE":
            texts = [piece.content]
        else:
            texts = splitter.split_text(piece.content)

        for position, text in enumerate(texts):
            text = text.strip()
            if not text:
                continue
            metadata = dict(base_metadata)
            # The id must be unique per chunk, not per content: a document that
            # repeats a paragraph (boilerplate headers, repeated table rows)
            # would otherwise hash to one id and silently overwrite itself in
            # the vector store, losing every duplicate but the last.
            metadata["id"] = sha256(
                f"{document_id}:{piece.content_type}:{piece.page}:{position}:{text}".encode()
            ).hexdigest()
            documents.append(Document(page_content=text, metadata=metadata))

    logger.info("Chunked %d pieces into %d documents.", len(pieces), len(documents))
    return documents
