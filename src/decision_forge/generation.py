"""Seeded scenario families, generated deterministically from a project's declared dimensions."""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from typing import Any

from decision_forge.io import digest
from decision_forge.models import Candidate, Episode, Node, Project, Rule, Scenario, Split
from decision_forge.policy import best_choices, evaluate

MAX_STATE_SPACE = 10**12


def state_space_size(node: Node) -> int:
    """Number of distinct dimension-value combinations a node declares."""
    return math.prod(len(dimension.values) for dimension in node.dimensions)


def requested_variants(project: Project, scenario: Scenario) -> int:
    """Number of distinct episodes a scenario is configured to contribute."""
    recipe = project.recipe
    return min(
        state_space_size(project.node(scenario.node_id)),
        recipe.candidate_pool_per_scenario or recipe.variants_per_scenario,
    )


def referenced_sources(scenario: Scenario, rules: list[Rule]) -> set[str]:
    """Sources a scenario's examples depend on: its own and those its rules quote."""
    return set(scenario.source_ids) | {evidence.source_id for rule in rules for evidence in rule.evidence}


def candidate_for(node: Node, path: str) -> Candidate:
    """Resolve the single candidate a candidates.<id>.<attribute> path belongs to."""
    matches = [candidate for candidate in node.candidates if path.startswith(f"candidates.{candidate.id}.")]
    if len(matches) != 1:
        raise ValueError(f"Path must identify exactly one candidate: {path}")
    return matches[0]


def set_path(root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = root
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise ValueError(f"Dimension traverses a scalar: {path}")
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def policy_fingerprint(project: Project, node_id: str, source_hashes: dict[str, str]) -> str:
    node = project.node(node_id)
    return digest(
        {
            "organization_id": project.organization.id,
            "policy_version": project.organization.policy_version,
            "node": node.model_dump(mode="json"),
            "rules": [rule.model_dump(mode="json") for rule in project.select_rules(node.rule_ids)],
            "sources": {
                s.id: {"hash": source_hashes[s.id], "version": s.version, "access": s.access}
                for s in project.sources
                if s.id in source_hashes
            },
        }
    )


@dataclass(frozen=True)
class Variant:
    """One decision-time situation a scenario family contributes, before labeling."""

    state: dict[str, Any]
    candidates: list[Candidate]
    intervention: dict[str, Any]


def family_variants(project: Project, scenario: Scenario) -> list[Variant]:
    """The situations a build samples for a scenario: its declared dimensions applied to the seed state."""
    node = project.node(scenario.node_id)
    widths = [len(dim.values) for dim in node.dimensions]
    population = math.prod(widths)
    if population > MAX_STATE_SPACE:
        raise ValueError("Scenario space exceeds the supported sampling range")
    rng = random.Random(digest([project.recipe.seed, scenario.id]))
    variants = []
    for index in sorted(rng.sample(range(population), requested_variants(project, scenario))):
        state = copy.deepcopy(scenario.state)
        candidates = [c.model_copy(deep=True) for c in node.candidates]
        intervention: dict[str, Any] = {}
        for dimension, width in zip(node.dimensions, widths, strict=True):
            value = dimension.values[index % width]
            index //= width
            intervention[dimension.path] = value
            if dimension.path.startswith("state."):
                set_path(state, dimension.path.removeprefix("state."), value)
            else:
                candidate_id = candidate_for(node, dimension.path).id
                candidate = next(c for c in candidates if c.id == candidate_id)
                attr_path = dimension.path.removeprefix(f"candidates.{candidate.id}.")
                if attr_path == "id":
                    raise ValueError("Candidate IDs cannot be mutated")
                set_path(candidate.attributes, attr_path, value)
        variants.append(Variant(state, candidates, intervention))
    return variants


def generate_family(project: Project, scenario: Scenario, source_hashes: dict[str, str], split: Split) -> list[Episode]:
    node = project.node(scenario.node_id)
    rules = project.select_rules(node.rule_ids)
    episodes: dict[str, Episode] = {}
    sources = sorted(referenced_sources(scenario, rules))
    hashes = {key: source_hashes[key] for key in sources}
    grounding = policy_fingerprint(project, node.id, hashes)
    for variant in family_variants(project, scenario):
        state, candidates = variant.state, variant.candidates
        verdicts = evaluate(state, candidates, rules)
        identity = digest(
            [
                project.organization.id,
                project.organization.policy_version,
                node.question,
                [rule.model_dump() for rule in rules],
                scenario.id,
                state,
                [c.model_dump() for c in candidates],
            ]
        )[:24]
        episodes[identity] = Episode(
            id=f"ep-{identity}",
            organization_id=project.organization.id,
            policy_version=project.organization.policy_version,
            node_id=node.id,
            kind=node.kind,
            scenario_id=scenario.id,
            family_id=scenario.family_id,
            split=split,
            stratum=scenario.stratum,
            state=state,
            question=node.question,
            candidates=candidates,
            policy_context=[r.description for r in rules],
            valid_choices=best_choices(verdicts),
            verdicts=verdicts,
            source_ids=sources,
            source_hashes=hashes,
            rule_ids=node.rule_ids,
            intervention=variant.intervention,
            policy_fingerprint=grounding,
        )
    return list(episodes.values())
