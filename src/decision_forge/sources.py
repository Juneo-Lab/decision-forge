"""Versioned source adapters with character spans in a normalized text snapshot."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from decision_forge.io import MAX_SOURCE_BYTES, canonical, digest, read_yaml
from decision_forge.models import Contract, Identifier, ensure_unique


class AccessMetadata(Contract):
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    allowed_roles: list[str] = Field(default_factory=list)
    owner: str | None = None


class SourceRequest(Contract):
    id: Identifier
    path: str
    version: str = Field(min_length=1)
    adapter: Literal["text", "pdf", "workflow"] = "text"
    access: AccessMetadata = Field(default_factory=AccessMetadata)


class CorpusSpec(Contract):
    schema_version: Literal["1.0"] = "1.0"
    sources: list[SourceRequest] = Field(min_length=1)


class Span(Contract):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    locator: str


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
    version = "pypdf-text-v1"

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
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 10_000_000:
                raise ValueError("PDF page exceeds the decoded content limit")
            value = page.extract_text() or ""
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
        # JSON round-trip rejects non-JSON YAML values and cyclic aliases.
        data = json.loads(canonical(data))
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
                segments.append((pointer, value if isinstance(value, str) else canonical(value)))

        visit(data, "")
        text = ""
        spans = []
        for pointer, value in segments:
            start = len(text)
            text += f"{pointer}: {value}\n"
            spans.append(Span(start=start, end=len(text), locator=f"json-pointer:{pointer}"))
        return text, spans


ADAPTERS: dict[str, SourceAdapter] = {"text": TextAdapter(), "pdf": PDFAdapter(), "workflow": WorkflowAdapter()}


def ingest_manifest(path: Path, max_total_bytes: int = 32_000_000) -> list[Document]:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source manifest exceeds the size limit")
    spec = CorpusSpec.model_validate(read_yaml(path))
    ensure_unique([source.id for source in spec.sources], "source IDs")
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
        adapter = ADAPTERS[source.adapter]
        text, spans = adapter.extract(source_path)
        if not text.strip() or len(text.encode()) > MAX_SOURCE_BYTES:
            raise ValueError(f"Empty or oversized normalized source: {source.id}")
        result.append(
            Document(
                id=source.id,
                version=source.version,
                adapter=source.adapter,
                adapter_version=adapter.version,
                raw_hash=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                content_hash=digest(text),
                text=text,
                spans=spans,
                access=source.access,
            )
        )
    return result


def locate(document: Document, start: int, end: int) -> str:
    if start < 0 or end <= start or end > len(document.text):
        raise ValueError("Evidence span is outside the source")
    return " | ".join(span.locator for span in document.spans if span.start < end and span.end > start)
