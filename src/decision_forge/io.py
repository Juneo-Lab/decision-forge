"""Local inputs, canonical hashes, and atomic artifact writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from decision_forge.models import Project

MAX_SOURCE_BYTES = 2_000_000


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".forge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_project(path: Path) -> Project:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Project specification exceeds the 2 MB limit")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    for rule in project.organization.rules:
        for evidence in rule.evidence:
            if evidence.quote not in contents[evidence.source_id]:
                raise ValueError(f"Evidence quote not found for rule {rule.id}")
    return contents, hashes
