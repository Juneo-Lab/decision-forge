# Roadmap

Decision Forge grows from a reproducible local data compiler into an extensible organizational
decision data platform. Milestones are ordered by dependencies and acceptance criteria. Release dates
will be assigned when maintainers confirm capacity.

The machine-readable backlog lives in [.github/roadmap.json](.github/roadmap.json). The publishing
script creates matching GitHub milestones and issues without duplicating previously published items.
The **Sync roadmap** workflow runs when this backlog changes on `main`, or on manual dispatch.
It uses GitHub's temporary repository token with issue-write permission and preserves existing
milestone and issue states so maintainers can track progress directly on GitHub.

## M0 · v0.1 · Reproducible foundation

Deliver typed contracts, deterministic rule evaluation, scenario families, split-aware exports,
provenance, content-addressed caching, document compilation, and single-step task export.

Acceptance: the fictional example builds offline; tests cover decision boundaries, evidence,
split isolation, integrity, and resumability; lint, typing, documentation, and package builds pass.

## M1 · v0.2 · Organization compiler

Add document ingestion adapters, evidence spans and source versioning, semantic rule review,
workflow extraction evaluation, and resumable compilation for larger corpora.

Acceptance: ingest at least two document formats through the adapter contract; preserve source
locations and access metadata; report extraction accuracy on reviewed fixtures; isolate incomplete
or conflicting rules in an explicit review queue.

## M2 · v0.3 · Distribution-aware synthesis

Integrate Data Designer through a version-pinned optional extra. Add conditional sampling,
stratum budgets, constrained counterfactual families, semantic-preserving rewrites, deduplication,
and bounded model-call budgets. Integrate a persistent orchestration adapter.

Acceptance: requested and realized distributions are reported; family relations have executable
checks; interrupted jobs resume; generation cost is recorded per accepted example; the offline core
continues to work without integration dependencies.

## M3 · v0.4 · Executable organization tasks

Build a multi-step customer workflow environment with mock CRM, sharing, and approval tools.
Integrate Harbor and independent verification, then add a Verifiers adapter where useful.

Acceptance: multiple harness/model combinations run the same tasks; environments reset reproducibly;
grading artifacts stay outside solver-visible inputs; policy compliance, task utility, cost, and
escalation are recorded separately; task validators pass positive, negative, and mutation fixtures.

## M4 · v0.5 · Feedback and targeted updates

Introduce runtime event ingestion, outcome association, review feedback, source-to-model lineage,
policy-change invalidation, and development-set-driven coverage updates. Evaluate Agent Lightning
integration for generative trajectories separately from typed decision-head training.

Acceptance: a source policy change identifies all affected artifacts; unchanged artifacts are reused;
held-out test families remain isolated; a data update has a reproducible quality and cost report.

## M5 · v1.0 · Stable open-source platform

Stabilize extension APIs and schema migrations, expand operational documentation, test larger datasets,
publish repeatable performance measurements, and establish signed release and package workflows.

Acceptance: compatibility tests cover supported versions and adapters; packaging and deployment
instructions reproduce on clean systems; model/data quality and throughput reports state hardware,
versions, costs, and protocols; maintainers publish a support and deprecation policy.

## Contribution lanes

- Organization source adapters and reviewed policy fixtures.
- Distribution samplers and scenario-family validators.
- Environment tools and executable acceptance checks.
- Training exporters and evaluation adapters.
- Documentation, reproducibility, performance, and developer tooling.
