"""Run an explicit live compilation and save metered results, including unsuccessful attempts.

Only use public, fictional, or explicitly authorized source corpora with remote endpoints.
Credentials are read from FORGE_API_KEY and never included in the report.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from pydantic import ValidationError

from decision_forge.corpus import CompilationOptions, compile_corpus, extraction_metrics
from decision_forge.io import read_json, write_json
from decision_forge.providers import OpenAICompatibleProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--response-mode", choices=["auto", "json_schema", "json_object"], default="auto")
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--max-input-bytes", type=int, default=200000)
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--validation-retries", type=int, default=2)
    args = parser.parse_args()
    provider = OpenAICompatibleProvider(
        args.base_url,
        args.model,
        os.environ.get("FORGE_API_KEY", ""),
        retries=0,
        temperature=None,
        reasoning_effort=args.reasoning_effort,
        response_mode=args.response_mode,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=300,
    )
    started = time.monotonic()
    report = {"configuration": provider.configuration, "status": "failed"}
    exit_code = 1
    try:
        state = compile_corpus(
            args.manifest,
            args.output,
            provider,
            args.model,
            CompilationOptions(
                max_calls=args.max_calls,
                max_input_bytes=args.max_input_bytes,
                validation_retries=args.validation_retries,
            ),
        )
        report.update(status=state["status"], compilation_fingerprint=state["fingerprint"])
        exit_code = 0
    except (ValueError, OSError, KeyboardInterrupt) as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, ValidationError):
            report["validation_errors"] = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        # Source excerpts and arbitrary exception strings are intentionally absent from reports.
    if exit_code == 0 and (args.output / "claims.json").exists():
        report["extraction"] = extraction_metrics(read_json(args.output / "claims.json"), read_json(args.reference))
    if (args.output / "calls.json").exists():
        events = read_json(args.output / "calls.json")
        report["calls"] = events
        report["known_cost"] = sum((e.get("usage") or {}).get("cost", 0) for e in events)
        report["calls_without_reported_cost"] = sum("cost" not in (e.get("usage") or {}) for e in events)
    report["elapsed_seconds_this_invocation"] = time.monotonic() - started
    write_json(args.output / "live-report.json", report)
    print(f"Status: {report['status']}. Report: {args.output / 'live-report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
