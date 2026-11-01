import json
import shutil
from collections import Counter

import pytest

from decision_forge.exporters import export_run, model_input
from decision_forge.harness import export_harbor
from decision_forge.io import load_project, load_sources, read_json, write_json
from decision_forge.models import SplitConfig
from decision_forge.pipeline import build, impact, load_run
from decision_forge.splits import split_plan


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
    before = split_plan(project)
    project.recipe.seed = 123
    assert split_plan(project) == before
    project.recipe.variants_per_scenario = 4
    assert split_plan(project) == before


def test_split_quotas_track_the_recipe(project):
    plan = split_plan(project)
    counts = Counter(plan.values())
    assert set(counts) <= {"train", "development", "test"}
    assert sum(counts.values()) == len({scenario.family_id for scenario in project.scenarios})
    # Quotas must stay proportional to the recipe instead of following an independent coin flip.
    for split in ("train", "development", "test"):
        share = counts[split] / sum(counts.values())
        target = getattr(project.recipe.splits, split)
        assert abs(share - target) <= 0.25, (split, share, target)


def test_every_configured_split_gets_a_family(project):
    project.recipe.splits = SplitConfig(train=0.8, development=0.1, test=0.1)
    seen: set[str] = set()
    distinct = []
    for scenario in project.scenarios:
        if scenario.family_id not in seen:
            seen.add(scenario.family_id)
            distinct.append(scenario)
    project.scenarios = distinct[:4]
    plan = split_plan(project)
    assert set(plan.values()) == {"train", "development", "test"}


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


def test_report_states_requested_and_realized_volumes(example_path, tmp_path):
    report = build(example_path, tmp_path / "run")["report"]
    assert report["requested_episodes"] == 336
    assert report["realized_episodes"] == 336
    assert report["episodes_collapsed_as_duplicates"] == 0
    assert report["scenarios_below_requested_variants"] == {}


def test_duplicate_dimension_values_are_rejected(project):
    from pydantic import ValidationError

    from decision_forge.models import Dimension

    node = project.nodes[0]
    with pytest.raises(ValidationError, match="unique"):
        node.dimensions = [Dimension(path="state.urgency", values=["low", "low"])]


def test_candidate_ids_cannot_make_dimension_paths_ambiguous():
    from pydantic import ValidationError

    from decision_forge.models import Candidate, Dimension, Node

    # A dotted candidate ID would make candidates.<id>.<attribute> resolution ambiguous.
    with pytest.raises(ValidationError):
        Candidate(id="exec.fast", description="Dotted")
    with pytest.raises(ValidationError, match="Unknown candidate dimension"):
        Node(
            id="ambiguous",
            kind="select_actions",
            question="Which?",
            candidates=[Candidate(id="exec", description="A"), Candidate(id="exec-fast", description="B")],
            rule_ids=["r1"],
            dimensions=[Dimension(path="candidates.other.tier", values=["gold"])],
        )


def test_candidate_path_resolution_is_exact():
    from decision_forge.generation import candidate_for
    from decision_forge.models import Candidate, Node

    node = Node(
        id="n",
        kind="select_actions",
        question="Which?",
        candidates=[Candidate(id="exec", description="A"), Candidate(id="exec-fast", description="B")],
        rule_ids=["r1"],
        dimensions=[],
    )
    assert candidate_for(node, "candidates.exec-fast.tier").id == "exec-fast"
    assert candidate_for(node, "candidates.exec.tier").id == "exec"
    with pytest.raises(ValueError, match="exactly one candidate"):
        candidate_for(node, "candidates.other.tier")


def test_harbor_tasks_make_shell_scripts_executable(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    export_harbor(tmp_path / "run", tmp_path / "tasks", 1, "test")
    # export_harbor also writes manifest.json, and directory order is filesystem-specific.
    task = next(path for path in (tmp_path / "tasks").iterdir() if path.is_dir())
    for relative in ("tests/test.sh", "solution/solve.sh"):
        mode = (task / relative).stat().st_mode
        assert mode & 0o111, relative
    assert (task / "environment/context.json").stat().st_mode & 0o004


def test_generation_cost_is_not_reported_as_zero_when_the_endpoint_hides_prices(example_path, tmp_path):
    report = build(example_path, tmp_path / "run")["report"]
    assert report["generation_cost"]["provider_cost_usd"] is None
    assert report["generation_cost"]["cost_basis"] == "no_model_calls"
    assert report["generation_cost"]["per_accepted_example_usd"] is None


def test_filtered_by_distribution_counts_merged_rows(example_path, tmp_path):
    report = build(example_path, tmp_path / "run")["report"]
    assert report["filtered_by_distribution"] >= 0
    assert report["filtered_by_distribution"] == (
        report["realized_episodes"] - report["dedup"]["removed"] - report["accepted"]
    )
