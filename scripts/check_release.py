"""Verify that a release tag, package metadata, citation, and changelog agree.

Without --tag, only checks that the checked-in version is consistent everywhere.
With --notes, writes the changelog section for the version as release notes.
"""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?")


def declared_versions(root: Path) -> dict[str, str]:
    module = ast.parse((root / "src/decision_forge/__init__.py").read_text(encoding="utf-8"))
    package = next(
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
        and isinstance(node.value, ast.Constant)
    )
    citation = yaml.safe_load((root / "CITATION.cff").read_text(encoding="utf-8"))
    return {
        "pyproject.toml": tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "src/decision_forge/__init__.py": package,
        "CITATION.cff": str(citation["version"]),
    }


def changelog_section(changelog: str, version: str) -> str:
    match = re.search(rf"^## {re.escape(version)}[ \t]*\n(.*?)(?=^## |\Z)", changelog, re.MULTILINE | re.DOTALL)
    if match is None or not match.group(1).strip():
        raise ValueError(f"CHANGELOG.md has no non-empty section for {version}")
    return match.group(1).strip() + "\n"


def check_release(root: Path, tag: str | None = None) -> dict[str, str | bool]:
    versions = declared_versions(root)
    if len(set(versions.values())) != 1:
        raise ValueError(f"Version mismatch: {versions}")
    version = next(iter(versions.values()))
    if not VERSION.fullmatch(version):
        raise ValueError(f"Unsupported version format: {version}")
    result: dict[str, str | bool] = {"version": version, "prerelease": not version.replace(".", "").isdigit()}
    if tag is not None:
        if tag != f"v{version}":
            raise ValueError(f"Tag {tag} does not match package version v{version}")
        result["notes"] = changelog_section((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag to verify, for example v0.3.0")
    parser.add_argument("--notes", type=Path, help="Write the matching changelog section to this file")
    args = parser.parse_args()
    if args.notes and not args.tag:
        parser.error("--notes requires --tag")
    try:
        release = check_release(ROOT, args.tag)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    if args.notes:
        args.notes.parent.mkdir(parents=True, exist_ok=True)
        args.notes.write_text(str(release["notes"]), encoding="utf-8")
    print(f"version={release['version']}")
    print(f"prerelease={str(release['prerelease']).lower()}")
