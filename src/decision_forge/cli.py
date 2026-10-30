"""Command-line entry points with explicit inputs and structured results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from filelock import Timeout

from decision_forge import __version__
from decision_forge.compiler import compile_documents
from decision_forge.exporters import export_run
from decision_forge.harness import export_harbor, replay
from decision_forge.io import load_project, load_sources, write_json
from decision_forge.models import Project
from decision_forge.pipeline import build, impact
from decision_forge.providers import OpenAICompatibleProvider

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
    spec: Path, output: Annotated[Path, typer.Option("--output", "-o")], cache: Path | None = None
) -> None:
    """Generate, verify, checkpoint, and report decision episodes. Runs offline."""
    try:
        display(build(spec, output, cache)["report"])
    except (ValueError, OSError, Timeout) as exc:
        fail(exc)


@app.command("export")
def export_command(run: Path, output: Annotated[Path, typer.Option("--output", "-o")], format: str = "typed") -> None:
    """Export split-aware typed, SFT, or set-valued canonical training data."""
    try:
        if format not in {"typed", "sft", "canonical"}:
            raise ValueError("Format must be typed, sft, or canonical")
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

    Set FORGE_API_KEY for authenticated endpoints. The endpoint must support json_schema responses.
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


if __name__ == "__main__":
    app()
