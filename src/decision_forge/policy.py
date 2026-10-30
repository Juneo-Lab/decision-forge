"""Small, deterministic policy interpreter. No Python evaluation or generated code."""

from __future__ import annotations

from typing import Any

from decision_forge.models import Candidate, CandidateVerdict, Condition, Predicate, Rule

MISSING = object()


def json_equal(left: Any, right: Any) -> bool:
    """JSON equality with recursive type identity, including nested booleans."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right, strict=True))
    return bool(left == right)


def lookup(root: dict[str, Any], path: str) -> Any:
    value: Any = root
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def matches_predicate(predicate: Predicate, context: dict[str, Any]) -> bool:
    actual = lookup(context, predicate.path)
    expected = predicate.value
    if predicate.op == "exists":
        if not isinstance(expected, bool):
            raise ValueError("The exists operator requires a boolean value")
        return (actual is not MISSING) == expected
    if actual is MISSING:
        return False
    if predicate.op in {"eq", "ne"}:
        equal = json_equal(actual, expected)
        return equal if predicate.op == "eq" else not equal
    if predicate.op == "in":
        if not isinstance(expected, list):
            raise ValueError("The in operator requires a list")
        return any(json_equal(actual, item) for item in expected)
    if predicate.op == "contains":
        if not isinstance(actual, list):
            return False
        return any(json_equal(item, expected) for item in actual)
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, (float, int)) or not isinstance(expected, (float, int)):
        return False
    return {
        "gt": actual > expected,
        "gte": actual >= expected,
        "lt": actual < expected,
        "lte": actual <= expected,
    }[predicate.op]


def matches(condition: Condition, context: dict[str, Any]) -> bool:
    return all(matches_predicate(p, context) for p in condition.all_of) and (
        not condition.any_of or any(matches_predicate(p, context) for p in condition.any_of)
    )


def evaluate(state: dict[str, Any], candidates: list[Candidate], rules: list[Rule]) -> list[CandidateVerdict]:
    verdicts = []
    for candidate in candidates:
        context = {"state": state, "candidate": {**candidate.attributes, "id": candidate.id}}
        matched = [rule for rule in rules if matches(rule.when, context)]
        priority = max((rule.priority for rule in matched), default=0)
        governing = [rule for rule in matched if rule.priority == priority]
        allowed = bool(governing) and all(rule.effect == "allow" for rule in governing)
        score = max((rule.score for rule in governing), default=0) if allowed else 0
        verdicts.append(
            CandidateVerdict(
                candidate_id=candidate.id,
                allowed=allowed,
                score=score,
                matched_rules=[r.id for r in matched],
            )
        )
    return verdicts


def best_choices(verdicts: list[CandidateVerdict]) -> list[str]:
    valid = [v for v in verdicts if v.allowed]
    if not valid:
        return []
    score = max(v.score for v in valid)
    return sorted(v.candidate_id for v in valid if v.score == score)
