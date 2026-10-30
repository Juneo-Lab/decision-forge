# Security policy

Decision Forge processes potentially sensitive organizational material. Use local or approved model
endpoints for private documents. The `compile` command explicitly transmits the selected files to
the endpoint configured by the user. Offline validation and builds perform no model requests.

## Reporting

Use GitHub's **Report a vulnerability** flow when private vulnerability reporting is enabled for the
repository. If that option is unavailable, contact a repository maintainer through an existing private
channel. Do not place credentials, private company data, or exploit details in public issues.

## Current boundaries

- The policy language uses fixed operators and never executes expressions as Python.
- Source paths are constrained to the specification directory, including resolved symlinks.
- Model endpoints use HTTPS, with HTTP allowed only for local development hosts.
- The compiler verifies exact evidence quotations; semantic policy review remains a separate step.
- Build artifacts may contain original business context. Store and share them with appropriate access.
- Task exports require an isolated execution environment before running generated or third-party agents.

Version 0.1 is an alpha release. Security fixes are applied to the current main branch and included in
the next patch release. The v1.0 milestone establishes the long-term support policy.
