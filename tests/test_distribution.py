import shutil

import pytest

from decision_forge.distribution import select_distribution
from decision_forge.generation import generate_family, split_plan
from decision_forge.io import load_project, load_sources, read_json, write_json
from decision_forge.models import Dimension, DistributionTarget
from decision_forge.pipeline import build, load_run


def test_candidate_intervention_is_applied_to_current_row_without_mutating_project(project, example_path):
    node = project.nodes[0]
    candidate = node.candidates[0]
    path = f"candidates.{candidate.id}.marker"
    node.dimensions = [Dimension(path=path, values=["A", "B"])]
    before = project.model_dump()
    _, hashes = load_sources(project, example_path.parent)
    scenario = next(s for s in project.scenarios if s.node_id == node.id)
    first = generate_family(project, scenario, hashes)
    assert project.model_dump() == before
    for episode in first:
        assert episode.candidates[0].attributes["marker"] == episode.intervention[path]
    assert first == generate_family(project, scenario, hashes)


def test_exact_label_quotas_are_reproducible_and_report_every_filter(example_path, project, tmp_path):
    build(example_path, tmp_path / "pool")
    pool = load_run(tmp_path / "pool")
    targets = []
    for choice in ("human", "agent"):
        e = next(e for e in pool if e.valid_choices == [choice] and e.split == "train")
        targets.append(
            DistributionTarget(node_id=e.node_id, stratum=e.stratum, split=e.split, valid_choices=[choice], count=3)
        )
    project.recipe.distribution = targets
    picked, report = select_distribution(project, pool, [])
    again, _ = select_distribution(project, list(reversed(pool)), [])
    assert picked == again
    assert len(picked) == 6
    assert report["feasible"] and report["jensen_shannon_divergence_bits"] == 0
    assert sum(c["filtered"] for c in report["cells"]) == len(pool) - 6
    project.recipe.distribution[0].count = 10000
    _, report = select_distribution(project, pool, [])
    assert report["shortfall"] > 0 and not report["feasible"]


def test_infeasible_build_preserves_diagnostics_and_cannot_export(example_path, tmp_path):
    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    project = load_project(spec)
    project.recipe.distribution = [
        DistributionTarget(
            node_id=project.nodes[0].id, stratum="feedback", split="train", valid_choices=["human"], count=2
        )
    ]
    write_json(spec, project.model_dump(mode="json"))
    with pytest.raises(ValueError, match="infeasible"):
        build(spec, tmp_path / "run")
    assert read_json(tmp_path / "run/distribution.json")["shortfall"] == 2
    with pytest.raises(ValueError, match="incomplete"):
        load_run(tmp_path / "run")
    project.recipe.require_full_distribution = False
    write_json(spec, project.model_dump(mode="json"))
    result = build(spec, tmp_path / "partial")
    assert result["report"]["accepted"] == 0
    assert result["report"]["distribution"]["shortfall"] == 2


def test_split_map_freezes_published_families(project):
    first = split_plan(project)
    project.recipe.family_splits = first
    for index in range(20):
        scenario = project.scenarios[0].model_copy(deep=True)
        scenario.id = f"new-{index}"
        scenario.family_id = f"new-family-{index}"
        project.scenarios.append(scenario)
    after = split_plan(project)
    assert all(after[family] == split for family, split in first.items())


def test_cache_cannot_keep_stale_splits_after_family_growth(example_path, tmp_path):
    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    build(spec, tmp_path / "first")
    project = load_project(spec)
    for index in range(2):
        extra = project.scenarios[0].model_copy(deep=True)
        extra.id = f"new-{index}"
        extra.family_id = f"new-family-{index}"
        project.scenarios.append(extra)
    write_json(spec, project.model_dump(mode="json"))
    build(spec, tmp_path / "second")
    plan = split_plan(project)
    assert all(e.split == plan[e.family_id] for e in load_run(tmp_path / "second"))
