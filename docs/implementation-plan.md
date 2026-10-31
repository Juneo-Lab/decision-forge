# Implementation and verification plan

## First principles

An organizational decision dataset is a claim about what action is justified in a particular state.
The implementation must retain the source of that claim, the interpretation of the rule, the conditions
under which it applies, and the evidence used to verify it. Four invariants drive development:

1. **Grounding:** every declared rule traces to a versioned source and exact quotation.
2. **Decision semantics:** explicit rules produce reproducible choices under controlled interventions.
3. **Evaluation independence:** source families and evaluation feedback remain isolated across splits.
4. **Reproducibility:** each accepted record can be rebuilt from versioned inputs and inspected artifacts.

## Dependency order

| Stage | Deliverable | Evidence required before completion |
| --- | --- | --- |
| Foundation repair | Correct JSON decisions, source snapshots, private error handling | Regression tests reproducing each defect |
| M1 organization compiler | Adapters, spans, review, corpus extraction and assembly | Real parsing, failure recovery, reviewed extraction fixtures, live model runs |
| M2 synthesis | Explicit distributions, transformations, optional backend/orchestrator adapters | Real adapter execution, requested/realized coverage, cost and recovery reports |
| M3 execution | Multi-step business environment and independent graders | Positive/negative/mutation cases, container execution, two harness/model configurations |
| M4 feedback | Event lineage, policy updates, development-feedback generation | Trace consistency, frozen test families, measured update costs and quality |
| M5 platform | Stable extensions, migrations, reproducible releases | Clean-system installs, real scale/model benchmarks, verified release provenance |

Each implementation increment receives behavioral tests and independent code review before commit.
Tests are evidence of the behavior they execute. Scripted provider responses establish transport and
recovery contracts; live model runs establish external compatibility and observed extraction outcomes.
Reported measurements include unsuccessful attempts. Model and adapter versions accompany results.

## Current state

The foundation is published. The M1 implementation increment adds versioned source adapters,
source-span claims, resumable bounded compilation, review records, and exact-span evaluation.
Live Opus 5.5 and GPT-6.1 Sol evidence and semantic review disagreements are preserved in
`experiments/2026-10-m1`. Both model drafts remain blocked by review. Remaining M1 corpus-scale
requirements remain open. Subsequent increments implement exact distribution quotas, verified
interventions, real optional SDK adapters, local multi-step workflows, independent grading, and
reviewed development feedback. M2–M5 remain open until their full acceptance evidence is available.
Milestone states reflect acceptance evidence;
adding an interface or configuration file alone does not complete an integration.
