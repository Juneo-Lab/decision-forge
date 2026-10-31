"""Decision-only training contracts and candidate-probability encoding."""

from __future__ import annotations

import json
import math
import random
import string
from functools import lru_cache
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from decision_forge.io import canonical, digest
from decision_forge.models import Episode


class DecisionOption(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    key: str = Field(min_length=1)
    description: str = Field(min_length=1)


class DecisionRow(BaseModel):
    """Preserve external metadata and explicit probability targets without relabeling."""

    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1)
    objective: Literal["candidate_readout"]
    system: str = Field(min_length=1)
    input: dict[str, JsonValue]
    options: list[DecisionOption] = Field(min_length=2)
    target_probs: list[float] = Field(min_length=2)
    root_id: str = Field(min_length=1)
    normalized_group: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    provenance: dict[str, JsonValue]
    weight: float = 1.0

    @model_validator(mode="after")
    def aligned_targets(self) -> Self:
        if "candidates" in self.input:
            raise ValueError("Input candidates are reserved for the renderer; use options")
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("Candidate keys must be unique")
        if len(keys) != len(self.target_probs):
            raise ValueError("Candidate and probability lengths must match")
        if any(p < 0 or p > 1 for p in self.target_probs) or not math.isclose(
            sum(self.target_probs), 1.0, rel_tol=0, abs_tol=1e-8
        ):
            raise ValueError("Target probabilities must be nonnegative and sum to one")
        if self.weight != 1.0:
            raise ValueError("The decision training contract requires unit row weights")
        if not self.provenance:
            raise ValueError("Decision supervision requires provenance")
        return self


def decision_row(episode: Episode, dataset_digest: str) -> dict[str, Any]:
    """A unique verified best choice defines one-hot supervision; ties need review."""
    keys = [candidate.id for candidate in episode.candidates]
    if len(episode.valid_choices) != 1 or episode.valid_choices[0] not in keys:
        raise ValueError("Decision export requires one verified best choice")
    group = "forge-family:" + digest([episode.organization_id, episode.family_id])
    row = {
        "id": episode.id,
        "objective": "candidate_readout",
        "system": "Select the best permitted candidate using the supplied policy and state. Output only its letter.",
        "input": {
            "state": {
                "organization_id": episode.organization_id,
                "policy_version": episode.policy_version,
                "policy_context": episode.policy_context,
                "decision_state": episode.state,
            },
            "question": episode.question,
        },
        # Existing renderers consume descriptions only: keep decision-relevant attributes here.
        "options": [
            {
                "key": c.id,
                "description": canonical({"id": c.id, "description": c.description, "attributes": c.attributes}),
            }
            for c in episode.candidates
        ],
        "target_probs": [1.0 if key == episode.valid_choices[0] else 0.0 for key in keys],
        "root_id": group,
        "normalized_group": group,
        "family_id": episode.family_id,
        "task_id": episode.node_id,
        "decision_kind": episode.kind.value,
        "stratum": episode.stratum,
        "split": episode.split,
        "scope": "organization_policy_verified_v1",
        "provenance": {
            "source": "decision-forge",
            "dataset_digest": dataset_digest,
            "source_ids": episode.source_ids,
            "source_hashes": episode.source_hashes,
            "rule_ids": episode.rule_ids,
            "policy_fingerprint": episode.policy_fingerprint,
            "label_basis": episode.label_basis,
            "target_basis": "unique_policy_verified_best_choice",
            "intervention": episode.intervention,
        },
        "weight": 1.0,
    }
    DecisionRow.model_validate(row)
    return row


class DecisionTokenizer(Protocol):
    def __hash__(self) -> int: ...

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]: ...

    def apply_chat_template(self, messages: list[dict[str, str]], **kwargs: Any) -> Any: ...


@lru_cache(maxsize=8)
def _candidate_labels(tokenizer: DecisionTokenizer) -> tuple[tuple[str, int], ...]:
    names = list(string.ascii_uppercase) + [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    pool = []
    seen = set()
    for name in names:
        ids = tokenizer.encode(name, add_special_tokens=False)
        if len(ids) == 1 and ids[0] not in seen:
            pool.append((name, ids[0]))
            seen.add(ids[0])
    return tuple(pool)


def encode_decision(
    row: dict[str, Any], tokenizer: DecisionTokenizer, *, seed: int = 1015, shuffle: bool = True, max_tokens: int = 8192
) -> dict[str, Any]:
    """Encode native non-thinking prefixes for candidate-restricted cross entropy.

    No answer token is appended. Targets align with candidate_token_ids at the last
    prefix position. Tokenizer-specific labels are unique single tokens.
    """
    DecisionRow.model_validate(row)
    pool = _candidate_labels(tokenizer)
    order = list(range(len(row["options"])))
    if shuffle:
        random.Random(str(seed) + row["id"]).shuffle(order)
    pairs = pool[: len(order)]
    if len(pairs) != len(order):
        raise ValueError("Tokenizer has insufficient unique single-token candidate labels")
    data = dict(row["input"])
    ordered = {}
    for key in ["state", "context", "history", "action", "candidate", "question"]:
        if key in data:
            ordered[key] = data.pop(key)
    ordered.update(data)
    ordered["candidates"] = [
        {"letter": label, "description": row["options"][j]["description"]}
        for (label, _), j in zip(pairs, order, strict=True)
    ]
    messages = [
        {"role": "system", "content": row["system"] + "\nReturn exactly one supplied candidate label."},
        {"role": "user", "content": json.dumps(ordered, ensure_ascii=False)},
    ]
    prefix = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False)
    if hasattr(prefix, "input_ids"):
        prefix = prefix.input_ids
    elif isinstance(prefix, dict):
        prefix = prefix["input_ids"]
    if prefix and isinstance(prefix[0], list):
        if len(prefix) != 1:
            raise ValueError("Unexpected tokenizer batch")
        prefix = prefix[0]
    prefix = list(prefix)
    if not prefix or max_tokens < 1 or len(prefix) > max_tokens:
        raise ValueError("Decision prefix is empty or exceeds the token budget; truncation is forbidden")
    return {
        "messages": messages,
        "input_ids": prefix,
        "prefix_len": len(prefix),
        "target_ids": [],
        "labels": [-100] * len(prefix),
        "candidate_token_ids": [token for _, token in pairs],
        "target_probs": [row["target_probs"][j] for j in order],
        "label_to_key": {label: row["options"][j]["key"] for (label, _), j in zip(pairs, order, strict=True)},
        "weight": 1.0,
    }
