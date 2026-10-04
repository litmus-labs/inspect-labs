"""Installed native reader dispatch, independent observations and replay boundaries."""

from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from inspect_ai import eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, Model, ModelOutput, get_model
from inspect_ai.tool import ToolCall, ToolError
from inspect_labs_plate_reader.environment import PlateReaderEnvironment
from inspect_labs_plate_reader.tasks import METRICS, absorbance_qc, qc_outcome
from pylabrobot.plate_reading import PlateReader

from inspect_labs.bindings import LabLogFile, rescore


@pytest.mark.parametrize(
    "case,expected_pass", [("passing", 1), ("high_blank", 0), ("low_control", 0)]
)
def test_native_qc_control_and_zero_dispatch_replay(case, expected_pass, tmp_path):
    log = eval(
        absorbance_qc(case=case, scripted=True, evidence_dir=str(tmp_path / "e")),
        model="mockllm/model",
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    assert log.status == "success", log.error
    values = next(iter(log.samples[0].scores.values())).value
    assert values["correct"] == values["honest"] == values["read_complete"] == 1
    assert values["qc_pass"] == expected_pass
    native = Path(log.location)
    with (
        patch.object(Model, "generate", side_effect=AssertionError("model dispatched")),
        patch.object(
            PlateReaderEnvironment, "__init__", side_effect=AssertionError("reader built")
        ),
    ):
        rescore(
            native,
            native.with_suffix(".labs"),
            tmp_path / "replay.eval",
            qc_outcome,
            metrics=METRICS,
        )
    replay = read_eval_log(str(tmp_path / "replay.eval"))
    assert replay.samples[0].scores == log.samples[0].scores


def test_model_answer_without_read_is_not_a_measurement(tmp_path):
    model = get_model(
        "mockllm/model",
        custom_outputs=[ModelOutput.from_content("mockllm/model", "ANSWER: pass")],
    )
    log = eval(
        absorbance_qc(evidence_dir=str(tmp_path / "e")),
        model=model,
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    assert log.status == "success", log.error
    values = next(iter(log.samples[0].scores.values())).value
    assert values["known"] == 1  # The absence of a read was observed.
    assert values["read_complete"] == values["correct"] == values["honest"] == 0


def test_native_generation_reads_actual_pylabrobot_backend(tmp_path):
    call = ToolCall(id="reader-1", function="read_absorbance", arguments={"wavelength_nm": 600})
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.from_message(ChatMessageAssistant(content="", tool_calls=[call])),
            ModelOutput.from_content("mockllm/model", "ANSWER: pass"),
        ],
    )
    log = eval(
        absorbance_qc(evidence_dir=str(tmp_path / "e")),
        model=model,
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    assert log.status == "success", log.error
    assert next(iter(log.samples[0].scores.values())).value["correct"] == 1
    assert Path(log.location).with_suffix(".labs").is_file()


def test_wrong_wavelength_does_not_dispatch_and_second_read_is_refused(tmp_path):
    async def check():
        env = PlateReaderEnvironment("sample", tmp_path / "private")
        with patch.object(env.reader, "read_absorbance", side_effect=AssertionError("dispatched")):
            with pytest.raises(ToolError, match="600 nm"):
                await env.tools[0](wavelength_nm=450)
        assert await env.observe() == {"status": "not_requested"}
        await env.tools[0](wavelength_nm=600)
        with pytest.raises(ToolError, match="already attempted"):
            await env.tools[0](wavelength_nm=600)
        assert len(env.artifacts) == 1
        await env.close()

    anyio.run(check)


def test_replay_rejects_mutated_reader_artifact(tmp_path):
    log = eval(
        absorbance_qc(scripted=True, evidence_dir=str(tmp_path / "e")),
        model="mockllm/model",
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    native = Path(log.location)
    bundle = LabLogFile.model_validate_json(native.with_suffix(".labs").read_text())
    record = next(iter(bundle.samples.values()))
    Path(record.artifacts[0].path).write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        rescore(
            native,
            native.with_suffix(".labs"),
            tmp_path / "replay.eval",
            qc_outcome,
            metrics=METRICS,
        )


def test_unknown_reader_outcome_is_not_scientific_failure(tmp_path):
    async def check():
        env = PlateReaderEnvironment("sample", tmp_path / "private")
        with patch.object(env.reader, "read_absorbance", side_effect=TimeoutError):
            with pytest.raises(ToolError, match="TimeoutError"):
                await env.tools[0](wavelength_nm=600)
        assert await env.observe() == {"status": "unknown"}
        assert env.artifacts == []
        await env.close()

    anyio.run(check)


def test_uncertain_backend_attempt_stays_unknown_in_native_scoring_and_replay(tmp_path):
    with patch.object(PlateReader, "read_absorbance", side_effect=TimeoutError):
        log = eval(
            absorbance_qc(scripted=True, evidence_dir=str(tmp_path / "e")),
            model="mockllm/model",
            log_dir=str(tmp_path / "logs"),
            display="none",
        )[0]
    assert log.status == "success", log.error
    values = next(iter(log.samples[0].scores.values())).value
    assert values["known"] == 0
    assert values["read_complete"] is None  # Native log serializes unscored NaN as null.
    assert values["correct"] is None
    native = Path(log.location)
    with patch.object(
        PlateReaderEnvironment, "__init__", side_effect=AssertionError("reader built")
    ):
        rescore(
            native,
            native.with_suffix(".labs"),
            tmp_path / "replay.eval",
            qc_outcome,
            metrics=METRICS,
        )
    replay_values = next(
        iter(read_eval_log(str(tmp_path / "replay.eval")).samples[0].scores.values())
    ).value
    assert replay_values["known"] == 0
    assert replay_values["read_complete"] is None
