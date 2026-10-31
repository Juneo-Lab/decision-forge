import pytest

from decision_forge.pipeline import build, load_run, load_run_project
from decision_forge.transformations import (
    PresentationRewrite,
    rewrite_presentation,
    verify_presentation,
)

PARAPHRASE = {
    "question": "Which refund action applies, and to which candidate?",
    "candidate_descriptions": {
        "execute": "Carry out the refund right away.",
        "escalate": "Send the refund for an escalation review.",
        "clarify": "Ask the requester for the amount.",
    },
    "rationale": "Reworded for presentation variety.",
}


class Scripted:
    def __init__(self, rewrite=None, verdict=None):
        self.rewrite = rewrite or PARAPHRASE
        self.verdict = verdict or {"equivalent": True, "reason": "Same options and numbers."}
        self.calls = []

    def generate(self, system, user, schema):
        self.calls.append(system.splitlines()[0] if system else "")
        if "equivalent" in schema.get("properties", {}):
            return self.verdict
        return self.rewrite


def parent_for(run):
    return next(e for e in load_run(run) if e.node_id == "refund" and e.valid_choices == ["execute"])


def test_verified_rewrite_keeps_the_decision_and_records_provenance(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    parent = parent_for(run)
    provider = Scripted()
    child = rewrite_presentation(load_run_project(run), parent, provider)
    assert set(child.valid_choices) == set(parent.valid_choices)
    assert child.question == PARAPHRASE["question"]
    assert {c.id for c in child.candidates} == {c.id for c in parent.candidates}
    assert child.id != parent.id and child.state == parent.state
    assert "presentation.rewrite" in child.intervention
    assert child.verifier == "policy-engine-v1+presentation-verifier-v1"
    assert len(provider.calls) == 2, "rewrite and verification are separate model calls"


def test_changed_numbers_are_rejected_before_any_verification(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    parent = parent_for(run)
    descriptions = {**PARAPHRASE["candidate_descriptions"], "execute": "Execute refunds up to 200 units."}
    tampered = {**PARAPHRASE, "candidate_descriptions": descriptions}
    provider = Scripted(rewrite=tampered)
    with pytest.raises(ValueError, match="numeric value"):
        rewrite_presentation(load_run_project(run), parent, provider)
    assert len(provider.calls) == 1, "the verifier must not be consulted for a failed invariant"


def test_verifier_rejection_blocks_publication(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    parent = parent_for(run)
    provider = Scripted(verdict={"equivalent": False, "reason": "Adds a new condition."})
    with pytest.raises(ValueError, match="verifier rejected"):
        rewrite_presentation(load_run_project(run), parent, provider)


def test_rewrite_must_describe_every_candidate(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    parent = parent_for(run)
    incomplete = {
        "question": PARAPHRASE["question"],
        "candidate_descriptions": {
            "execute": "Carry out the refund right away.",
            "escalate": "Send the refund for an escalation review.",
        },
        "rationale": "Dropped an option.",
    }
    with pytest.raises(ValueError, match="exactly the declared candidates"):
        rewrite_presentation(load_run_project(run), parent, Scripted(rewrite=incomplete))


def test_verify_presentation_rejects_implausible_lengths(synthesis_project, tmp_path):
    run = tmp_path / "run"
    build(synthesis_project, run)
    parent = parent_for(run)
    bloated = {
        "question": PARAPHRASE["question"] * 20,
        "candidate_descriptions": PARAPHRASE["candidate_descriptions"],
        "rationale": "Too long.",
    }
    with pytest.raises(ValueError, match="length"):
        verify_presentation(parent, PresentationRewrite.model_validate(bloated), Scripted().generate)
