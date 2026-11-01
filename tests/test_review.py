import shutil

import pytest

from decision_forge.generation import generate_family
from decision_forge.io import load_project, load_sources, read_json, write_json
from decision_forge.models import Dimension, Predicate
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


def dimension_project(tmp_path, dimension_path="state.amount", priority=5):
    """A fictional approval policy whose deny rule does not match the seed."""
    quotes = ["Managers may approve requests.", "Requests above 1000 units must be denied."]
    (tmp_path / "policy.txt").write_text("\n".join(quotes), encoding="utf-8")
    project = {
        "organization": {
            "id": "fictional-approvals",
            "name": "Fictional Approvals",
            "policy_version": "1",
            "rules": [
                {
                    "id": "mgr",
                    "description": quotes[0],
                    "when": {
                        "all_of": [
                            {"path": "candidate.id", "op": "eq", "value": "approve"},
                            {"path": "state.role", "op": "eq", "value": "manager"},
                        ]
                    },
                    "effect": "allow",
                    "priority": priority,
                    "evidence": [{"source_id": "policy", "quote": quotes[0]}],
                },
                {
                    "id": "cap",
                    "description": quotes[1],
                    "when": {
                        "all_of": [
                            {
                                "path": dimension_path.replace("candidates.approve.", "candidate."),
                                "op": "gt",
                                "value": 1000,
                            }
                        ]
                    },
                    "effect": "deny",
                    "evidence": [{"source_id": "policy", "quote": quotes[1]}],
                },
            ],
        },
        "sources": [{"id": "policy", "path": "policy.txt"}],
        "nodes": [
            {
                "id": "approval",
                "kind": "select_actions",
                "question": "May this request be approved?",
                "candidates": [
                    {"id": "approve", "description": "Approve", "attributes": {"limits": {"amount": 200}}},
                    {"id": "defer", "description": "Defer"},
                ],
                "rule_ids": ["mgr", "cap"],
                "dimensions": [{"path": dimension_path, "values": [200, 5000]}],
            }
        ],
        "scenarios": [
            {
                "id": "seed",
                "node_id": "approval",
                "family_id": "approvals",
                "state": {"role": "manager", "amount": 200},
                "source_ids": ["policy"],
            }
        ],
        "recipe": {"seed": 7, "variants_per_scenario": 2},
    }
    spec = tmp_path / "project.json"
    write_json(spec, project)
    return spec


@pytest.mark.parametrize("dimension_path", ["state.amount", "candidates.approve.limits.amount"])
@pytest.mark.parametrize("priority,kind", [(5, "override"), (0, "conflict")])
def test_dimension_only_overlap_requires_review(tmp_path, dimension_path, priority, kind):
    spec = dimension_project(tmp_path, dimension_path, priority)
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    episodes = generate_family(project, project.scenarios[0], hashes)
    overlap = next(
        episode
        for episode in episodes
        if any(set(verdict.matched_rules) == {"mgr", "cap"} for verdict in episode.verdicts)
    )
    verdict = next(verdict for verdict in overlap.verdicts if verdict.candidate_id == "approve")
    assert verdict.allowed == (priority > 0)  # Review must not alter priority or deny-wins semantics.
    status = review_status(spec)
    item = next(item for item in status["items"] if item["id"] == "conflict:approval:cap:mgr")
    assert item["kind"] == kind
    assert item["content"]["seed_witnesses"] == []
    assert item["content"]["generated_witnesses"] == [
        {
            "scenario": "seed",
            "candidate": "approve",
            "episode": overlap.id,
            "intervention": {dimension_path: 5000},
        }
    ]
    write_json(spec.parent / "compilation.json", {"status": "draft"})
    for other in status["items"]:
        if other["id"] != item["id"]:
            record_review(spec, other["id"], "accepted", "test-reviewer", "Fictional fixture.", other["fingerprint"])
    with pytest.raises(ValueError, match="requires accepted reviews"):
        build(spec, tmp_path / "blocked")
    record_review(spec, item["id"], "accepted", "test-reviewer", "Verified the override.", item["fingerprint"])
    assert build(spec, tmp_path / "accepted")["status"] == "complete"


