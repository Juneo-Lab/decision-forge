# Organization compiler

The compiler connects five independently inspectable objects: source snapshots, extracted claims,
a typed project, review decisions, and training episodes. Each transition has its own validation.

## Source corpus

Install office/PDF/HTML support with `uv sync --extra documents`. Add `--extra ocr-rapid` for
local image/scanned-PDF OCR. See [Document formats and OCR](document-ingestion.md) for the complete
format matrix and backend configuration. Describe sources in a manifest:

```yaml
schema_version: '1.0'
sources:
  - id: handbook
    path: handbook.md
    version: '2026-10'
    adapter: text
    access:
      classification: internal
      allowed_roles: [support, operations]
      owner: Operations
```

Source paths resolve inside the manifest directory. Text adapters retain line/Markdown-heading
locations. The PDF adapter uses pypdf and preserves page references; configured OCR handles image-only
pages or all rendered pages in `mode: always`. Image-only pages without OCR fail explicitly.
The workflow adapter reads JSON/YAML
objects and records JSON pointers for normalized leaf values. Raw file hashes and normalized content
hashes capture different stages of the transformation. Unicode character offsets refer to normalized
text, with an inclusive start and exclusive end.

```bash
uv run decision-forge ingest examples/customer_support/corpus.yaml --output runs/corpus.json
```

Access metadata follows sources into the project and snapshots. The compiler replaces model-proposed
access metadata with the supplied values. This metadata supports downstream access enforcement;
local filesystem ownership and model-endpoint authorization remain the operator's responsibility.

## Resumable compilation

```bash
export FORGE_API_KEY="your-provider-key"
uv run decision-forge compile-corpus examples/customer_support/corpus.yaml \
  --base-url https://your-model-host.example/v1 --model your-model \
  --reasoning-effort high --max-calls 20 --max-input-bytes 500000 --output runs/compiled
```

The endpoint must accept chat completions with JSON responses. Fixed record schemas use native strict
`json_schema` output after normalization. Project schemas contain dynamic state/attribute maps;
these use `json_object` output with the full schema in context and mandatory local Pydantic validation.
Every call records its selected mode. A response containing exactly one enclosing Markdown JSON
fence is unwrapped; surrounding prose is rejected. The selected encoding is journaled. The command transmits
only the explicitly listed corpus. It extracts quoted claims from overlapping chunks, resolves exact
source positions from unique exact quotes without asking models to count characters, then assembles the claims into a typed organizational project. Assembly sees all
extracted claims and source metadata, which allows rules to refer across documents. The output remains
a review draft.

`--max-calls` bounds actual HTTP attempts for the built-in provider; `--max-input-bytes` bounds serialized HTTP request-body
bytes for the OpenAI-compatible provider, including transport-added schema instructions. Custom
providers can implement `input_bytes(system, user, schema)` to meter their wire representation;
otherwise the journal explicitly labels its logical prompt/schema measurement.
Response-format fallback reserves a second HTTP attempt before sending it. A failed or
interrupted attempt consumes its reservation. Resume explicitly after a failure; an operator can increase
the budgets. Actual provider token counts and returned cost values are recorded when available;
usage on invalid generated JSON is retained; unreported usage stays unknown. Custom metered providers
reset `last_usage` at the start of each invocation to prevent attribution to a previous call. Input bytes are distinct from model tokens.

`--max-total-tokens` adds a conservative preflight reservation of serialized request bytes plus the
provider output-token cap, assuming byte-based text tokenization. Returned token usage replaces a
reservation; unknown/interrupted attempts retain it. Providers must declare a finite output cap.
This is a planning bound for compatible text tokenizers, not an endpoint-independent billing guarantee.
Assembly evidence can cite `claim_id` to distinguish identical quotes at different source positions;
the compiler verifies its source/quote and copies its trusted offsets and locator.

`--validation-retries` permits two repair requests by default in the CLI (the library defaults to zero).
Rejected structured outputs and sanitized validation diagnostics are retained locally. Repairs include
the original request and previous response, consume the same global call/byte budget, and resume from
an integrity-checked checkpoint. Network errors require explicit resume. Exhausted validation repairs
require an explicit increase of their limit. Repairs never bypass evidence or schema validation.

`--reasoning-effort high`, `--max-output-tokens`, and `--timeout-seconds` configure reasoning models.
Temperature is omitted by default in `compile-corpus`; configure it only for endpoints that support it.
Model/configuration identity participates in cache keys. Flagship validation currently uses Claude Opus
5.5 and GPT-6.1 Sol through OpenRouter.

Shared content-addressed caches live in `.forge-compile-cache` next to output directories. Cache keys
include source identity/version/text, prompt, schema, and endpoint/model configuration identity.
Changed documents invalidate their extraction results; unchanged chunks can be reused in a new run.
Changed sources or compilation configuration require a new output directory. Failed validation is
never cached as a usable response. Rejected drafts are repaired with bounded, checkpointed prompts that
include the contract message for each violation, because a bare error location does not describe the
defect. Reviewer edits to an assembled draft are accepted on resume as long as the result stays bound
to extracted claim spans; edits that cite unknown evidence are refused.

