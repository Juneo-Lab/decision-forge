import copy
from pathlib import Path

import pytest

from decision_forge.evaluation import DecisionReference, NodeBinding, evaluate_decisions
from decision_forge.io import read_json

ROOT = Path(__file__).parents[1] / "examples/customer_support"


def inputs():
    reference = DecisionReference.model_validate(read_json(ROOT / "decision-reference.json"))
    bindings = {
        key: NodeBinding.model_validate(value) for key, value in read_json(ROOT / "decision-bindings.json").items()
    }
    return reference, bindings


def test_handbook_decisions_include_thresholds_missing_values_and_all_surfaces(project):
    reference, bindings = inputs()
    report = evaluate_decisions(project, reference, bindings)
    assert report["cases"] == report["correct"] == 32
    assert len(report["by_kind"]) == 4
    assert report["accuracy"] == 1


def test_semantic_reference_detects_wrong_threshold_and_ungrounded_candidate_ties(project):
    reference, bindings = inputs()
    original = evaluate_decisions(project, reference, bindings)
    rule = next(r for r in project.organization.rules if r.id == "execute")
    rule.when.any_of[0].value = 1001
    mutated = evaluate_decisions(project, reference, bindings)
    assert mutated["correct"] < original["correct"]
    assert mutated["project_digest"] != original["project_digest"]
    for rule in project.organization.rules:
        rule.when.all_of = [p for p in rule.when.all_of if not p.path.startswith("candidate.")]
    assert evaluate_decisions(project, reference, bindings)["accuracy"] < 0.5


def test_bindings_cannot_drop_facts_or_candidates(project):
    reference, bindings = inputs()
    bindings["allocate_work"].facts.pop()
    with pytest.raises(ValueError, match="Every reference fact"):
        evaluate_decisions(project, reference, bindings)
    _, bindings = inputs()
    bindings["allocate_work"].candidate_actions.pop("human")
    with pytest.raises(ValueError, match="Every node candidate"):
        evaluate_decisions(project, reference, bindings)


def test_bindings_reject_overlapping_paths_and_type_confusion(project):
    reference, bindings = inputs()
    field = bindings["allocate_work"].facts[0]
    field.path = "state.sensitivity.child"
    with pytest.raises(ValueError, match="must not overlap"):
        evaluate_decisions(project, reference, bindings)
    _, bindings = inputs()
    data = bindings["allocate_work"].model_dump()
    data["facts"][0]["values"] = [{"source": 0, "target": False}, {"source": 1, "target": True}]
    bindings["allocate_work"] = NodeBinding.model_validate(data)
    with pytest.raises(ValueError, match="type-sensitive"):
        evaluate_decisions(project, reference, bindings)
    duplicate = copy.deepcopy(reference)
    duplicate.cases.append(duplicate.cases[0])
    with pytest.raises(ValueError, match="reference case IDs"):
        evaluate_decisions(project, duplicate, bindings)


def test_composite_binding_rejects_partial_inputs_instead_of_dropping_facts(project):
    reference, bindings = inputs()
    data = bindings["share_information"].model_dump()
    data["facts"] = [
        {
            "fact": ["recipient", "partner_authorized"],
            "path": "state.recipient",
            "values": [
                {"source": ["internal", False], "target": "internal"},
            ],
        },
        {"fact": "full_approval", "path": "state.full_approval"},
    ]
    bindings["share_information"] = NodeBinding.model_validate(data)
    reference.cases = [case for case in reference.cases if case.kind == "share_information"][:1]
    assert evaluate_decisions(project, reference, bindings)["correct"] == 1
    reference.cases[0].facts.pop("partner_authorized")
    with pytest.raises(ValueError, match="cannot discard a supplied fact"):
        evaluate_decisions(project, reference, bindings)


@pytest.mark.parametrize("name", ["opus55-high", "gpt61sol-high"])
def test_published_live_results_replay_without_network(name):
    from decision_forge.corpus import extraction_metrics
    from decision_forge.io import load_project
    from decision_forge.review import require_review

    path = ROOT.parents[1] / "experiments/2026-10-m1" / name
    reference, _ = inputs()
    bindings = {
        key: NodeBinding.model_validate(value) for key, value in read_json(path / "decision-bindings.json").items()
    }
    report = evaluate_decisions(load_project(path / "project.json"), reference, bindings)
    assert report == read_json(path / "decision-metrics.json")
    assert report["correct"] == 31
    assert (
        extraction_metrics(read_json(path / "claims.json"), read_json(ROOT / "extraction-reference.json"))["matched"]
        == 13
    )
    with pytest.raises(ValueError, match="requires accepted reviews"):
        require_review(path / "project.json")
