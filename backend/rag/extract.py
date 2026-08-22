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
from rag.quality import IngestionDiagnostics

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


def extract_file(path: Path, name: str) -> tuple[list[Piece], IngestionDiagnostics]:
    """Extract content from a local file, walking the fallback chain."""
    config = get_config().ingestion
    extension = path.suffix.lower()
    diagnostics = IngestionDiagnostics()

    if config.prefer_docling:
        pieces, diag = _try_docling(path, name)
        if pieces:
            diagnostics = diag
            diagnostics.extractor_used = "docling"
            return pieces, diagnostics

    if extension in {".txt", ".md"}:
        pieces, diag = _extract_plain_text(path)
        diagnostics = diag
        diagnostics.extractor_used = "plain_text"
        return pieces, diagnostics
    if extension == ".csv":
        pieces, diag = _extract_csv(path)
        diagnostics = diag
        diagnostics.extractor_used = "csv_parser"
        return pieces, diagnostics

    pieces, diag = _try_markitdown(path, name)
    if pieces:
        diagnostics = diag
        diagnostics.extractor_used = "markitdown"
        return pieces, diagnostics

    if extension == ".pdf":
        pieces, diag = _try_pypdf(path)
        if pieces:
            diagnostics = diag
            diagnostics.extractor_used = "pypdf"
            return pieces, diagnostics

    logger.warning("All extractors failed for '%s'; falling back to raw decode.", name)
    pieces, diag = _extract_plain_text(path)
    diagnostics = diag
    diagnostics.extractor_used = "plain_text_fallback"
    return pieces, diagnostics


def _try_docling(path: Path, name: str) -> tuple[list[Piece], IngestionDiagnostics]:
    """Docling extraction with table structure and OCR."""
    config = get_config().ingestion
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrOptions
        from docling.document_converter import DocumentConverter, FormatOption
        from docling_core.types.doc import TableItem, TextItem
    except ImportError:
        logger.debug("Docling not installed; skipping. Install with: uv sync --extra docling")
        return [], IngestionDiagnostics()

    try:
        pipeline_options = PdfPipelineOptions()
        if config.ocr_enabled:
            languages = [lang.strip() for lang in config.ocr_languages.split(",") if lang.strip()]
            pipeline_options.ocr_options = OcrOptions(lang=languages or ["eng"])

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: FormatOption(pipeline_options=pipeline_options),
            }
        )
        result = converter.convert(str(path))
    except Exception:
        logger.warning("Docling failed on '%s'; falling back.", name, exc_info=True)
        return [], IngestionDiagnostics()

    pieces: list[Piece] = []
    diagnostics = IngestionDiagnostics()
    seen_pages: set[int] = set()

    for item, _level in result.document.iterate_items():
        page_no = _resolve_page(item)
        seen_pages.add(page_no)
        page = str(page_no)

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

    diagnostics.pages_detected = len(seen_pages) if seen_pages else 0
    for piece in pieces:
        if piece.page and piece.page not in ("-1",):
            try:
                page_int = int(piece.page)
                seen_pages.add(page_int)
            except ValueError:
                pass
        if piece.content_type == "TEXT":
            diagnostics.text_chunks += 1
            diagnostics.total_extracted_chars += len(piece.content)
            if piece.page and piece.page not in ("-1",):
                diagnostics.pages_with_text += 1
        elif piece.content_type == "TABLE":
            diagnostics.table_chunks += 1
        elif piece.content_type == "IMAGE":
            diagnostics.image_chunks += 1

    diagnostics.pages_without_text = diagnostics.pages_detected - diagnostics.pages_with_text
    if config.ocr_enabled and diagnostics.pages_without_text > 0 and diagnostics.text_chunks > 0:
        diagnostics.ocr_used = True
        diagnostics.ocr_language = config.ocr_languages

    if pieces:
        logger.info("Docling extracted %d pieces from '%s'.", len(pieces), name)
    return pieces, diagnostics


def _resolve_page(item) -> int:
    """Pull a page number out of Docling provenance; -1 when unknown."""
    provenance = getattr(item, "prov", None)
    if isinstance(provenance, list):
        for entry in provenance:
            page = getattr(entry, "page_no", None)
            if isinstance(page, int):
                return page
    return -1


