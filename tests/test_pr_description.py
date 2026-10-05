"""The pull request title and description check used by the PR description status."""

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

TITLE = "fix(gateway): refuse actions when a domain check fails"
BODY = """A domain check that raised used to let the action through; now it refuses it.

The gateway treats an exception in a check as a refusal and records why. Rules and
approvals are unchanged.

Ran the gateway tests: all pass.
"""


def test_a_good_title_and_description_pass():
    assert check.problems(TITLE, BODY) == []


def test_the_untouched_template_fails():
    issues = check.problems(TITLE, TEMPLATE)
    assert any("guidance comment" in issue for issue in issues)
    assert any("two prose paragraphs" in issue for issue in issues)


def test_titles_need_a_type_and_a_short_outcome():
    assert any("type(scope)" in i for i in check.problems("Improve safety", BODY))
    assert check.problems("docs: explain tiered release", BODY) == []
    long_title = "feat(connectors): " + "x" * 70
    assert any("characters; keep it to 75" in i for i in check.problems(long_title, BODY))


def test_long_descriptions_are_flagged_but_screenshots_and_links_dont_count():
    long_body = BODY + "\n\n" + ("A sentence that keeps going. " * 60)
    assert any("aim for about 1,200" in i for i in check.problems(TITLE, long_body))
    with_extras = BODY + "\n![before](https://example.com/a.png)\n\nhttps://example.com/issue/1\n"
    assert check.problems(TITLE, with_extras) == []


def run(tmp_path, title, body, user_type="User"):
    event = tmp_path / "event.json"
    payload = {"pull_request": {"title": title, "body": body, "user": {"type": user_type}}}
    event.write_text(json.dumps(payload))
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={"GITHUB_EVENT_PATH": str(event)},
        capture_output=True,
        text=True,
    )


def test_the_script_reads_the_event_and_reports(tmp_path):
    assert run(tmp_path, TITLE, BODY).returncode == 0
    failed = run(tmp_path, "Update stuff", TEMPLATE)
    assert failed.returncode == 1 and "::error title=PR description::" in failed.stdout


def test_bot_pull_requests_are_not_checked(tmp_path):
    assert run(tmp_path, "Bump x from 1 to 2", "Bumps x.", user_type="Bot").returncode == 0


def test_headings_and_lists_dont_count_as_paragraphs():
    issues = check.problems(TITLE, "## Summary\n\n- Changed the gate\n")
    assert any("headings" in issue for issue in issues)
    assert any("two prose paragraphs" in issue for issue in issues)
    only_lists = "- Changed the gate\n\n1. Ran tests\n\n> A quote\n\n| a | b |\n"
    assert any("two prose paragraphs" in issue for issue in check.problems(TITLE, only_lists))


def test_a_list_alongside_two_paragraphs_is_fine():
    assert check.problems(TITLE, BODY + "\n- One remaining risk to watch.\n") == []


def test_headings_inside_code_blocks_dont_count():
    body = BODY + "\n```markdown\n## Example heading\n```\n"
    assert check.problems(TITLE, body) == []
