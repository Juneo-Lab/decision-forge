"""Global duplicate detection across scenarios, with held-out leak detection.

Two examples are the same learning example when they present the same decision situation with the
same wording. Scenario-local identity cannot see that, because it includes the scenario ID.

So duplicates are found in two stages:

1. A structural key (`state` + candidate identities and attributes + question) groups examples that
   pose the same problem. Groups are where wording can be compared at all.
2. Within a group, MinHash over normalized word tokens estimates presentation similarity, so a
   cosmetic edit collapses instead of training the model on the same sentence twice. Word tokens
   rather than character n-grams are used because a one-word edit in a short presentation changes
   most character n-grams, which would miss the duplicates this check exists to find.

Near-identical wording about *different* states is not a duplicate: those are distinct situations.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from itertools import combinations
from typing import Any

from decision_forge.inputs import model_input
from decision_forge.io import canonical
from decision_forge.models import Episode

NEAR_DUPLICATE_THRESHOLD = 0.9
SHINGLE_SIZE = 1  # normalized word tokens
PERMUTATIONS = 64
# Prime larger than the 64-bit space of the shingle hashes used below.
_PRIME = 2**61 - 1
SPLIT_PRIORITY: dict[str, int] = {"test": 0, "development": 1, "train": 2}


def input_text(episode: Episode) -> str:
    """Canonical model-visible input; two equal texts are unambiguously the same example."""
    return canonical(model_input(episode))


def input_digest(episode: Episode) -> str:
    return hashlib.sha256(input_text(episode).encode()).hexdigest()


def structure_key(episode: Episode) -> str:
    """The decision situation, excluding wording and provenance."""
    return canonical(
        {
            "state": episode.state,
            "candidates": [
                {"id": candidate.id, "attributes": candidate.attributes} for candidate in episode.candidates
            ],
        }
    )


def presentation_text(episode: Episode) -> str:
    """The wording a model reads: the question and the candidate descriptions."""
    return (
        episode.question
        + "\n"
        + "\n".join(f"{candidate.id}: {candidate.description}" for candidate in episode.candidates)
    )


def shingle_hashes(text: str, size: int = SHINGLE_SIZE) -> list[int]:
    """Stable hashed word n-grams; no Python-level hashing so runs reproduce across processes."""
    words = text.split()
    grams = [text] if len(words) < size else [" ".join(words[i : i + size]) for i in range(len(words) - size + 1)]
    return [int.from_bytes(hashlib.blake2b(gram.encode(), digest_size=8).digest(), "big") for gram in grams]


def minhash_signature(shingles: Iterable[int], permutations: int = PERMUTATIONS) -> tuple[int, ...]:
    values = list(dict.fromkeys(shingles))
    if not values:
        return tuple(0 for _ in range(permutations))
    signature = []
    for index in range(permutations):
        multiplier = (index * 2 + 1) % _PRIME
        offset = (index * 7919 + 104729) % _PRIME
        signature.append(min((multiplier * value + offset) % _PRIME for value in values))
    return tuple(signature)


def jaccard_estimate(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a == b for a, b in zip(left, right, strict=True)) / len(left)


def _choose_keeper(members: list[Episode]) -> Episode:
    """Keep the most held-out member, then the lowest ID, so dedup never weakens evaluation."""
    return sorted(members, key=lambda e: (SPLIT_PRIORITY[e.split], e.id))[0]


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _clusters_by_structure(episodes: list[Episode]) -> list[list[int]]:
    groups: dict[str, list[int]] = {}
    for index, episode in enumerate(episodes):
        groups.setdefault(structure_key(episode), []).append(index)
    return list(groups.values())


def find_duplicates(
    episodes: list[Episode],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
    permutations: int = PERMUTATIONS,
) -> dict[str, Any]:
    """Group structurally identical situations and collapse near-identical wording within them.

    Cross-split duplicates are a split leak, not a sampling problem: the held-out copy survives and
    the training copy is dropped, because a near-identical training row inflates held-out scores.
    """
    if not 0 < threshold <= 1:
        raise ValueError("Duplicate threshold must be in (0, 1]")
    if permutations < 8:
        raise ValueError("At least eight permutations are required for a stable estimate")
    groups = _DisjointSet(len(episodes))
    signals = [minhash_signature(shingle_hashes(presentation_text(e)), permutations) for e in episodes]
    exact_pairs: set[tuple[int, int]] = set()
    for members in _clusters_by_structure(episodes):
        digests: dict[str, list[int]] = {}
        for index in members:
            digests.setdefault(input_digest(episodes[index]), []).append(index)
        for same_input in digests.values():
            for left, right in zip(same_input, same_input[1:], strict=False):
                exact_pairs.add((left, right))
                groups.union(left, right)
        for left, right in combinations(sorted(members), 2):
            if (left, right) in exact_pairs:
                continue
            if jaccard_estimate(signals[left], signals[right]) >= threshold:
                groups.union(left, right)

    clusters: dict[int, list[int]] = {}
    for index in range(len(episodes)):
        clusters.setdefault(groups.find(index), []).append(index)
    dropped: list[dict[str, Any]] = []
    leaks: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []
    for _, indexes in sorted(clusters.items()):
        if len(indexes) == 1:
            continue
        cluster = [episodes[index] for index in indexes]
        keeper = _choose_keeper(cluster)
        exact = len({input_digest(e) for e in cluster}) == 1
        # Identical inputs must not disagree about the metadata a quota is keyed on. Keeping one copy
        # is correct for training, but the disagreement has to reach a reviewer, otherwise a cell can
        # silently empty out.
        stratum_conflict = len({e.stratum for e in cluster}) > 1
        label_conflict = len({tuple(sorted(e.valid_choices)) for e in cluster}) > 1
        if stratum_conflict or label_conflict:
            conflicts.append(
                {
                    "kept": keeper.id,
                    "members": sorted(e.id for e in cluster),
                    "strata": sorted({e.stratum for e in cluster}),
                    "labels": sorted({tuple(sorted(e.valid_choices)) for e in cluster}),
                }
            )
        for episode in cluster:
            if episode.id == keeper.id:
                continue
            split_leak = episode.split != keeper.split
            dropped.append(
                {
                    "id": episode.id,
                    "split": episode.split,
                    "case": "split_leak" if split_leak else ("exact_duplicate" if exact else "near_duplicate"),
                    "kept": keeper.id,
                    "reason": "held_out_copy_kept" if split_leak else "redundant_input",
                }
            )
            if split_leak:
                leaks.append(
                    {
                        "dropped": episode.id,
                        "dropped_split": episode.split,
                        "kept": keeper.id,
                        "kept_split": keeper.split,
                    }
                )
        duplicate_groups.append(
            {
                "kept": keeper.id,
                "kept_split": keeper.split,
                "members": sorted(e.id for e in cluster),
                "duplicate_inputs": len(cluster) - 1,
                "case": "exact_duplicate" if exact else "near_duplicate",
            }
        )
    dropped_ids = {row["id"] for row in dropped}
    kept = [e for e in episodes if e.id not in dropped_ids]
    return {
        "protocol": "structural-group-token-minhash-v1",
        "threshold": threshold,
        "shingle_size": SHINGLE_SIZE,
        "permutations": permutations,
        "examples": len(episodes),
        "structural_groups": len(_clusters_by_structure(episodes)),
        "kept": len(kept),
        "removed": len(dropped),
        "groups": sorted(duplicate_groups, key=lambda group: group["kept"]),
        "dropped": sorted(dropped, key=lambda row: row["id"]),
        "split_leaks": sorted(leaks, key=lambda row: row["dropped"]),
        "cross_split_pairs": len(leaks),
        "metadata_conflicts": sorted(conflicts, key=lambda row: row["kept"]),
        "kept_ids": sorted(e.id for e in kept),
    }


def deduplicate(episodes: list[Episode], **kwargs: Any) -> tuple[list[Episode], dict[str, Any]]:
    """Return the surviving episodes plus the report, ordered by ID for reproducibility."""
    report = find_duplicates(episodes, **kwargs)
    kept_ids = set(report["kept_ids"])
    return sorted((e for e in episodes if e.id in kept_ids), key=lambda e: e.id), report


def reject_duplicate_inputs(episodes: list[Episode], existing: Iterable[Episode]) -> list[Episode]:
    """Filter proposed examples that repeat an input already present in the pool."""
    known = {input_digest(e) for e in existing}
    unique: list[Episode] = []
    for episode in episodes:
        digest_value = input_digest(episode)
        if digest_value in known:
            continue
        known.add(digest_value)
        unique.append(episode)
    return unique


def duplicate_verdict(
    episode: Episode,
    others: Iterable[Episode],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
    permutations: int = PERMUTATIONS,
) -> str | None:
    """Cheap pairwise check used before publishing a single generated example."""
    target_structure = structure_key(episode)
    target_digest = input_digest(episode)
    target_signal: tuple[int, ...] | None = None
    for other in others:
        if structure_key(other) != target_structure:
            continue
        if input_digest(other) == target_digest:
            return "exact"
        if target_signal is None:
            target_signal = minhash_signature(shingle_hashes(presentation_text(episode)), permutations)
        signal = minhash_signature(shingle_hashes(presentation_text(other)), permutations)
        if jaccard_estimate(target_signal, signal) >= threshold:
            return "near"
    return None
