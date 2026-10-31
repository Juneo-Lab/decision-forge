# Changelog

## Unreleased

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