The initial implementation bounds individual source files to 2 MB, corpora to 32 MB, individual PDFs
to 500 pages, and assembled claim context to 1 MB. Partition larger claim collections by workflow.
This version assembles retrieved claims in one request; hierarchical retrieval is tracked in M1.

Artifacts include normalized `sources/`, `corpus.json`, `claims.json`, `calls.json`, `project.json`,
`compilation.json`, and `review-queue.json`. Treat the output and caches as source-derived private data.
Completed resumes verify source/project/artifact integrity and can reconstruct a missing review queue.

## Rule and workflow review

```bash
uv run decision-forge review-status runs/compiled/project.json --output runs/review-details.json
uv run decision-forge review-record runs/compiled/project.json \
  --item rule:refund-threshold --decision accepted \
  --reviewer reviewer-id --reason "Verified threshold and approval exception against the handbook." \
  --fingerprint FINGERPRINT_FROM_REVIEW_STATUS
uv run decision-forge build runs/compiled/project.json --output runs/reviewed-data
```

Review every rule, decision node, and flagged conflict. Nodes expose candidates, rules, and generation
dimensions for inspection. An allow/deny pair on the same node enters the queue unless its conditions
are provably disjoint:

- **Observed overlap.** The build's own generated situations (every sampled combination of dimension
  values applied to each seed scenario) contain a candidate that both rules govern. The item lists up
  to five witnesses, each with its scenario, candidate, and dimension values, plus the total count.
- **Possible overlap.** No generated situation exercises the pair, but the conditions cannot be
  proven disjoint, so a synthesized or future state could. Disjointness is judged conservatively from
  the interpreter's semantics, for example `eq` against a different `eq`, non-overlapping numeric
  bounds, or `exists: false` against any predicate that needs a value.
- **Identical conditions.**

The item kind is `override` when the rules differ in priority, so the higher-priority rule decides,
and `conflict` otherwise, where deny wins. Rules that a higher-priority rule of the same effect always
outranks enter the queue as shadowed, dead rules. Reviewers examine the full rule context.

A review records identity, reason, time, decision, and the fingerprint of the reviewed item plus the
sources its evidence cites. Editing an unrelated rule no longer invalidates an accepted decision,
while any change to the reviewed rule, its evidence, or a cited source re-opens that item. A
Node reviews also bind referenced rules, scenarios and their source permissions. A dataset review
binds the recipe and organization policy version. A hash-linked history detects accidental modification. Identity is operator-supplied; organization SSO
and cryptographic reviewer authentication are future integration work.

Builds of compiler outputs require current acceptance for every item. Hand-authored specifications
retain the offline example workflow. The gate is a local correctness mechanism, not a security boundary
against an operator who controls the artifact directory. Generation outputs retain the complete source
snapshot so evidence can be audited after the original files change.

## Extraction evaluation

```bash
uv run decision-forge evaluate-extraction runs/compiled/claims.json \
  examples/customer_support/extraction-reference.json --output runs/extraction-metrics.json
```

The included reference contains 13 sentence-level policy statements, annotated by decision surface
before the live model experiment. Exact source/span/kind precision and recall penalize missing and
unsupported claims. The metric is sensitive to quote segmentation; evaluate rule semantics separately.
Unit tests verify the metric on deliberate misses and extras. Local HTTP tests use scripted responses
to validate transport and accounting. Their results do not measure model extraction quality.

## Acceptance evidence

- `test_sources.py`: actual PDF parsing, text/workflow locations, permissions, version changes, path boundaries.
- `test_corpus.py`: provider interruption, budgets, cross-run reuse, invalid responses, source changes,
  persisted size limits, integrity, chunk overlap, and local HTTP transport.
- `test_review.py`: pending/rejected review gates, content changes, conflicts, and history integrity.
- `test_regressions.py`: recursive JSON comparison, source snapshots, and private YAML error redaction.

Live evidence is published in [the experiment report](https://github.com/Juneo-Lab/decision-forge/tree/feat/organization-compiler/experiments/2026-10-m1).
Both flagship models extracted 13/13 reference claims and agreed with 31/32 fixed decision cases.
One Opus mismatch concerns an absent amount key; the GPT mismatch exposes a source/reference ambiguity.
Both drafts retain rejected review nodes. The authored example passes all 32 reference cases.

Use `evaluate-decisions SPEC REFERENCE BINDINGS` to replay semantic cases without model calls. Explicit
bindings translate field names, candidate IDs, and categorical values; all supplied facts must be
consumed. Composite bindings reject partial inputs. Project, reference, and binding digests accompany
results. Frozen reference answers stay outside generation prompts.

M1 remains open for larger and independently annotated multi-document corpora. M2–M5 remain tracked
separately; this increment implements and tests the compiler and review path.
