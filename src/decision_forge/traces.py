"""Audit recorded agent tool calls against an organization's declared policy.

A trace is what an agent actually did. A mapping says which decision node each tool call is, which
candidate executing it corresponds to, and where its decision-time state lives in the call arguments.
Replaying each call through the policy interpreter shows whether the organization's own standard
allowed it, which outcome that standard prefers, and which situations the dataset does not yet cover.

Traces are read from JSON Lines (one ``{"id", "tool", "arguments"}`` object per line) or from OTLP JSON
exports of OpenTelemetry GenAI ``execute_tool`` spans (``gen_ai.tool.name``, ``gen_ai.tool.call.id``
and ``gen_ai.tool.call.arguments``). Nothing is sent anywhere; the audit runs offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

from decision_forge.io import MAX_SOURCE_BYTES, read_yaml
from decision_forge.models import CandidateIdentifier, Contract, Identifier, Project
from decision_forge.policy import MISSING, best_choices, evaluate, governing_rule, json_equal, lookup

MAX_TRACE_BYTES = 8 * MAX_SOURCE_BYTES


class ToolCall(Contract):
    id: str = Field(min_length=1, max_length=256)
    tool: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolMapping(Contract):
    node: Identifier
    candidate: CandidateIdentifier = Field(description="The outcome that executing this call amounts to.")
    state: dict[str, str] = Field(
        default_factory=dict, description="Decision-state field to dotted argument path, e.g. amount: payment.amount"
    )


class TraceMapping(Contract):
    schema_version: Literal["1.0"] = "1.0"
    tools: dict[str, ToolMapping] = Field(min_length=1)


def load_mapping(path: Path, project: Project) -> TraceMapping:
    mapping = TraceMapping.model_validate(read_yaml(path))
    for tool, entry in mapping.tools.items():
        node = project.find_node(entry.node)
        if node is None:
            raise ValueError(f"Mapping for {tool} names an unknown node: {entry.node}")
        if entry.candidate not in {candidate.id for candidate in node.candidates}:
            raise ValueError(f"Mapping for {tool} names an unknown candidate: {entry.candidate}")
    return mapping


def read_trace(path: Path) -> list[ToolCall]:
    """Tool calls from JSON Lines or an OTLP JSON export, in recorded order."""
    if path.stat().st_size > MAX_TRACE_BYTES:
        raise ValueError("Trace exceeds the size limit")
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, dict) and "resourceSpans" in document:
        return _otlp_tool_calls(document)
    try:
        return [ToolCall.model_validate_json(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        raise ValueError("Trace must be JSON Lines tool calls or an OTLP JSON export") from None


def audit_trace(project: Project, calls: list[ToolCall], mapping: TraceMapping) -> dict[str, Any]:
    """Replay each mapped call through the policy and summarize where the agent and policy differ."""
    rows: list[dict[str, Any]] = []
    for call in calls:
        entry = mapping.tools.get(call.tool)
        if entry is None:
            rows.append({"call": call.id, "tool": call.tool, "status": "unmapped"})
            continue
        state = {}
        for field, argument in entry.state.items():
            value = lookup(call.arguments, argument)
            if value is MISSING:
                raise ValueError(f"Call {call.id} has no argument {argument} for {field}")
            state[field] = value
        node = project.node(entry.node)
        rules = project.select_rules(node.rule_ids)
        verdicts = evaluate(state, node.candidates, rules)
        preferred = best_choices(verdicts)
        by_candidate = {verdict.candidate_id: verdict for verdict in verdicts}
        taken = by_candidate[entry.candidate]
        if entry.candidate in preferred:
            status = "aligned"
        elif taken.allowed:
            status = "permitted"
        else:
            status = "not_permitted"
        decisive = governing_rule(rules, by_candidate[preferred[0]]) if preferred else None
        refusal = None if taken.allowed else governing_rule(rules, taken)
        rows.append(
            {
                "call": call.id,
                "tool": call.tool,
                "node": node.id,
                "state": state,
                "action": entry.candidate,
                "status": status,
                "policy_prefers": preferred,
                "policy_basis": decisive.evidence[0].quote if decisive else None,
                "refused_by": refusal.evidence[0].quote if refusal else None,
                "uncovered": _uncovered(node.dimensions, state),
            }
        )
    statuses = [row["status"] for row in rows]
    return {
        "organization_id": project.organization.id,
        "policy_version": project.organization.policy_version,
        "calls": len(rows),
        "summary": {name: statuses.count(name) for name in ("aligned", "permitted", "not_permitted", "unmapped")},
        "coverage_gaps": sum(bool(row.get("uncovered")) for row in rows),
        "rows": rows,
    }


def _uncovered(dimensions: Any, state: dict[str, Any]) -> list[str]:
    """State fields whose observed value the node's declared dimensions never generate."""
    gaps = []
    for dimension in dimensions:
        field = dimension.path.removeprefix("state.")
        if field in state and not any(json_equal(state[field], value) for value in dimension.values):
            gaps.append(field)
    return gaps


def _otlp_tool_calls(document: dict[str, Any]) -> list[ToolCall]:
    calls = []
    for resource in document.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                attributes = {item["key"]: _any_value(item.get("value", {})) for item in span.get("attributes", [])}
                if attributes.get("gen_ai.operation.name") != "execute_tool" or "gen_ai.tool.name" not in attributes:
                    continue
                arguments = attributes.get("gen_ai.tool.call.arguments", {})
                if isinstance(arguments, str):
                    # The conventions allow a serialized object when instrumentation cannot decode it.
                    arguments = json.loads(arguments)
                calls.append(
                    ToolCall(
                        id=str(attributes.get("gen_ai.tool.call.id") or span.get("spanId")),
                        tool=str(attributes["gen_ai.tool.name"]),
                        arguments=arguments,
                    )
                )
    return calls


def _any_value(value: dict[str, Any]) -> Any:
    """Decode an OTLP JSON AnyValue; 64-bit integers arrive as strings."""
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "arrayValue" in value:
        return [_any_value(item) for item in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return {item["key"]: _any_value(item.get("value", {})) for item in value["kvlistValue"].get("values", [])}
    return None
