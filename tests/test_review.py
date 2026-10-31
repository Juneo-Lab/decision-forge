import shutil

import pytest

from decision_forge.io import load_project, read_json, write_json
from decision_forge.pipeline import build
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
    assert current["pending"] == 1
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
