# Data and evaluation protocol

## Evidence and supervision

The v0.1 verifier checks declared policies. Source quotation matching establishes traceability.
It does not establish that a policy was interpreted correctly. Review the rule representation against
independent business fixtures before using an organization dataset for training or production claims.

Future adapters will attach distinct labels for executable environment results, reviewed preferences,
observational outcomes, and unresolved cases. A whole-workflow outcome must not automatically become
a positive or negative label for every intermediate decision.

## Data isolation

Assign original events to families before augmentation. Keep all variants and paraphrases of a family
within one split. Public test sets, including COMPASS and OrgAccess, belong in evaluation inventories;
exclude their answers and derivative examples from training. Use development families to decide what
to generate next. Freeze final test families and the test protocol.

Stable organization rules may appear in runtime context across splits. Event answers, future outcomes,
and verifier-only information must remain outside model-visible inputs. Build exports implement this
input/target separation, while adapters remain responsible for source-time semantics.

## Export semantics

The canonical format preserves a set of valid best choices. Typed-choice and SFT formats require a
single target and report skipped tied-answer examples. Empty valid-choice sets are retained in
`rejected.jsonl`, excluded from training, and counted in reports. Empty splits produce empty JSONL
files; inspect the coverage report before training.

## Evaluation

Report decision quality by surface and stratum, candidate coverage, rule responsiveness, paraphrase
stability, and calibration on a representative held-out distribution. Report task success, constraint
violations, cost, latency, and escalation separately for executable environments. A failed grader or
provider call is an infrastructure error, not a model error.

For data-pipeline comparisons, hold the backbone, source materials, split definitions, and training
budget constant. Compare direct document synthesis, workflow-structured synthesis, controlled
scenario families, independent verification, and execution feedback. Report accepted data volume and
cost as well as downstream model quality.

## Reproducibility

Release source permissions and hashes, policy versions, recipe configuration, engine/dependency
versions, verifier versions, dataset hashes, and evaluation protocols. Preserve actual model outputs
when external model behavior affects reproduction. The checked-in example is fictional and safe to
redistribute under the repository license.
