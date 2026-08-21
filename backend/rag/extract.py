"""Content extraction.

Collapses the whole upstream `extractor-api-lib` service into one module with
the same deterministic fallback chain:

    Docling (optional extra, best quality + OCR + table structure)
      -> MarkItDown (fast, no torch, handles Office/PDF/HTML)
        -> plain-text decode

Every extractor yields `Piece` objects, the local equivalent of upstream's
`InternalInformationPiece`.
"""

from __future__ import annotations

import base64
import csv
import io
import logging
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from rag.conf import get_config

logger = logging.getLogger(__name__)


@dataclass
class Piece:
    """One extracted unit of content, pre-chunking."""

    content: str
    content_type: str = "TEXT"  # TEXT | TABLE | IMAGE
    page: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return sha256(self.content.encode("utf-8")).hexdigest()


def extract_file(path: Path, name: str) -> list[Piece]:
    """Extract content from a local file, walking the fallback chain."""
    config = get_config().ingestion
    extension = path.suffix.lower()

    if config.prefer_docling:
        pieces = _try_docling(path, name)
        if pieces:
            return pieces

    if extension in {".txt", ".md"}:
        return _extract_plain_text(path)
    if extension == ".csv":
        return _extract_csv(path)

    pieces = _try_markitdown(path, name)
    if pieces:
        return pieces

    if extension == ".pdf":
        pieces = _try_pypdf(path)
        if pieces:
            return pieces

    logger.warning("All extractors failed for '%s'; falling back to raw decode.", name)
    return _extract_plain_text(path)


def _try_docling(path: Path, name: str) -> list[Piece]:
    """Docling extraction with table structure and OCR. Ported from
    `extractor_api_lib/impl/extractors/file_extractors/docling_extractor.py`."""
    try:
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import TableItem, TextItem
    except ImportError:
        logger.debug("Docling not installed; skipping. Install with: uv sync --extra docling")
        return []

    try:
        result = DocumentConverter().convert(str(path))
    except Exception:
        logger.warning("Docling failed on '%s'; falling back.", name, exc_info=True)
        return []

    pieces: list[Piece] = []
    for item, _level in result.document.iterate_items():
        page = str(_resolve_page(item))
        if isinstance(item, TableItem):
            try:
                markdown = item.export_to_markdown()
            except Exception:
                continue
            if any(character.isalnum() for character in markdown):
                pieces.append(Piece(content=markdown, content_type="TABLE", page=page))
        elif hasattr(item, "get_image") or type(item).__name__ == "PictureItem":
            try:
                image = item.get_image(result.document)
                data = image.data if hasattr(image, "data") else image
                pieces.append(
                    Piece(
                        content=base64.b64encode(data).decode("utf-8"),
                        content_type="IMAGE",
                        page=page,
                        metadata={"mime_type": "image/png"},
                    )
                )
            except Exception:
                logger.debug("Failed to extract picture item on page %s.", page, exc_info=True)
        elif isinstance(item, TextItem):
            text = (item.text or "").strip()
            if text:
                pieces.append(Piece(content=text, content_type="TEXT", page=page))

    if pieces:
        logger.info("Docling extracted %d pieces from '%s'.", len(pieces), name)
    return pieces


def _resolve_page(item) -> int:
    """Pull a page number out of Docling provenance; -1 when unknown."""
    provenance = getattr(item, "prov", None)
    if isinstance(provenance, list):
        for entry in provenance:
            page = getattr(entry, "page_no", None)
            if isinstance(page, int):
                return page
    return -1


