"""Versioned contracts shared by compilers, generators, verifiers, and exporters."""

from __future__ import annotations

import math
import re
from collections.abc import Collection
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")]
# Candidate IDs are embedded in candidate.<id>.<attribute> paths, so a dot would make
# candidate selection in Node dimensions and evaluation bindings ambiguous.
CandidateIdentifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")]
Split = Literal["train", "development", "test"]
Stratum = Literal["routine", "boundary", "change", "feedback"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DecisionKind(StrEnum):
    ALLOCATE = "allocate_work"
    SHARE = "share_information"
    ACT = "select_actions"
    EVALUATE = "evaluate_outcomes"


class Source(Contract):
    id: Identifier
    path: str
    description: str = ""
    version: str | None = None
    content_hash: str | None = None
    access: dict[str, JsonValue] = Field(default_factory=dict)


class Evidence(Contract):
    source_id: Identifier
    claim_id: str | None = None
    quote: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=1)
    locator: str | None = None

    @model_validator(mode="after")
    def span_bounds(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("Evidence start and end must be supplied together")
        if self.start is not None and self.end is not None:
            # Quotes bind to source text modulo whitespace runs: hard-wrapped sources can make the span
            # longer than the quote, and a quote with extra spaces can be longer than its span.
            if self.end <= self.start:
                raise ValueError("Evidence end must be greater than start")
            if self.end - self.start < len(" ".join(self.quote.split())):
                raise ValueError("Evidence offsets must delimit at least the whitespace-normalized quote")
        return self


class Predicate(Contract):
    path: str = Field(pattern=r"^(state|candidate)\.[a-zA-Z0-9_.-]+$")
    op: Literal["eq", "ne", "in", "contains", "gt", "gte", "lt", "lte", "exists"]
    value: JsonValue = Field(
        default=None,
        description=(
            "Comparison operand. Required and list-valued for 'in', boolean for 'exists' "
            "(omitted means true), numeric for 'gt'/'gte'/'lt'/'lte', any JSON value otherwise."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def default_exists_value(cls, data: Any) -> Any:
        # 'exists' reads naturally without an operand; defaulting avoids a schema/validator conflict.
        if isinstance(data, dict) and data.get("op") == "exists" and "value" not in data:
            return {**data, "value": True}
        return data

    @model_validator(mode="after")
    def operator_value(self) -> Self:
        if self.op == "exists" and not isinstance(self.value, bool):
            raise ValueError("The exists operator requires a boolean value; omit it to mean true")
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("The in operator requires a list")
        if self.op in {"gt", "gte", "lt", "lte"} and (
            isinstance(self.value, bool) or not isinstance(self.value, (float, int))
        ):
            raise ValueError("Ordering operators require a numeric value")
        return self


class Condition(Contract):
    all_of: list[Predicate] = Field(default_factory=list)
    any_of: list[Predicate] = Field(default_factory=list)


class Rule(Contract):
    id: Identifier
    description: str = Field(min_length=1)
    when: Condition
    effect: Literal["allow", "deny"]
    priority: int = 0
    score: float = 0
    evidence: list[Evidence] = Field(min_length=1)


class Candidate(Contract):
    id: CandidateIdentifier
    description: str = Field(min_length=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reserved_id(self) -> Self:
        if "id" in self.attributes:
            raise ValueError("Candidate attribute 'id' is reserved")
        return self


class Dimension(Contract):
    path: str = Field(pattern=r"^(state|candidates\.[a-zA-Z0-9_-]+)\.[a-zA-Z0-9_.-]+$")
    values: list[JsonValue] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_values(self) -> Self:
        # Duplicate values silently shrink the sampled space and inflate the reported population.
        seen: list[JsonValue] = []
        for value in self.values:
            if any(value == other and type(value) is type(other) for other in seen):
                raise ValueError(f"Dimension values must be unique: {self.path}")
            seen.append(value)
        return self


class Node(Contract):
    id: Identifier
    kind: DecisionKind
    question: str = Field(min_length=1)
    candidates: list[Candidate] = Field(min_length=2)
    rule_ids: list[Identifier] = Field(min_length=1)
    dimensions: list[Dimension] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        ensure_unique([c.id for c in self.candidates], "candidate IDs")
        ensure_unique(self.rule_ids, "node rule IDs")
        ensure_unique([d.path for d in self.dimensions], "dimension paths")
        candidate_ids = {c.id for c in self.candidates}
        paths = [d.path for d in self.dimensions]
        for dim in self.dimensions:
            if any(path != dim.path and path.startswith(dim.path + ".") for path in paths):
                raise ValueError("Dimension paths must not overlap")
            if dim.path.startswith("candidates."):
                matches = [
                    candidate_id for candidate_id in candidate_ids if dim.path.startswith(f"candidates.{candidate_id}.")
                ]
                if not matches:
                    raise ValueError(f"Unknown candidate dimension: {dim.path}")
                if len(matches) > 1:
                    raise ValueError(f"Dimension path is ambiguous across candidates: {dim.path}")
            if any(
                dim.path == f"candidates.{i}.id" or dim.path.startswith(f"candidates.{i}.id.") for i in candidate_ids
            ):
                raise ValueError("Candidate IDs cannot be mutated")
        return self


class Scenario(Contract):
    id: Identifier
    node_id: Identifier
    family_id: Identifier
    state: dict[str, JsonValue]
    source_ids: list[Identifier] = Field(min_length=1)
    stratum: Stratum = "routine"


class SplitConfig(Contract):
    train: float = Field(default=0.8, ge=0, le=1)
    development: float = Field(default=0.1, ge=0, le=1)
    test: float = Field(default=0.1, ge=0, le=1)

    @model_validator(mode="after")
    def sums_to_one(self) -> Self:
        if not math.isclose(self.train + self.development + self.test, 1.0, abs_tol=1e-9):
            raise ValueError("Split fractions must sum to one")
        return self


class DistributionTarget(Contract):
    """An exact accepted-example quota, including its answer set and evaluation split."""

    node_id: Identifier
    stratum: Stratum
    split: Split
    valid_choices: list[CandidateIdentifier] = Field(min_length=1)
    count: int = Field(ge=0, le=100000)

    @model_validator(mode="after")
    def choices_unique(self) -> Self:
        ensure_unique(self.valid_choices, "distribution choices")
        self.valid_choices = sorted(self.valid_choices)
        return self


class Recipe(Contract):
    seed: int = 42
    variants_per_scenario: int = Field(default=16, ge=1, le=10000)
    splits: SplitConfig = Field(default_factory=SplitConfig)
    family_splits: dict[str, Split] = Field(default_factory=dict)
    candidate_pool_per_scenario: int | None = Field(default=None, ge=1, le=10000)
    distribution: list[DistributionTarget] = Field(default_factory=list)
    require_full_distribution: bool = True


class Organization(Contract):
    id: Identifier
    name: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)


class Project(Contract):
    schema_version: Literal["1.0"] = "1.0"
    organization: Organization
    sources: list[Source] = Field(min_length=1)
    nodes: list[Node] = Field(min_length=1)
    scenarios: list[Scenario] = Field(min_length=1)
    recipe: Recipe = Field(default_factory=Recipe)

    def find_node(self, node_id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def node(self, node_id: str) -> Node:
        node = self.find_node(node_id)
        if node is None:
            raise ValueError(f"Unknown node: {node_id}")
        return node

    def select_rules(self, rule_ids: Collection[str]) -> list[Rule]:
        """Rules with the given IDs, in organization order, which identities and fingerprints hash."""
        return [rule for rule in self.organization.rules if rule.id in rule_ids]

    @model_validator(mode="after")
    def references(self) -> Self:
        for values, label in [
            ([s.id for s in self.sources], "source IDs"),
            ([r.id for r in self.organization.rules], "rule IDs"),
            ([n.id for n in self.nodes], "node IDs"),
            ([s.id for s in self.scenarios], "scenario IDs"),
        ]:
            ensure_unique(values, label)
        sources = {s.id for s in self.sources}
        rules = {r.id for r in self.organization.rules}
        nodes = {n.id for n in self.nodes}
        for rule in self.organization.rules:
            for evidence in rule.evidence:
                if evidence.source_id not in sources:
                    raise ValueError(f"Unknown evidence source: {evidence.source_id}")
        for node in self.nodes:
            if not set(node.rule_ids) <= rules:
                raise ValueError(f"Unknown rule referenced by node {node.id}")
        for scenario in self.scenarios:
            if scenario.node_id not in nodes or not set(scenario.source_ids) <= sources:
                raise ValueError(f"Unknown reference in scenario {scenario.id}")
        families = {s.family_id for s in self.scenarios}
        if not set(self.recipe.family_splits) <= families:
            raise ValueError("Split assignments reference unknown families")
        cells: set[tuple[str, str, str, tuple[str, ...]]] = set()
        for target in self.recipe.distribution:
            target_node = self.find_node(target.node_id)
            if target_node is None or not set(target.valid_choices) <= {c.id for c in target_node.candidates}:
                raise ValueError("Distribution target references an unknown node or candidate")
            cell = (target.node_id, target.stratum, target.split, tuple(target.valid_choices))
            if cell in cells:
                raise ValueError("Duplicate distribution target")
            cells.add(cell)
        return self


class CandidateVerdict(Contract):
    candidate_id: str
    allowed: bool
    score: float
    matched_rules: list[str]


class Episode(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: Identifier
    organization_id: str
    policy_version: str
    node_id: str
    kind: DecisionKind
    scenario_id: str
    family_id: str
    split: Split
    stratum: str
    state: dict[str, JsonValue]
    question: str
    candidates: list[Candidate]
    policy_context: list[str]
    valid_choices: list[str]
    verdicts: list[CandidateVerdict]
    source_ids: list[str]
    source_hashes: dict[str, str]
    rule_ids: list[str]
    intervention: dict[str, JsonValue]
    policy_fingerprint: str | None = None
    verifier: str = "policy-engine-v1"
    label_basis: str = "declarative_policy"


def ensure_unique(values: list[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"Duplicate {label}")


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value):
        raise ValueError("Invalid artifact identifier")
    return value
