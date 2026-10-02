"""Exercise the CLI surface offline and its live-call admission boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_run_and_rescore(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "inspect_labs.cli", "run", "--scripted", "--log-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    paths = json.loads(result.stdout)
    assert paths["submissions"] == 1
    assert paths["live"] is False
    for name in ("native_log", "private_evidence"):
        assert Path(paths[name]).stat().st_mode & 0o777 == 0o600
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inspect_labs.cli",
            "rescore",
            paths["native_log"],
            "--evidence",
            paths["private_evidence"],
            "--output",
            str(tmp_path / "rescored.eval"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["new_submissions"] == 0


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--allow-live"],
        ["--cost-limit", "1"],
        ["--allow-live", "--cost-limit", "nan"],
        ["--allow-live", "--cost-limit", "-1"],
    ],
)
def test_cli_live_flags_rejected_before_eval(tmp_path: Path, flags: list[str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inspect_labs.cli",
            "run",
            "--model",
            "openrouter/unapproved",
            "--log-dir",
            str(tmp_path),
            *flags,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not list(tmp_path.iterdir())


def test_cli_rescore_rejects_non_object_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "bad.labs"
    evidence.write_text("[1]")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inspect_labs.cli",
            "rescore",
            str(tmp_path / "missing.eval"),
            "--evidence",
            str(evidence),
            "--output",
            str(tmp_path / "out.eval"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("price", [[], ["0", "1"], ["nan", "1"]])
def test_cli_live_requires_enforceable_cost_data(tmp_path: Path, price: list[str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inspect_labs.cli",
            "run",
            "--model",
            "openrouter/unpriced/model",
            "--allow-live",
            "--cost-limit",
            "0.05",
            "--log-dir",
            str(tmp_path),
            *(["--price", *price] if price else []),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--price" in result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("flags", [["--cost-limit", "1"], ["--price", "1", "2"]])
def test_cli_cost_flags_rejected_for_non_live_runs(tmp_path: Path, flags: list[str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inspect_labs.cli",
            "run",
            "--model",
            "mockllm/model",
            "--log-dir",
            str(tmp_path),
            *flags,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "only to live model runs" in result.stderr
    assert not list(tmp_path.iterdir())
