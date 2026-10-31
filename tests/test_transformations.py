import pytest

from decision_forge.io import load_project
from decision_forge.pipeline import build, load_run
from decision_forge.transformations import Intervention, transform_episode


def test_boundary_change_has_verified_relation_and_shared_family(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    project = load_project(example_path)
    parent = next(
        e
        for e in load_run(tmp_path / "run")
        if e.node_id == "refund" and e.state.get("amount") == 1000 and not e.state.get("approved")
    )
    child = transform_episode(
        project, parent, Intervention(state_updates={"amount": 1001}, relation="disjoint_choices")
    )
    assert child.valid_choices == ["request_approval"]
    assert child.family_id == parent.family_id and child.split == parent.split
    assert parent.state["amount"] == 1000
    with pytest.raises(ValueError, match="relationship"):
        transform_episode(project, parent, Intervention(state_updates={"amount": 999}, relation="disjoint_choices"))


def test_candidate_reordering_preserves_decision_and_rejects_test_feedback(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    parent = next(e for e in load_run(tmp_path / "run") if e.split == "test")
    project = load_project(example_path)
    child = transform_episode(project, parent, Intervention(relation="same_choices", candidate_order_seed=12))
    assert set(child.valid_choices) == set(parent.valid_choices)
    assert {c.id for c in child.candidates} == {c.id for c in parent.candidates}
    with pytest.raises(ValueError, match="training family"):
        transform_episode(project, parent, Intervention(relation="same_choices", stratum="feedback"))


def test_changed_policy_cannot_relabel_a_counterfactual_under_old_provenance(example_path, tmp_path):
    from decision_forge.io import read_json, write_json
    from decision_forge.pipeline import load_run_project

    build(example_path, tmp_path / "run")
    project = load_project(example_path)
    parent = next(
        e
        for e in load_run(tmp_path / "run")
        if e.node_id == "refund" and e.state.get("amount") == 1000 and not e.state.get("approved")
    )
    for rule in project.organization.rules:
        for predicate in rule.when.all_of + rule.when.any_of:
            if predicate.path == "state.amount" and predicate.value == 1000:
                predicate.value = 2000
    with pytest.raises(ValueError, match="policy fingerprint"):
        transform_episode(project, parent, Intervention(state_updates={"amount": 1500}, relation="same_choices"))
    snapshot = tmp_path / "run/project.snapshot.json"
    data = read_json(snapshot)
    data["organization"]["rules"][0]["priority"] += 1
    write_json(snapshot, data)
    with pytest.raises(ValueError, match="snapshot integrity"):
        load_run_project(tmp_path / "run")


def test_source_metadata_changes_invalidate_cached_policy_provenance(example_path, tmp_path):
    import shutil

    from decision_forge.io import write_json
    from decision_forge.pipeline import load_run_project

    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    build(spec, tmp_path / "first")
    project = load_project(spec)
    project.sources[0].access = {"classification": "restricted"}
    write_json(spec, project.model_dump(mode="json"))
    result = build(spec, tmp_path / "second")
    assert result["report"]["cache_hits"] == 0
    parent = load_run(tmp_path / "second")[0]
    transform_episode(load_run_project(tmp_path / "second"), parent, Intervention(relation="same_choices"))
