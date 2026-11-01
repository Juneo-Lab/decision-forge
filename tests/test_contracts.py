import copy

import pytest
from pydantic import ValidationError

from decision_forge.generation import generate_family
from decision_forge.models import Candidate, Predicate, Project


@pytest.mark.parametrize(("op", "value"), [("exists", "yes"), ("in", "role"), ("gte", True)])
def test_invalid_operator_arguments_rejected_at_load(op, value):
    with pytest.raises(ValidationError):
        Predicate(path="state.field", op=op, value=value)


def test_candidate_id_cannot_be_shadowed():
    with pytest.raises(ValidationError, match="reserved"):
        Candidate(id="agent", description="Agent", attributes={"id": "human"})


def test_overlapping_dimensions_are_rejected(project):
    data = project.model_dump(mode="json")
    data["nodes"][0]["dimensions"] = [
        {"path": "state.person", "values": [{"role": "staff"}]},
        {"path": "state.person.role", "values": ["guest"]},
    ]
    with pytest.raises(ValidationError, match="overlap"):
        Project.model_validate(data)


def test_rule_change_changes_episode_identity(project):
    hashes = {s.id: "fixture" for s in project.sources}
    before = generate_family(project, project.scenarios[0], hashes, "train")
    updated = copy.deepcopy(project)
    updated.organization.rules[0].score = 100
    after = generate_family(updated, updated.scenarios[0], hashes, "train")
    assert {e.id for e in before}.isdisjoint(e.id for e in after)
    assert {e.family_id for e in before} == {e.family_id for e in after}


def test_duplicate_rule_ids_rejected(project):
    data = project.model_dump(mode="json")
    data["organization"]["rules"].append(data["organization"]["rules"][0])
    with pytest.raises(ValidationError, match="Duplicate"):
        Project.model_validate(data)
