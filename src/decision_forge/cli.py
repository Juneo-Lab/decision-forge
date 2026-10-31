"""Command-line entry points with explicit inputs and structured results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from filelock import Timeout

from decision_forge import __version__
from decision_forge.compiler import compile_documents
from decision_forge.corpus import CompilationOptions, compile_corpus, extraction_metrics
from decision_forge.evaluation import DecisionReference, NodeBinding, evaluate_decisions
from decision_forge.exporters import export_run
from decision_forge.harness import export_harbor, replay
from decision_forge.io import digest, load_project, load_sources, read_json, write_json
from decision_forge.models import Project
from decision_forge.pipeline import build, impact
from decision_forge.providers import OpenAICompatibleProvider
from decision_forge.review import record_review, review_status
from decision_forge.sources import ingest_manifest

app = typer.Typer(no_args_is_help=True, help="Compile organizational workflows into verifiable decision data.")


def display(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


def fail(exc: Exception) -> None:
    # Validation errors may contain private source excerpts; display only their class here.
    if type(exc).__name__ == "ValidationError":
        typer.echo("Error: specification validation failed; check field types and references.", err=True)
    else:
        typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def schema(output: Path | None = None) -> None:
    """Export the versioned project JSON Schema."""
    if output:
        write_json(output, Project.model_json_schema())
    else:
        display(Project.model_json_schema())


@app.command()
def validate(spec: Path) -> None:
    """Validate project references and exact evidence quotations."""
    try:
        project = load_project(spec)
        _, hashes = load_sources(project, spec.parent)
        display(
            {
                "valid": True,
                "organization": project.organization.id,
                "source_hashes": hashes,
                "nodes": len(project.nodes),
                "scenarios": len(project.scenarios),
            }
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("build")
def build_command(
    spec: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    cache: Path | None = None,
    recipe_plan: Annotated[Path | None, typer.Option(help="Plan produced by plan-recipe.")] = None,
    expansion: Annotated[Path | None, typer.Option(help="Verified synthesis directory.")] = None,
    near_duplicate_threshold: Annotated[
        float, typer.Option(help="Presentation similarity above which a duplicate is dropped.")
    ] = 0.9,
) -> None:
    """Generate, verify, deduplicate, checkpoint, and report decision episodes. Runs offline."""
    try:
        display(
            build(
                spec,
                output,
                cache,
                recipe_plan=recipe_plan,
                expansion=expansion,
                near_duplicate_threshold=near_duplicate_threshold,
            )["report"]
        )
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("export")
def export_command(run: Path, output: Annotated[Path, typer.Option("--output", "-o")], format: str = "typed") -> None:
    """Export split-aware decision-probability, typed, SFT, or canonical training data."""
    try:
        if format not in {"typed", "sft", "canonical", "decision"}:
            raise ValueError("Format must be typed, sft, canonical, or decision")
        display(export_run(run, output, format))  # type: ignore[arg-type]
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("impact")
def impact_command(spec: Path, previous: Path) -> None:
    """Identify changed and reusable scenario families before rebuilding."""
    try:
        display(impact(spec, previous))
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("compile")
def compile_command(
    sources: list[Path],
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
) -> None:
    """Send the listed documents to the configured model and write an evidence-linked draft spec.

    Set FORGE_API_KEY for authenticated endpoints. The endpoint must support JSON responses;
    fixed records use native strict schemas.
    """
    try:
        provider = OpenAICompatibleProvider(base_url, model, os.environ.get("FORGE_API_KEY", ""))
        project = compile_documents(sources, output, provider)
        display(
            {
                "status": "draft",
                "spec": str(output / "project.json"),
                "nodes": len(project.nodes),
                "evidence": "exact quotes checked; review policy interpretation before training",
            }
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("replay")
def replay_command(run: Path, predictions: Path, output: Path | None = None) -> None:
    """Score recorded candidate choices against a verified decision dataset."""
    try:
        result = replay(run, predictions)
        if output:
            write_json(output, result)
        display({key: value for key, value in result.items() if key != "events"})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("harbor-export")
def harbor_export_command(
    run: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    limit: int = 20,
    split: str = "test",
) -> None:
    """Create single-step decision task directories for Harbor."""
    try:
        if split not in {"train", "development", "test"}:
            raise ValueError("Choose train, development, or test")
        display({"tasks": export_harbor(run, output, limit, split), "output": str(output)})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("ingest")
def ingest_command(manifest: Path, output: Annotated[Path, typer.Option("--output", "-o")]) -> None:
    """Normalize source documents with versions, access metadata, and source spans."""
    try:
        if output.exists():
            raise ValueError("Ingestion output already exists")
        documents = ingest_manifest(manifest)
        write_json(output, [d.model_dump(mode="json") for d in documents])
        display({"sources": len(documents), "output": str(output)})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("compile-corpus")
def compile_corpus_command(
    manifest: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    max_calls: int = 100,
    validation_retries: int = 2,
    response_mode: str = "auto",
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int = 32000,
    timeout_seconds: float = 300,
    max_input_bytes: int = 4_000_000,
    max_total_tokens: int | None = None,
    chunk_characters: int = 8000,
    overlap_characters: int = 400,
) -> None:
    """Send an explicit source corpus to a model, checkpoint extraction, and assemble a review draft."""
    try:
        # One journal reservation equals one HTTP attempt; retries require an explicit resume.
        if response_mode not in {"auto", "json_schema", "json_object"}:
            raise ValueError("Choose auto, json_schema, or json_object response mode")
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            os.environ.get("FORGE_API_KEY", ""),
            retries=0,
            response_mode=response_mode,  # type: ignore[arg-type]
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        options = CompilationOptions(
            validation_retries=validation_retries,
            max_calls=max_calls,
            max_input_bytes=max_input_bytes,
            max_total_tokens=max_total_tokens,
            chunk_characters=chunk_characters,
            overlap_characters=overlap_characters,
        )
        identity = digest({"endpoint": provider.base_url, "model": model, "temperature": 0})
        display(compile_corpus(manifest, output, provider, identity, options))
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("review-status")
def review_status_command(spec: Path, output: Path | None = None) -> None:
    """Inspect current rule/node reviews and observed rule conflicts; export details locally."""
    try:
        status = review_status(spec)
        if output:
            write_json(output, status)
        display(
            {
                **{k: status[k] for k in ("fingerprint", "ready", "pending", "rejected")},
                "items": [
                    {"id": i["id"], "kind": i["kind"], "status": i["status"], "fingerprint": i["fingerprint"]}
                    for i in status["items"]
                ],
            }
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("review-record")
def review_record_command(
    spec: Path,
    item: Annotated[str, typer.Option()],
    decision: Annotated[str, typer.Option()],
    reviewer: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    fingerprint: Annotated[str, typer.Option()],
) -> None:
    """Record a named review against the exact fingerprint shown by review-status."""
    try:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Decision must be accepted or rejected")
        event = record_review(spec, item, decision, reviewer, reason, fingerprint)  # type: ignore[arg-type]
        display({"recorded": event["item_id"], "decision": event["decision"], "hash": event["hash"]})
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("evaluate-extraction")
def evaluate_extraction_command(predicted: Path, reference: Path, output: Path | None = None) -> None:
    """Compute exact source-span/kind metrics against a separately authored reference."""
    try:
        result = extraction_metrics(read_json(predicted), read_json(reference))
        if output:
            write_json(output, result)
        display(result)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        fail(exc)


@app.command("evaluate-decisions")
def evaluate_decisions_command(spec: Path, reference: Path, bindings: Path, output: Path | None = None) -> None:
    """Check compiled rule semantics against fixed model-neutral cases and explicit field bindings."""
    try:
        project = load_project(spec)
        load_sources(project, spec.parent)
        result = evaluate_decisions(
            project,
            DecisionReference.model_validate(read_json(reference)),
            {key: NodeBinding.model_validate(value) for key, value in read_json(bindings).items()},
        )
        if output:
            write_json(output, result)
        display({key: value for key, value in result.items() if key != "events"})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("workflow-run")
def workflow_run_command(
    spec: Path,
    task: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    reasoning_effort: str = "high",
    max_calls: int = 20,
    max_total_tokens: int = 500000,
) -> None:
    """Run a declared business task against an explicit model endpoint and independently grade its trace."""
    from decision_forge.corpus import CallJournal
    from decision_forge.review import require_review
    from decision_forge.workflows import WorkflowTask, solve_workflow

    try:
        if output.exists():
            raise ValueError("Workflow output already exists; choose a new directory")
        project = load_project(spec)
        load_sources(project, spec.parent)
        require_review(spec)
        task_spec = WorkflowTask.model_validate(read_json(task))
        output.mkdir(parents=True)
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            os.environ.get("FORGE_API_KEY", ""),
            retries=0,
            temperature=None,
            reasoning_effort=reasoning_effort,
            max_output_tokens=4096,
            timeout_seconds=300,
        )
        journal = CallJournal(
            output / "calls.json", CompilationOptions(max_calls=max_calls, max_total_tokens=max_total_tokens)
        )
        result = solve_workflow(project, task_spec, provider, journal)
        write_json(output / "result.json", result)
        write_json(
            output / "configuration.json",
            {"provider": provider.configuration, "task": task_spec.model_dump(mode="json")},
        )
        display({k: v for k, v in result.items() if k not in {"trace", "calls"}})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("workflow-grade")
def workflow_grade_command(spec: Path, task: Path, trace: Path, output: Path | None = None) -> None:
    """Recompute workflow utility and policy compliance from a recorded trace."""
    from decision_forge.workflows import WorkflowTask, grade_workflow

    try:
        project = load_project(spec)
        load_sources(project, spec.parent)
        result = grade_workflow(project, WorkflowTask.model_validate(read_json(task)), read_json(trace))
        if output:
            write_json(output, result)
        display(result)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        fail(exc)


@app.command("events-ingest")
def events_ingest_command(events: Path, database: Annotated[Path, typer.Option()]) -> None:
    """Append versioned runtime events atomically; repeated identical IDs are idempotent."""
    from decision_forge.feedback import RuntimeEvent, ingest_events

    try:
        display(ingest_events(database, [RuntimeEvent.model_validate(row) for row in read_json(events)]))
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("feedback-plan")
def feedback_plan_command(run: Path, database: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Create generation requests only from explicitly reviewed development errors."""
    from decision_forge.feedback import feedback_requests

    try:
        result = feedback_requests(run, database)
        write_json(output, result)
        display({"requests": len(result["requests"]), "excluded": result["excluded"]})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("update")
