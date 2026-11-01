"""Small, deterministic policy interpreter. No Python evaluation or generated code."""

from __future__ import annotations

from typing import Any

from decision_forge.models import ORDERING_OPERATORS, Candidate, CandidateVerdict, Condition, Predicate, Rule

MISSING = object()


def json_equal(left: Any, right: Any) -> bool:
    """JSON equality with recursive type identity, except that numbers compare by value.

    Booleans never equal numbers. Integers and floats do compare equal, because YAML sources
    and model-generated JSON legitimately disagree about ``1000`` versus ``1000.0``.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and bool(left) == bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
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


def conditions_disjoint(left: Condition, right: Condition) -> bool:
    """Whether no context can satisfy both conditions, judged conservatively.

    ``True`` is a proof under the interpreter's semantics: some predicate one condition requires
    rules out a predicate the other requires, or every ``any_of`` branch of one side is ruled out by
    the requirements it would have to meet together. ``False`` means the conditions may overlap.
    """
    for one, other in ((left, right), (right, left)):
        required = [*one.all_of, *other.all_of]
        if any(_excludes(predicate, requirement) for predicate in one.all_of for requirement in other.all_of):
            return True
        if one.any_of and all(any(_excludes(branch, r) for r in required) for branch in one.any_of):
            return True
    return False


def _excludes(first: Predicate, second: Predicate) -> bool:
    """Whether two predicates on the same path can never hold for the same value."""
    if first.path != second.path:
        return False
    for one, other in ((first, second), (second, first)):
        if one.op == "exists":
            # Every operator except 'exists' requires the value to be present.
            return (other.op == "exists" and one.value != other.value) or (one.value is False and other.op != "exists")
        if one.op == "eq":
            return not _holds(other, one.value)
        if one.op == "in" and isinstance(one.value, list):
            return not any(_holds(other, item) for item in one.value)
    if first.op in ORDERING_OPERATORS and second.op in ORDERING_OPERATORS:
        return _intervals_disjoint(first, second)
    # An ordering needs a number, 'contains' needs a list.
    operators = {first.op, second.op}
    return bool(operators & ORDERING_OPERATORS) and "contains" in operators


def _holds(predicate: Predicate, value: Any) -> bool:
    context: dict[str, Any] = {}
    *parents, leaf = predicate.path.split(".")
    node = context
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value
    return matches_predicate(predicate, context)


def _intervals_disjoint(first: Predicate, second: Predicate) -> bool:
    lower = [p for p in (first, second) if p.op in {"gt", "gte"}]
    upper = [p for p in (first, second) if p.op in {"lt", "lte"}]
    if len(lower) != 1 or len(upper) != 1:
        return False
    low, high = lower[0].value, upper[0].value
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return False
    return low > high or (low == high and (lower[0].op == "gt" or upper[0].op == "lt"))


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
