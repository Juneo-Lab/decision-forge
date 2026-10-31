"""Executable counterfactual relations and semantics-preserving presentation rewrites."""

from __future__ import annotations

import random
from typing import Literal

from pydantic import Field, JsonValue

from decision_forge.generation import policy_fingerprint, set_path
from decision_forge.io import digest
from decision_forge.models import Contract, Episode, Project
from decision_forge.policy import best_choices, evaluate


class Intervention(Contract):
    state_updates: dict[str, JsonValue] = Field(default_factory=dict)
    relation: Literal["same_choices", "disjoint_choices"]
    stratum: Literal["routine", "boundary", "change", "feedback"] = "boundary"
    candidate_order_seed: int | None = None


def transform_episode(project: Project, parent: Episode, intervention: Intervention) -> Episode:
    """Verify the declared answer relationship before accepting an augmentation.

    Candidate-order rewrites preserve candidate IDs and attributes. Free-text paraphrases require
    a separate semantic verifier and are deliberately excluded from this deterministic contract.
    """
    if (
        parent.organization_id != project.organization.id
        or parent.policy_version != project.organization.policy_version
    ):
        raise ValueError("Transformation requires the originating organization policy version")
    node = next((n for n in project.nodes if n.id == parent.node_id), None)
    if node is None or set(parent.rule_ids) != set(node.rule_ids):
        raise ValueError("Transformation node or rule lineage does not match")
    if parent.policy_fingerprint != policy_fingerprint(project, node.id, parent.source_hashes):
        raise ValueError("Parent policy fingerprint does not match the supplied project")
    rules = [r for r in project.organization.rules if r.id in node.rule_ids]
    original = best_choices(evaluate(parent.state, parent.candidates, rules))
    if set(original) != set(parent.valid_choices) or not original:
        raise ValueError("Parent labels fail independent policy replay")
    if intervention.stratum == "feedback" and parent.split != "train":
        raise ValueError("Adaptive feedback augmentation requires a training family")
    paths = list(intervention.state_updates)
    if any(not p or any(not x for x in p.split(".")) for p in paths):
        raise ValueError("Intervention paths must be nonempty")
    if any(a != b and b.startswith(a + ".") for a in paths for b in paths):
        raise ValueError("Intervention paths must not overlap")
    child = parent.model_copy(deep=True)
    for path, value in intervention.state_updates.items():
        set_path(child.state, path, value)
    if intervention.candidate_order_seed is not None:
        random.Random(intervention.candidate_order_seed).shuffle(child.candidates)
    child.verdicts = evaluate(child.state, child.candidates, rules)
    child.valid_choices = best_choices(child.verdicts)
    expected = (
        (set(original) == set(child.valid_choices))
        if intervention.relation == "same_choices"
        else not (set(original) & set(child.valid_choices))
    )
    if not child.valid_choices or not expected:
        raise ValueError("Counterfactual failed its declared answer relationship")
    child.stratum = intervention.stratum
    child.intervention = {**child.intervention, **{f"state.{k}": v for k, v in intervention.state_updates.items()}}
    child.id = (
        "ep-"
        + digest(
            [parent.id, intervention.model_dump(mode="json"), child.state, [c.model_dump() for c in child.candidates]]
        )[:24]
    )
    return child
