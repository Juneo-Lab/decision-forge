import json
import shutil

import pytest

from decision_forge.exporters import export_run, model_input
from decision_forge.generation import split_for
from decision_forge.io import load_project, load_sources, read_json, write_json
from decision_forge.pipeline import build, impact, load_run


def test_build_resume_and_split_isolation(example_path, tmp_path):
    first = build(example_path, tmp_path / "run")
    episodes = load_run(tmp_path / "run")
    assert first["report"]["accepted"] == 336
    assert len(first["report"]["by_kind"]) == 4
    assert first["report"]["rejected_no_valid_choice"] == 0
    assert {e.split for e in episodes} == {"train", "development", "test"}
    families = {}
    for episode in episodes:
        families.setdefault(episode.family_id, set()).add(episode.split)
    assert all(len(splits) == 1 for splits in families.values())
    second = build(example_path, tmp_path / "run")
    assert second["dataset_digest"] == first["dataset_digest"]
    assert second["report"]["cache_hits"] == 48


def test_changed_input_requires_new_run_and_reports_impact(example_path, tmp_path):
    folder = tmp_path / "input"
    shutil.copytree(example_path.parent, folder)
    spec = folder / example_path.name
    build(spec, tmp_path / "v1")
    project = load_project(spec)
    project.nodes[0].question += " Consider the current workload."
    write_json(spec, project.model_dump(mode="json"))
    changes = impact(spec, tmp_path / "v1")
    assert len(changes["changed"]) == 12
    assert len(changes["reusable"]) == 36
    with pytest.raises(ValueError, match="Inputs changed"):
        build(spec, tmp_path / "v1")
    assert build(spec, tmp_path / "v2")["report"]["cache_hits"] == 36


def test_corrupted_cache_and_dataset_are_detected(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    dataset = tmp_path / "run/episodes.jsonl"
    dataset.write_text(dataset.read_text().replace('"policy_version":"2026-10-demo"', '"policy_version":"tampered"', 1))
    with pytest.raises(ValueError, match="integrity"):
        load_run(tmp_path / "run")
    path = next((tmp_path / ".forge-cache").glob("*.json"))
    data = read_json(path)
    data["episodes"][0]["valid_choices"] = ["tampered"]
    write_json(path, data)
    with pytest.raises(ValueError, match="Cache integrity"):
        build(example_path, tmp_path / "run")


def test_sampling_seed_does_not_change_split(project):
    before = split_for("customer-case", project)
    project.recipe.seed = 123
    assert split_for("customer-case", project) == before


def test_exports_keep_supervision_out_of_inputs(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    episode = load_run(tmp_path / "run")[0]
    assert "valid_choices" not in model_input(episode)
    assert "verdicts" not in model_input(episode)
    counts = export_run(tmp_path / "run", tmp_path / "export", "typed")
    assert sum(counts.values()) == 336
    for path in (tmp_path / "export").glob("*.jsonl"):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            assert set(row) == {"state", "questions", "gold"}
            assert "valid_choices" not in json.loads(row["state"])
    with pytest.raises(ValueError, match="empty"):
        export_run(tmp_path / "run", tmp_path / "export")


def test_evidence_quotes_and_source_paths_are_checked(project, example_path, tmp_path):
    project.organization.rules[0].evidence[0].quote = "Invented policy"
    with pytest.raises(ValueError, match="Evidence quote"):
        load_sources(project, example_path.parent)
    project.sources[0].path = "../outside.md"
    with pytest.raises(ValueError, match="inside"):
        load_sources(project, tmp_path)
