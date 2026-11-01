import json
import shutil
from collections import Counter

import pytest

from decision_forge.distribution import cell_key
from decision_forge.io import read_json, read_yaml, write_json
from decision_forge.pipeline import build
from decision_forge.planner import (
    AdvisorWeight,
    AdvisorWeights,
    PlanObjective,
    apply_advisor,
    plan_recipe,
)

TOOL_ORDER = [
    "inspect_project",
    "measure_pool",
    "propose_quotas",
    "check_feasibility",
    "plan_expansion",
    "commit_plan",
]


def test_planner_measures_the_real_pool_and_names_the_shortfall(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=20))
    assert plan["status"] == "complete"
    assert [call["tool"] for call in plan["ledger"]] == TOOL_ORDER
    assert plan["feasibility"]["available"] == 336
    assert plan["feasibility"]["requested"] > plan["feasibility"]["available"]
    assert plan["feasibility"]["feasible"] is False
    # This fixture enumerates every declared state, so the shortfall is unreachable rather than
    # synthesizable, and the plan must say so instead of proposing model calls that cannot help.
    assert plan["expansion_requests"] == []
    assert plan["unfillable"], "an unreachable quota must be explained"
    assert plan["cost_estimate"]["model_calls"] == 0
    assert (tmp_path / "plan/quotas.md").exists()
    assert read_json(tmp_path / "plan/expansion-requests.json") == plan["expansion_requests"]
    for target in plan["distribution"]:
        assert 1 <= target["count"] <= 20
        assert target["valid_choices"] == sorted(target["valid_choices"])


def test_feasible_plan_reports_no_shortfall(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=1))
    assert plan["feasibility"]["feasible"] is True
    assert plan["feasibility"]["shortfall"] == 0
    assert plan["expansion_requests"] == []


def test_planner_is_deterministic_and_resumable(example_path, tmp_path):
    objective = PlanObjective(per_cell=5)
    first = plan_recipe(example_path, tmp_path / "plan", objective)
    again = plan_recipe(example_path, tmp_path / "plan", objective)
    assert again["resumed_complete"] is True
    assert again["distribution"] == first["distribution"]
    with pytest.raises(ValueError, match="changed"):
        plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=6))


@pytest.mark.parametrize(
    ("node", "split", "choices", "message"),
    [
        ("refund", "train", ["nobody"], "unknown candidates"),
        ("missing-node", "train", ["execute"], "unknown node"),
    ],
)
def test_planner_rejects_nonsense_explicit_targets(example_path, tmp_path, node, split, choices, message):
    from decision_forge.models import DistributionTarget

    target = DistributionTarget(node_id=node, stratum="routine", split=split, valid_choices=choices, count=2)
    with pytest.raises(ValueError, match=message):
        plan_recipe(example_path, tmp_path / "plan", PlanObjective(mode="explicit", targets=[target]))


def test_planner_rejects_a_target_for_an_unreachable_split(synthesis_project, tmp_path):
    from decision_forge.models import DistributionTarget

    # The single-family fixture assigns its only family to train, so a test quota is unreachable.
    target = DistributionTarget(node_id="refund", stratum="routine", split="test", valid_choices=["execute"], count=1)
    with pytest.raises(ValueError, match="never reaches"):
        plan_recipe(synthesis_project, tmp_path / "plan", PlanObjective(mode="explicit", targets=[target]))


def test_infeasible_plan_is_refused_by_build(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=20))
    assert plan["feasibility"]["feasible"] is False
    with pytest.raises(ValueError, match="infeasible"):
        build(example_path, tmp_path / "run", recipe_plan=tmp_path / "plan/plan.json")


