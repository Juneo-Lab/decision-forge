import shutil

import pytest
from test_review_conflicts import CAP, MANAGER, write_project

from decision_forge.exporters import export_run
from decision_forge.io import load_project, read_json, write_json
from decision_forge.pipeline import build, load_run
from decision_forge.review import record_review, review_status


def copied_project(example_path, tmp_path):
    folder = tmp_path / "project"
    shutil.copytree(example_path.parent, folder)
    write_json(folder / "compilation.json", {"status": "draft"})
    return folder / example_path.name


def accept_fixture(spec):
    status = review_status(spec)
    for item in status["items"]:
        record_review(
            spec,
            item["id"],
            "accepted",
            "test-reviewer",
            "Test fixture approval, not a production review.",
            status["fingerprint"],
        )


def test_draft_build_is_blocked_until_reviews_are_accepted(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    with pytest.raises(ValueError, match="requires accepted reviews"):
        build(spec, tmp_path / "run")
    accept_fixture(spec)
    assert review_status(spec)["ready"]
    assert build(spec, tmp_path / "run")["report"]["accepted"] == 336


def test_rejected_rule_blocks_build_and_revision_invalidates_reviews(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    accept_fixture(spec)
    status = review_status(spec)
    item = status["items"][0]["id"]
    record_review(spec, item, "rejected", "test-reviewer", "Threshold needs correction.", status["fingerprint"])
    assert review_status(spec)["rejected"] == 1
    with pytest.raises(ValueError, match="requires accepted reviews"):
        build(spec, tmp_path / "run")
    project = load_project(spec)
    project.organization.rules[0].priority += 1
    write_json(spec, project.model_dump(mode="json"))
    current = review_status(spec)
    # Only the revision that actually changed loses its decision; unrelated reviews survive.
    assert current["pending"] == 2  # Changed rule and its dependent node.
    assert not current["ready"]
    with pytest.raises(ValueError, match="changed since review"):
        record_review(spec, item, "accepted", "test-reviewer", "Old decision", status["fingerprint"])


def test_review_of_unrelated_rule_survives_an_edit(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    accept_fixture(spec)
    status = review_status(spec)
    edited_rule = load_project(spec).organization.rules[0]
    survivor = next(i for i in status["items"] if i["id"] == "rule:reject")
    project = load_project(spec)
    project.organization.rules[0].priority += 1
    write_json(spec, project.model_dump(mode="json"))
    refreshed = review_status(spec)
    assert edited_rule.id != "reject"
    kept = next(i for i in refreshed["items"] if i["id"] == survivor["id"])
    assert kept["status"] == "accepted"
    assert kept["fingerprint"] == survivor["fingerprint"]
    record_review(spec, "rule:reject", "accepted", "test-reviewer", "Recheck", kept["fingerprint"])


def test_shadowed_rule_enters_queue(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    project = load_project(spec)
    base = project.organization.rules[0]
    shadow = base.model_copy(deep=True)
    shadow.id = "shadowing-rule"
    shadow.priority = base.priority + 10
    shadow.when.all_of = []
    project.organization.rules.append(shadow)
    for node in project.nodes:
        if base.id in node.rule_ids:
            node.rule_ids.append(shadow.id)
    write_json(spec, project.model_dump(mode="json"))
    items = review_status(spec)["items"]
    shadowed = [item for item in items if item["kind"] == "shadowed"]
    assert shadowed, [item["id"] for item in items]
    assert shadowed[0]["content"]["rule_shadowing"] is True
    # A reviewer edit to either rule re-opens the item instead of leaving a stale decision.
    before = shadowed[0]["fingerprint"]
    project = load_project(spec)
    project.organization.rules[0].priority += 2
    write_json(spec, project.model_dump(mode="json"))
    after = next(item for item in review_status(spec)["items"] if item["id"] == shadowed[0]["id"])
    assert after["fingerprint"] != before


def test_source_change_invalidates_reviews_without_quote_changes(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    accept_fixture(spec)
    source = spec.parent / "sources/policies.md"
    source.write_text(source.read_text() + "\nNew organizational guidance.\n")
    assert not review_status(spec)["ready"]


def test_review_history_tampering_is_detected(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    accept_fixture(spec)
    history = spec.parent / "reviews.json"
    data = read_json(history)
    data[0]["decision"] = "rejected"
    write_json(history, data)
    with pytest.raises(ValueError, match="integrity"):
        review_status(spec)


def test_equal_priority_conflicts_enter_queue(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    project = load_project(spec)
    first = project.organization.rules[0]
    opposite = first.model_copy(deep=True)
    opposite.id = "conflicting-rule"
    opposite.effect = "deny" if first.effect == "allow" else "allow"
    project.organization.rules.append(opposite)
    for node in project.nodes:
        if first.id in node.rule_ids:
            node.rule_ids.append(opposite.id)
    write_json(spec, project.model_dump(mode="json"))
    assert any(item["kind"] == "conflict" for item in review_status(spec)["items"])


def test_priority_override_conflict_enters_queue(example_path, tmp_path):
    spec = copied_project(example_path, tmp_path)
    project = load_project(spec)
    first = project.organization.rules[0]
    overriding = first.model_copy(deep=True)
    overriding.id = "overriding-rule"
    overriding.effect = "deny" if first.effect == "allow" else "allow"
    overriding.priority = first.priority + 5
    project.organization.rules.append(overriding)
    for node in project.nodes:
        if first.id in node.rule_ids:
            node.rule_ids.append(overriding.id)
    write_json(spec, project.model_dump(mode="json"))
    items = [item for item in review_status(spec)["items"] if item["kind"] == "override"]
    assert items, [item["kind"] for item in review_status(spec)["items"]]
    assert items[0]["content"]["priority_decides"] is True
    assert items[0]["content"]["identical_condition"] is True


def sampling_project(folder, overlap, kind, pool=None):
    """Exhaust a small fictional population, with an unrelated same-effect shadowed pair."""
    amounts = [200, 5000] if overlap == "observed" else [200, 500]
    spec = write_project(folder, [MANAGER, CAP], amounts)
    project = load_project(spec)
    manager = project.organization.rules[0]
    manager.priority = 5 if kind == "override" else 0
    manager.score = 2
    shadow = project.organization.rules[-1].model_copy(deep=True)
    shadow.id = "escalate-shadow"
    shadow.priority = 1
    shadow.score = 1
    project.organization.rules.append(shadow)
    project.nodes[0].rule_ids.append(shadow.id)
    project.recipe.seed = 7
    project.recipe.variants_per_scenario = 4
    project.recipe.candidate_pool_per_scenario = pool
    project.recipe.family_splits = {"family": "train"}
    write_json(spec, project.model_dump(mode="json"))
    return spec


def sampling_pair(status):
    return next(item for item in status["items"] if item["kind"] in {"conflict", "override"})


@pytest.mark.parametrize("overlap", ["observed", "possible"])
@pytest.mark.parametrize("kind", ["conflict", "override"])
@pytest.mark.parametrize(
    "setting,value,pool",
    [
        ("seed", 8, None),
        ("variants_per_scenario", 5, None),
        ("candidate_pool_per_scenario", 4, None),
        ("variants_per_scenario", 5, 3),
        ("candidate_pool_per_scenario", 4, 3),
    ],
    ids=["seed", "variants-fallback", "pool-from-fallback", "variants-with-pool", "pool-exhausted"],
)
def test_sampling_changes_require_pair_review_before_build_and_export(tmp_path, overlap, kind, setting, value, pool):
    spec = sampling_project(tmp_path, overlap, kind, pool)
    accept_fixture(spec)
    before = review_status(spec)
    assert before == review_status(spec)  # Unchanged configuration and history are stable.
    old_pair = sampling_pair(before)
    assert old_pair["kind"] == kind
    assert old_pair["content"]["overlap"] == overlap
    assert old_pair["content"]["witness_count"] == (1 if overlap == "observed" else 0)
    build(spec, tmp_path / "before")
    episodes = load_run(tmp_path / "before")
    assert len(episodes) == 2

    project = load_project(spec)
    setattr(project.recipe, setting, value)
    write_json(spec, project.model_dump(mode="json"))
    changed = review_status(spec)
    current_pair = sampling_pair(changed)
    for key in ["overlap", "witness_count", "witnesses"]:
        assert current_pair["content"][key] == old_pair["content"][key]
    assert current_pair["fingerprint"] != old_pair["fingerprint"]
    assert current_pair["status"] == "pending"
    assert current_pair["content"]["sampling"] == {
        "seed": project.recipe.seed,
        "variants_per_scenario": project.recipe.variants_per_scenario,
        "candidate_pool_per_scenario": project.recipe.candidate_pool_per_scenario,
    }
    unchanged = {item["id"]: item for item in before["items"] if item["id"] != "dataset:recipe"}
    for item in changed["items"]:
        if item["id"] in unchanged and item["id"] != current_pair["id"]:
            assert item == unchanged[item["id"]]  # Rule, node, and shadowed approvals survive.
            if item["kind"] == "shadowed":
                assert "sampling" not in item["content"]

    recipe = next(item for item in changed["items"] if item["id"] == "dataset:recipe")
    record_review(spec, recipe["id"], "accepted", "test-reviewer", "Fixture recipe change.", recipe["fingerprint"])
    assert review_status(spec)["pending"] == 1
    with pytest.raises(ValueError, match="requires accepted reviews"):
        build(spec, tmp_path / "blocked")
    assert not (tmp_path / "blocked").exists()
    with pytest.raises(ValueError, match="changed since review"):
        record_review(
            spec, old_pair["id"], "accepted", "test-reviewer", "Stale fixture review.", old_pair["fingerprint"]
        )
    record_review(
        spec, current_pair["id"], "accepted", "test-reviewer", "Reviewed the new sample.", current_pair["fingerprint"]
    )
    assert review_status(spec)["ready"]
    build(spec, tmp_path / "after")
    assert load_run(tmp_path / "after") == episodes
    for format in ["typed", "decision"]:
        counts = export_run(tmp_path / "after", tmp_path / f"export-{format}", format)
        assert counts["train"] == 2
        assert counts["skipped_multiple_valid_choices"] == 0


@pytest.mark.parametrize(
    "setting,value",
    [("require_full_distribution", False), ("splits", {"train": 0.6, "development": 0.2, "test": 0.2})],
)
@pytest.mark.parametrize("overlap", ["observed", "possible"])
def test_unrelated_recipe_fields_preserve_pair_and_other_reviews(tmp_path, setting, value, overlap):
    spec = sampling_project(tmp_path, overlap, "override")
    accept_fixture(spec)
    before = review_status(spec)
    assert before == review_status(spec)
    project = load_project(spec).model_dump(mode="json")
    project["recipe"][setting] = value
    write_json(spec, project)
    after = review_status(spec)
    assert after["pending"] == 1  # Only the dataset recipe changes.
    assert [item for item in before["items"] if item["id"] != "dataset:recipe"] == [
        item for item in after["items"] if item["id"] != "dataset:recipe"
    ]
