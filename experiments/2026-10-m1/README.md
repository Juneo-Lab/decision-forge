# M1 live compiler experiment

Date: 2026-10-31 (Asia/Shanghai). Inputs: the fictional Northstar Support handbook shipped with this
repository. No customer documents were used. This directory preserves model outputs, source snapshots,
field bindings, exact extraction metrics, execution results, usage journals, and rejected review items.

## Protocol

The extraction reference contains 13 sentence-level source spans, authored before the live experiments.
The decision reference contains 32 model-neutral cases, authored before inspecting the flagship outputs.
These are maintainer-authored fixtures, not an external benchmark or independent human expert annotation.
Models receive the handbook and project schema; neither reference answers nor evaluation bindings are
included in generation prompts. Bindings translate model-chosen names and values into the fixed cases;
they cannot change rules or expected actions. Compound recipient/authorization bindings are explicit.

Both flagship runs use `reasoning_effort=high`, no temperature parameter, a 32,000 output-token limit,
and the OpenRouter chat-completions endpoint. The source and model generation remain stochastic;
replaying the saved rule specifications is deterministic.

| Model | Exact claims | Decision agreement | Known cost of the successful run | Calls |
| --- | ---: | ---: | ---: | ---: |
| Claude Opus 5.5 | 13/13 | 31/32 | $0.293428 | 2 |
| GPT-6.1 Sol | 13/13 | 31/32 | $0.1505675 | 2 |

These measurements establish compatibility and observed behavior on one small fictional handbook.
They do not estimate performance across companies, document types, or downstream fine-tuning.

## Findings preserved for review

- **Opus, missing-key handling:** `select_actions-06` omits the amount key. The generated clarification
  rule checks equality with null; the policy interpreter treats an absent key separately. No candidate
  is allowed. The corresponding node is rejected for review. Raw rules have not been repaired to improve
  the reported score.
- **GPT, reference disagreement:** `evaluate_outcomes-01` maps unsuccessful checks to `failed`, with
  complete evidence. The source explicitly requests revision for `incomplete` checks. GPT distinguishes
  failed from incomplete and returns no action in this case. This is a source/reference ambiguity that
  needs adjudication, not a conclusively incorrect quotation or an unambiguous extraction error. The
  original binding and 31/32 score are retained. The node is rejected pending clarification.
- Both projects expose all four decision surfaces. All rule references resolve to validated source
  spans. Both remain review drafts, and dataset builds are blocked until the current review queue is
  accepted. Passing the extraction metric alone does not authorize training-data publication.

## Diagnostic history and cost

`attempt-ledger.json` includes earlier GPT-4.1, GPT-4.1 mini, and Gemini 2.5 Flash diagnostics, failed
protocol/validation attempts, and interrupted calls. Those older models were retired from acceptance
work when the project owner requested flagship models. They are not the project's default evaluators.

Failures exposed strict-schema requirements, inaccurate model-generated offsets, nested conditions,
malformed dimension paths, and Markdown-fenced JSON. The implementation now computes offsets itself,
validates schemas locally, supports bounded checkpointed validation repairs, and accepts one complete
JSON fence while rejecting surrounding prose. Two earlier Opus assembly attempts were billed and
failed parsing before the fence compatibility fix; their costs remain in the ledger.

Across the journaled experiment attempts, reported cost totals **$1.08587197**. Five attempts have no
returned cost, including interrupted requests and HTTP errors; the billed total is therefore unknown.
The per-model table above is not the total development expenditure. Some early journal entries predate
validation-outcome and response-metadata instrumentation; missing fields remain missing.

## Replay without model calls

From the repository root:

```bash
uv run decision-forge evaluate-extraction experiments/2026-10-m1/opus55-high/claims.json \
  examples/customer_support/extraction-reference.json
uv run decision-forge evaluate-decisions experiments/2026-10-m1/opus55-high/project.json \
  examples/customer_support/decision-reference.json \
  experiments/2026-10-m1/opus55-high/decision-bindings.json
uv run decision-forge evaluate-decisions experiments/2026-10-m1/gpt61sol-high/project.json \
  examples/customer_support/decision-reference.json \
  experiments/2026-10-m1/gpt61sol-high/decision-bindings.json
```

To run a new live experiment, set `FORGE_API_KEY` locally and select a new output directory:

```bash
uv run python scripts/evaluate_compiler.py examples/customer_support/corpus.yaml \
  examples/customer_support/extraction-reference.json \
  --base-url https://openrouter.ai/api/v1 --model anthropic/claude-opus-5.5 \
  --reasoning-effort high --output runs/opus55-reproduction
```

The GPT snapshot predates the single-fence parser update; the request semantics were unchanged, but
new compiler fingerprints differ. Its original configuration is preserved in `live-report.json`.
Online model availability and prices were checked against the
[OpenRouter catalog](https://openrouter.ai/api/v1/models). Reported costs come from responses, not an
estimate based on listed prices. Generation responses and reasoning text are not required for replay
and are excluded from this public snapshot.

## Software verification

The implementation passed 92 offline behavioral tests with 88% total statement coverage on macOS
ARM64 / CPython 3.12.12. Ruff, strict mypy, strict MkDocs, and source/wheel builds passed. A clean
virtual environment installed the built wheel without development or PDF extras, generated the 336
fictional baseline episodes, and replayed the archived Opus result as 31/32. These are local results;
remote GitHub CI status is reported separately. Optional PDF parsing was exercised in the development
environment with pypdf 6.21.4.
