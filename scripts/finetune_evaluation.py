"""Fixed-budget fine-tuning loop: export, train plan, and independent held-out evaluation.

This script does not train a model by itself. Training needs a GPU runtime and a spending decision,
so `prepare` writes a pinned, reproducible plan and refuses to claim a result. `evaluate` and
`compare` run without a trainer: they score any OpenAI-compatible endpoint on the frozen held-out
split, so the baseline can be measured before spending anything.

The point is the loop, not the trainer: one dataset digest in, one comparable accuracy-per-surface
report out, with the held-out families identical for every arm.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from decision_forge.exporters import export_run
from decision_forge.inputs import model_input
from decision_forge.io import atomic_text, canonical, read_json, write_json
from decision_forge.pipeline import load_run
from decision_forge.providers import OpenAICompatibleProvider

app = typer.Typer(no_args_is_help=True, help="Fixed-budget fine-tuning and held-out evaluation loop.")

TRAINER = {
    "framework": "trl",
    "framework_version": "0.20.3",
    "method": "lora",
    "base_model": "Qwen/Qwen2.5-7B-Instruct",
    "lora_rank": 16,
    "lora_alpha": 32,
    "learning_rate": 1e-4,
    "epochs": 1,
    "max_seq_length": 2048,
    "micro_batch_size": 1,
    "gradient_accumulation_steps": 8,
    "bf16": True,
    "seed": 42,
}

CHOICE_PROMPT = """You select the best permitted action for an organizational decision.
Answer with one JSON object containing exactly one field, "choice", whose value is one candidate ID
from the supplied list. Output JSON only, with no explanation.
"""


def dataset_digest(run: Path) -> str:
    value = read_json(run / "manifest.json").get("dataset_digest")
    if not isinstance(value, str):
        raise ValueError("Run manifest has no dataset digest; rebuild the run")
    return value


def prepare(run: Path, output: Path, budget_examples: int, trainer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Materialise a pinned training plan for a frozen dataset digest."""
    episodes = load_run(run)
    train = [e for e in episodes if e.split == "train"]
    development = [e for e in episodes if e.split == "development"]
    test = [e for e in episodes if e.split == "test"]
    if not train:
        raise ValueError("The run contains no training examples")
    if not test:
        raise ValueError(
            "The run contains no held-out examples; evaluation would not measure anything. "
            "Add scenarios to the held-out families before training."
        )
    if len(train) > budget_examples:
        # load_run is ordered by episode ID, so the budget is a deterministic prefix rather than a
        # class-balanced sample; every arm sees the same prefix, which is what the comparison needs.
        train = train[:budget_examples]
    output.mkdir(parents=True, exist_ok=True)
    export_dir = output / "training"
    if export_dir.exists() and any(export_dir.iterdir()):
        raise ValueError("Training export directory must be empty")
    export_run(run, export_dir, "canonical")
    # The evaluation prompt format is fixed here so every arm is scored identically.
    atomic_text(output / "prompt.txt", CHOICE_PROMPT)
    config: dict[str, Any] = {
        "schema_version": "1.0",
        "dataset_digest": dataset_digest(run),
        "budget_examples": budget_examples,
        "examples": {"train": len(train), "development": len(development), "test": len(test)},
        "trainer": {**TRAINER, **(trainer or {})},
        "reporting": {
            "metrics": ["accuracy", "permitted_rate", "coverage", "per_kind_accuracy"],
            "arms": ["base_model", "fine_tuned"],
            "held_out": "test split only; identical families for every arm",
        },
        "status": "planned",
    }
    write_json(output / "plan.json", config)
    settings: dict[str, Any] = config["trainer"]
    command = (
        "trl sft --model_name_or_path {model} --dataset_name json --data_files {data}/train.jsonl "
        "--max_steps -1 --num_train_epochs {epochs} --per_device_train_batch_size {bs} "
        "--gradient_accumulation_steps {ga} --learning_rate {lr} --max_length {msl} "
        "--use_peft --lora_r {r} --lora_alpha {a} --bf16 {bf16} --seed {seed} --output_dir {out}"
    ).format(
        model=settings["base_model"],
        data=export_dir.resolve(),
        epochs=settings["epochs"],
        bs=settings["micro_batch_size"],
        ga=settings["gradient_accumulation_steps"],
        lr=settings["learning_rate"],
        msl=settings["max_seq_length"],
        r=settings["lora_rank"],
        a=settings["lora_alpha"],
        bf16=str(settings["bf16"]).lower(),
        seed=settings["seed"],
        out=(output / "checkpoint").resolve(),
    )
    script = f"#!/bin/sh\nset -eu\n# Requires a GPU runtime and trl {TRAINER['framework_version']}\n{command}\n"
    atomic_text(output / "train.sh", script, mode=0o755)
    config["train_command"] = command
    write_json(output / "plan.json", config)
    return config


