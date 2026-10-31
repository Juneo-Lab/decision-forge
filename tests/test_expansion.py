import json
from pathlib import Path

import pytest

from decision_forge.expansion import (
    ExpansionOptions,
    ExpansionRequest,
    ProposalBatch,
    expand,
    load_expansion,
)
from decision_forge.io import read_json, write_json
from decision_forge.models import Project
from decision_forge.pipeline import build, load_run


class ScriptedStates:
    """Deterministic synthesis fixture; one batch of proposed states per call."""

    def __init__(self, batches, usage=None):
        self.batches = batches
        self.calls = 0
        self.last_usage = usage or {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.002}
        self.last_response_metadata = {"id": "fixture"}

    def generate(self, system, user, schema):
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return {"states": batch}

    def input_bytes(self, system, user, schema):
        return len(user.encode())


GOLD = [{"bindings": [{"path": "state.tier", "value": "gold"}], "rationale": "Gold accounts escalate."}]


def escalate_request(count=1, stratum="boundary"):
    return ExpansionRequest(node_id="refund", stratum=stratum, split="train", valid_choices=["escalate"], count=count)


def snapshot(run):
    return Project.model_validate(read_json(run / "project.snapshot.json")), read_json(run / "manifest.json")


def test_synthesis_publishes_only_policy_verified_states(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    summary = expand(run, [escalate_request()], tmp_path / "expansion", ScriptedStates([GOLD]))
    assert summary["status"] == "complete"
    assert summary["published"] == 1
    assert summary["quarantine"] == 0
    assert summary["usage"]["per_published_example_usd"] == pytest.approx(0.002)

    project, manifest = snapshot(run)
    episodes, _ = load_expansion(tmp_path / "expansion", project, manifest["source_hashes"])
    assert episodes[0].valid_choices == ["escalate"]
    assert episodes[0].state["tier"] == "gold"
    assert episodes[0].label_basis == "model_proposed_policy_verified"
    assert episodes[0].split == "train"


def test_verified_synthesis_is_merged_into_a_build(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    batches = [
        GOLD,
        [
            {
                "bindings": [
                    {"path": "state.tier", "value": "silver"},
                    {"path": "state.amount", "value": 150},
                ],
                "rationale": "Silver account with a large refund.",
            }
        ],
    ]
    expand(run, [escalate_request(count=2)], tmp_path / "expansion", ScriptedStates(batches))
    report = build(synthesis_project, tmp_path / "merged", expansion=tmp_path / "expansion")["report"]
    assert report["synthesized"] == 2
    assert report["generation_cost"]["provider_calls"] == 2
    assert report["generation_cost"]["synthesized_examples"] == 2
    assert report["generation_cost"]["scope"] == "offline_generation_plus_verified_synthesis"
    synthesized = [e for e in load_run(tmp_path / "merged") if e.label_basis == "model_proposed_policy_verified"]
    assert len(synthesized) == 2
    assert all(e.split != "test" for e in synthesized)


def test_wrong_labels_are_quarantined_not_published(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    request = ExpansionRequest(node_id="refund", stratum="boundary", split="train", valid_choices=["execute"], count=1)
    wrong = [
        {
            "bindings": [{"path": "state.tier", "value": "gold"}, {"path": "state.amount", "value": 150}],
            "rationale": "Claims this executes a refund.",
        }
    ]
    with pytest.raises(ValueError, match="No proposed state survived"):
        expand(run, [request], tmp_path / "expansion", ScriptedStates([wrong]))
    quarantine = read_json(tmp_path / "expansion/quarantine.json")
    assert quarantine[0]["request"]["valid_choices"] == ["execute"]
    assert quarantine[0]["reason"].startswith("Policy replay produced")
    assert read_json(tmp_path / "expansion/expansion.json")["published"] == 0


def test_duplicate_and_out_of_domain_proposals_are_quarantined(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    request = ExpansionRequest(node_id="refund", stratum="routine", split="train", valid_choices=["execute"], count=1)
    batch = [
        {"bindings": [{"path": "state.amount", "value": 50}], "rationale": "Restates the pool."},
        {"bindings": [{"path": "state.amount", "value": 5000}], "rationale": "Outside the dimension."},
        {"bindings": [{"path": "state.unknown", "value": True}], "rationale": "Undeclared path."},
    ]
    with pytest.raises(ValueError, match="No proposed state survived"):
        expand(run, [request], tmp_path / "expansion", ScriptedStates([batch]))
    reasons = [row["reason"] for row in read_json(tmp_path / "expansion/quarantine.json")]
    assert any("outside the declared dimension domain" in reason for reason in reasons)
    assert any("undeclared state path" in reason for reason in reasons)


def test_budget_exhaustion_resumes_without_republishing(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    provider = ScriptedStates(
        [
            GOLD,
            [
                {
                    "bindings": [
                        {"path": "state.tier", "value": "platinum"},
                        {"path": "state.amount", "value": 150},
                    ],
                    "rationale": "Platinum account with a large refund.",
                }
            ],
        ]
    )
    request = escalate_request(count=2)
    with pytest.raises(ValueError, match="budget exhausted"):
        expand(run, [request], tmp_path / "expansion", provider, ExpansionOptions(states_per_call=1, max_calls=1))
    assert read_json(tmp_path / "expansion/expansion.json")["status"] == "budget_exhausted"
    assert provider.calls == 1
    summary = expand(run, [request], tmp_path / "expansion", provider, ExpansionOptions(states_per_call=1, max_calls=2))
    assert summary["published"] == 2
    lines = (tmp_path / "expansion/accepted.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert len({json.loads(line)["id"] for line in lines}) == 2


def test_failed_provider_call_resumes_without_duplicating_publication(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)

    class Flaky(ScriptedStates):
        def __init__(self, batches, fail_first=True):
            super().__init__(batches)
            self.fail_first = fail_first

        def generate(self, system, user, schema):
            if self.fail_first:
                self.fail_first = False
                raise ValueError("Injected provider outage")
            return super().generate(system, user, schema)

    with pytest.raises(ValueError, match="outage"):
        expand(run, [escalate_request()], tmp_path / "expansion", Flaky([GOLD]))
    summary = expand(run, [escalate_request()], tmp_path / "expansion", Flaky([GOLD], fail_first=False))
    assert summary["published"] == 1
    rows = (tmp_path / "expansion/accepted.jsonl").read_text().strip().splitlines()
    assert len(rows) == 1


def test_tampered_expansion_is_refused_by_the_build(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    expand(run, [escalate_request()], tmp_path / "expansion", ScriptedStates([GOLD]))
    rows = [json.loads(line) for line in (tmp_path / "expansion/accepted.jsonl").read_text().splitlines() if line]
    rows[0]["valid_choices"] = ["execute"]
    (tmp_path / "expansion/accepted.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="integrity"):
        build(synthesis_project, tmp_path / "merged", expansion=tmp_path / "expansion")


def test_requests_cannot_target_held_out_splits():
    with pytest.raises(ValueError):
        ExpansionRequest(node_id="refund", split="test", valid_choices=["execute"], count=1)


def test_batch_contract_validates_request_files(tmp_path):
    batch = ProposalBatch.model_validate(
        {"requests": [{"node_id": "refund", "valid_choices": ["execute"], "count": 1}]}
    )
    assert batch.requests[0].split == "train"
    write_json(tmp_path / "requests.json", [row.model_dump(mode="json") for row in batch.requests])
    assert read_json(tmp_path / "requests.json")[0]["count"] == 1


def test_prompt_states_the_requested_outcome_without_asking_for_a_label():
    from decision_forge.expansion import _merge_domains, allowed_state_paths, build_prompt
    from decision_forge.io import load_project

    project = load_project(Path("examples/customer_support/project.yaml"))
    node = next(n for n in project.nodes if n.id == "refund")
    request = ExpansionRequest(
        node_id="refund", stratum="boundary", split="train", valid_choices=["request_approval"], count=3
    )
    prompt = build_prompt(project, node, request, _merge_domains(allowed_state_paths(project, node)), 3)
    assert '"request_approval"' in prompt
    assert '"target_outcome"' in prompt
    assert "never a label you supply" in prompt


def test_declared_dimension_owns_its_value_space(example_path):
    from decision_forge.expansion import allowed_state_paths
    from decision_forge.io import load_project

    project = load_project(example_path)
    node = next(n for n in project.nodes if n.id == "refund")
    domains = allowed_state_paths(project, node)
    assert domains["amount"] == {"kind": "dimension", "values": [500, 1000, 1001, 5000]}


def test_partial_success_publishes_verified_rows_and_reports_the_shortfall(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    request = escalate_request(count=3)
    # One verified state, then the model repeats itself until the empty-round guard trips.
    summary = expand(run, [request], tmp_path / "expansion", ScriptedStates([GOLD]))
    assert summary["status"] == "complete"
    assert summary["published"] == 1
    assert summary["complete_requests"] is False
    assert summary["shortfalls"] == [{"request": request.model_dump(mode="json"), "requested": 3, "verified": 1}]
