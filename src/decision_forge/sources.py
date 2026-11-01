"""Versioned source adapters with character spans in a normalized text snapshot."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import re
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol

from pydantic import Field

from decision_forge.document_formats import (
    Fragment,
    collect,
    csv_fragments,
    dependency,
    html_fragments,
    office_fragments,
)
from decision_forge.io import MAX_SOURCE_BYTES, canonical, digest, read_yaml
from decision_forge.models import Contract, Identifier, ensure_unique
from decision_forge.ocr import OCRConfig, OCRRunner, image_png

PDF_RENDER_LOCK = RLock()


class AccessMetadata(Contract):
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    allowed_roles: list[str] = Field(default_factory=list)
    owner: str | None = None


class SourceRequest(Contract):
    id: Identifier
    path: str
    version: str = Field(min_length=1)
    adapter: Literal["auto", "text", "pdf", "workflow", "docx", "xlsx", "pptx", "html", "csv", "image"] = "auto"
    access: AccessMetadata = Field(default_factory=AccessMetadata)
    ocr: OCRConfig | None = None


class CorpusSpec(Contract):
    schema_version: Literal["1.0"] = "1.0"
    sources: list[SourceRequest] = Field(min_length=1)


class Span(Contract):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    locator: str
    method: str = "native"
    confidence: float | None = Field(default=None, ge=0, le=1)
    box: list[float] | None = None


class Document(Contract):
    id: Identifier
    version: str
    adapter: str
    adapter_version: str
    raw_hash: str
    content_hash: str
    text: str
    spans: list[Span]
    access: AccessMetadata
    extraction: dict[str, object] = Field(default_factory=dict)


class SourceAdapter(Protocol):
    version: str

    def extract(self, path: Path) -> tuple[str, list[Span]]: ...


class TextAdapter:
    version = "text-v1"

    def extract(self, path: Path) -> tuple[str, list[Span]]:
        text = path.read_text(encoding="utf-8-sig")
        spans = []
        offset = 0
        section = "document"
        for number, line in enumerate(text.splitlines(keepends=True), 1):
            if re.match(r"^#{1,6}\s+", line):
                section = line.strip()
            spans.append(Span(start=offset, end=offset + len(line), locator=f"line:{number};section:{section}"))
            offset += len(line)
        return text, spans


class PDFAdapter:
    version = "pypdf-ocr-v2"

    def __init__(self, runner: OCRRunner | None = None):
        self.runner = runner

    def extract(self, path: Path) -> tuple[str, list[Span]]:
        try:
            pypdf = importlib.import_module("pypdf")
        except ImportError as exc:
            raise ValueError("PDF sources require the 'documents' installation extra") from exc
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs must be decrypted before ingestion")
        if len(reader.pages) > 500:
            raise ValueError("PDF exceeds the 500-page ingestion limit")
        text = ""
        spans = []
        ocr_pages = 0
        for number, page in enumerate(reader.pages, 1):
            value = ""
            if not (self.runner and self.runner.config.mode == "always"):
                content = page.get_contents()
                if content is not None and len(content.get_data()) > 10_000_000:
                    raise ValueError("PDF page exceeds the decoded content limit")
                value = normalize_newlines(page.extract_text() or "")
            if self.runner and (self.runner.config.mode == "always" or not value.strip()):
                ocr_pages += 1
                if ocr_pages > self.runner.config.max_pages:
                    raise ValueError("PDF exceeds the configured OCR page limit")
                pdfium = dependency("pypdfium2", "ocr")
                with (
                    PDF_RENDER_LOCK,
                    pdfium.PdfDocument(str(path)) as raster,
                    closing(raster[number - 1]) as raster_page,
                ):
                    scale = self.runner.config.dpi / 72
                    width, height = raster_page.get_size()
                    if math.ceil(width * scale) * math.ceil(height * scale) > self.runner.config.max_pixels:
                        raise ValueError("PDF page exceeds the configured OCR pixel limit")
                    bitmap = raster_page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        try:
                            png = image_png(image, self.runner.config)
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                page_text, page_spans = join_fragments(self.runner.recognize(png, f"page:{number}"))
                offset = len(text)
                for span in page_spans:
                    span.start += offset
                    span.end += offset
                text += page_text
                spans.extend(page_spans)
                if len(text.encode()) > MAX_SOURCE_BYTES:
                    raise ValueError("Extracted PDF text exceeds the source limit")
                continue
            if not value.strip():
                raise ValueError(f"PDF page {number} has no extractable text; provide an OCR text source")
            start = len(text)
            text += value + "\n"
            spans.append(Span(start=start, end=len(text), locator=f"page:{number}"))
            if len(text.encode()) > MAX_SOURCE_BYTES:
                raise ValueError("Extracted PDF text exceeds the source limit")
        return text, spans


class WorkflowAdapter:
    """Normalize JSON/YAML leaf values with their RFC 6901 JSON pointers."""

    version = "workflow-leaves-v1"

    def extract(self, path: Path) -> tuple[str, list[Span]]:
        data = read_yaml(path)
        try:
            # JSON round-trip rejects non-JSON YAML values and cyclic aliases.
            data = json.loads(canonical(data))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Workflow source contains values that are not JSON (for example YAML dates); quote them as strings"
            ) from exc
        if not isinstance(data, (dict, list)):
            raise ValueError("A workflow source must be a JSON/YAML object or array")
        segments: list[tuple[str, str]] = []

        def visit(value: object, pointer: str, depth: int = 0) -> None:
            if depth > 64:
                raise ValueError("Workflow nesting exceeds 64 levels")
            if isinstance(value, dict):
                for key, child in value.items():
                    escaped = str(key).replace("~", "~0").replace("/", "~1")
                    visit(child, pointer + "/" + escaped, depth + 1)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, pointer + "/" + str(index), depth + 1)
            else:
                segments.append((pointer, normalize_newlines(value) if isinstance(value, str) else canonical(value)))

        visit(data, "")
        text = ""
        spans = []
        for pointer, value in segments:
            start = len(text)
            text += f"{pointer}: {value}\n"
            spans.append(Span(start=start, end=len(text), locator=f"json-pointer:{pointer}"))
        return text, spans


ADAPTERS: dict[str, SourceAdapter] = {"text": TextAdapter(), "pdf": PDFAdapter(), "workflow": WorkflowAdapter()}

SUFFIXES = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".json": "workflow",
    ".yaml": "workflow",
    ".yml": "workflow",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".html": "html",
    ".htm": "html",
    ".csv": "csv",
    ".tsv": "csv",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
}


def normalize_newlines(text: str) -> str:
    """Use ``\\n`` line endings so stored snapshots read back with an identical content hash."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def join_fragments(fragments: list[Fragment]) -> tuple[str, list[Span]]:
    text = ""
    spans = []
    for fragment in collect(iter(fragments)):
        start = len(text)
        text += normalize_newlines(fragment.text) + "\n"
        spans.append(
            Span(
                start=start,
                end=len(text),
                locator=fragment.locator,
                method=fragment.method,
                confidence=fragment.confidence,
                box=fragment.box,
            )
        )
    return text, spans


