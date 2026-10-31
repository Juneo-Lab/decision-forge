"""Canonical compatibility rules for additive project schema extensions."""

from __future__ import annotations

from typing import Any

from decision_forge.models import Project, Recipe


def project_payload(project: Project) -> dict[str, Any]:
    """Preserve v1 hashes when optional extensions are inactive.

    Published experiment hashes were computed with the original v1 defaults included.
    New fields with inactive defaults are omitted; configured extensions remain hash-bound.
    """
    data = project.model_dump(mode="json")
    defaults = Recipe().model_dump(mode="json")
    for name in ("family_splits", "candidate_pool_per_scenario", "distribution", "require_full_distribution"):
        if data["recipe"][name] == defaults[name]:
            del data["recipe"][name]
    for rule in data["organization"]["rules"]:
        for evidence in rule["evidence"]:
            if evidence.get("claim_id") is None:
                evidence.pop("claim_id", None)
    return data
