import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from decision_forge.cli import app
from decision_forge.io import load_project
from decision_forge.traces import ToolCall, audit_trace, load_mapping, read_trace

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/agent_delegation"
TRACES = EXAMPLE / "traces"


def audit(organization, calls=None):
    project = load_project(EXAMPLE / organization / "project.yaml")
    mapping = load_mapping(TRACES / "mapping.yaml", project)
    return audit_trace(project, calls or read_trace(TRACES / "session.jsonl"), mapping)


def row(report, call):
    return next(r for r in report["rows"] if r["call"] == call)


def test_json_lines_and_opentelemetry_exports_yield_the_same_calls():
    otlp = read_trace(TRACES / "session.otlp.json")
    assert otlp == read_trace(TRACES / "session.jsonl")
    # The export also holds a chat span; only execute_tool spans are tool calls.
    assert len(otlp) == 8


def test_the_same_session_is_judged_by_each_organizations_standard():
    lumen, halcyon = audit("lumen-labs"), audit("halcyon-health")
    assert lumen["summary"] == {"aligned": 5, "permitted": 1, "not_permitted": 1, "unmapped": 1}
    assert halcyon["summary"] == {"aligned": 1, "permitted": 1, "not_permitted": 5, "unmapped": 1}
    refund = row(halcyon, "call-02")
    assert (refund["status"], refund["policy_prefers"]) == ("not_permitted", ["hand-back"])
    assert refund["policy_basis"] == "Every other refund for a verified patient goes to a person in patient accounts."
    assert row(lumen, "call-02")["status"] == "aligned"


def test_an_unnecessary_hand_back_is_permitted_but_not_preferred():
    escalation = row(audit("lumen-labs"), "call-08")
    assert (escalation["status"], escalation["action"], escalation["policy_prefers"]) == (
        "permitted",
        "hand-back",
        ["proceed"],
    )


def test_a_refusing_deny_rule_is_reported():
    call = ToolCall(
        id="over-delegation",
        tool="agents.delegate",
        arguments={"target": {"verified": True}, "scope": {"exceeds_delegator": True}},
    )
    result = row(audit("lumen-labs", [call]), "over-delegation")
    assert result["status"] == "not_permitted"
    assert result["refused_by"] == "An agent must never delegate more authority than it holds."
    assert result["policy_prefers"] == ["block"]


def test_values_the_dataset_never_generates_are_coverage_gaps():
    report = audit("halcyon-health")
    assert report["coverage_gaps"] == 1
    assert row(report, "call-02")["uncovered"] == ["amount"]
    assert row(report, "call-04")["status"] == "unmapped"


def test_mappings_must_name_declared_nodes_and_candidates(tmp_path):
    project = load_project(EXAMPLE / "lumen-labs/project.yaml")
    for entry, message in [
        ({"node": "missing", "candidate": "proceed"}, "unknown node"),
        ({"node": "refund", "candidate": "approve"}, "unknown candidate"),
    ]:
        path = tmp_path / "mapping.json"
        path.write_text(json.dumps({"tools": {"payments.refund": entry}}), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_mapping(path, project)


def test_missing_arguments_and_unreadable_traces_are_errors(tmp_path):
    with pytest.raises(ValueError, match="no argument customer.verified"):
        audit("lumen-labs", [ToolCall(id="c", tool="payments.refund", arguments={"amount": 40})])
    trace = tmp_path / "trace.txt"
    trace.write_text("not a trace\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON Lines tool calls or an OTLP JSON export"):
        read_trace(trace)


def test_cli_reports_the_summary_and_writes_the_full_audit(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "audit-trace",
            str(EXAMPLE / "halcyon-health/project.yaml"),
            str(TRACES / "session.otlp.json"),
            "--mapping",
            str(TRACES / "mapping.yaml"),
            "--output",
            str(tmp_path / "audit.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["not_permitted"] == 5
    assert len(json.loads((tmp_path / "audit.json").read_text())["rows"]) == 8
