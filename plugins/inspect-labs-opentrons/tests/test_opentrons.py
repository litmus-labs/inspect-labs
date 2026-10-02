"""Hardware-free checks: discovery, doctor, the OT-2 simulator end to end, physical gating."""

import json
import subprocess
import sys
from pathlib import Path

import anyio
from inspect_ai import eval
from inspect_ai.log import read_eval_log

from inspect_labs import plugins
from inspect_labs.conformance import diagnose
from inspect_labs.liquid_tasks import worklist_transfer


def test_backends_are_discoverable_without_import() -> None:
    assert {"opentrons-ot2", "opentrons-ot2-simulator"} <= set(plugins.available("backend"))


def test_doctor_reports_real_robot_requirements() -> None:
    report = anyio.run(diagnose, "backend", "opentrons-ot2")
    assert report["device_slots"] == [
        {"arg": "host", "kind": "http", "label": "OT-2 robot address"}
    ]
    if "ot_api" in report["missing_requirements"]:
        assert (
            not report["ok"]
            and "inspect-labs-opentrons[robot]" in report["missing_requirements"]["ot_api"]
        )


def test_worklist_on_the_ot2_simulator(tmp_path: Path) -> None:
    task = worklist_transfer(
        lure=True,
        scripted=True,
        backend="opentrons-ot2-simulator",
        evidence_dir=str(tmp_path / "e"),
    )
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")[0]
    log = read_eval_log(log.location)
    assert log.status == "success", log.error
    score = next(iter(log.samples[0].scores.values())).value
    assert score["correct"] == 1 and score["deck_ok"] == 1


def test_cli_lists_plugin_backends() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "inspect_labs.cli", "list", "backend"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "opentrons-ot2" in json.loads(result.stdout)["backend"]
