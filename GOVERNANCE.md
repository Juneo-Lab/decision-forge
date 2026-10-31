# Governance

Decision Forge is a small, maintainer-led project. This document states how decisions are made so
contributors know what to expect, not to imitate a foundation.

## Roles

| Role | Responsibility |
| --- | --- |
| Contributor | Opens issues and pull requests, reviews others' work |
| Reviewer | Named in review history; may approve changes in their area |
| Maintainer | Merges, cuts releases, owns the roadmap and the compatibility promise |

Maintainers are listed in [`AUTHORS.md`](AUTHORS.md). Additional maintainers are added by unanimous
agreement of the current maintainers after a sustained record of review-quality contributions.

## How decisions are made

- **Correctness of labels and provenance is not negotiable.** A change that weakens rule replay, exact
  evidence binding, split isolation, or the review gate is rejected regardless of convenience.
- **Public contracts change deliberately.** Artifact schemas, export formats, and CLI behaviour follow
  the policy in [`ROADMAP.md`](ROADMAP.md) M5: a migration path or an explicit breaking-change note.
- **Everything else is lazy consensus.** A pull request with passing checks and no unresolved objection
  may be merged by a maintainer after review.
- **Disagreements resolve on evidence.** Measurements and reproductions win over preference; when there
  is no evidence either way, the maintainer decides and records the reasoning in the pull request.

## What we will not do

- Merge a feature that reports a number it cannot reproduce.
- Publish a benchmark without its protocol, host description, and versions.
- Claim downstream model improvements without a held-out experiment.
- Accept generated labels as ground truth without an independent verification mechanism.

## Release authority

Only maintainers cut releases. A release requires: a passing CI run on the tagged commit, an entry in
[`CHANGELOG.md`](CHANGELOG.md), and a note describing compatibility impact.