def _try_markitdown(path: Path, name: str) -> tuple[list[Piece], IngestionDiagnostics]:
    """MarkItDown covers PDF/DOCX/PPTX/XLSX/HTML/EPUB without pulling torch."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        logger.debug("MarkItDown not installed; skipping.")
        return [], IngestionDiagnostics()

    try:
        result = MarkItDown().convert(str(path))
    except Exception:
        logger.warning("MarkItDown failed on '%s'; falling back.", name, exc_info=True)
        return [], IngestionDiagnostics()

    text = (getattr(result, "text_content", "") or "").strip()
    if not text:
        return [], IngestionDiagnostics()
    pieces = _split_markdown_tables(text)
    diagnostics = IngestionDiagnostics()
    diagnostics.total_extracted_chars = len(text)
    diagnostics.extractor_used = "markitdown"
    for piece in pieces:
        if piece.content_type == "TEXT":
            diagnostics.text_chunks += 1
        elif piece.content_type == "TABLE":
            diagnostics.table_chunks += 1
    return pieces, diagnostics


def _split_markdown_tables(text: str) -> list[Piece]:
    """Separate markdown tables from prose so each gets the right content type."""
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


def _try_pypdf(path: Path) -> tuple[list[Piece], IngestionDiagnostics]:
    """Last-resort PDF text extraction, one piece per page."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return [], IngestionDiagnostics()
    try:
        reader = PdfReader(str(path))
    except Exception:
        logger.warning("pypdf failed on '%s'.", path.name, exc_info=True)
        return [], IngestionDiagnostics()

    pieces = []
    diagnostics = IngestionDiagnostics()
    diagnostics.pages_detected = len(reader.pages)

    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pieces.append(Piece(content=text, content_type="TEXT", page=str(number)))
            diagnostics.pages_with_text += 1
            diagnostics.total_extracted_chars += len(text)

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

    diagnostics.pages_without_text = diagnostics.pages_detected - diagnostics.pages_with_text
    for piece in pieces:
        if piece.content_type == "TEXT":
            diagnostics.text_chunks += 1
        elif piece.content_type == "TABLE":
            diagnostics.table_chunks += 1
        elif piece.content_type == "IMAGE":
            diagnostics.image_chunks += 1
    diagnostics.extractor_used = "pypdf"
    return pieces, diagnostics


def _extract_plain_text(path: Path) -> tuple[list[Piece], IngestionDiagnostics]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    diagnostics = IngestionDiagnostics()
    diagnostics.total_extracted_chars = len(text)
    if text:
        diagnostics.pages_detected = 1
        diagnostics.pages_with_text = 1
        diagnostics.text_chunks = 1
        diagnostics.extractor_used = "plain_text"
        return [Piece(content=text, content_type="TEXT")], diagnostics
    return [], diagnostics


def _extract_csv(path: Path) -> tuple[list[Piece], IngestionDiagnostics]:
    """Render a CSV as a markdown table so the LLM can read it."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return [], IngestionDiagnostics()

    output = io.StringIO()
    header, *body = rows
    output.write("| " + " | ".join(header) + " |\n")
    output.write("| " + " | ".join("---" for _ in header) + " |\n")
    for row in body:
        padded = row + [""] * (len(header) - len(row))
        output.write("| " + " | ".join(padded[: len(header)]) + " |\n")
    content = output.getvalue().strip()
    diagnostics = IngestionDiagnostics()
    diagnostics.pages_detected = 1
    diagnostics.table_chunks = 1
    diagnostics.extractor_used = "csv_parser"
    return [Piece(content=content, content_type="TABLE")], diagnostics


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
    """Walk a sitemap and extract every page it lists."""
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
            validate_public_url(url)
            pieces.extend(extract_url(url))
        except Exception:
            logger.warning("Skipping sitemap entry '%s'.", url, exc_info=True)
    logger.info("Sitemap '%s' yielded %d pieces from %d URLs.", sitemap_url, len(pieces), len(urls))
    return pieces


def extract_confluence(url: str, space_key: str, token: str, verify_ssl: bool = True) -> list[Piece]:
    """Confluence space ingestion."""
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
