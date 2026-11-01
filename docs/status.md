# Project status

Decision Forge is an operational alpha for organizational decision data. The core path is:

**Organization sources → reviewed policy and decision nodes → distribution plan → verified scenarios
→ candidate-probability training rows → held-out decision evaluation.**

All four decision surfaces share this path: allocation, information sharing, action selection,
and outcome acceptance. Workflow execution and feedback supply decision-time states and reviewed
outcomes for the data pipeline.

| Area | Implemented | Remaining acceptance |
| --- | --- | --- |
| M0: foundation | Offline builds, typed contracts, policy interpreter, provenance, split isolation, cache integrity, CLI and packaging | Completed foundation; regression validation continues |
| M1: organization compiler | Text/PDF/workflow, office/tabular/HTML and image ingestion, local OCR, source spans, bounded resumable extraction, review queue, recorded model experiments | Cross-document retrieval and interaction studies; broader independently reviewed organizational corpora |
| M2: decision synthesis | Distribution quotas, recipe-planning tools, model-proposed states verified by policy replay, rewrites, policy-aware deduplication, resumable orchestration | Data Designer conditional-generation integration; multi-provider recovery campaign; training-quality evidence |
| Decision training interface | Candidate options and probabilities, source/family metadata, native non-thinking candidate-label encoding, real exported budgets | Execute fixed-budget training comparisons and report candidate NLL and decision accuracy |
| M3: execution support | Resettable fictional workflows, independent trace grading, local model runs, Harbor Docker oracle/no-op controls | Multi-step container and multi-harness acceptance |
| M4: feedback updates | Runtime events, reviewed development feedback, lineage, cached regeneration, held-out family registry | Span-level reuse, model-weight lineage, measured curriculum gains |
| M5: release maturity | CI matrix, optional-adapter checks, documentation/package builds, offline scale measurements | Stable extension/migration policy, public package provenance, operational release procedures |

## Next decision-focused milestones

1. Freeze a reviewed organizational decision corpus with independent family-level evaluation splits.
2. Train the baseline and generated-data arms with the same candidate-readout objective and actual
   example budgets. Report candidate NLL, accuracy by decision surface/stratum, and generation cost.
3. Use reviewed development errors to adjust the generation distribution, then measure the next
   dataset version against the unchanged held-out set.
4. Complete release acceptance around the resulting decision-data pipeline.

Encoder compatibility, rule replay, and software tests establish interface and execution correctness.
The downstream training study establishes whether generated data improves decision quality. Those
results are tracked separately; no training-quality gain is recorded until the study executes.