def test_advisor_may_only_reweight_measured_cells():
    cell = cell_key("refund", "routine", "train", ["execute"])
    wanted = {cell: 10}
    available = Counter({cell: 10})
    weights = AdvisorWeights(
        weights=[
            AdvisorWeight(node_id="refund", stratum="routine", split="train", valid_choices=["execute"], weight=0.5)
        ]
    )
    scaled, note = apply_advisor(weights, wanted, available)
    assert scaled == {cell: 5}
    assert note["mode"] == "advisor_scaled"
    outside = AdvisorWeights(
        weights=[
            AdvisorWeight(node_id="refund", stratum="routine", split="test", valid_choices=["execute"], weight=0.5)
        ]
    )
    with pytest.raises(ValueError, match="outside the measured pool"):
        apply_advisor(outside, wanted, available)


def test_advisor_provider_scales_quotas_and_is_recorded(example_path, tmp_path):
    objective = PlanObjective(per_cell=10)
    baseline = plan_recipe(example_path, tmp_path / "baseline", objective)
    target = baseline["distribution"][0]

    class Advisor:
        def __init__(self):
            self.calls = 0

        def generate(self, system, user, schema):
            self.calls += 1
            return {
                "weights": [
                    {
                        "node_id": target["node_id"],
                        "stratum": target["stratum"],
                        "split": target["split"],
                        "valid_choices": target["valid_choices"],
                        "weight": 0.5,
                    }
                ]
            }

    advisor = Advisor()
    plan = plan_recipe(example_path, tmp_path / "advised", objective, provider=advisor)
    assert advisor.calls == 1
    assert plan["proposal"]["mode"] == "advisor_scaled"
    scaled = next(
        row
        for row in plan["distribution"]
        if row["node_id"] == target["node_id"]
        and row["stratum"] == target["stratum"]
        and row["split"] == target["split"]
        and row["valid_choices"] == target["valid_choices"]
    )
    assert scaled["count"] == 5
    # Unmentioned cells are excluded rather than silently kept at full weight.
    assert sum(row["count"] for row in plan["distribution"]) == 5


def test_build_applies_a_matching_plan_and_refuses_a_stale_one(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=1))
    manifest = build(example_path, tmp_path / "run", recipe_plan=tmp_path / "plan/plan.json")
    assert plan["feasibility"]["feasible"] is True
    assert manifest["report"]["accepted"] == len(plan["distribution"])
    assert manifest["report"]["distribution"]["explicit_targets"] is True

    moved = tmp_path / "moved"
    shutil.copytree(example_path.parent, moved)
    spec = moved / example_path.name
    data = read_yaml(spec)
    data["organization"]["policy_version"] = "changed"
    write_json(spec, data)
    with pytest.raises(ValueError, match="different project or source content"):
        build(spec, tmp_path / "run2", recipe_plan=tmp_path / "plan/plan.json")


def test_derived_plans_never_need_synthesis_for_held_out_cells(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=20))
    assert all(request["split"] != "test" for request in plan["expansion_requests"])
    assert plan["held_out_shortfall"]["shortfall"] == 0


def test_explicit_held_out_quota_is_reported_as_unfillable(example_path, tmp_path):
    from decision_forge.models import DistributionTarget

    probe = plan_recipe(example_path, tmp_path / "probe", PlanObjective(per_cell=1))
    held_out = [row for row in probe["feasibility"]["cells"] if row["available"] and '"test"' in row["cell"]]
    if not held_out:
        pytest.skip("this project has no held-out cells")
    row = held_out[0]
    node, stratum, _split, choices = json.loads(row["cell"])
    target = DistributionTarget(
        node_id=node, stratum=stratum, split="test", valid_choices=choices, count=row["available"] + 5
    )
    plan = plan_recipe(example_path, tmp_path / "explicit", PlanObjective(mode="explicit", targets=[target]))
    assert [item["reason"] for item in plan["unfillable"]] == ["held_out_split"]
    assert plan["expansion_requests"] == []


