"""Evaluate compiled decisions against independently authored, model-neutral cases."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field, JsonValue

from decision_forge.compatibility import project_payload
from decision_forge.generation import candidate_for, set_path
from decision_forge.io import digest
from decision_forge.models import Contract, DecisionKind, Project, ensure_unique
from decision_forge.policy import best_choices, evaluate, json_equal


class ReferenceCase(Contract):
    id: str = Field(min_length=1)
    kind: DecisionKind
    facts: dict[str, JsonValue]
    expected_action: str = Field(min_length=1)


class DecisionReference(Contract):
    protocol: str
    source: str
    annotation: str
    cases: list[ReferenceCase] = Field(min_length=1)


class ValueMapping(Contract):
    source: JsonValue
    target: JsonValue


class FactBinding(Contract):
    fact: str | list[str] = Field(min_length=1)
    path: str = Field(pattern=r"^(state|candidates)\..+$")
    values: list[ValueMapping] = Field(default_factory=list)


class NodeBinding(Contract):
    node_id: str
    facts: list[FactBinding]
    candidate_actions: dict[str, str]


def evaluate_decisions(
    project: Project, reference: DecisionReference, bindings: dict[str, NodeBinding]
) -> dict[str, Any]:
    """Bindings translate names/values only; they cannot modify rules or expected actions."""
    ensure_unique([case.id for case in reference.cases], "reference case IDs")
    nodes = {node.id: node for node in project.nodes}
    rules = {rule.id: rule for rule in project.organization.rules}
    rows = []
    for case in reference.cases:
        if case.kind not in bindings:
            raise ValueError(f"Missing evaluation binding for {case.kind}")
        binding = bindings[case.kind]
        if binding.node_id not in nodes or nodes[binding.node_id].kind != case.kind:
            raise ValueError("Evaluation binding must identify a node of the reference decision kind")
        node = nodes[binding.node_id]
        if set(binding.candidate_actions) != {c.id for c in node.candidates}:
            raise ValueError("Every node candidate must have exactly one action mapping")
        bound_facts = {name for b in binding.facts for name in ([b.fact] if isinstance(b.fact, str) else b.fact)}
        if not set(case.facts) <= bound_facts:
            raise ValueError("Every reference fact requires an explicit input binding")
        paths = [b.path for b in binding.facts]
        ensure_unique(paths, "evaluation binding paths")
        if any(a != b and b.startswith(a + ".") for a in paths for b in paths):
            raise ValueError("Evaluation binding paths must not overlap")
        state: dict[str, Any] = {}
        candidates = [c.model_copy(deep=True) for c in node.candidates]
        for field in binding.facts:
            names = [field.fact] if isinstance(field.fact, str) else field.fact
            present = [name in case.facts for name in names]
            if any(present) and not all(present):
                raise ValueError("Composite binding cannot discard a supplied fact when another constituent is absent")
            if not any(present):
                continue
            value: JsonValue = case.facts[field.fact] if isinstance(field.fact, str) else [case.facts[n] for n in names]
            if field.values:
                mapped = [row.target for row in field.values if json_equal(row.source, value)]
                if len(mapped) != 1:
                    raise ValueError("Fact translation requires one exact, type-sensitive value mapping")
                value = mapped[0]
            if field.path.startswith("state."):
                set_path(state, field.path.removeprefix("state."), value)
            else:
                resolved = candidate_for(node, field.path)
                # Bind into the working copy, never into the loaded project.
                candidate = next(c for c in candidates if c.id == resolved.id)
                path = field.path.removeprefix(f"candidates.{candidate.id}.")
                if path == "id" or path.startswith("id."):
                    raise ValueError("Evaluation bindings cannot change candidate identifiers")
                set_path(candidate.attributes, path, value)
        verdicts = evaluate(state, candidates, [rules[r] for r in node.rule_ids])
        choices = best_choices(verdicts)
        actions = sorted({binding.candidate_actions[choice] for choice in choices})
        rows.append(
            {
                "id": case.id,
                "kind": case.kind.value,
                "expected": case.expected_action,
                "predicted_actions": actions,
                "predicted_candidates": choices,
                "correct": actions == [case.expected_action],
                "matched_rules": sorted({rule for v in verdicts for rule in v.matched_rules}),
            }
        )
    totals = Counter(row["kind"] for row in rows)
    correct = Counter(row["kind"] for row in rows if row["correct"])
    return {
        "protocol": reference.protocol,
        "project_digest": digest(project_payload(project)),
        "reference_digest": digest(reference.model_dump(mode="json")),
        "bindings_digest": digest({key: value.model_dump(mode="json") for key, value in bindings.items()}),
        "cases": len(rows),
        "correct": sum(correct.values()),
        "accuracy": sum(correct.values()) / len(rows),
        "by_kind": {
            kind: {"cases": count, "correct": correct[kind], "accuracy": correct[kind] / count}
            for kind, count in sorted(totals.items())
        },
        "events": rows,
    }
