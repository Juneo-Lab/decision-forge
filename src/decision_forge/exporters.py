"""Training views keep labels and provenance outside model-visible inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from decision_forge.inputs import model_input
from decision_forge.io import atomic_text, canonical, digest, read_json, write_json
from decision_forge.pipeline import load_run
from decision_forge.splits import SPLITS
from decision_forge.training import decision_row

__all__ = ["export_run", "model_input"]


def export_run(
    run: Path,
    output: Path,
    format: Literal["canonical", "typed", "sft", "decision"] = "typed",
    *,
    train_limit: int | None = None,
) -> dict[str, int]:
    if format not in {"canonical", "typed", "sft", "decision"}:
        raise ValueError("Unknown training export format")
    if train_limit is not None and train_limit < 1:
        raise ValueError("Training example limit must be positive")
    episodes = load_run(run)
    dataset_digest = read_json(run / "manifest.json")["dataset_digest"]
    if output.resolve() == run.resolve():
        raise ValueError("Export to a separate directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Export directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    skipped = 0
    budget_filtered = 0
    skipped_by_split = {s: 0 for s in rows}
    for episode in episodes:
        row: dict[str, Any]
        inputs = model_input(episode)
        if format == "canonical":
            row = {
                "id": episode.id,
                "input": inputs,
                "target": {"valid_choices": episode.valid_choices},
                "metadata": {"family_id": episode.family_id, "source_ids": episode.source_ids},
            }
        elif len(episode.valid_choices) != 1:
            skipped += 1
            skipped_by_split[episode.split] += 1
            continue
        elif format == "decision":
            row = decision_row(episode, dataset_digest)
        elif format == "typed":
            row = {
                "state": canonical(
                    {key: inputs[key] for key in ["organization_id", "policy_version", "policy_context", "state"]}
                ),
                "questions": {
                    "decision": {
                        "type": "choice",
                        "instructions": episode.question,
                        "criteria": {
                            c.id: canonical({"description": c.description, "attributes": c.attributes})
                            for c in episode.candidates
                        },
                    }
                },
                "gold": {"decision": episode.valid_choices[0]},
            }
        else:
            row = {
                "messages": [
                    {
                        "role": "system",
                        "content": "Select the best permitted candidate. Return its ID only.",
                    },
                    {"role": "user", "content": canonical(inputs)},
                    {"role": "assistant", "content": episode.valid_choices[0]},
                ]
            }
        if episode.split == "train" and train_limit is not None and len(rows["train"]) >= train_limit:
            budget_filtered += 1
            continue
        rows[episode.split].append(row)
    for split, records in rows.items():
        atomic_text(output / f"{split}.jsonl", "".join(canonical(row) + "\n" for row in records))
    summary = {split: len(records) for split, records in rows.items()}
    summary["skipped_multiple_valid_choices"] = skipped
    write_json(
        output / "export.json",
        {
            "format": format,
            "counts": summary,
            "skipped_by_split": skipped_by_split,
            "budget_filtered_train": budget_filtered,
            "train_limit": train_limit,
            "dataset_digest": dataset_digest,
            "selected_ids": {split: [row["id"] for row in records] for split, records in rows.items()}
            if format in {"canonical", "decision"}
            else None,
            "file_hashes": {
                f"{split}.jsonl": digest((output / f"{split}.jsonl").read_text(encoding="utf-8")) for split in rows
            },
        },
    )
    return summary
