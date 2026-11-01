# Project specification

Specifications use JSON or YAML. `decision-forge schema` exports the complete machine-readable contract.
Unknown fields are rejected, and every node, rule, source, and scenario reference is checked.

## Sources and rules

Each source has an ID and a path relative to the specification. Symlinks are resolved before the path
boundary is checked. Source files and specifications are limited to 2 MB each. Every rule cites at
least one source and an exact quotation.

```yaml
id: authorize-refund
description: Approved refunds may be executed.
when:
  all_of:
    - path: candidate.id
      op: eq
      value: execute
    - path: state.approved
      op: eq
      value: true
effect: allow
priority: 0
score: 2
evidence:
  - source_id: handbook
    quote: An approved refund may be executed regardless of amount.
```

Conditions combine `all_of` and `any_of`: every `all_of` predicate and at least one `any_of` predicate
must match. An empty `any_of` adds no condition. Both lists empty means the rule matches all candidates.

Supported operators are `eq`, `ne`, `in`, `contains`, `gt`, `gte`, `lt`, `lte`, and `exists`.
Equality and membership use type-sensitive comparisons. Numeric ordering excludes booleans.
Missing paths fail comparisons; `exists` handles missing fields explicitly. `contains` checks list
membership. Arbitrary code and expression evaluation are excluded from the language.

For each candidate, only matching rules at the highest priority govern its verdict. A denial wins
over an allowance at the same priority. Candidates with no matching rule are denied. The highest
score among governing allowances ranks permitted candidates. Equal best scores preserve all tied IDs.
Descriptions must explain operative conditions and preferences because they appear in training inputs.

## Scenarios, dimensions, and families

A scenario contains a node reference, decision-time state, evidence sources, and a family ID.
Dimensions modify `state.<path>` or `candidates.<id>.<attribute>` across explicitly listed values;
dimension values must be unique, and candidate IDs may not contain dots so that candidate paths
resolve to exactly one candidate. The generator samples unique Cartesian-product positions up to
`variants_per_scenario`, then deduplicates identical resulting episode identities within the scenario.
Build reports state requested and realized episode volumes, plus any scenario that produced fewer
episodes than requested, so a shrunken sampling space is visible instead of silent.

Use one family ID for every seed scenario derived from the same original business event. All four
decision surfaces of one event can share that ID. Families are never divided across splits.

### Split lock

A family's split must not change once data from it has been published, or held-out examples leak
into training in the next version. The first build of a specification ranks families by a hash of
organization ID and family ID, independent of the sampling seed, and slices them into quotas computed
from the split fractions; a configured split with a nonzero fraction receives at least one family
once the project has enough families. The build then records every assignment in a split lock next
to the specification (`project.yaml` → `project.splits.lock.json`). Commit it with the specification.

Later builds keep every locked family in its split. A family added later receives a split from a
hash of its own ID, which no other family influences, and is appended to the lock. Removed families
stay in the lock, so a family that returns keeps its original split. An explicit
`recipe.family_splits` pin that contradicts the lock is an error; to move a family deliberately,
edit the lock. Builds write the lock only after they succeed, and `build --locked-splits` fails
instead of creating or changing it, which suits CI. `update` additionally carries the previous run's
cumulative registry forward. Cache keys and run snapshots include each scenario's resolved split.

Without a lock, no assignment can be both stable under added families and bounded in split sizes:
stability requires each family's split to depend on that family alone, and independent hashing can
leave a small project without a test split.

The `stratum` field labels routine, boundary, change, or feedback scenarios. Exact accepted-example
quotas use `recipe.distribution`; `candidate_pool_per_scenario` controls the finite search pool.
See [platform workflows](platform-workflows.md) for feasibility reports and checked transformations.

## Model-visible inputs

Training views include organization ID, policy version, applicable policy descriptions, decision-time
state, question, and candidates. Valid answers, verdicts, interventions, source hashes, and verification
metadata stay outside the model-visible input. Keep future outcomes out of scenario state.
