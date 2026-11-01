# Changelog

## Unreleased

### Changed

- Tell the project's story in the README and a new [Why Decision Forge](docs/vision.md) page: the missing
  trust layer for AI agents, the three kinds of organizational data it requires, and where Decision Forge
  fits in the data → model → organization stack.

### Fixed

- Attest only the wheel and sdist in the release workflow; `uv build` also writes `dist/.gitignore`.

## 0.3.1

Version 0.3.1 adds office, HTML, tabular, and OCR ingestion, fixes several integrity and portability
issues in the data pipeline, and is the first release built and attested by the release workflow.
Ingestion metadata has changed; recompile corpora into new output directories.

### Added

- Add automatic source format detection and DOCX, XLSX, PPTX, HTML and CSV/TSV parsers with locations.
- Add image and per-page PDF OCR through local RapidOCR/PP-OCR small models, Tesseract, and explicitly
  selected OpenAI-compatible vision endpoints. Keep model weights local during ingestion.
- Preserve OCR model identity, confidence, bounding boxes, access metadata, cache integrity and attempt
  journals. Require an explicit flag for OCR service calls and retain the business review gate.
- Add real local PP-OCR inference tests and a fictional seven-format example generator.
- Add a tag-triggered release workflow that verifies version consistency, runs the test suite, builds and
  smoke-tests the wheel, attests build provenance, creates a GitHub release from the changelog, and
  optionally publishes to PyPI through trusted publishing.
- Ship a `py.typed` marker so downstream type checkers use the package's inline annotations.
- Add project URLs, supported Python versions, and typing classifiers to the package metadata, and
  repository links to the citation file.
- Add opt-in dependency review on pull requests (`DEPENDENCY_REVIEW` repository variable), CodeQL analysis
  for Python and workflows, and OpenSSF Scorecard.
- Declare the `type/` and `area/` label taxonomy in `.github/roadmap.json`; the roadmap publisher now
  creates and updates those labels and applies them to roadmap issues without removing triage labels.
- Label pull requests by area automatically from the files they change.
- Add `.editorconfig` and `.gitattributes` (LF line endings, generated experiment artifacts), and a
  definition-of-done checklist to the pull request template.

### Changed

- Ingestion metadata and default format detection have changed. Recompile into a new output directory;
  existing content-bound reviews are not transferred to newly parsed source snapshots.
- Bound archive expansion, image pixels, page counts, spreadsheet traversal and normalized output.
- Run CI on pull requests, merge queues, and `main` only, cancel superseded pull request runs, bound every
  job with a timeout, and expose a single `CI result` status for branch protection.
- Group Dependabot updates so related upgrades land in one lock-file change, and track pre-commit hooks.
- Validate workflow and Dependabot files in pre-commit.

### Fixed

- Fix `compile-corpus` failing with "Source content hash changed" after spending its model budget when a
  CSV/TSV, spreadsheet, office, HTML, PDF, or JSON/YAML value contains carriage returns. Parsed text now
  uses `\n` line endings so stored snapshots read back with the recorded hash.
- Re-derive every model-visible field of imported synthesis rows: `load_expansion` now rejects rows whose
  question, policy context, candidates, verdicts, or ID differ from what the project produces, even when
  `accepted.jsonl` and its digest were rewritten together.
- Accept compiler and corpus evidence whose quote differs from the source only in whitespace runs. Such
  quotes were bound during compilation but produced a `project.json` that failed validation on load.
- Count the recipe planner's available pool after the duplicate removal a build applies, so `plan-recipe`
  no longer reports quotas as feasible that `build --recipe-plan` then rejects.
- Hash exported JSONL files and Data Designer outputs as UTF-8 regardless of the platform locale.
- Close SQLite connections after runtime event ingestion, open the read-only event store through a
  properly escaped file URI, and report missing or empty event stores as CLI errors instead of tracebacks.
- Report non-object decision bindings in `evaluate-decisions` as a CLI error instead of a traceback.

## 0.3.0

Version 0.3.0 packages organization compilation and distribution-aware decision synthesis.
Engine fingerprints have changed; use new output directories for builds created with this version.
The roadmap tracks the remaining training-quality and platform-release acceptance work.

- Consolidate dependency upgrades for checkout, setup-uv, mypy, filelock, pytest-cov, and pytest.
- Preserve subprocess coverage when migrating to pytest-cov 7.
- Deep-copy nested synthesis state so accepted and rejected proposals cannot mutate parent examples.
- Keep different organizational policies separate during near-duplicate detection.
- Reject stale expansion scenario, family, source, and split assignments before dataset publication.
- Reconcile roadmap status with implemented decision generation and training interfaces.

- Add documentation build and opt-in Pages deployment, a logo, governance, support, and label guidance.
- Maintain a Simplified Chinese README with language-switch links and matching examples.
- Add a reproducible offline benchmark report with host and dependency versions.
- Unify the project tagline across the README, package metadata, citation, and docs site.

