"""Optional office parsers and offline web/tabular normalization."""

from __future__ import annotations

import csv
import importlib
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decision_forge.io import MAX_SOURCE_BYTES


@dataclass
class Fragment:
    text: str
    locator: str
    method: str = "native"
    confidence: float | None = None
    box: list[float] | None = None


def dependency(module: str, extra: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        raise ValueError(f"This source requires the '{extra}' installation extra ({module})") from None


def check_office_archive(path: Path) -> None:
    """Bound expansion before an optional parser opens an OOXML container."""
    try:
        with zipfile.ZipFile(path) as archive:
            items = archive.infolist()
            if len(items) > 5000 or sum(item.file_size for item in items) > 50_000_000:
                raise ValueError("Office archive exceeds expanded size/member limits")
            if any(item.flag_bits & 1 for item in items):
                raise ValueError("Encrypted office archives must be decrypted before ingestion")
    except zipfile.BadZipFile:
        raise ValueError("Invalid office archive; convert legacy/encrypted files to OOXML first") from None


def office_fragments(path: Path, kind: str) -> Iterator[Fragment]:
    check_office_archive(path)
    if kind == "docx":
        docx = dependency("docx", "documents")
        document = docx.Document(path)

        def walk(container: Any, prefix: str) -> Iterator[Fragment]:
            for index, item in enumerate(container.iter_inner_content(), 1):
                if hasattr(item, "rows"):
                    for row_index, row in enumerate(item.rows, 1):
                        seen = set()
                        for col, cell in enumerate(row.cells, 1):
                            if cell._tc in seen:
                                continue
                            seen.add(cell._tc)
                            yield from walk(cell, f"{prefix};table:{index};row:{row_index};column:{col}")
                else:
                    yield Fragment(item.text, f"{prefix};paragraph:{index}")

        yield from walk(document, "body")
        seen_parts = set()
        for index, section in enumerate(document.sections, 1):
            for name in (
                "header",
                "first_page_header",
                "even_page_header",
                "footer",
                "first_page_footer",
                "even_page_footer",
            ):
                part = getattr(section, name)
                if part.is_linked_to_previous or part.part.partname in seen_parts:
                    continue
                seen_parts.add(part.part.partname)
                yield from walk(part, f"section:{index};{name}")
    elif kind == "xlsx":
        openpyxl = dependency("openpyxl", "documents")
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        count = 0
        try:
            for sheet in workbook:
                if (sheet.max_row or 0) * (sheet.max_column or 0) > 200_000:
                    raise ValueError("Spreadsheet exceeds the 200,000-cell traversal limit")
                # Third-party writers can under-report dimensions; stream the actual XML.
                sheet.reset_dimensions()
                for row in sheet.iter_rows():
                    count += max(1, len(row))
                    if count > 200_000:
                        raise ValueError("Spreadsheet exceeds the 200,000-cell traversal limit")
                    for cell in row:
                        if cell.value is not None:
                            value = str(cell.value)
                            if cell.data_type == "f":
                                value = f"[formula, not evaluated] {value}"
                            yield Fragment(f"{cell.coordinate}: {value}", f"sheet:{sheet.title};cell:{cell.coordinate}")
        finally:
            workbook.close()
    elif kind == "pptx":
        pptx = dependency("pptx", "documents")
        presentation = pptx.Presentation(path)
        if len(presentation.slides) > 500:
            raise ValueError("Presentation exceeds the 500-slide limit")

        def shapes(items: Any, prefix: str) -> Iterator[Fragment]:
            for index, shape in enumerate(items, 1):
                locator = f"{prefix};shape:{index}"
                if hasattr(shape, "shapes"):
                    yield from shapes(shape.shapes, locator)
                if shape.has_text_frame:
                    for para, paragraph in enumerate(shape.text_frame.paragraphs, 1):
                        yield Fragment(paragraph.text, f"{locator};paragraph:{para}")
                if shape.has_table:
                    for row_index, row in enumerate(shape.table.rows, 1):
                        for col, cell in enumerate(row.cells, 1):
                            if not cell.is_spanned:
                                yield Fragment(cell.text, f"{locator};row:{row_index};column:{col}")

        for index, slide in enumerate(presentation.slides, 1):
            yield from shapes(slide.shapes, f"slide:{index}")
            if slide.has_notes_slide:
                frame = slide.notes_slide.notes_text_frame
                if frame is not None:
                    yield Fragment(frame.text, f"slide:{index};notes")
    else:
        raise ValueError("Unsupported office format")


def csv_fragments(path: Path) -> Iterator[Fragment]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        try:
            for row_index, row in enumerate(reader, 1):
                for column, value in enumerate(row, 1):
                    yield Fragment(value, f"row:{row_index};column:{column}")
        except csv.Error:
            raise ValueError("Invalid CSV/TSV or a field exceeds the CSV parser limit") from None


def html_fragments(path: Path) -> Iterator[Fragment]:
    bs4 = dependency("bs4", "documents")
    soup = bs4.BeautifulSoup(path.read_text(encoding="utf-8-sig"), "html.parser")
    for item in soup(["script", "style", "template", "noscript", "head"]):
        item.decompose()
    for index, node in enumerate(soup.find_all(string=True), 1):
        if isinstance(node, (bs4.Comment, bs4.Doctype)):
            continue
        if any(parent.has_attr("hidden") for parent in node.parents if hasattr(parent, "has_attr")):
            continue
        # Text-node positions describe the parsed DOM, not browser-rendered coordinates.
        yield Fragment(str(node).strip(), f"html:text-node:{index};tag:{node.parent.name}")


def collect(fragments: Iterator[Fragment]) -> list[Fragment]:
    result: list[Fragment] = []
    total = 0
    for fragment in fragments:
        if not fragment.text.strip():
            continue
        total += len(fragment.text.encode()) + 1
        if total > MAX_SOURCE_BYTES or len(result) >= 100_000:
            raise ValueError("Normalized source exceeds text/fragment limits")
        result.append(fragment)
    return result
