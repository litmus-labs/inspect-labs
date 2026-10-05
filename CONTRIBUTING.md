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

Every pull request runs CI: lint and strict types, tests, the installed-package
check and the documentation site. All four must pass before merge, the branch must
be up to date with `main`, and every review conversation must be resolved. Site
changes also get a Vercel preview link.

Security checks run on every pull request too. CodeQL scans the Python code and the
GitHub Actions workflows (also weekly), dependency review blocks new dependencies
with known high-severity vulnerabilities, and secret scanning blocks pushes that
contain credentials. Findings appear under the repository's Security tab.

Reviews are layered so each catches different problems. All of them check
[the review guide](.github/REVIEW_GUIDE.md).

| Reviewer | When | Why |
|---|---|---|
| Greptile | Every pull request, automatically | Reads the whole codebase, so it catches changes that break code elsewhere |
| Claude | Code pull requests when opened; `@claude` to ask again | Checks the review guide's rules line by line |
| Codex | Comment `@codex review` on higher-risk changes | An independent second opinion from a different model |
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
