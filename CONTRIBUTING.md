# Contributing

Inspect Labs is an early framework built on Inspect AI. We welcome environments,
evaluation methods, adapter improvements and fixes that help researchers test
scientific agents and safeguards. See [the vision](docs/vision.md) and
[environment authoring](docs/authoring.md).

Start by describing the research question and the observations needed to answer
it. An adapter connects a system; a useful environment also needs meaningful
tasks, legitimate controls and a judge whose limits are explicit. Keep sensitive
scenarios and provider data in separately controlled task packages.

## Development

Use Python 3.12+ and follow the installation and verification commands in
[README](README.md#development). Work in a focused branch with one reviewable
change. Keep native Inspect execution, artifacts and ownership intact.

For a change, explain the concrete problem, resulting behavior and verification.
Add regression coverage when an important boundary or subtle bug needs it.
Run relevant tests and the installed surface; a successful import or wheel build
alone does not establish that the feature works. Public modules must appear in
the [source registry](docs/design.md), enforced by architecture tests.

Unknown observations must remain unknown. An accepted request does not establish
completion, a cancellation acknowledgement does not establish stopping, and an
agent report does not establish a laboratory outcome. Rescoring must dispatch no
new actions. Distinguish software mechanics from scientific and physical validity.

## Checks and reviews

Every pull request must pass these checks before it can merge into `main`:

| Check | What it checks |
|---|---|
| `validate` | Ruff lint and format, and strict mypy |
| `test` | The test suite, including partner Labs |
| `Installed packages` | Wheels build, install outside the checkout and run |
| `Documentation site` | The site renders with no broken links |
| `PR description` | A `type(scope): outcome` title and a short plain-paragraph description, as the template asks |
| `Codex review` | A Codex review of the latest commit found no P0–P2 problems |

The branch must also be up to date with `main`, and every review conversation must be
resolved. `Codex review` is set by `scripts/codex-review-pr.sh PR --post`, which runs
on your ChatGPT subscription: run it after each push, fix what it finds, and run it
again until it passes. Auto-merge then merges once everything is green. Site changes
also get a Vercel preview link.

Security checks run on every pull request too. CodeQL scans the Python code and the
GitHub Actions workflows (also weekly), dependency review blocks new dependencies
with known high-severity vulnerabilities, and GitHub push protection blocks pushes
containing secrets that match its supported patterns (a contributor can bypass a
block where the repository allows it, with a reason). Findings appear under the
repository's Security tab, including for pull requests from forks and Dependabot.

Reviews are layered so each catches different problems. All of them check
[the review guide](.github/REVIEW_GUIDE.md).

| Reviewer | When | Why |
|---|---|---|
| CodeRabbit | Every pull request, automatically; `@coderabbitai review` to ask again | A summary plus line-by-line review, with per-path rules in `.coderabbit.yaml` |
| Greptile | Every pull request, automatically | Reads the whole codebase, so it catches changes that break code elsewhere |
| Claude | Code pull requests when opened; `@claude` to ask again | Checks the review guide's rules line by line |
| Codex | `scripts/codex-review-pr.sh PR --post` after every push; its `Codex review` status gates merging | An independent second opinion from a different model |
| A maintainer | Every pull request | Decides; automated reviews advise |

Higher-risk changes are those touching the gateway, action rules, lab logs and
their hashes, rescoring, serving, or anything a partner Lab depends on. After you
push fixes, resolve each review conversation or reply with why it doesn't apply.

## Docs and website

The [documentation site](site/README.md) is a Quarto website; preview it with
`quarto preview site`. Use plain,
descriptive language and examples readers can reproduce. Preserve the mission
and community role while keeping implemented behavior distinct from planned work.
Check links, keyboard navigation and mobile layout when changing the site.

## Before sharing artifacts

Native logs can include private prompts, answers, tool output and credentials.
Review artifacts before sharing them. Never attach raw study logs or provider
data to a public issue or pull request. Use harmless synthetic examples for
public reproductions. See [security reporting](SECURITY.md).

The MIT license covers the original software. Preserve third-party notices and
do not copy code from read-only reference repositories.
