"""The pull request description check used by the PR description status."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "check-pr-description.py"
TEMPLATE = (Path(__file__).parents[1] / ".github" / "pull_request_template.md").read_text()
spec = importlib.util.spec_from_file_location("check_pr_description", SCRIPT)
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

FILLED = """## What this does

Adds a check.

## Why

Reviewers need context.

## Verification

- [x] `pytest` passes
"""


def test_the_untouched_template_fails():
    issues = check.problems(TEMPLATE)
    assert "'## What this does' is empty" in issues
    assert "'## Why' is empty" in issues
    assert any("Verification" in issue for issue in issues)


def test_a_filled_description_passes():
    assert check.problems(FILLED) == []


def test_a_line_on_what_was_run_counts_as_verification():
    body = FILLED.replace("- [x] `pytest` passes", "Ran the test suite: 389 passed.")
    assert check.problems(body) == []


def test_missing_sections_and_empty_bodies_fail():
    assert "missing the '## Why' section" in check.problems("## What this does\n\nX\n")
    assert len(check.problems("")) == 3


def run(tmp_path, body, user_type="User"):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"body": body, "user": {"type": user_type}}}))
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={"GITHUB_EVENT_PATH": str(event)},
        capture_output=True,
        text=True,
    )


def test_the_script_reads_the_event_and_reports(tmp_path):
    assert run(tmp_path, FILLED).returncode == 0
    failed = run(tmp_path, TEMPLATE)
    assert failed.returncode == 1 and "::error title=PR description::" in failed.stdout


def test_bot_descriptions_are_not_checked(tmp_path):
    assert run(tmp_path, "Bumps x from 1 to 2.", user_type="Bot").returncode == 0
