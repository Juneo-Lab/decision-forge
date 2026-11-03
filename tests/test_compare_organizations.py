import runpy
import shutil
from pathlib import Path

import pytest

from decision_forge.io import load_project
from decision_forge.pipeline import build

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/agent_delegation"
SPECS = [EXAMPLE / "lumen-labs/project.yaml", EXAMPLE / "halcyon-health/project.yaml"]
script = runpy.run_path(str(ROOT / "scripts/compare_organizations.py"))


@pytest.fixture(scope="module")
def comparison(tmp_path_factory):
    return script["compare"](SPECS, tmp_path_factory.mktemp("workspace"))


def decision(comparison, node_id, **situation):
    row = next(r for r in comparison["rows"] if r["node_id"] == node_id and r["situation"] == situation)
    return {org: d["choice"] for org, d in row["decisions"].items()}


def test_the_committed_comparison_is_current(comparison):
    assert (EXAMPLE / "COMPARISON.md").read_text(encoding="utf-8") == script["render_markdown"](comparison)


def test_organizations_share_situations_but_not_rules():
    lumen, halcyon = (load_project(spec) for spec in SPECS)
    assert [n.model_dump(exclude={"rule_ids"}) for n in lumen.nodes] == [
        n.model_dump(exclude={"rule_ids"}) for n in halcyon.nodes
    ]
    assert lumen.scenarios == halcyon.scenarios
    assert {r.evidence[0].quote for r in lumen.organization.rules}.isdisjoint(
        {r.evidence[0].quote for r in halcyon.organization.rules}
        - {"An agent must never delegate more authority than it holds."}
    )


def test_the_same_action_is_decided_by_each_organizations_own_standard(comparison):
    assert len(comparison["rows"]) == 17
    assert sum(row["differs"] for row in comparison["rows"]) == 8
    assert decision(comparison, "refund", amount=400, customer_verified=True) == {
        "lumen-labs": "proceed",
        "halcyon-health": "hand-back",
    }
    assert decision(comparison, "share-record", recipient="internal-team", record_type="health") == {
        "lumen-labs": "send-full",
        "halcyon-health": "send-summary",
    }


def test_delegation_never_exceeds_the_delegators_authority_in_either_organization(comparison):
    for verified in (True, False):
        choices = decision(comparison, "delegate", target_verified=verified, exceeds_own_authority=True)
        assert set(choices.values()) == {"block"}


def test_every_cited_sentence_appears_in_that_organizations_policy(comparison):
    policies = {spec.parent.name: (spec.parent / "sources/policy.md").read_text(encoding="utf-8") for spec in SPECS}
    for row in comparison["rows"]:
        for org, cited in row["decisions"].items():
            assert cited["quote"] in policies[org]


def test_the_committed_split_locks_are_current(tmp_path):
    for spec in SPECS:
        shutil.copytree(spec.parent, tmp_path / spec.parent.name)
        build(tmp_path / spec.parent.name / spec.name, tmp_path / f"{spec.parent.name}-run", locked_splits=True)


def test_specifications_with_different_situations_are_refused(tmp_path):
    with pytest.raises(ValueError, match="same nodes"):
        script["compare"]([SPECS[0], ROOT / "examples/customer_support/project.yaml"], tmp_path)