def test_seed_witness_is_retained_when_it_is_not_generated(tmp_path):
    spec = dimension_project(tmp_path)
    project = load_project(spec)
    project.scenarios[0].state["amount"] = 5000
    project.nodes[0].dimensions[0].values = [200]
    write_json(spec, project.model_dump(mode="json"))
    item = next(item for item in review_status(spec)["items"] if item["kind"] == "override")
    assert item["content"]["seed_witnesses"] == [
        {"scenario": "seed", "candidate": "approve"},
    ]
    assert item["content"]["generated_witnesses"] == []


@pytest.mark.parametrize(
    "setting,value", [("seed", 8), ("variants_per_scenario", 3), ("candidate_pool_per_scenario", 3)]
)
def test_sampling_changes_reopen_conflict_review_even_if_first_witness_is_unchanged(tmp_path, setting, value):
    spec = dimension_project(tmp_path)
    project = load_project(spec)
    project.nodes[0].dimensions[0].values = [5000]
    write_json(spec, project.model_dump(mode="json"))
    accept_fixture(spec)
    before = next(item for item in review_status(spec)["items"] if item["kind"] == "override")
    setattr(project.recipe, setting, value)
    write_json(spec, project.model_dump(mode="json"))
    after = next(item for item in review_status(spec)["items"] if item["kind"] == "override")
    assert before["content"]["generated_witnesses"] == after["content"]["generated_witnesses"]
    assert before["fingerprint"] != after["fingerprint"]
    assert after["status"] == "pending"


def test_opposite_rules_must_match_the_same_candidate(tmp_path):
    spec = dimension_project(tmp_path)
    project = load_project(spec)
    for rule, candidate_id in zip(project.organization.rules, ["approve", "defer"], strict=True):
        rule.when.all_of.append(Predicate(path="candidate.id", op="eq", value=candidate_id))
    write_json(spec, project.model_dump(mode="json"))
    assert not any(item["kind"] in {"override", "conflict"} for item in review_status(spec)["items"])


@pytest.mark.parametrize("setting,value", [("candidate_pool_per_scenario", 2), ("seed", 1)])
def test_review_uses_only_the_sampled_pool_and_finds_newly_sampled_overlaps(tmp_path, setting, value):
    spec = dimension_project(tmp_path)
    project = load_project(spec)
    project.recipe.seed = 0
    project.recipe.variants_per_scenario = 1
    write_json(spec, project.model_dump(mode="json"))
    _, hashes = load_sources(project, spec.parent)
    episodes = generate_family(project, project.scenarios[0], hashes)
    assert [episode.state["amount"] for episode in episodes] == [200]
    assert not any(item["kind"] == "override" for item in review_status(spec)["items"])
    accept_fixture(spec)
    assert review_status(spec)["ready"]
    setattr(project.recipe, setting, value)
    write_json(spec, project.model_dump(mode="json"))
    status = review_status(spec)
    item = next(item for item in status["items"] if item["kind"] == "override")
    assert item["status"] == "pending"
    assert not status["ready"]


def test_review_generates_each_scenario_once_for_all_rule_pairs(tmp_path, monkeypatch):
    import decision_forge.review as review

    spec = dimension_project(tmp_path)
    project = load_project(spec)
    extra = project.organization.rules[1].model_copy(deep=True)
    extra.id = "second-cap"
    project.organization.rules.append(extra)
    project.nodes[0].rule_ids.append(extra.id)
    # Over a billion possible combinations, but the recipe requests only two episodes.
    project.nodes[0].dimensions.extend(Dimension(path=f"state.extra{i}", values=[0, 1]) for i in range(30))
    write_json(spec, project.model_dump(mode="json"))
    calls = []

    def counted_generation(project, scenario, hashes):
        episodes = generate_family(project, scenario, hashes)
        calls.append((scenario.id, len(episodes)))
        return episodes

    monkeypatch.setattr(review, "generate_family", counted_generation)
    review_status(spec)
    assert calls == [("seed", 2)]


def test_review_retains_generation_population_limit(tmp_path):
    spec = dimension_project(tmp_path)
    project = load_project(spec)
    project.nodes[0].dimensions.extend(Dimension(path=f"state.extra{i}", values=[0, 1]) for i in range(40))
    write_json(spec, project.model_dump(mode="json"))
    with pytest.raises(ValueError, match="exceeds the supported sampling range"):
        review_status(spec)
