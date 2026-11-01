import shutil

import pytest

from decision_forge.feedback import update_dataset
from decision_forge.io import load_project, read_json, write_json
from decision_forge.models import Project
from decision_forge.pipeline import build
from decision_forge.planner import PlanObjective, plan_recipe
from decision_forge.splits import SplitLock, lock_path, read_lock, resolve_splits, split_plan, write_lock

POLICY = "Refunds up to 100 units may be executed immediately.\n"


def write_project(folder, families, organization="split-co"):
    """A minimal valid project with one scenario per family."""
    (folder / "sources").mkdir(parents=True, exist_ok=True)
    (folder / "sources/policy.md").write_text(POLICY, encoding="utf-8")
    spec = folder / "project.json"
    write_json(
        spec,
        {
            "organization": {
                "id": organization,
                "name": "Split Co",
                "policy_version": "1",
                "rules": [
                    {
                        "id": "execute-small",
                        "description": "Execute small refunds.",
                        "when": {"all_of": [{"path": "candidate.id", "op": "eq", "value": "execute"}]},
                        "effect": "allow",
                        "evidence": [{"source_id": "policy", "quote": POLICY.strip()}],
                    }
                ],
            },
            "sources": [{"id": "policy", "path": "sources/policy.md"}],
            "nodes": [
                {
                    "id": "refund",
                    "kind": "select_actions",
                    "question": "How should the refund proceed?",
                    "candidates": [
                        {"id": "execute", "description": "Execute the refund."},
                        {"id": "escalate", "description": "Escalate the refund."},
                    ],
                    "rule_ids": ["execute-small"],
                    "dimensions": [{"path": "state.amount", "values": [10, 50]}],
                }
            ],
            "scenarios": [
                {"id": f"s-{family}", "node_id": "refund", "family_id": family, "state": {}, "source_ids": ["policy"]}
                for family in families
            ],
            "recipe": {"variants_per_scenario": 2},
        },
    )
    return spec


def lock_once(spec):
    project = load_project(spec)
    lock = resolve_splits(project, spec)
    write_lock(spec, lock)
    return project.recipe.family_splits


def test_adding_families_never_moves_a_locked_family(tmp_path):
    families = ["f-0", "f-1", "f-2"]
    spec = write_project(tmp_path, families)
    published = lock_once(spec)
    moved_without_lock = 0
    for index in range(3, 60):
        families.append(f"f-{index}")
        spec = write_project(tmp_path, families)
        unlocked = split_plan(load_project(spec))
        moved_without_lock += any(unlocked[family] != split for family, split in published.items())
        current = lock_once(spec)
        assert {family: current[family] for family in published} == published
        published = current
    # The quota assignment alone reshuffles families as the project grows; the lock is what prevents it.
    assert moved_without_lock > 0


def test_first_version_fills_every_configured_split(tmp_path):
    spec = write_project(tmp_path, [f"f-{index}" for index in range(5)])
    assert set(lock_once(spec).values()) == {"train", "development", "test"}
    assert read_lock(spec).families == split_plan(load_project(spec))


def test_removed_family_returns_to_its_original_split(tmp_path):
    families = [f"f-{index}" for index in range(6)]
    spec = write_project(tmp_path, families)
    original = lock_once(spec)
    held_out = next(family for family, split in original.items() if split == "test")
    lock_once(write_project(tmp_path, [family for family in families if family != held_out]))
    assert read_lock(spec).families[held_out] == "test"
    assert lock_once(write_project(tmp_path, families))[held_out] == "test"


def test_spec_pins_cannot_silently_contradict_the_lock(tmp_path):
    spec = write_project(tmp_path, [f"f-{index}" for index in range(4)])
    locked = lock_once(spec)
    family = next(family for family, split in locked.items() if split == "test")
    data = read_json(spec)
    data["recipe"]["family_splits"] = {family: "train"}
    write_json(spec, data)
    with pytest.raises(ValueError, match=f"{family} is locked to test"):
        resolve_splits(load_project(spec), spec)


def test_a_lock_from_another_organization_is_rejected(tmp_path):
    spec = write_project(tmp_path, ["f-0", "f-1"])
    write_lock(spec, SplitLock(organization_id="someone-else", families={"f-0": "test"}))
    with pytest.raises(ValueError, match="belongs to organization someone-else"):
        resolve_splits(load_project(spec), spec)


def test_build_records_the_lock_and_rebuilds_into_the_same_directory(tmp_path):
    spec = write_project(tmp_path / "input", [f"f-{index}" for index in range(4)])
    first = build(spec, tmp_path / "run")
    assert read_lock(spec).families == first["family_splits"]
    # The lock now pins every family, yet the resolved project and therefore the run are unchanged.
    assert build(spec, tmp_path / "run")["dataset_digest"] == first["dataset_digest"]


def test_locked_splits_refuses_a_missing_or_stale_lock(tmp_path):
    families = [f"f-{index}" for index in range(4)]
    spec = write_project(tmp_path / "input", families)
    with pytest.raises(ValueError, match="missing or out of date"):
        build(spec, tmp_path / "locked", locked_splits=True)
    assert not lock_path(spec).exists()
    build(spec, tmp_path / "first")
    build(spec, tmp_path / "locked", locked_splits=True)
    spec = write_project(tmp_path / "input", [*families, "f-new"])
    with pytest.raises(ValueError, match="missing or out of date"):
        build(spec, tmp_path / "grown", locked_splits=True)


def test_failed_builds_leave_the_lock_untouched(tmp_path):
    spec = write_project(tmp_path / "input", ["f-0", "f-1", "f-2"])
    data = read_json(spec)
    data["recipe"]["distribution"] = [
        {"node_id": "refund", "stratum": "routine", "split": "train", "valid_choices": ["execute"], "count": 999}
    ]
    write_json(spec, data)
    with pytest.raises(ValueError, match="infeasible"):
        build(spec, tmp_path / "run")
    assert not lock_path(spec).exists()


def test_plans_made_before_and_after_the_first_build_both_load(tmp_path):
    spec = write_project(tmp_path / "input", [f"f-{index}" for index in range(4)])
    plan_recipe(spec, tmp_path / "before", PlanObjective(per_cell=1))
    build(spec, tmp_path / "first", recipe_plan=tmp_path / "before/plan.json")
    plan_recipe(spec, tmp_path / "after", PlanObjective(per_cell=1))
    build(spec, tmp_path / "second", recipe_plan=tmp_path / "after/plan.json")


def test_dataset_updates_keep_removed_families_in_the_lock(tmp_path):
    families = [f"f-{index}" for index in range(6)]
    spec = write_project(tmp_path / "input", families)
    first = build(spec, tmp_path / "v1")["family_splits"]
    lock_path(spec).unlink()
    spec = write_project(tmp_path / "input", families[1:])
    update_dataset(spec, tmp_path / "v1", tmp_path / "v2")
    assert read_lock(spec).families == first


def test_the_example_lock_is_current_and_keeps_the_documented_split_sizes(example_path, tmp_path):
    shutil.copytree(example_path.parent, tmp_path / "input")
    report = build(tmp_path / "input/project.yaml", tmp_path / "run", locked_splits=True)["report"]
    assert report["by_split"] == {"train": 196, "development": 84, "test": 56}
    assert Project.model_validate(read_json(tmp_path / "run/project.snapshot.json")).recipe.family_splits == (
        read_lock(example_path).families
    )
