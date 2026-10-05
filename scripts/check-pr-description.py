"""Check that a pull request description fills in the template's key sections.

Reads the pull request event that GitHub Actions provides (GITHUB_EVENT_PATH), so
the description is never interpolated into a shell command. Passes for bots such as
Dependabot, whose descriptions are generated.

A section counts as filled in when it has text beyond the template's placeholders:
HTML comments, a lone "-" and unchecked template boxes don't count. Verification
needs at least one checked box or a line describing what was run.
"""

from __future__ import annotations

import json
import os
import re
import sys

REQUIRED = ("What this does", "Why", "Verification")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
UNCHECKED = re.compile(r"^\s*[-*]\s*\[ \]")


def sections(body: str) -> dict[str, list[str]]:
    """Map each level-2 heading to the meaningful lines under it."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in COMMENT.sub("", body).splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            found.setdefault(current, [])
        elif current is not None and line.strip() and line.strip() != "-":
            found[current].append(line)
    return found


def problems(body: str) -> list[str]:
    """Each way the description falls short; empty when it is complete."""
    found = sections(body or "")
    issues = []
    for name in REQUIRED:
        lines = found.get(name)
        if lines is None:
            issues.append(f"missing the '## {name}' section")
            continue
        meaningful = [line for line in lines if not UNCHECKED.match(line)]
        if not meaningful:
            issues.append(
                "'## Verification' needs a checked box or a line on what was run"
                if name == "Verification"
                else f"'## {name}' is empty"
            )
    return issues


def main() -> int:
    """Check the pull request in the current GitHub Actions event."""
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
        event = json.load(stream)
    pull = event.get("pull_request") or {}
    author = (pull.get("user") or {}).get("type")
    if author == "Bot":
        print("Generated description from a bot; not checked.")
        return 0
    issues = problems(pull.get("body") or "")
    for issue in issues:
        print(f"::error title=PR description::The description is {issue}.")
    if issues:
        print("Fill in the pull request template (.github/pull_request_template.md).")
        return 1
    print("The description fills in What this does, Why and Verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
