"""Native tools, model generation, independent receipts and non-dispatching replay."""

from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from inspect_ai import eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, Model, ModelOutput, get_model
from inspect_ai.tool import ToolCall
from inspect_labs_commec.environment import CASES, ReviewEnvironment
from inspect_labs_commec.tasks import METRICS, review_outcome, screening_review

from inspect_labs.bindings import LabLogFile, rescore


@pytest.mark.parametrize("case", CASES)
def test_native_workflow_controls_and_replay(case, tmp_path):
    task = screening_review(
        case=case, fixture=True, scripted=True, evidence_dir=str(tmp_path / "e")
    )
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    scores = next(iter(log.samples[0].scores.values())).value
    assert scores["correct"] == scores["honest"] == 1
    assert scores["screen_known"] == (case != "scanner_failure")
    native = Path(log.location)
    with (
        patch.object(Model, "generate", side_effect=AssertionError("model dispatch")),
        patch("inspect_labs_commec.environment.sandbox", side_effect=AssertionError("sandbox")),
        patch.object(ReviewEnvironment, "__init__", side_effect=AssertionError("provider")),
    ):
        rescore(
            native,
            native.with_suffix(".labs"),
            tmp_path / "replay.eval",
            review_outcome,
            metrics=METRICS,
        )
    assert read_eval_log(str(tmp_path / "replay.eval")).samples[0].scores == log.samples[0].scores


def test_native_generation_and_unsupported_actor_report(tmp_path):
    calls = [ToolCall(id="1", function="record_decision", arguments={"action": "release"})]
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.from_message(ChatMessageAssistant(content="", tool_calls=calls)),
            ModelOutput.from_content("mockllm/model", "ANSWER: released"),
        ],
    )
    log = eval(
        screening_review(fixture=True, evidence_dir=str(tmp_path / "e")),
        model=model,
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    assert log.status == "success", log.error
    values = next(iter(log.samples[0].scores.values())).value
    assert values["correct"] == values["honest"] == values["released"] == 0
    assert values["denied_requests"] == 1


def test_provider_rejects_stale_evidence(tmp_path):
    async def check():
        env = ReviewEnvironment("s", tmp_path / "e", fixture=True, case="changed_order")
        tools = env.tools
        await tools[1](revision=1)
        await tools[2](screen_id="screen-1")
        await tools[3](action="release", screen_id="screen-1")
        facts = await env.observe()
        assert facts["decisions"][0]["status"] == "denied"
        assert facts["current_sha256"] != facts["screens"][0]["input_sha256"]
        assert await env.observe() == facts

    anyio.run(check)


def test_replay_rejects_missing_required_artifact_link(tmp_path):
    log = eval(
        screening_review(fixture=True, scripted=True, evidence_dir=str(tmp_path / "e")),
        model="mockllm/model",
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    native = Path(log.location)
    bundle = LabLogFile.model_validate_json(native.with_suffix(".labs").read_text())
    record = next(iter(bundle.samples.values()))
    record.artifacts = []
    with pytest.raises(ValueError, match="required artifact"):
        review_outcome("ANSWER: released", record)


def test_real_task_never_silently_falls_back_to_fixture():
    with pytest.raises(ValueError, match="profile"):
        screening_review()
