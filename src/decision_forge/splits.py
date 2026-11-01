"""Family split assignment that stays fixed across dataset versions.

A family's split must never change once data from it has been published: a family that moves from
test to train leaks held-out examples into training. Any assignment that is stable when families are
added without remembering previous assignments must depend on each family alone, which makes split
sizes unbounded for small projects. Decision Forge therefore assigns the first version by quota and
records every assignment in a split lock kept next to the specification, like a package lock file.
Later builds keep locked families in place and give new families a stable per-family hash.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field

from decision_forge.io import digest, read_json, write_json
from decision_forge.models import Contract, Identifier, Project, Split

SPLITS: tuple[Split, ...] = get_args(Split)


class SplitLock(Contract):
    """Every family split ever assigned for one organization, including removed families."""

    schema_version: Literal["1.0"] = "1.0"
    organization_id: Identifier
    families: dict[Identifier, Split] = Field(default_factory=dict)


def lock_path(spec: Path) -> Path:
    return spec.with_name(f"{spec.stem}.splits.lock.json")


def read_lock(spec: Path) -> SplitLock | None:
    path = lock_path(spec)
    return SplitLock.model_validate(read_json(path)) if path.exists() else None


def write_lock(spec: Path, lock: SplitLock) -> None:
    write_json(lock_path(spec), {**lock.model_dump(mode="json"), "families": dict(sorted(lock.families.items()))})


def split_plan(project: Project) -> dict[str, Split]:
    """Assign every family of the project to a split.

    Pinned families keep their split. When nothing is pinned, families are ranked by a stable hash
    and sliced into the recipe's quotas so that even a small first version has every configured
    split. Unpinned families of a pinned project use a per-family hash, which no other family affects.
    """
    families = sorted({scenario.family_id for scenario in project.scenarios})
    pinned = project.recipe.family_splits
    if pinned:
        return {family: pinned.get(family) or _hashed_split(project, family) for family in families}
    return _quota_plan(project, families)


def resolve_splits(project: Project, spec: Path) -> SplitLock:
    """Pin the project to its split lock and return the lock this build should leave behind.

    After this call ``project.recipe.family_splits`` holds the full, resolved assignment, so the
    project fingerprint does not depend on whether the lock already existed.
    """
    lock = read_lock(spec)
    if lock is not None:
        if lock.organization_id != project.organization.id:
            raise ValueError(f"{lock_path(spec).name} belongs to organization {lock.organization_id}")
        for family, split in project.recipe.family_splits.items():
            locked = lock.families.get(family)
            if locked is not None and locked != split:
                raise ValueError(
                    f"Family {family} is locked to {locked} in {lock_path(spec).name}; "
                    "edit the lock file to move it deliberately"
                )
        active = {scenario.family_id for scenario in project.scenarios}
        locked_active = {family: split for family, split in lock.families.items() if family in active}
        project.recipe.family_splits = locked_active | project.recipe.family_splits
    plan = split_plan(project)
    project.recipe.family_splits = plan
    previous = lock.families if lock else {}
    return SplitLock(organization_id=project.organization.id, families=previous | plan)


def require_current_lock(spec: Path, expected: SplitLock) -> None:
    if read_lock(spec) != expected:
        raise ValueError(
            f"{lock_path(spec).name} is missing or out of date; build without --locked-splits to update it"
        )


def _hashed_split(project: Project, family: str) -> Split:
    point = int(digest(f"{project.organization.id}:{family}:split-v1"), 16) / 2**256
    splits = project.recipe.splits
    if point < splits.train:
        return "train"
    return "development" if point < splits.train + splits.development else "test"


def _quota_plan(project: Project, families: list[str]) -> dict[str, Split]:
    fractions = {split: getattr(project.recipe.splits, split) for split in SPLITS}
    total = len(families)
    quotas = {split: math.floor(fraction * total) for split, fraction in fractions.items()}
    remainders = sorted(SPLITS, key=lambda split: (-(fractions[split] * total - quotas[split]), split))
    for split in remainders[: total - sum(quotas.values())]:
        quotas[split] += 1
    # A configured split with no family is silently unusable downstream, for example in Harbor export.
    required = [split for split in SPLITS if fractions[split] > 0]
    if total >= len(required):
        for split in required:
            if quotas[split] == 0:
                donor = max(quotas, key=lambda candidate: (quotas[candidate], candidate))
                quotas[donor] -= 1
                quotas[split] += 1
    order = sorted(families, key=lambda family: digest(f"{project.organization.id}:{family}:split-v2"))
    plan: dict[str, Split] = {}
    for split in SPLITS:
        for family in order[: quotas[split]]:
            plan[family] = split
        order = order[quotas[split] :]
    return plan
