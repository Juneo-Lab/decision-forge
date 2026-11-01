"""Executable counterfactual relations and semantics-preserving presentation rewrites."""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, JsonValue

from decision_forge.generation import policy_fingerprint, set_path
from decision_forge.io import canonical, digest
from decision_forge.models import Contract, Episode, Project, Stratum
from decision_forge.policy import best_choices, evaluate
from decision_forge.providers import StructuredProvider


class Intervention(Contract):
    state_updates: dict[str, JsonValue] = Field(default_factory=dict)
    relation: Literal["same_choices", "disjoint_choices"]
    stratum: Stratum = "boundary"
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
    node = project.find_node(parent.node_id)
    if node is None or set(parent.rule_ids) != set(node.rule_ids):
        raise ValueError("Transformation node or rule lineage does not match")
    if parent.policy_fingerprint != policy_fingerprint(project, node.id, parent.source_hashes):
        raise ValueError("Parent policy fingerprint does not match the supplied project")
    rules = project.select_rules(node.rule_ids)
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


QUESTION_REWRITE_PROMPT = """Rewrite the presentation of one organizational decision.
Change wording only. Never change thresholds, amounts, roles, time limits, or the number of options.
Do not add requirements, exceptions, or new facts. Keep every numeric value exactly as written.
Return JSON only, matching the schema.
"""

VERIFY_REWRITE_PROMPT = """Decide whether two presentations describe the same decision problem.
They are equivalent only if the options, the conditions, and every numeric value agree.
Wording, order, and phrasing may differ. Return JSON only, matching the schema.
"""

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class PresentationRewrite(Contract):
    question: str = Field(min_length=1, max_length=2000)
    candidate_descriptions: dict[str, str] = Field(min_length=2, max_length=64)
    rationale: str = Field(min_length=1, max_length=500)


class RewriteVerdict(Contract):
    equivalent: bool
    reason: str = Field(min_length=1, max_length=500)


def _numbers(text: str) -> list[str]:
    return sorted(_NUMBER.findall(text))


def presentation_text(episode: Episode) -> str:
    return (
        episode.question
        + "\n"
        + "\n".join(f"{candidate.id}: {candidate.description}" for candidate in episode.candidates)
    )


def verify_presentation(
    original: Episode, rewrite: PresentationRewrite, verifier: Callable[[str, str, dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    """Deterministic invariants first, then an explicit equivalence decision.

    A paraphrase cannot be accepted on plausibility: changing one threshold silently relabels every
    example derived from it. Numbers, option set, and policy replay are checked before any model
    opinion is consulted, and the model must still agree.
    """
    candidate_ids = {candidate.id for candidate in original.candidates}
    if set(rewrite.candidate_descriptions) != candidate_ids:
        raise ValueError("Rewrite must describe exactly the declared candidates")
    if any(not text.strip() for text in rewrite.candidate_descriptions.values()):
        raise ValueError("Rewrite must not blank a candidate description")
    original_numbers = _numbers(presentation_text(original))
    rewritten = (
        rewrite.question + "\n" + "\n".join(f"{key}: {value}" for key, value in rewrite.candidate_descriptions.items())
    )
    if _numbers(rewritten) != original_numbers:
        raise ValueError("Rewrite changed a numeric value; thresholds must be preserved")
    original_length = len(presentation_text(original))
    if not 0.4 <= len(rewritten) / max(original_length, 1) <= 3.0:
        raise ValueError("Rewrite length is not a paraphrase of the original presentation")
    verdict = RewriteVerdict.model_validate(
        verifier(
            VERIFY_REWRITE_PROMPT,
            canonical(
                {
                    "original": {
                        "question": original.question,
                        "candidates": {candidate.id: candidate.description for candidate in original.candidates},
                    },
                    "rewrite": {
                        "question": rewrite.question,
                        "candidates": rewrite.candidate_descriptions,
                    },
                }
            ),
            RewriteVerdict.model_json_schema(),
        )
    )
    if not verdict.equivalent:
        raise ValueError(f"Independent verifier rejected the rewrite: {verdict.reason}")
    return verdict.model_dump(mode="json")


def rewrite_presentation(
    project: Project,
    episode: Episode,
    provider: StructuredProvider,
    verifier: StructuredProvider | None = None,
) -> Episode:
    """Paraphrase a decision presentation and publish it only after independent verification."""
    node = project.find_node(episode.node_id)
    if node is None or set(episode.rule_ids) != set(node.rule_ids):
        raise ValueError("Rewrite node or rule lineage does not match")
    if episode.policy_fingerprint != policy_fingerprint(project, episode.node_id, episode.source_hashes):
        raise ValueError("Parent policy fingerprint does not match the supplied project")
    rules = project.select_rules(node.rule_ids)
    original_choices = best_choices(evaluate(episode.state, episode.candidates, rules))
    if original_choices != episode.valid_choices:
        raise ValueError("Parent labels fail independent policy replay")
    request = canonical(
        {
            "question": episode.question,
            "candidates": {candidate.id: candidate.description for candidate in episode.candidates},
            "task": "rewrite for presentation variety without changing the decision problem",
        }
    )
    rewrite = PresentationRewrite.model_validate(
        provider.generate(QUESTION_REWRITE_PROMPT, request, PresentationRewrite.model_json_schema())
    )
    verdict = verify_presentation(episode, rewrite, (verifier or provider).generate)
    child = episode.model_copy(deep=True)
    child.question = rewrite.question
    for candidate in child.candidates:
        candidate.description = rewrite.candidate_descriptions[candidate.id]
    child.verdicts = evaluate(child.state, child.candidates, rules)
    child.valid_choices = best_choices(child.verdicts)
    if child.valid_choices != original_choices or not child.valid_choices:
        raise ValueError("Rewrite changed the decision; presentation may not affect labels")
    child.id = (
        "ep-" + digest([episode.id, rewrite.model_dump(mode="json"), [c.model_dump() for c in child.candidates]])[:24]
    )
    child.intervention = {
        **child.intervention,
        "presentation.rewrite": digest(rewrite.model_dump(mode="json")),
        "presentation.verifier": digest(verdict),
    }
    child.label_basis = episode.label_basis
    child.verifier = "policy-engine-v1+presentation-verifier-v1"
    return child
