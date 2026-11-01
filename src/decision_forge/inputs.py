"""The model-visible projection of an episode.

Kept in its own module because exporters, duplicate detection, and synthesis all need it, and the
pipeline imports those; a shared low-level module avoids an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
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


def presentation_text(question: str, descriptions: Iterable[tuple[str, str]]) -> str:
    """The wording a model reads: the question, then one ``id: description`` line per candidate."""
    return question + "\n" + "\n".join(f"{candidate_id}: {text}" for candidate_id, text in descriptions)


def episode_presentation(episode: Episode) -> str:
    return presentation_text(episode.question, ((c.id, c.description) for c in episode.candidates))
