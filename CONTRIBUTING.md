# Contributing

Welcome to Decision Forge. Contributions should improve the path from organizational evidence to
verifiable decision data. Use English for code, comments, documentation, issues, and pull requests.

## Where to start

| You want to | Start here |
| --- | --- |
| Make a first contribution | Issues labelled [`good first issue`](https://github.com/Juneo-Lab/decision-forge/labels/good%20first%20issue): scoped, testable offline, no design decision |
| Own a larger piece | Issues labelled [`help wanted`](https://github.com/Juneo-Lab/decision-forge/labels/help%20wanted), usually an open acceptance item from the [roadmap](ROADMAP.md) |
| Propose something new | [Discussions → Ideas](https://github.com/Juneo-Lab/decision-forge/discussions/categories/ideas) first, then an issue once it has an acceptance criterion |
| Ask a question | [Discussions → Q&A](https://github.com/Juneo-Lab/decision-forge/discussions/categories/q-a) |

Comment on an issue before starting so two people do not build the same thing. If you stop, say so and
the issue goes back to the pool.

## Local development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run mkdocs build --strict
uv build
```

Start from an issue with a clear use case and acceptance criteria. For new architecture or public
interfaces, write a short design proposal explaining inputs, outputs, failure behavior, provenance,
and compatibility. Keep generated examples fictional or explicitly licensed for redistribution.

## Pull requests

Explain the user-visible behavior, implementation choices, and validation performed. Include tests
for policy semantics, lineage, split isolation, and failure recovery when affected. Preserve existing
public contracts or include a migration plan. Public APIs use typed inputs and explicit outputs.

## Integration boundaries

The offline core must remain usable without model credentials, hosted services, or heavyweight
generation runtimes. Optional adapters declare their dependency versions and have isolated contract
tests. Never send source documents to a model endpoint as an implicit side effect of validation.

## Data and claims

Keep raw company materials in ignored directories such as `private/` and generated data in `runs/`.
Record source permissions, dataset licenses, and provenance in adapters that ingest external material.
State whether a result comes from declared rules, simulation, human review, or real execution.
Use development data for iteration and freeze test families before augmentation.

Contributions are licensed under Apache-2.0. By submitting a contribution, you confirm that you have
the right to contribute it under this license. Maintainers review changes before release.

## Labels

Issues and pull requests carry one `type/` label and at least one `area/` label, so the backlog stays
readable without a project board.

| Label | Meaning |
| --- | --- |
| `type/bug` | Wrong behaviour, including a wrong label or a false rejection |
| `type/feature` | New capability with an acceptance criterion |
| `type/docs` | Documentation, examples, or diagrams |
| `type/benchmark` | Measurement protocol, host reporting, or a reproducible result |
| `area/compiler` | Ingestion, extraction, evidence binding, review gates |
| `area/policy` | Rule semantics, priority, conflict analysis |
| `area/generation` | Scenarios, quotas, synthesis, deduplication |
| `area/training` | Export contracts, candidate-readout encoding, evaluation loop |
| `area/harness` | Workflow tasks, Harbor export, grading |
| `area/feedback` | Runtime events, reviewed feedback, lineage, targeted updates |
| `area/infra` | CI, packaging, releases, and repository automation |
| `area/docs` | Site, README, and reference material |
| `good first issue` | Scoped, has a clear acceptance criterion, no design decision required |
| `help wanted` | Maintainers will review but will not drive it |
| `blocked` | Waiting on an external dependency or a maintainer decision |

The taxonomy is declared in [.github/roadmap.json](.github/roadmap.json) and synchronized to GitHub by
the **Sync roadmap** workflow; edit labels there rather than in the GitHub UI. Pull requests receive
`area/` labels automatically from the files they change ([.github/labeler.yml](.github/labeler.yml));
maintainers add the `type/` label and adjust areas during triage. A pull request that changes behaviour
should reference the issue whose acceptance criterion it satisfies, so closing the issue closes the claim.

## Definition of done

A behaviour change is complete when it has: a behavioural test that fails without the change, a note in
`CHANGELOG.md` if user-visible, updated documentation for any changed command or contract, and a
passing `ruff`, `mypy`, `pytest --cov-fail-under=80`, and `mkdocs build --strict`.

Documentation-only changes require relevant link, asset, and strict site-build checks.

## Releases

Maintainers cut releases by pushing a version tag; the [release process](docs/releasing.md) describes
the checklist, the automated verification, and how to verify published artifacts.

## Documentation deployment

The Docs workflow builds the site for every documentation pull request and publishes it from `main` to
[juneo-lab.github.io/decision-forge](https://juneo-lab.github.io/decision-forge/). Publishing is gated by
the Actions repository variable `DEPLOY_DOCS`; a manual Docs workflow run on `main` republishes the site.