def evaluate(
    run: Path,
    output: Path,
    provider: OpenAICompatibleProvider,
    arm: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """Score one endpoint on the frozen held-out split with a fixed prompt."""
    episodes = [e for e in load_run(run) if e.split == "test"]
    if limit is not None:
        episodes = episodes[:limit]
    if not episodes:
        raise ValueError("No held-out episodes to evaluate")
    rows = []
    for episode in episodes:
        request = canonical({"input": model_input(episode), "candidates": [c.id for c in episode.candidates]})
        started = time.monotonic()
        choice: str | None = None
        error: str | None = None
        try:
            answer = provider.generate(
                CHOICE_PROMPT,
                request,
                {
                    "type": "object",
                    "properties": {"choice": {"type": "string"}},
                    "required": ["choice"],
                    "additionalProperties": False,
                },
            )
            candidate = answer.get("choice")
            choice = candidate if isinstance(candidate, str) else None
        except ValueError as exc:
            error = str(exc)[:200]
        rows.append(
            {
                "episode_id": episode.id,
                "kind": episode.kind.value,
                "choice": choice,
                "correct": choice in episode.valid_choices,
                "permitted": choice in {v.candidate_id for v in episode.verdicts if v.allowed},
                "latency_seconds": time.monotonic() - started,
                "error": error,
                "usage": getattr(provider, "last_usage", None),
            }
        )
    answered = [row for row in rows if row["choice"] is not None]
    by_kind: dict[str, dict[str, float | int]] = {}
    for row in rows:
        bucket = by_kind.setdefault(row["kind"], {"cases": 0, "correct": 0})
        bucket["cases"] = int(bucket["cases"]) + 1
        bucket["correct"] = int(bucket["correct"]) + int(row["correct"])
    for bucket in by_kind.values():
        bucket["accuracy"] = bucket["correct"] / bucket["cases"]
    cost = sum(
        float(row["usage"].get("cost", 0))
        for row in rows
        if isinstance(row["usage"], dict) and isinstance(row["usage"].get("cost"), (int, float))
    )
    report = {
        "schema_version": "1.0",
        "arm": arm,
        "endpoint": provider.base_url,
        "model": provider.model,
        "dataset_digest": dataset_digest(run),
        "cases": len(rows),
        "answered": len(answered),
        "coverage": len(answered) / len(rows),
        "accuracy": sum(row["correct"] for row in rows) / len(rows),
        "permitted_rate": sum(row["permitted"] for row in rows) / len(rows),
        "errors": sum(1 for row in rows if row["error"]),
        "by_kind": by_kind,
        "latency_seconds": sum(row["latency_seconds"] for row in rows),
        "cost_usd": cost or None,
        "prompt": CHOICE_PROMPT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, {**report, "events": rows})
    return report


def compare(baseline: Path, candidate: Path, output: Path) -> dict[str, Any]:
    """Compare two arm reports that scored the same frozen dataset."""
    left, right = read_json(baseline), read_json(candidate)
    if left["dataset_digest"] != right["dataset_digest"]:
        raise ValueError("Arms were evaluated on different datasets; the comparison is not valid")
    kinds = sorted(set(left["by_kind"]) | set(right["by_kind"]))
    rows = []
    for kind in kinds:
        base = left["by_kind"].get(kind, {"cases": 0, "correct": 0, "accuracy": 0.0})
        tuned = right["by_kind"].get(kind, {"cases": 0, "correct": 0, "accuracy": 0.0})
        rows.append(
            {
                "kind": kind,
                "cases": tuned["cases"],
                "baseline_accuracy": base["accuracy"],
                "candidate_accuracy": tuned["accuracy"],
                "delta": tuned["accuracy"] - base["accuracy"],
            }
        )
    report = {
        "schema_version": "1.0",
        "dataset_digest": left["dataset_digest"],
        "baseline": {"arm": left["arm"], "model": left["model"], "accuracy": left["accuracy"]},
        "candidate": {"arm": right["arm"], "model": right["model"], "accuracy": right["accuracy"]},
        "overall_delta": right["accuracy"] - left["accuracy"],
        "coverage_delta": right["coverage"] - left["coverage"],
        "permitted_rate_delta": right["permitted_rate"] - left["permitted_rate"],
        "by_kind": rows,
        "scope": "one frozen held-out split; identical prompt and case set for both arms",
    }
    write_json(output, report)
    return report


@app.command("prepare")
def prepare_command(
    run: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    budget_examples: int = 2000,
    base_model: str | None = None,
    epochs: int = 1,
) -> None:
    """Export a frozen dataset and write a pinned training plan. Does not train."""
    override: dict[str, Any] = {}
    if base_model:
        override["base_model"] = base_model
    if epochs:
        override["epochs"] = epochs
    try:
        config = prepare(run, output, budget_examples, override or None)
        typer.echo(json.dumps({key: config[key] for key in ("dataset_digest", "examples", "status")}, indent=2))
        typer.echo(f"Training plan written to {output / 'plan.json'}; run {output / 'train.sh'} on a GPU host.")
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("evaluate")
def evaluate_command(
    run: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    arm: str = "base_model",
    base_url: Annotated[str, typer.Option()] = "https://api.openai.com/v1",
    model: Annotated[str, typer.Option()] = "gpt-4o-mini",
    limit: int | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = 256,
) -> None:
    """Score one endpoint on the frozen held-out split, without any training."""
    try:
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            os.environ.get("FORGE_API_KEY", ""),
            retries=1,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        report = evaluate(run, output, provider, arm, limit)
        typer.echo(
            json.dumps({key: report[key] for key in ("arm", "cases", "accuracy", "coverage", "cost_usd")}, indent=2)
        )
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("compare")
def compare_command(baseline: Path, candidate: Path, output: Annotated[Path, typer.Option("--output", "-o")]) -> None:
    """Report the measured difference between two arms on the same frozen split."""
    try:
        report = compare(baseline, candidate, output)
        typer.echo(json.dumps({key: report[key] for key in ("overall_delta", "coverage_delta", "by_kind")}, indent=2))
        typer.echo("A positive delta is only evidence of improvement when both arms ran on this dataset digest.")
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
