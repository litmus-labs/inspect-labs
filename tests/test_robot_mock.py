"""Validate the optional robot recipe through native persistence."""

from pathlib import Path

import pytest

pytest.importorskip("inspect_robots")

from inspect_robots import read_eval_log  # noqa: E402

from inspect_labs.robot_mock import run_mock  # noqa: E402


def test_native_robot_mock_roundtrip(tmp_path: Path) -> None:
    log = run_mock(tmp_path)
    assert log.status == "success"
    assert log.results.total_trials == 1
    assert log.results.metrics["success_at_end"] == 1
    records = list(tmp_path.glob("*.json"))
    assert len(records) == 1
    reopened = read_eval_log(str(records[0]))
    assert reopened == log
    assert list((tmp_path / "actions").rglob("*.jsonl"))
