import runpy
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
release = runpy.run_path(str(ROOT / "scripts/check_release.py"))
check_release = release["check_release"]


def copy_release_files(tmp_path: Path) -> Path:
    for name in ["pyproject.toml", "CITATION.cff", "CHANGELOG.md", "src/decision_forge/__init__.py"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, target)
    return tmp_path


def test_repository_versions_are_consistent():
    result = check_release(ROOT)
    assert result["version"] == __import__("decision_forge").__version__


def test_tagged_release_extracts_only_its_changelog_section(tmp_path):
    root = copy_release_files(tmp_path)
    version = check_release(root)["version"]
    notes = check_release(root, f"v{version}")["notes"]
    assert notes.strip()
    assert not any(line.startswith("## ") for line in notes.splitlines())
    assert "Unreleased" not in notes


def test_tag_must_match_package_version(tmp_path):
    root = copy_release_files(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        check_release(root, "v99.0.0")


def test_version_drift_is_rejected(tmp_path):
    root = copy_release_files(tmp_path)
    citation = root / "CITATION.cff"
    citation.write_text(citation.read_text().replace("\nversion: ", "\nversion: 9", 1))
    with pytest.raises(ValueError, match="Version mismatch"):
        check_release(root)


def test_missing_changelog_section_is_rejected(tmp_path):
    root = copy_release_files(tmp_path)
    version = check_release(root)["version"]
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- Pending.\n")
    with pytest.raises(ValueError, match="no non-empty section"):
        check_release(root, f"v{version}")


def test_pre_release_versions_are_flagged(tmp_path):
    root = copy_release_files(tmp_path)
    current = check_release(root)["version"]
    for name in ["pyproject.toml", "CITATION.cff", "src/decision_forge/__init__.py"]:
        path = root / name
        path.write_text(path.read_text().replace(current, "1.0.0rc1", 1))
    assert check_release(root) == {"version": "1.0.0rc1", "prerelease": True}
    assert check_release(ROOT)["prerelease"] is False
