import pytest

from decision_forge.models import Candidate, Condition, Evidence, Predicate, Rule
from decision_forge.policy import best_choices, evaluate, matches_predicate


def make_rule(effect="allow", priority=0, score=0):
    return Rule(
        id=f"{effect}-{priority}-{score}",
        description="Fixture rule",
        when=Condition(),
        effect=effect,
        priority=priority,
        score=score,
        evidence=[Evidence(source_id="fixture", quote="fixture")],
    )


def test_deny_wins_at_equal_priority():
    results = evaluate({}, [Candidate(id="send", description="Send")], [make_rule(), make_rule("deny")])
    assert best_choices(results) == []


def test_higher_priority_override_and_ties():
    candidates = [Candidate(id=v, description=v) for v in ["a", "b"]]
    rules = [make_rule("deny"), make_rule("allow", priority=10, score=2)]
    assert best_choices(evaluate({}, candidates, rules)) == ["a", "b"]


def test_default_deny():
    assert not evaluate({}, [Candidate(id="a", description="A")], [])[0].allowed


@pytest.mark.parametrize(("op", "value"), [("eq", None), ("ne", 1), ("gt", 0), ("in", [None])])
def test_missing_values_never_satisfy_comparisons(op, value):
    assert not matches_predicate(Predicate(path="state.absent", op=op, value=value), {"state": {}})


def test_boolean_is_not_numeric_authorization():
    assert not matches_predicate(Predicate(path="state.amount", op="lte", value=1000), {"state": {"amount": True}})
    assert not matches_predicate(Predicate(path="state.approved", op="eq", value=True), {"state": {"approved": 1}})


def test_explicit_missing_value_condition():
    assert matches_predicate(Predicate(path="state.amount", op="exists", value=False), {"state": {}})


def test_refund_boundary_and_approval(project):
    node = next(n for n in project.nodes if n.id == "refund")
    rules = [r for r in project.organization.rules if r.id in node.rule_ids]
    assert best_choices(evaluate({"amount": 1000, "approved": False}, node.candidates, rules)) == ["execute"]
    assert best_choices(evaluate({"amount": 1001, "approved": False}, node.candidates, rules)) == ["request_approval"]
    assert best_choices(evaluate({"amount": 1001, "approved": True}, node.candidates, rules)) == ["execute"]
    assert best_choices(evaluate({"approved": False}, node.candidates, rules)) == ["clarify"]


def test_candidate_order_is_semantically_invariant(project):
    node = next(n for n in project.nodes if n.id == "disclosure")
    rules = [r for r in project.organization.rules if r.id in node.rule_ids]
    state = {"recipient": "partner", "full_approval": False, "partner_authorized": True}
    assert best_choices(evaluate(state, node.candidates, rules)) == ["summary"]
    assert best_choices(evaluate(state, list(reversed(node.candidates)), rules)) == ["summary"]
