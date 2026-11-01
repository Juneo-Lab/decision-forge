import itertools

import pytest

from decision_forge.io import write_json
from decision_forge.models import Condition, Predicate
from decision_forge.pipeline import build, load_run
from decision_forge.policy import conditions_disjoint, matches_predicate
from decision_forge.review import MAX_WITNESSES, record_review, review_status

POLICY = "Managers approve refunds. Refunds above 1000 must not be approved.\n"


def rule(rule_id, effect, predicates, priority=0):
    return {
        "id": rule_id,
        "description": rule_id,
        "when": {"all_of": [{"path": "candidate.id", "op": "eq", "value": "approve"}, *predicates]},
        "effect": effect,
        "priority": priority,
        "evidence": [{"source_id": "policy", "quote": POLICY.split(". ")[0]}],
    }


def write_project(folder, rules, amounts):
    (folder / "sources").mkdir(parents=True, exist_ok=True)
    (folder / "sources/policy.md").write_text(POLICY, encoding="utf-8")
    spec = folder / "project.json"
    escalate = {
        "id": "escalate",
        "description": "Escalation is always available.",
        "when": {"all_of": [{"path": "candidate.id", "op": "eq", "value": "escalate"}]},
        "effect": "allow",
        "evidence": [{"source_id": "policy", "quote": POLICY.split(". ")[0]}],
    }
    write_json(
        spec,
        {
            "organization": {
                "id": "refund-co",
                "name": "Refund Co",
                "policy_version": "1",
                "rules": [*rules, escalate],
            },
            "sources": [{"id": "policy", "path": "sources/policy.md"}],
            "nodes": [
                {
                    "id": "refund",
                    "kind": "select_actions",
                    "question": "How should the refund proceed?",
                    "candidates": [
                        {"id": "approve", "description": "Approve the refund."},
                        {"id": "escalate", "description": "Escalate the refund."},
                    ],
                    "rule_ids": [r["id"] for r in [*rules, escalate]],
                    "dimensions": [{"path": "state.amount", "values": amounts}],
                }
            ],
            "scenarios": [
                {
                    "id": "seed",
                    "node_id": "refund",
                    "family_id": "family",
                    "state": {"role": "manager", "amount": amounts[0]},
                    "source_ids": ["policy"],
                }
            ],
            "recipe": {"variants_per_scenario": len(amounts)},
        },
    )
    write_json(folder / "compilation.json", {"status": "draft"})
    return spec


def conflicts(spec):
    return [item for item in review_status(spec)["items"] if item["id"].startswith("conflict:")]


MANAGER = rule("manager-approves", "allow", [{"path": "state.role", "op": "eq", "value": "manager"}], priority=5)
CAP = rule("cap-large", "deny", [{"path": "state.amount", "op": "gt", "value": 1000}])


def test_an_override_that_only_appears_at_a_dimension_value_enters_review(tmp_path):
    # The seed amount never exceeds the cap; only the generated 5000 case lets the allow outrank the deny.
    spec = write_project(tmp_path, [MANAGER, CAP], [200, 5000])
    (item,) = conflicts(spec)
    assert item["kind"] == "override"
    assert item["content"]["overlap"] == "observed"
    assert item["content"]["witnesses"] == [
        {"scenario": "seed", "candidate": "approve", "intervention": {"state.amount": 5000}}
    ]
    with pytest.raises(ValueError, match="requires accepted reviews"):
        build(spec, tmp_path / "run")


def test_a_pair_no_generated_state_exercises_is_still_reviewed_when_it_can_overlap(tmp_path):
    # Synthesis may later propose amounts above the cap, so the pair is reviewed before it is witnessed.
    (item,) = conflicts(write_project(tmp_path, [MANAGER, CAP], [200, 500]))
    assert item["content"]["overlap"] == "possible"
    assert item["content"]["witnesses"] == []


def test_provably_disjoint_rules_add_no_review_item(tmp_path):
    small = rule("approve-small", "allow", [{"path": "state.amount", "op": "lte", "value": 1000}])
    assert conflicts(write_project(tmp_path, [small, CAP], [200, 5000])) == []


