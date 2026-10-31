"""The model-visible projection of an episode.

Kept in its own module because exporters, duplicate detection, and synthesis all need it, and the
pipeline imports those; a shared low-level module avoids an import cycle.
"""

from __future__ import annotations

from typing import Any

from decision_forge.models import Episode


def model_input(episode: Episode) -> dict[str, Any]:
    """Everything a model may see; labels and provenance stay outside this projection."""
    return {
        "organization_id": episode.organization_id,
        "policy_version": episode.policy_version,
        "policy_context": episode.policy_context,
        "state": episode.state,
        "question": episode.question,
        "candidates": [c.model_dump(mode="json") for c in episode.candidates],
    }
