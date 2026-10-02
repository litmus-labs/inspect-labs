"""Build and exercise all four non-editable packages outside the checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    ROOT,
    ROOT / "plugins/inspect-labs-opentrons",
    ROOT / "plugins/inspect-labs-commec",
    ROOT / "plugins/inspect-labs-plate-reader",
)


def run(command: list[str], *, cwd: Path, log: Path) -> str:
    """Run one verification command and retain its output privately."""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True)
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); inspect {log}")
    return result.stdout


def one_eval(directory: Path) -> Path:
    """Find the single native evaluation log from a control run."""
    logs = list(directory.glob("*.eval"))
    if len(logs) != 1:
        raise AssertionError(f"Expected one native log in {directory}, found {len(logs)}")
    return logs[0]


def verify_installed(work: Path, python: Path) -> None:
    """Drive installed entry points, native tasks, and pure replay."""
    import inspect_labs_commec
    import inspect_labs_opentrons
    import inspect_labs_plate_reader
    from inspect_ai.log import read_eval_log
    from inspect_robots import read_eval_log as read_robot_log

    import inspect_labs

    for package in (
        inspect_labs,
        inspect_labs_commec,
        inspect_labs_opentrons,
        inspect_labs_plate_reader,
    ):
        location = Path(package.__file__).resolve()
        assert "site-packages" in location.parts, location
        assert not location.is_relative_to(ROOT), location

    bin_dir = python.parent
    listing = json.loads(
        run([str(bin_dir / "inspect-labs"), "list"], cwd=work, log=work / "list.log")
    )
    assert "commec-review" in listing["environment"]
    assert "plate-reader-qc" in listing["environment"]
    assert {"opentrons-ot2", "opentrons-ot2-simulator"} <= set(listing["backend"])
    run(
        [str(bin_dir / "inspect-labs"), "doctor", "--environment", "plate-reader-qc"],
        cwd=work,
        log=work / "reader-doctor.log",
    )

    measurement = json.loads(
        run(
            [
                str(bin_dir / "inspect-labs"),
                "run",
                "--scripted",
                "--log-dir",
                str(work / "measurement"),
            ],
            cwd=work,
            log=work / "measurement.log",
        )
    )
    assert measurement["status"] == "success"
    assert measurement["submissions"] == 1
    native = Path(measurement["native_log"])
    replay = work / "measurement-replay.eval"
    result = json.loads(
        run(
            [
                str(bin_dir / "inspect-labs"),
                "rescore",
                str(native),
                "--evidence",
                measurement["private_evidence"],
                "--output",
                str(replay),
            ],
            cwd=work,
            log=work / "measurement-replay.log",
        )
    )
    assert result["new_submissions"] == 0
    assert (
        read_eval_log(str(replay)).samples[0].scores == read_eval_log(str(native)).samples[0].scores
    )

    run(
        [
            str(bin_dir / "inspect"),
            "eval",
            "inspect_labs/robot_step",
            "--model",
            "mockllm/model",
            "-T",
            "scripted=true",
            "-T",
            f"evidence_dir={work / 'robot-evidence'}",
            "--log-dir",
            str(work / "robot"),
            "--display",
            "none",
        ],
        cwd=work,
        log=work / "robot.log",
    )
    robot_native = one_eval(work / "robot")
    robot_parent = read_eval_log(str(robot_native))
    assert robot_parent.status == "success", robot_parent.error
    robot_score = next(iter(robot_parent.samples[0].scores.values())).value
    assert robot_score["correct"] == robot_score["rollouts"] == 1
    from inspect_labs.bindings import WorkflowEvidence

    robot_evidence = robot_native.with_suffix(".labs")
    bundle = WorkflowEvidence.model_validate_json(robot_evidence.read_text())
    (sample,) = bundle.samples.values()
    (rollout,) = sample.payload["rollouts"]
    child_logs = [Path(path) for path in rollout["logs"]]
    assert child_logs and {str(path) for path in child_logs} <= {
        artifact.path for artifact in sample.artifacts
    }
    assert all(
        path.is_file() and read_robot_log(str(path)).status == "success" for path in child_logs
    )
    child_before = [(path, path.stat().st_mtime_ns) for path in child_logs]
    robot_replay = work / "robot-replay.eval"
    robot_result = json.loads(
        run(
            [
                str(bin_dir / "inspect-labs"),
                "rescore",
                str(robot_native),
                "--evidence",
                str(robot_evidence),
                "--output",
                str(robot_replay),
            ],
            cwd=work,
            log=work / "robot-replay.log",
        )
    )
    assert robot_result["new_submissions"] == 0
    assert read_eval_log(str(robot_replay)).samples[0].scores == robot_parent.samples[0].scores
    assert [(path, path.stat().st_mtime_ns) for path in child_logs] == child_before

    run(
        [
            str(bin_dir / "inspect"),
            "eval",
            "inspect_labs/worklist_transfer",
            "--model",
            "mockllm/model",
            "-T",
            "scripted=true",
            "-T",
            "backend=opentrons-ot2-simulator",
            "-T",
            f"evidence_dir={work / 'opentrons-evidence'}",
            "--log-dir",
            str(work / "opentrons"),
            "--display",
            "none",
        ],
        cwd=work,
        log=work / "opentrons.log",
    )
    opentrons = read_eval_log(str(one_eval(work / "opentrons")))
    assert opentrons.status == "success", opentrons.error
    opentrons_score = next(iter(opentrons.samples[0].scores.values())).value
    assert opentrons_score["correct"] == opentrons_score["deck_ok"] == 1

    run(
        [
            str(bin_dir / "inspect"),
            "eval",
            "inspect_labs_commec/screening_review",
            "--model",
            "mockllm/model",
            "-T",
            "fixture=true",
            "-T",
            "scripted=true",
            "-T",
            f"evidence_dir={work / 'commec-evidence'}",
            "--log-dir",
            str(work / "commec"),
            "--display",
            "none",
        ],
        cwd=work,
        log=work / "commec.log",
    )
    commec_native = one_eval(work / "commec")
    commec_log = read_eval_log(str(commec_native))
    assert commec_log.status == "success", commec_log.error
    commec_score = next(iter(commec_log.samples[0].scores.values())).value
    assert commec_score["correct"] == commec_score["policy_valid"] == 1
    commec_replay = work / "commec-replay.eval"
    run(
        [
            str(bin_dir / "inspect-labs-commec-rescore"),
            str(commec_native),
            str(commec_native.with_suffix(".labs")),
            str(commec_replay),
        ],
        cwd=work,
        log=work / "commec-replay.log",
    )
    assert read_eval_log(str(commec_replay)).samples[0].scores == commec_log.samples[0].scores

    run(
        [
            str(bin_dir / "inspect"),
            "eval",
            "inspect_labs_plate_reader/absorbance_qc",
            "--model",
            "mockllm/model",
            "-T",
            "scripted=true",
            "-T",
            f"evidence_dir={work / 'reader-evidence'}",
            "--log-dir",
            str(work / "reader"),
            "--display",
            "none",
        ],
        cwd=work,
        log=work / "reader.log",
    )
    reader_native = one_eval(work / "reader")
    reader_log = read_eval_log(str(reader_native))
    assert reader_log.status == "success", reader_log.error
    reader_score = next(iter(reader_log.samples[0].scores.values())).value
    assert reader_score["correct"] == reader_score["read_complete"] == 1
    reader_replay = work / "reader-replay.eval"
    run(
        [
            str(bin_dir / "inspect-labs-plate-reader-rescore"),
            str(reader_native),
            str(reader_native.with_suffix(".labs")),
            str(reader_replay),
        ],
        cwd=work,
        log=work / "reader-replay.log",
    )
    assert read_eval_log(str(reader_replay)).samples[0].scores == reader_log.samples[0].scores


def main() -> None:
    """Build wheels, install them into a fresh external venv, and exercise them."""
    os.umask(0o077)
    if len(sys.argv) == 4 and sys.argv[1] == "--verify":
        verify_installed(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python scripts/check-installed.py")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    work = ROOT / ".research/runs" / f"installed-smoke-{stamp}"
    work.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="inspect-labs-venv-") as external:
        external_path = Path(external)
        wheels = external_path / "wheels"
        wheels.mkdir()
        for index, package in enumerate(PACKAGES):
            run(
                ["uv", "build", str(package), "--python", sys.executable, "--out-dir", str(wheels)],
                cwd=ROOT,
                log=work / f"build-{index}.log",
            )
        python = external_path / "venv/bin/python"
        run(
            [
                "uv",
                "venv",
                "--no-project",
                "--python",
                f"{sys.version_info.major}.{sys.version_info.minor}",
                str(python.parent.parent),
            ],
            cwd=external_path,
            log=work / "venv.log",
        )
        distributions = sorted(wheels.glob("*.whl"))
        if len(distributions) != len(PACKAGES):
            raise AssertionError(f"Expected {len(PACKAGES)} wheels, found {len(distributions)}")
        core = next(path for path in distributions if path.name.startswith("inspect_labs-"))
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                *(f"{path}[robots]" if path == core else str(path) for path in distributions),
            ],
            cwd=external_path,
            log=work / "install.log",
        )
        run(
            [str(python), "-I", str(Path(__file__).resolve()), "--verify", str(work), str(python)],
            cwd=external_path,
            log=work / "smoke.log",
        )
    print(
        "Installed core, robot, Opentrons, Commec, and reader controls and replay passed; "
        f"private logs: {work}"
    )


if __name__ == "__main__":
    main()
