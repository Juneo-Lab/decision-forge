# Architecture

## Stable contracts

`Project` binds source documents, organization rules, workflow decision nodes, seed scenarios, and a
generation recipe. `Episode` records a decision-time input, valid choices, evidence, interventions,
and verification results. JSON Schema is generated from the same Pydantic contracts used at runtime.

The package is organized by responsibility:

| Module | Responsibility |
| --- | --- |
| `models` | Versioned contracts and reference validation |
| `io` | Canonical serialization, source loading, hashing, atomic writes |
| `compiler` / `providers` | Explicit model-assisted document compilation |
| `policy` | Deterministic predicate evaluation and candidate ranking |
| `generation` | Controlled dimensions and scenario families |
| `splits` | Family split assignment and the split lock that keeps it across versions |
| `pipeline` | Caching, manifest lifecycle, reporting, change analysis |
| `exporters` | Model-visible training views and supervision formats |
| `harness` | Offline replay and portable task exports |
| `cli` | User-facing commands |

## Build lifecycle

1. Validate the project and resolve every referenced source inside the project directory.
   Pin every family to its split from the split lock, assigning new families.
2. Match rule quotations against source text and hash source contents.
3. Compute the run fingerprint and acquire a single-writer output lock.
4. For each scenario, compute a fingerprint from its node, applicable rules, sources, and recipe.
5. Reuse an integrity-checked cache entry or sample and verify a new scenario family.
6. Separate accepted episodes from cases with no valid candidate.
7. Write episodes, reports, the specification snapshot, and a completed manifest atomically,
   then record any new family assignments in the split lock.

A crash leaves the run incomplete. Rerunning the same command reuses completed scenario caches.
Changed inputs require a new output directory; unaffected caches remain reusable. `impact` previews
which scenario-level work changed. Source hashing currently operates at document granularity.

## Extension strategy

The core provides a working reference implementation. M1 expands the source/compiler boundary;
M2 adds Data Designer generation and persistent orchestration adapters; M3 adds multi-step business
environments; M4 associates runtime outcomes with decision data. Integrations are optional packages
or extras with pinned compatibility tests.

An orchestration agent plans and revises recipes. Deterministic tools execute batches and checks.
The agent's working context contains artifact references and summaries, while full source documents,
records, and traces stay in the artifact store.

## Execution scope

The initial harness scores individual decisions. Its Harbor adapter exports independent tasks with
model-visible context, an answer contract, and separate verifier files. It does not simulate an entire
enterprise workflow. Multi-step environments will expose mock business tools and state transitions,
with isolated verification and separate compliance, utility, cost, and escalation measurements.
