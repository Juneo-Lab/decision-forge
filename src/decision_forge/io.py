"""Local inputs, canonical hashes, and atomic artifact writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from decision_forge.models import Project

MAX_SOURCE_BYTES = 2_000_000
# Halfwidth and Fullwidth Forms contains halfwidth Hangul at U+FFA0–U+FFDC.
_CJK_NEIGHBOR = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uff00-\uff9f\uffdd-\uffef]"
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def collapse_whitespace(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace for evidence matching and map each character to its original offset."""
    collapsed: list[str] = []
    positions: list[int] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            end = index
            while end < len(text) and text[end].isspace():
                end += 1
            # Preserve a space between ordinary tokens. CJK-adjacent runs drop out,
            # as do leading and trailing runs.
            if collapsed and end < len(text) and not _CJK_NEIGHBOR.search(collapsed[-1] + text[end]):
                collapsed.append(" ")
                positions.append(index)
            index = end
            continue
        collapsed.append(text[index])
        positions.append(index)
        index += 1
    return "".join(collapsed), positions


def _normalized_quote(quote: str) -> tuple[str, tuple[int, ...]]:
    normalized, positions = collapse_whitespace(quote)
    # A gap between two retained non-space characters marks elided CJK whitespace.
    # A gap after a normalized space comes from the existing space collapse instead.
    gaps = tuple(
        i
        for i in range(1, len(positions))
        if positions[i] > positions[i - 1] + 1 and " " not in normalized[i - 1 : i + 1]
    )
    return normalized, gaps


def _contains_quote_gaps(positions: list[int], start: int, quote_gaps: tuple[int, ...]) -> bool:
    return all(positions[start + i] > positions[start + i - 1] + 1 for i in quote_gaps)


def evidence_quote_matches(text: str, quote: str) -> bool:
    """Check a bound evidence span without accepting whitespace invented by the quote."""
    normalized_text, positions = collapse_whitespace(text)
    normalized_quote, gaps = _normalized_quote(quote)
    return normalized_text == normalized_quote and _contains_quote_gaps(positions, 0, gaps)


def _find_all(haystack: str, needle: str) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    start = haystack.find(needle)
    while start >= 0:
        hits.append((start, start + len(needle)))
        start = haystack.find(needle, start + 1)
    return hits


def quote_span(text: str, quote: str) -> tuple[int, int]:
    """Locate exactly one source span for a quote, tolerating whitespace-only differences.

    Extracted text from PDF and office documents hard-wraps sentences, so a model that
    collapses those line breaks still describes the same source span. The returned span
    always covers the original text, so its length can differ from a whitespace-collapsed quote.
    """
    if not quote:
        raise ValueError("Quote must not be empty")
    exact = _find_all(text, quote)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError("Quote matches more than one source span")
    collapsed_text, positions = collapse_whitespace(text)
    collapsed_quote, gaps = _normalized_quote(quote)
    if not collapsed_quote:
        raise ValueError("Quote must not be empty")
    hits = [
        (start, end)
        for start, end in _find_all(collapsed_text, collapsed_quote)
        if _contains_quote_gaps(positions, start, gaps)
    ]
    if len(hits) == 1:
        start, end = hits[0]
        return positions[start], positions[end - 1] + 1
    if len(hits) > 1:
        raise ValueError("Quote matches more than one source span")
    raise ValueError("Quote is not present in the source text")


def atomic_text(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".forge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Source-derived artifacts are private by default. Portable task files opt in to sharing.
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_text(value: Any) -> str:
    """The exact text ``write_json`` stores: indented, UTF-8, no NaN, trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json_text(value))


def read_jsonl(path: Path) -> list[Any]:
    """One JSON value per non-empty line."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Invalid JSON artifact encoding or syntax") from None


def read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise ValueError("Invalid YAML/JSON input encoding or syntax") from None


def load_project(path: Path) -> Project:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Project specification exceeds the 2 MB limit")
    data = read_yaml(path)
    return Project.model_validate(data)


def load_sources(project: Project, root: Path) -> tuple[dict[str, str], dict[str, str]]:
    root = root.resolve()
    contents: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for source in project.sources:
        path = (root / source.path).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Source must remain inside the project directory: {source.id}")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError(f"Source exceeds the 2 MB limit: {source.id}")
        contents[source.id] = path.read_text(encoding="utf-8")
        hashes[source.id] = digest(contents[source.id])
        if source.content_hash is not None and source.content_hash != hashes[source.id]:
            raise ValueError(f"Source content hash changed: {source.id}")
    for rule in project.organization.rules:
        for evidence in rule.evidence:
            source_text = contents[evidence.source_id]
            if evidence.start is None or evidence.end is None:
                try:
                    quote_span(source_text, evidence.quote)
                except ValueError as exc:
                    raise ValueError(f"Evidence quote rejected for rule {rule.id}: {exc}") from None
                continue
            if not 0 <= evidence.start < evidence.end <= len(source_text):
                raise ValueError(f"Evidence span is outside source bounds for rule {rule.id}")
            span = source_text[evidence.start : evidence.end]
            if not evidence_quote_matches(span, evidence.quote):
                raise ValueError(f"Evidence span does not match for rule {rule.id}")
    return contents, hashes