def update_command(
    spec: Path, previous: Path, output: Annotated[Path, typer.Option()], cache: Path | None = None
) -> None:
    """Rebuild affected scenarios while freezing published train/development/test membership."""
    from decision_forge.feedback import update_dataset

    try:
        display(update_dataset(spec, previous, output, cache))
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("lineage")
def lineage_command(run: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Export source-span, rule, scenario, and episode lineage for a completed dataset."""
    from decision_forge.feedback import lineage

    try:
        result = lineage(run)
        write_json(output, result)
        display({"dataset_digest": result["dataset_digest"], "episodes": len(result["episodes"])})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("orchestrate")
def orchestrate_command(
    spec: Path,
    output: Annotated[Path, typer.Option()],
    database: Annotated[Path, typer.Option()],
    thread_id: Annotated[str, typer.Option()],
) -> None:
    """Run or resume a durable LangGraph review/build/export job (requires orchestration extra)."""
    from decision_forge.adapters import orchestrate

    try:
        display(orchestrate(spec, output, database, thread_id))
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("designer-process")
def designer_process_command(run: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Process ordered dataset seeds through the pinned Data Designer expression engine."""
    from decision_forge.adapters import designer_process

    try:
        display(designer_process(run, output))
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("transform")
def transform_command(run: Path, episode_id: str, intervention: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Check a counterfactual or candidate-order rewrite and save its verified parent/child pair."""
    from decision_forge.pipeline import load_run, load_run_project
    from decision_forge.transformations import Intervention, transform_episode

    try:
        parent = next((e for e in load_run(run) if e.id == episode_id), None)
        if parent is None:
            raise ValueError("Unknown episode ID")
        request = Intervention.model_validate(read_json(intervention))
        child = transform_episode(load_run_project(run), parent, request)
        write_json(
            output,
            {
                "parent": parent.model_dump(mode="json"),
                "child": child.model_dump(mode="json"),
                "intervention": request.model_dump(mode="json"),
            },
        )
        display({"parent": parent.id, "child": child.id, "relation": request.relation, "verified": True})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("plan-recipe")
def plan_recipe_command(
    spec: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    mode: str = "uniform_cells",
    per_cell: int = 50,
    total: int | None = None,
    stratum: Annotated[list[str] | None, typer.Option(help="Restrict planning to a stratum.")] = None,
    targets: Annotated[Path | None, typer.Option(help="JSON list of explicit distribution targets.")] = None,
    base_url: Annotated[str | None, typer.Option(help="Optional advisor model endpoint.")] = None,
    model: Annotated[str | None, typer.Option(help="Optional advisor model name.")] = None,
    states_per_call: int = 8,
) -> None:
    """Plan exact per-cell quotas from the measured deterministic pool, offline by default."""
    from decision_forge.models import DistributionTarget
    from decision_forge.planner import PlanObjective, PlanOptions, plan_recipe

    try:
        if mode not in {"uniform_cells", "explicit"}:
            raise ValueError("Choose uniform_cells or explicit")
        objective = PlanObjective.model_validate(
            {
                "mode": mode,
                "per_cell": per_cell,
                "total_examples": total,
                "strata": [value for value in (stratum or []) if value],
                "targets": [DistributionTarget.model_validate(row) for row in read_json(targets)] if targets else [],
            }
        )
        provider = None
        if base_url or model:
            if not (base_url and model):
                raise ValueError("An advisor model requires both --base-url and --model")
            provider = OpenAICompatibleProvider(base_url, model, os.environ.get("FORGE_API_KEY", ""))
        plan = plan_recipe(
            spec,
            output,
            objective,
            options=PlanOptions(states_per_call=states_per_call),
            provider=provider,
        )
        display(
            {
                "status": plan["status"],
                "cells": len(plan["distribution"]),
                "requested": plan["feasibility"]["requested"],
                "available": plan["feasibility"]["available"],
                "shortfall": plan["feasibility"]["shortfall"],
                "feasible": plan["feasibility"]["feasible"],
                "expansion_requests": len(plan["expansion_requests"]),
                "held_out_shortfall": plan["held_out_shortfall"]["shortfall"],
                "model_calls_estimate": plan["cost_estimate"]["model_calls"],
                "output": str(output),
            }
        )
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("expand")
def expand_command(
    run: Path,
    requests: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    states_per_call: int = 8,
    max_calls: int = 20,
    max_input_bytes: int = 4_000_000,
    max_output_tokens: int = 4096,
    near_duplicate_threshold: float = 0.9,
    reasoning_effort: str | None = None,
) -> None:
    """Synthesize extra examples with an explicit model call and verify every label by replay."""
    from decision_forge.expansion import ExpansionOptions, ProposalBatch, expand

    try:
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            os.environ.get("FORGE_API_KEY", ""),
            retries=0,
            temperature=None,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        try:
            batch = ProposalBatch.model_validate({"requests": read_json(requests)})
        except ValueError as invalid:
            # Request files hold no source content, so the field location is safe to report.
            detail: list[dict[str, Any]] = getattr(invalid, "errors", lambda: [])()
            location = detail[0]["loc"] if detail else "unknown field"
            raise ValueError(f"Invalid expansion request file; first problem at {location}") from None
        summary = expand(
            run,
            batch.requests,
            output,
            provider,
            ExpansionOptions(
                states_per_call=states_per_call,
                max_calls=max_calls,
                max_input_bytes=max_input_bytes,
                near_duplicate_threshold=near_duplicate_threshold,
            ),
            provider_id=digest({"endpoint": base_url, "model": model}),
        )
        display(summary)
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("dedup")
def dedup_command(run: Path, output: Path | None = None, threshold: float = 0.9) -> None:
    """Report duplicate inputs and held-out leaks in a completed run."""
    from decision_forge.dedup import find_duplicates
    from decision_forge.pipeline import load_run

    try:
        report = find_duplicates(load_run(run), threshold=threshold)
        if output:
            write_json(output, report)
        display(
            {key: report[key] for key in ("protocol", "threshold", "examples", "kept", "removed", "cross_split_pairs")}
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("rewrite")
def rewrite_command(
    run: Path,
    episode_id: str,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    verifier_model: Annotated[str | None, typer.Option(help="Independent verifier model name.")] = None,
) -> None:
    """Paraphrase one presentation and publish it only after independent verification."""
    from decision_forge.pipeline import load_run, load_run_project
    from decision_forge.transformations import rewrite_presentation

    try:
        provider = OpenAICompatibleProvider(base_url, model, os.environ.get("FORGE_API_KEY", ""), temperature=None)
        verifier = (
            OpenAICompatibleProvider(base_url, verifier_model, os.environ.get("FORGE_API_KEY", ""), temperature=None)
            if verifier_model
            else None
        )
        parent = next((e for e in load_run(run) if e.id == episode_id), None)
        if parent is None:
            raise ValueError("Unknown episode ID")
        child = rewrite_presentation(load_run_project(run), parent, provider, verifier)
        write_json(output, {"parent": parent.model_dump(mode="json"), "child": child.model_dump(mode="json")})
        display({"parent": parent.id, "child": child.id, "verified": True})
    except (ValueError, OSError) as exc:
        fail(exc)


if __name__ == "__main__":
    app()