def parse_image(path: Path, runner: OCRRunner | None) -> tuple[str, list[Span]]:
    if runner is None:
        raise ValueError("Image sources require an explicit OCR configuration")
    pil = dependency("PIL.Image", "ocr")
    fragments = []
    with pil.open(path) as image:
        frames = getattr(image, "n_frames", 1)
        if frames > runner.config.max_pages:
            raise ValueError("Image exceeds the configured OCR frame limit")
        for frame in range(frames):
            image.seek(frame)
            fragments.extend(runner.recognize(image_png(image, runner.config), f"frame:{frame + 1}"))
    return join_fragments(fragments)


def ingest_manifest(
    path: Path, max_total_bytes: int = 32_000_000, *, allow_external_ocr: bool = False
) -> list[Document]:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source manifest exceeds the size limit")
    spec = CorpusSpec.model_validate(read_yaml(path))
    ensure_unique([source.id for source in spec.sources], "source IDs")
    if not allow_external_ocr and any(s.ocr and s.ocr.backend == "openai" for s in spec.sources):
        raise ValueError("OCR service calls require --allow-external-ocr (including localhost)")
    root = path.parent.resolve()
    result = []
    total = 0
    for source in spec.sources:
        source_path = (root / source.path).resolve()
        if not source_path.is_relative_to(root):
            raise ValueError(f"Source escapes manifest directory: {source.id}")
        size = source_path.stat().st_size
        total += size
        if size > MAX_SOURCE_BYTES or total > max_total_bytes:
            raise ValueError("Source corpus exceeds ingestion byte limits")
        kind = SUFFIXES.get(source_path.suffix.lower()) if source.adapter == "auto" else source.adapter
        if kind is None:
            raise ValueError("Unsupported source format; convert legacy DOC/XLS/PPT to DOCX/XLSX/PPTX")
        if source.ocr and kind not in {"image", "pdf"}:
            raise ValueError("OCR configuration applies only to PDF and image sources")
        runner = OCRRunner(source.ocr, root, allow_external=allow_external_ocr) if source.ocr else None
        metadata: dict[str, object] = {}
        package = {
            "pdf": "pypdf",
            "docx": "python-docx",
            "xlsx": "openpyxl",
            "pptx": "python-pptx",
            "html": "beautifulsoup4",
        }.get(kind)
        if package:
            try:
                metadata["parser_version"] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                raise ValueError(f"This source requires the 'documents' installation extra ({package})") from None
        if runner:
            metadata["ocr"] = runner.identity
            metadata["review_required"] = True
            if kind == "pdf":
                dependency("pypdfium2", "ocr")
                metadata["renderer_version"] = importlib.metadata.version("pypdfium2")
        adapter_version = f"{kind}-v1"
        if kind in ADAPTERS:
            adapter = PDFAdapter(runner) if kind == "pdf" else ADAPTERS[kind]
            adapter_version = adapter.version
            text, spans = adapter.extract(source_path)
        elif kind == "image":
            text, spans = parse_image(source_path, runner)
        else:
            if kind in {"docx", "xlsx", "pptx"}:
                fragments = office_fragments(source_path, kind)
                metadata["scope"] = "native text and tables; embedded images, charts and drawing text are not OCRed"
            elif kind == "html":
                fragments = html_fragments(source_path)
                metadata["scope"] = (
                    "static parsed text; scripts, styles and hidden attributes excluded; CSS not evaluated"
                )
            else:
                fragments = csv_fragments(source_path)
            text, spans = join_fragments(collect(fragments))
        if not text.strip() or len(text.encode()) > MAX_SOURCE_BYTES:
            raise ValueError(f"Empty or oversized normalized source: {source.id}")
        result.append(
            Document(
                id=source.id,
                version=source.version,
                adapter=kind,
                adapter_version=adapter_version,
                raw_hash=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                content_hash=digest(text),
                text=text,
                spans=spans,
                access=source.access,
                extraction=metadata,
            )
        )
    return result


def locate(document: Document, start: int, end: int) -> str:
    if start < 0 or end <= start or end > len(document.text):
        raise ValueError("Evidence span is outside the source")
    return " | ".join(span.locator for span in document.spans if span.start < end and span.end > start)
