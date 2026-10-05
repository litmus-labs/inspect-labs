"""Results that arrive after the run: attach them, then rescore without running anything."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import Model, ModelOutput, get_model
from inspect_ai.solver import generate

from inspect_labs import rescore
from inspect_labs.bindings import (
    LabInfo,
    attach_late_result,
    connect_lab,
    read_lab_logs,
    run_time_hash,
)
from inspect_labs.tasks import final_answer


class SlowInstrument:
    """A Lab whose result is not readable when the run ends (for example, still running)."""

    info = LabInfo(
        name="slow-instrument",
        version="1",
        mode="simulation",
        capabilities=frozenset({"slow_read"}),
    )
    constructed = 0

    def __init__(self) -> None:
        type(self).constructed += 1

    @property
    def tools(self):
        return []

    @property
    def artifacts(self):
        return []

    async def observe(self):
        raise TimeoutError("result not ready")

    async def close(self) -> None:
        return None


def outcome(report, lab_log):
    value = (lab_log.payload or {}).get("value")
    if not isinstance(value, int):
        raise ValueError("Lab log lacks a value")
    return {"known": 1, "correct": int(final_answer(report) == str(value))}


@pytest.fixture
def run(tmp_path):
    task = connect_lab(
        Task(dataset=[Sample(input="Report the reading")], solver=generate()),
        lab=lambda state: SlowInstrument(),
        scorer=outcome,
        requires=frozenset({"slow_read"}),
        lab_log_dir=tmp_path / "evidence",
    )
    model = get_model(
        "mockllm/model", custom_outputs=[ModelOutput.from_content("mockllm/model", "ANSWER: 7")]
    )
    log = eval(task, model=model, log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    native = Path(log.location)
    return native, native.with_suffix(".labs"), log.samples[0].uuid


def _score(path):
    return next(iter(read_eval_log(str(path)).samples[0].scores.values())).value


def test_unknown_at_run_time_becomes_known_after_a_late_result(run, tmp_path):
    native, labs, uuid = run
    assert _score(native)["known"] == 0
    original = labs.read_bytes()
    late = tmp_path / "late.labs"
    attach_late_result(labs, uuid, {"value": 7}, late, note="plate read finished later")
    assert labs.read_bytes() == original
    built = SlowInstrument.constructed
    with patch.object(Model, "generate", side_effect=AssertionError("model dispatched")):
        rescore(native, late, tmp_path / "rescored.eval", outcome)
    assert SlowInstrument.constructed == built
    assert _score(tmp_path / "rescored.eval") == {"known": 1, "correct": 1}
    (record,) = read_lab_logs(late).samples.values()
    assert record.observation_error == "TimeoutError"
    assert [entry.note for entry in record.late_results] == ["plate read finished later"]
    assert record.late_results[0].previous_chain_sha256 == run_time_hash(record)


def test_the_latest_late_result_is_used_and_each_step_is_chained(run, tmp_path):
    native, labs, uuid = run
    first, second = tmp_path / "first.labs", tmp_path / "second.labs"
    attach_late_result(labs, uuid, {"value": 3}, first)
    attach_late_result(first, uuid, {"value": 7}, second)
    rescore(native, second, tmp_path / "rescored.eval", outcome)
    assert _score(tmp_path / "rescored.eval")["correct"] == 1


def test_an_edited_late_result_is_refused(run, tmp_path):
    native, labs, uuid = run
    late = tmp_path / "late.labs"
    attach_late_result(labs, uuid, {"value": 3}, late)
    document = json.loads(late.read_text())
    document["samples"][uuid]["late_results"][0]["payload"]["value"] = 7
    late.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Lab log hash mismatch"):
        rescore(native, late, tmp_path / "rescored.eval", outcome)


def test_attach_never_overwrites_and_rejects_unknown_samples(run, tmp_path):
    _, labs, uuid = run
    with pytest.raises(FileExistsError):
        attach_late_result(labs, uuid, {"value": 7}, labs)
    with pytest.raises(ValueError, match="No sample"):
        attach_late_result(labs, "not-a-sample", {"value": 7}, tmp_path / "x.labs")


def test_cli_attach_then_rescore(run, tmp_path):
    native, labs, uuid = run
    observation = tmp_path / "observation.json"
    observation.write_text(json.dumps({"value": 7}))
    late = tmp_path / "late.labs"
    cli = [sys.executable, "-m", "inspect_labs.cli"]
    attached = subprocess.run(
        [*cli, "attach", str(labs), "--sample", uuid, "--observation", str(observation)]
        + ["--output", str(late), "--note", "finished overnight"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert attached.returncode == 0, attached.stderr
    assert json.loads(attached.stdout) == {"lab_log": str(late), "sample": uuid}
    rescored = subprocess.run(
        [*cli, "rescore", str(native), "--evidence", str(late)]
        + ["--output", str(tmp_path / "r.eval"), "--scorer", f"{__file__}:outcome"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rescored.returncode == 0, rescored.stderr
    assert _score(tmp_path / "r.eval") == {"known": 1, "correct": 1}
