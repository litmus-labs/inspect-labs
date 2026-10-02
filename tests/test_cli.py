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
    assert json.loads(result.stdout)["replay_only"] is True


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


def _custom_run(tmp_path: Path) -> tuple[Path, Path]:
    pytest.importorskip("pylabrobot")
    import importlib.util

    from inspect_ai import eval

    example = Path(__file__).resolve().parents[1] / "examples" / "reagent_addition.py"
    spec = importlib.util.spec_from_file_location("reagent_addition_cli", example)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    task = module.reagent_addition(columns=3, scripted=True, evidence_dir=str(tmp_path / "e"))
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")[0]
    native = Path(log.location)
    return native, native.with_suffix(".labs")


def _rescore(native: Path, evidence: Path, output: Path, *extra: str):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "inspect_labs.cli",
            "rescore",
            str(native),
            "--evidence",
            str(evidence),
            "--output",
            str(output),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def test_cli_rescores_a_custom_task_with_an_explicit_scorer(tmp_path: Path) -> None:
    from inspect_ai.log import read_eval_log

    native, evidence = _custom_run(tmp_path)
    example = Path(__file__).resolve().parents[1] / "examples" / "reagent_addition.py"
    missing = _rescore(native, evidence, tmp_path / "a.eval")
    assert missing.returncode == 2 and "--scorer FILE.py:function" in missing.stderr
    legacy = _rescore(
        native, evidence, tmp_path / "legacy.eval", "--judge", f"{example}:reagent_outcome"
    )
    assert legacy.returncode == 0, legacy.stderr
    result = _rescore(
        native, evidence, tmp_path / "b.eval", "--scorer", f"{example}:reagent_outcome"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "native_log": str(tmp_path / "b.eval"),
        "status": "success",
        "replay_only": True,
    }
    assert (
        read_eval_log(str(tmp_path / "b.eval")).samples[0].scores
        == read_eval_log(str(native)).samples[0].scores
    )


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("no-colon", "FILE.py:function or module:function"),
        ("missing_file.py:judge", "Judge file not found"),
        ("not_a_real_module_xyz:judge", "Cannot import judge module"),
        ("json:not_there", "is not a callable judge"),
    ],
)
def test_cli_judge_errors_are_clear(tmp_path: Path, spec: str, message: str) -> None:
    native, evidence = _custom_run(tmp_path)
    result = _rescore(native, evidence, tmp_path / "x.eval", "--scorer", spec)
    assert result.returncode == 2 and message in result.stderr
    assert "Traceback" not in result.stderr
