"""Check a pull request's title and description against the pull request template.

Reads the pull request event that GitHub Actions provides (GITHUB_EVENT_PATH), so
the title and description are never interpolated into a shell command. Passes for
bots such as Dependabot, whose titles and descriptions are generated.

The template (.github/pull_request_template.md) asks for a ``type(scope): outcome``
title of at most 75 characters, and a short description in plain paragraphs: the
problem and outcome, then the main changes, optionally verification and risks.
"""

from __future__ import annotations

import json
import os
import re
import sys

TYPES = ("feat", "fix", "docs", "test", "refactor", "ci", "build", "chore", "security")
TITLE = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9][a-z0-9-]*\))?!?: \S.*$")
MAX_TITLE = 75
MAX_PROSE = 1500
"""A little above the template's "about 1,200 characters", so it guides, not nags."""
GUIDANCE = "Remove this guidance before submitting"
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Lines that aren't prose: images, links on their own, code fences and their contents.
NOT_PROSE = re.compile(r"^\s*(!\[|<img|https?://\S+$)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
# Blocks that aren't prose paragraphs: lists, quotes, tables and headings.
BLOCK = re.compile(r"^\s{0,3}([-*+]\s|\d+[.)]\s|>|\||#{1,6}\s)")


def prose(body: str) -> str:
    """The description without comments, code blocks, images and bare links."""
    text = COMMENT.sub("", body)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return "\n".join(line for line in text.splitlines() if not NOT_PROSE.match(line)).strip()


def problems(title: str, body: str) -> list[str]:
    """Each way the title or description falls short; empty when both are fine."""
    issues = []
    title = title.strip()
    if not TITLE.match(title):
        issues.append(f"title should look like 'type(scope): outcome' with a type from {TYPES}")
    if len(title) > MAX_TITLE:
        issues.append(f"title is {len(title)} characters; keep it to {MAX_TITLE}")
    if GUIDANCE in (body or ""):
        issues.append("description still contains the template's guidance comment")
    text = prose(body or "")
    if any(HEADING.match(line) for line in text.splitlines()):
        issues.append("description has headings; write plain paragraphs instead")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    paragraphs = [block for block in blocks if not BLOCK.match(block)]
    if len(paragraphs) < 2:
        issues.append(
            "description needs at least two prose paragraphs (lists, quotes and tables don't"
            " count): the problem and outcome, then the changes"
        )
    if len(text) > MAX_PROSE:
        issues.append(f"description has {len(text)} characters of prose; aim for about 1,200")
    return issues


def main() -> int:
    """Check the pull request in the current GitHub Actions event."""
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
        event = json.load(stream)
    pull = event.get("pull_request") or {}
    if (pull.get("user") or {}).get("type") == "Bot":
        print("Generated title and description from a bot; not checked.")
        return 0
    issues = problems(pull.get("title") or "", pull.get("body") or "")
    for issue in issues:
        print(f"::error title=PR description::The {issue}.")
    if issues:
        print("See .github/pull_request_template.md.")
        return 1
    print("The title and description follow the template.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