def _try_markitdown(path: Path, name: str) -> list[Piece]:
    """MarkItDown covers PDF/DOCX/PPTX/XLSX/HTML/EPUB without pulling torch."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        logger.debug("MarkItDown not installed; skipping.")
        return []

    try:
        result = MarkItDown().convert(str(path))
    except Exception:
        logger.warning("MarkItDown failed on '%s'; falling back.", name, exc_info=True)
        return []

    text = (getattr(result, "text_content", "") or "").strip()
    if not text:
        return []
    return _split_markdown_tables(text)


def _split_markdown_tables(text: str) -> list[Piece]:
    """Separate markdown tables from prose so each gets the right content type.

    Tables are retrieved with their own threshold upstream, so keeping them
    distinct materially improves table question answering.
    """
    pieces: list[Piece] = []
    buffer: list[str] = []
    table: list[str] = []

    def flush(lines: list[str], content_type: str) -> None:
        content = "\n".join(lines).strip()
        if content:
            pieces.append(Piece(content=content, content_type=content_type))

    for line in text.splitlines():
        is_table_row = line.lstrip().startswith("|") and line.rstrip().endswith("|")
        if is_table_row:
            if buffer:
                flush(buffer, "TEXT")
                buffer = []
            table.append(line)
        else:
            if table:
                flush(table, "TABLE")
                table = []
            buffer.append(line)
    flush(table, "TABLE")
    flush(buffer, "TEXT")
    return pieces


def _try_pypdf(path: Path) -> list[Piece]:
    """Last-resort PDF text extraction, one piece per page."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    try:
        reader = PdfReader(str(path))
    except Exception:
        logger.warning("pypdf failed on '%s'.", path.name, exc_info=True)
        return []

    pieces = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pieces.append(Piece(content=text, content_type="TEXT", page=str(number)))

        try:
            for image in getattr(page, "images", []):
                data = image.data if hasattr(image, "data") else image
                if data:
                    pieces.append(
                        Piece(
                            content=base64.b64encode(data).decode("utf-8"),
                            content_type="IMAGE",
                            page=str(number),
                            metadata={"mime_type": getattr(image, "name", "image/png") or "image/png"},
                        )
                    )
        except Exception:
            logger.debug("pypdf image extraction failed on page %d.", number, exc_info=True)
    return pieces


def _extract_plain_text(path: Path) -> list[Piece]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return [Piece(content=text, content_type="TEXT")] if text else []


def _extract_csv(path: Path) -> list[Piece]:
    """Render a CSV as a markdown table so the LLM can read it."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return []

    output = io.StringIO()
    header, *body = rows
    output.write("| " + " | ".join(header) + " |\n")
    output.write("| " + " | ".join("---" for _ in header) + " |\n")
    for row in body:
        padded = row + [""] * (len(header) - len(row))
        output.write("| " + " | ".join(padded[: len(header)]) + " |\n")
    return [Piece(content=output.getvalue().strip(), content_type="TABLE")]


# --- remote sources -------------------------------------------------------


def extract_url(url: str) -> list[Piece]:
    """Fetch and extract a single web page. Caller must have SSRF-validated the URL."""
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url, timeout=30, headers={"User-Agent": "rag-system/1.0"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title else url
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    if not text:
        return []
    return [
        Piece(
            content=text,
            content_type="TEXT",
            page=title,
            metadata={"document_url": url, "title": title},
        )
    ]


def extract_sitemap(sitemap_url: str) -> list[Piece]:
    """Walk a sitemap and extract every page it lists.

    Ported from `extractor_api_lib/impl/extractors/sitemap_extractor.py`.
    """
    import requests
    from bs4 import BeautifulSoup

    from rag.security import validate_public_url

    config = get_config().ingestion
    response = requests.get(sitemap_url, timeout=30, headers={"User-Agent": "rag-system/1.0"})
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "xml")
    urls = [loc.get_text(strip=True) for loc in soup.find_all("loc")][: config.sitemap_max_pages]

    pieces: list[Piece] = []
    for url in urls:
        try:
            # Each discovered URL is untrusted too — re-validate before fetching.
            validate_public_url(url)
            pieces.extend(extract_url(url))
        except Exception:
            logger.warning("Skipping sitemap entry '%s'.", url, exc_info=True)
    logger.info("Sitemap '%s' yielded %d pieces from %d URLs.", sitemap_url, len(pieces), len(urls))
    return pieces


def extract_confluence(url: str, space_key: str, token: str, verify_ssl: bool = True) -> list[Piece]:
    """Confluence space ingestion.

    Ported from `extractor_api_lib/impl/extractors/confluence_extractor.py`.
    Requires the optional extra: uv sync --extra confluence
    """
    try:
        from langchain_community.document_loaders import ConfluenceLoader
    except ImportError as exc:
        raise RuntimeError(
            "Confluence support requires the 'confluence' extra: uv sync --extra confluence"
        ) from exc

    loader = ConfluenceLoader(
        url=url, token=token, space_key=space_key, confluence_kwargs={"verify_ssl": verify_ssl}
    )
    return [
        Piece(
            content=document.page_content,
            content_type="TEXT",
            page=document.metadata.get("title", ""),
            metadata={
                "document_url": document.metadata.get("source", url),
                "title": document.metadata.get("title", ""),
            },
        )
        for document in loader.load()
        if document.page_content.strip()
    ]