def test_planner_does_not_request_synthesis_for_an_exhausted_state_space(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=20))
    # Every declared node enumerates its full dimension product in this fixture.
    assert all(not row["synthesizable"] for row in plan["synthesizability"].values())
    assert plan["expansion_requests"] == []
    assert plan["unfillable"], "an unreachable quota must be reported, not silently planned"
    assert all("already exist" in row["reason"] for row in plan["unfillable"])


def test_planner_requests_synthesis_when_a_rule_reads_an_open_path(synthesis_project, tmp_path):
    plan = plan_recipe(synthesis_project, tmp_path / "plan", PlanObjective(per_cell=10))
    assert plan["synthesizability"]["refund"]["synthesizable"] is True
    assert "tier" in plan["synthesizability"]["refund"]["reason"]
    assert plan["expansion_requests"], "an open state path is exactly what synthesis is for"


def test_derived_quotas_never_exceed_held_out_supply(example_path, tmp_path):
    plan = plan_recipe(example_path, tmp_path / "plan", PlanObjective(per_cell=50))
    assert plan["held_out_shortfall"]["shortfall"] == 0
    assert plan["held_out_clamped"], "a clamped test cell must be reported"
    for row in plan["held_out_clamped"]:
        assert row["held_out_available"] < row["requested"]


def _duplicate_family_project(tmp_path):
    """Two families share every decision-relevant state, so a build keeps only one copy of each."""
    (tmp_path / "policy.md").write_text("Approve when amount is at most 100. Escalate otherwise.\n", encoding="utf-8")
    evidence = [{"source_id": "policy", "quote": "Approve when amount is at most 100."}]
    write_json(
        tmp_path / "project.json",
        {
            "organization": {
                "id": "org",
                "name": "Org",
                "policy_version": "1",
                "rules": [
                    {
                        "id": "approve-small",
                        "description": "Approve amounts up to 100.",
                        "effect": "allow",
                        "score": 1,
                        "when": {
                            "all_of": [
                                {"path": "candidate.id", "op": "eq", "value": "approve"},
                                {"path": "state.amount", "op": "lte", "value": 100},
                            ]
                        },
                        "evidence": evidence,
                    },
                    {
                        "id": "escalate",
                        "description": "Escalation is always available.",
                        "effect": "allow",
                        "score": 0,
                        "when": {"all_of": [{"path": "candidate.id", "op": "eq", "value": "escalate"}]},
                        "evidence": evidence,
                    },
                ],
            },
            "sources": [{"id": "policy", "path": "policy.md"}],
            "nodes": [
                {
                    "id": "refund",
                    "kind": "select_actions",
                    "question": "How should the refund proceed?",
                    "candidates": [
                        {"id": "approve", "description": "Approve the refund."},
                        {"id": "escalate", "description": "Escalate the refund."},
                    ],
                    "rule_ids": ["approve-small", "escalate"],
                    "dimensions": [{"path": "state.amount", "values": [10, 50, 500, 900]}],
                }
            ],
            "scenarios": [
                {"id": f"s-{family}", "node_id": "refund", "family_id": family, "state": {}, "source_ids": ["policy"]}
                for family in ("family-a", "family-b")
            ],
            "recipe": {"splits": {"train": 1.0, "development": 0.0, "test": 0.0}},
        },
    )
    return tmp_path / "project.json"


@pytest.mark.parametrize(("per_cell", "feasible"), [(2, True), (4, False)])
def test_planner_counts_the_pool_a_build_keeps_after_deduplication(tmp_path, per_cell, feasible):
    spec = _duplicate_family_project(tmp_path)
    plan = plan_recipe(spec, tmp_path / "plan", PlanObjective(mode="uniform_cells", per_cell=per_cell))
    assert plan["feasibility"]["available"] == 4
    assert plan["feasibility"]["feasible"] is feasible
    if feasible:
        build(spec, tmp_path / "run", recipe_plan=tmp_path / "plan/plan.json")
    else:
        with pytest.raises(ValueError, match="infeasible"):
            build(spec, tmp_path / "run", recipe_plan=tmp_path / "plan/plan.json")