def test_witness_lists_are_bounded_but_counted(tmp_path):
    amounts = [1001 + index for index in range(MAX_WITNESSES + 3)]
    (item,) = conflicts(write_project(tmp_path, [MANAGER, CAP], amounts))
    assert item["content"]["witness_count"] == len(amounts)
    assert len(item["content"]["witnesses"]) == MAX_WITNESSES


def test_an_accepted_override_lets_the_higher_priority_allow_govern(tmp_path):
    spec = write_project(tmp_path, [MANAGER, CAP], [200, 5000])
    status = review_status(spec)
    for item in status["items"]:
        record_review(spec, item["id"], "accepted", "test-reviewer", "Fixture approval.", status["fingerprint"])
    build(spec, tmp_path / "run")
    large = next(e for e in load_run(tmp_path / "run") if e.state["amount"] == 5000)
    verdict = next(v for v in large.verdicts if v.candidate_id == "approve")
    assert verdict.allowed and set(verdict.matched_rules) == {"manager-approves", "cap-large"}


VALUES = [None, True, False, 0, 1, 5, 10, 10.5, 20, "a", "b", [1, 5], ["a"], []]
PREDICATES = [
    Predicate(path="state.x", op="exists", value=True),
    Predicate(path="state.x", op="exists", value=False),
    *[Predicate(path="state.x", op=op, value=v) for op in ("eq", "ne") for v in (1, 5, "a", True, None)],
    *[Predicate(path="state.x", op=op, value=v) for op in ("gt", "gte", "lt", "lte") for v in (1, 5, 10)],
    Predicate(path="state.x", op="in", value=[1, 5]),
    Predicate(path="state.x", op="in", value=["a", "b"]),
    Predicate(path="state.x", op="contains", value=5),
    Predicate(path="state.y", op="eq", value=1),
]


def satisfiable(left, right):
    contexts = [{"state": {}}, *[{"state": {"x": value, "y": 1}} for value in VALUES]]
    return any(
        all(matches_predicate(p, c) for p in left.all_of)
        and (not left.any_of or any(matches_predicate(p, c) for p in left.any_of))
        and all(matches_predicate(p, c) for p in right.all_of)
        and (not right.any_of or any(matches_predicate(p, c) for p in right.any_of))
        for c in contexts
    )


def test_disjointness_is_never_claimed_for_conditions_that_can_co_occur():
    for first, second in itertools.product(PREDICATES, repeat=2):
        left, right = Condition(all_of=[first]), Condition(all_of=[second])
        if conditions_disjoint(left, right):
            assert not satisfiable(left, right), (first, second)


def test_disjointness_recognizes_the_common_exclusive_shapes():
    def disjoint(a, b):
        return conditions_disjoint(Condition(all_of=[Predicate(**a)]), Condition(all_of=[Predicate(**b)]))

    assert disjoint(
        {"path": "candidate.id", "op": "eq", "value": "a"}, {"path": "candidate.id", "op": "eq", "value": "b"}
    )
    assert disjoint(
        {"path": "state.amount", "op": "lte", "value": 100}, {"path": "state.amount", "op": "gt", "value": 100}
    )
    assert disjoint(
        {"path": "state.amount", "op": "exists", "value": False}, {"path": "state.amount", "op": "gt", "value": 0}
    )
    assert disjoint(
        {"path": "state.tier", "op": "in", "value": ["gold"]}, {"path": "state.tier", "op": "eq", "value": "silver"}
    )
    assert not disjoint(
        {"path": "state.amount", "op": "gte", "value": 100}, {"path": "state.amount", "op": "lte", "value": 100}
    )
    any_of = Condition(
        any_of=[
            Predicate(path="state.tier", op="eq", value="gold"),
            Predicate(path="state.tier", op="eq", value="silver"),
        ]
    )
    assert conditions_disjoint(any_of, Condition(all_of=[Predicate(path="state.tier", op="eq", value="bronze")]))


def test_any_of_disjointness_is_never_claimed_for_conditions_that_can_co_occur():
    for required, branches in itertools.product(PREDICATES, itertools.combinations(PREDICATES, 2)):
        left, right = Condition(any_of=list(branches)), Condition(all_of=[required])
        if conditions_disjoint(left, right):
            assert not satisfiable(left, right), (branches, required)