- Add decision-only candidate-probability exports with family grouping and source provenance.
- Preserve explicit soft targets in validated external rows and encode native single-token candidates.
- Keep target probabilities aligned through candidate shuffling; reject truncation and label collisions.
- Apply training budgets to actual exported rows and record selected IDs and file digests.
- Replace the generic SFT launch stub with an explicit candidate-readout training handoff.
- Require explicit endpoint and model selection for generated-choice evaluation.

- Add a recipe-planning agent with a typed tool registry, an auditable ledger, and resumable plans.
- Separate synthesizable shortfalls from held-out and exhausted-state shortfalls before spending calls.
- Add verified model-written scenario synthesis: models propose state only, labels are replayed.
- Never synthesize held-out data, and refuse any synthesized row in a test split at build time.
- Checkpoint synthesis after every call so a provider failure cannot republish verified rows.
- Add global duplicate detection over structural groups with MinHash presentation similarity.
- Report cross-split duplicate leaks and metadata conflicts instead of silently dropping rows.
- Add semantics-preserving presentation rewrites gated on invariants plus an independent verifier.
- Report provider cost as unknown rather than zero when the endpoint returns no price.
- Add a fixed-budget fine-tuning and held-out evaluation loop with dataset-digest-bound comparisons.

- Add exact node/stratum/split/answer-set quotas with feasibility and divergence reports.
- Add verified counterfactuals and candidate-order rewrites that retain family ownership.
- Add resettable four-surface business workflows and independent trace grading.
- Add idempotent runtime events, reviewed development feedback, lineage, and targeted updates.
- Add pinned Data Designer expression processing and durable LangGraph orchestration.
- Keep Data Designer resume identity independent of first-run SDK model defaults.
- Run eight Harbor Docker tasks under both oracle and no-op controls in CI.
- Preserve a cumulative family split registry across removal and restoration.
- Bind review approvals to dependent rules, scenarios, source metadata and dataset recipes.
- Fix candidate-dimension mutations, split-cache leakage, and out-of-bounds evidence spans.
- Resolve repeated claim text through explicit trusted claim IDs.
- Reserve every HTTP attempt, including response-mode negotiation; add aggregate token reservations.
- Keep source-derived files private and sanitize remote error text.
- Record export file digests, split-specific ambiguity counts and stratified Harbor task IDs.


- Forward contract messages in validation-repair prompts; a bare location cannot describe a defect.
- Accept `exists` predicates without an operand and document per-operator operand requirements.
- Degrade `auto` response mode to `json_object` when an endpoint rejects JSON Schema output.
- Report HTTP status and recognized parameter names while excluding arbitrary provider error text.
- Assign dataset splits by family quotas instead of an independent hash per family.
- Report requested versus realized episode volumes and collapsed duplicates in build reports.
- Bind review decisions to individual items and re-open them only when that item changes.
- Detect shadowed dead rules and priority-override conflicts in the review queue.
- Accept reviewer edits to an assembled draft when they stay bound to extracted claim spans.
- Anchor quotes across hard-wrapped sources by content, with distinct not-found and ambiguous errors.
- Reject duplicate dimension values, and forbid dots in candidate IDs to keep paths unambiguous.
- Compare integers and floats by value while keeping booleans out of numeric comparisons.
- Give exported Harbor task files portable permissions and make shell scripts executable.
- Report non-JSON YAML workflow values, such as dates, as input errors instead of tracebacks.
- Publish reproducible Claude Opus 5.5 and GPT-6.1 Sol compiler experiments with all recorded failures and costs.
- Add model-neutral decision evaluation with explicit bindings and preserved reference disagreements.
- Add bounded checkpointed output repair, reasoning configuration, and exact JSON-fence compatibility.
- Count actual HTTP request bytes and retain usage for failed generated responses.
- Treat absent and null refund amounts explicitly in the fictional example.

- Add versioned text/PDF/workflow ingestion, exact source spans, and access metadata.
- Add resumable corpus compilation with persistent call reservations and integrity checks.
- Require content-bound rule/node/conflict review for compiler output builds.
- Add exact-span extraction metrics with a predeclared fictional policy reference.
- Fix recursive JSON equality, missing build source snapshots, and private YAML error leakage.

## 0.1.0

- Introduce typed organization, rule, workflow, scenario, and episode contracts.
- Add evidence quotation checks, deterministic policy verification, seeded generation, and stable splits.
- Add content-addressed caching, resumable builds, integrity checks, and targeted change reports.
- Export canonical, typed-choice, chat SFT, and single-step Harbor task formats.
- Compile text documents through explicitly configured JSON Schema model endpoints.
- Include a fictional four-surface workflow example, regression tests, CI, and project documentation.
