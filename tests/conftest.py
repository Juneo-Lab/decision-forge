from pathlib import Path

import pytest

from decision_forge.io import load_project, write_json
from decision_forge.models import Project

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/customer_support/project.yaml"


@pytest.fixture
def example_path() -> Path:
    return EXAMPLE


@pytest.fixture
def project() -> Project:
    return load_project(EXAMPLE)


SYNTHESIS_POLICY = """Refunds up to 100 units may be executed immediately.
Refunds above 100 units require an escalation review.
An unknown refund amount requires clarification from the requester.
Gold tier accounts always receive an escalation review.
"""


QUOTES = {
    "execute-small": "Refunds up to 100 units may be executed immediately.",
    "escalate-large": "Refunds above 100 units require an escalation review.",
    "clarify-missing": "An unknown refund amount requires clarification from the requester.",
    "tier-gold": "Gold tier accounts always receive an escalation review.",
}


@pytest.fixture
def synthesis_project(tmp_path: Path) -> Path:
    """A project whose ``state.tier`` is decision-relevant but not a declared dimension.

    Deterministic generation can never vary it, so synthesis is the only way to reach those states.
    """
    folder = tmp_path / "synthesis"
    (folder / "sources").mkdir(parents=True)
    (folder / "sources/policy.md").write_text(SYNTHESIS_POLICY)
    rules = []
    for rule_id, candidate, conditions, score in [
        ("execute-small", "execute", [{"path": "state.amount", "op": "lte", "value": 100}], 2),
        ("escalate-large", "escalate", [{"path": "state.amount", "op": "gt", "value": 100}], 2),
        ("clarify-missing", "clarify", [{"path": "state.amount", "op": "exists", "value": False}], 2),
        ("tier-gold", "escalate", [{"path": "state.tier", "op": "eq", "value": "gold"}], 5),
    ]:
        quote = QUOTES[rule_id]
        rules.append(
            {
                "id": rule_id,
                "description": quote,
                "when": {
                    "all_of": [
                        {"path": "candidate.id", "op": "eq", "value": candidate},
                        *conditions,
                    ]
                },
                "effect": "allow",
                "score": score,
                "evidence": [{"source_id": "handbook", "quote": quote}],
            }
        )
    project = {
        "schema_version": "1.0",
        "organization": {"id": "synthesis-co", "name": "Synthesis Co", "policy_version": "1", "rules": rules},
        "sources": [{"id": "handbook", "path": "sources/policy.md", "version": "1"}],
        "nodes": [
            {
                "id": "refund",
                "kind": "select_actions",
                "question": "Which refund action applies?",
                "candidates": [
                    {"id": "execute", "description": "Execute the refund."},
                    {"id": "escalate", "description": "Send the refund for escalation review."},
                    {"id": "clarify", "description": "Ask the requester for the amount."},
                ],
                "rule_ids": ["execute-small", "escalate-large", "clarify-missing", "tier-gold"],
                "dimensions": [{"path": "state.amount", "values": [50, 150]}],
            }
        ],
        "scenarios": [
            {
                "id": "seed",
                "node_id": "refund",
                "family_id": "family",
                "state": {"amount": 50},
                "source_ids": ["handbook"],
                "stratum": "routine",
            }
        ],
        "recipe": {"seed": 7, "variants_per_scenario": 8},
    }
    path = folder / "project.json"
    write_json(path, project)
    return path
