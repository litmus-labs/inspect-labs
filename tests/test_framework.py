"""Native model paths, interchangeable scaffolds, linked child evidence and author reuse."""

import math
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from inspect_ai import Task, eval, eval_set
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, Model, ModelOutput, get_model
from inspect_ai.solver import solver
from inspect_ai.tool import ToolCall

from inspect_labs.bindings import LabInfo, LabLogFile, connect_lab, rescore
from inspect_labs.environments import MeasurementEnvironment
from inspect_labs.litmus_labs import FixtureService, Request
from inspect_labs.tasks import (
    OUTCOME_METRICS,
    final_answer,
    handoff,
    handoff_outcome,
    measurement,
    measurement_outcome,
    scripted_measurement,
)

SUCCESS = {"known": 1, "executed": 1, "answered": 1, "honest": 1, "correct": 1}


def unscored(value) -> bool:
    """NaN marks an unscored value natively; persisted logs store it as null."""
    return value is None or math.isnan(value)


def metric_means(log) -> dict[str, float]:
    return {score.name: score.metrics["mean"].value for score in log.results.scores}


def run_task(task, tmp_path, model="mockllm/model", **kwargs):
    log = eval(task, model=model, log_dir=str(tmp_path / "native"), display="none", **kwargs)[0]
    return read_eval_log(log.location)


def test_model_driven_measurement_with_custom_inputs(tmp_path: Path) -> None:
    calls = [
        ToolCall(
            id="submit",
            function="submit_measurement",
            arguments={"request_id": "different", "resource": "sample2", "values": [7, 11]},
        ),
        ToolCall(id="poll", function="read_measurement", arguments={"job_id": "job-1"}),
    ]
    outputs = [
        ModelOutput.from_message(ChatMessageAssistant(content="", tool_calls=[call]))
        for call in calls
    ]
    outputs.append(ModelOutput.from_content("mockllm/model", "Measured.\nANSWER: 18"))
    log = run_task(
        measurement(
            resource="sample2",
            values=[7, 11],
            request_id="different",
            evidence_dir=str(tmp_path / "evidence"),
        ),
        tmp_path,
        get_model("mockllm/model", custom_outputs=outputs),
    )
    assert log.status == "success"
    assert next(iter(log.samples[0].scores.values())).value == SUCCESS
    assert Path(log.location).with_suffix(".labs").is_file()
    assert "litmus-fixture/v1" not in log.model_dump_json()


@pytest.mark.parametrize("backend", ["digital", "robot"])
def test_handoff_and_linked_rescore(tmp_path: Path, backend: str) -> None:
    if backend == "robot":
        pytest.importorskip("inspect_robots")
    log = run_task(
        handoff(
            artifact="report-9",
            destination="review",
            content="result=17",
            backend=backend,
            scripted=True,
            evidence_dir=str(tmp_path / "evidence"),
        ),
        tmp_path,
    )
    assert log.status == "success", log.error
    assert next(iter(log.samples[0].scores.values())).value == SUCCESS
    native = Path(log.location)
    bundle_path = native.with_suffix(".labs")
    bundle = LabLogFile.model_validate_json(bundle_path.read_text())
    record = next(iter(bundle.samples.values()))
    assert record.payload["observation"]["destination"] == "review"
    if backend == "robot":
        assert any("readiness.json" in link.path for link in record.artifacts)
        assert any(link.path.endswith(".jsonl") for link in record.artifacts)
    with (
        patch.object(FixtureService, "submit", side_effect=AssertionError("dispatch")),
        patch.object(Model, "generate", side_effect=AssertionError("model call")),
    ):
        rescore(native, bundle_path, tmp_path / "rescored.eval", handoff_outcome)
    assert read_eval_log(str(tmp_path / "rescored.eval")).samples[0].scores == log.samples[0].scores
    Path(record.artifacts[0].path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Child artifact"):
        rescore(native, bundle_path, tmp_path / "invalid.eval", handoff_outcome)


def test_native_solver_override_preserves_binding(tmp_path: Path) -> None:
    request = Request(resource="sample3", request_id="r3", values=(19, -4))
    task = measurement(
        resource="sample3",
        request_id="r3",
        values=[19, -4],
        evidence_dir=str(tmp_path / "evidence"),
    )
    log = run_task(task, tmp_path, solver=scripted_measurement(request))
    assert log.status == "success"
    assert next(iter(log.samples[0].scores.values())).value == SUCCESS


def test_external_native_task_authoring(tmp_path: Path) -> None:
    request = Request(resource="custom-sample", request_id="custom", values=(31, 7))
    native_task = Task(
        name="external-assay",
        dataset=[Sample(id="custom", input="Measure 31+7")],
        solver=scripted_measurement(request),
    )
    task = connect_lab(
        native_task,
        lab=lambda state: MeasurementEnvironment(
            state.uuid, request, FixtureService(frozenset({"custom-sample"}))
        ),
        scorer=measurement_outcome,
        requires=frozenset({"measurement"}),
        lab_log_dir=tmp_path / "external-evidence",
        metrics=OUTCOME_METRICS,
    )
    log = run_task(task, tmp_path)
    assert next(iter(log.samples[0].scores.values())).value == SUCCESS
    rescore(
        Path(log.location),
        Path(log.location).with_suffix(".labs"),
        tmp_path / "rescored.eval",
        measurement_outcome,
    )


@pytest.mark.parametrize("case", ["missing", "rejected", "error"])
def test_unknown_and_error_lifecycle(tmp_path: Path, case: str) -> None:
    task = measurement(
        scripted=True,
        observation_available=case != "missing",
        reject=case == "rejected",
        evidence_dir=str(tmp_path / "evidence"),
    )
    if case == "error":

        @solver
        def fail_after_execution():
            async def solve(state, generate):
                await scripted_measurement(
                    Request(request_id="r1", resource="sample1", values=(2, 3))
                )(state, generate)
                raise RuntimeError("failure after provider completion")

            return solve

        task.solver = fail_after_execution()
    log = run_task(task, tmp_path)
    native = Path(log.location)
    bundle_path = native.with_suffix(".labs")
    bundle = LabLogFile.model_validate_json(bundle_path.read_text())
    record = next(iter(bundle.samples.values()))
    score = log.samples[0].scores and next(iter(log.samples[0].scores.values())).value
    if case == "error":
        assert log.status == "error"
        assert record.payload["observation"] is not None
    elif case == "missing":
        # Observer failure is unknown and unscored, with its category preserved.
        assert record.observation_error == "ObserverUnavailable"
        assert score["known"] == 0 and unscored(score["correct"])
    else:
        # Rejection is observed non-completion: the provider has no job for the sample.
        assert record.payload["jobs"] == []
        assert score == {"known": 1, "executed": 0, "answered": 1, "honest": 1, "correct": 0}
    rescore(native, bundle_path, tmp_path / "rescored.eval", measurement_outcome)
    assert read_eval_log(str(tmp_path / "rescored.eval")).status == log.status


def test_capability_preflight_prevents_actor_dispatch(tmp_path: Path) -> None:
    class Incompatible(MeasurementEnvironment):
        info = LabInfo(name="incompatible", version="1", mode="physical", capabilities=frozenset())

    service = FixtureService(frozenset({"sample1"}))
    request = Request(request_id="r1", resource="sample1", values=(2, 3))
    task = connect_lab(
        Task(
            name="unsupported",
            dataset=[Sample(input="measure")],
            solver=scripted_measurement(request),
        ),
        lab=lambda state: Incompatible(state.uuid, request, service),
        scorer=measurement_outcome,
        requires=frozenset({"measurement"}),
        lab_log_dir=tmp_path / "evidence",
        metrics=OUTCOME_METRICS,
    )
    log = run_task(task, tmp_path)
    assert log.status == "error"
    assert service.submissions == 0


def test_empty_or_unlinked_handoff_evidence_is_not_success(tmp_path: Path) -> None:
    log = run_task(handoff(scripted=True, evidence_dir=str(tmp_path / "evidence")), tmp_path)
    bundle = LabLogFile.model_validate_json(Path(log.location).with_suffix(".labs").read_text())
    record = next(iter(bundle.samples.values()))
    with pytest.raises(ValueError, match="delivered-data"):
        handoff_outcome("ANSWER: complete", record.model_copy(update={"artifacts": []}))
    with pytest.raises(ValueError):
        handoff_outcome(
            "complete",
            record.model_copy(
                update={"payload": {"expected": {}, "observation": {}, "ready": True}}
            ),
        )


def test_observer_timeout_is_preserved_as_unknown(tmp_path: Path) -> None:
    import anyio

    class SlowObserver(MeasurementEnvironment):
        async def observe(self):
            await anyio.sleep(1)
            return await super().observe()

    request = Request(request_id="r1", resource="sample1", values=(2, 3))
    task = connect_lab(
        Task(
            name="slow-observer",
            dataset=[Sample(input="measure")],
            solver=scripted_measurement(request),
        ),
        lab=lambda state: SlowObserver(state.uuid, request, FixtureService(frozenset({"sample1"}))),
        scorer=measurement_outcome,
        requires=frozenset({"measurement"}),
        lab_log_dir=tmp_path / "evidence",
        metrics=OUTCOME_METRICS,
        observation_timeout=0.01,
    )
    log = run_task(task, tmp_path)
    score = next(iter(log.samples[0].scores.values())).value
    assert score["known"] == 0 and unscored(score["correct"])
    bundle = LabLogFile.model_validate_json(Path(log.location).with_suffix(".labs").read_text())
    assert next(iter(bundle.samples.values())).observation_error == "TimeoutError"


@pytest.mark.parametrize("retry_cleanup", [False, True])
def test_native_eval_set_does_not_repeat_completed_dispatch(
    tmp_path: Path, retry_cleanup: bool
) -> None:
    request = Request(request_id="r1", resource="sample1", values=(2, 3))

    @solver
    def fail_after_dispatch():
        async def solve(state, generate):
            await scripted_measurement(request)(state, generate)
            raise RuntimeError("injected failure after dispatch")

        return solve

    original = FixtureService.submit
    with patch.object(FixtureService, "submit", autospec=True, side_effect=original) as submit:
        complete, logs = eval_set(
            measurement(evidence_dir=str(tmp_path / "evidence")),
            solver=fail_after_dispatch(),
            model="mockllm/model",
            display="none",
            log_dir=str(tmp_path / "native"),
            retry_attempts=2,
            retry_wait=0.01,
            retry_on_error=0,
            retry_cleanup=retry_cleanup,
        )
    assert not complete
    assert submit.call_count == 1
    # Errored attempts emit no native task-end, and the eval-set run end lists only
    # the final retry. Evidence must still be sealed against the dispatching attempt.
    (bundle_path,) = (tmp_path / "native").glob("*.labs")
    bundle = LabLogFile.model_validate_json(bundle_path.read_text())
    (record,) = bundle.samples.values()
    assert record.payload["observation"]["value"] == 5
    assert (tmp_path / "evidence" / f"{record.sample_uuid}.lab-sample").is_file()
    native = bundle_path.with_suffix(".eval")
    if retry_cleanup:
        # Native eval-set cleanup deletes the failed attempt's log; replay cannot bind.
        assert not native.exists()
        with pytest.raises(OSError):
            rescore(native, bundle_path, tmp_path / "rescored.eval", measurement_outcome)
        return
    assert [s.uuid for s in read_eval_log(str(native)).samples] == [record.sample_uuid]
    with patch.object(FixtureService, "submit", side_effect=AssertionError("dispatch")):
        rescore(native, bundle_path, tmp_path / "rescored.eval", measurement_outcome)
    assert read_eval_log(str(tmp_path / "rescored.eval")).status == "error"


def test_omitted_provenance_is_not_success(tmp_path: Path) -> None:
    log = run_task(measurement(scripted=True, evidence_dir=str(tmp_path / "m")), tmp_path)
    (record,) = LabLogFile.model_validate_json(
        Path(log.location).with_suffix(".labs").read_text()
    ).samples.values()
    assert measurement_outcome("ANSWER: 5", record) == SUCCESS
    unlinked = record.model_copy(update={"payload": {**record.payload, "jobs": []}})
    with pytest.raises(ValueError, match="provider job"):
        measurement_outcome("ANSWER: 5", unlinked)


def test_robot_handoff_requires_every_native_link(tmp_path: Path) -> None:
    pytest.importorskip("inspect_robots")
    log = run_task(
        handoff(backend="robot", scripted=True, evidence_dir=str(tmp_path / "evidence")),
        tmp_path,
    )
    (record,) = LabLogFile.model_validate_json(
        Path(log.location).with_suffix(".labs").read_text()
    ).samples.values()
    assert handoff_outcome("ANSWER: complete", record) == SUCCESS
    robot = record.payload["robot_observation"]
    for required in [robot["native_log"], robot["observation_file"], *robot["actions"]]:
        omitted = [link for link in record.artifacts if link.path != required]
        with pytest.raises(ValueError, match="Robot readiness"):
            handoff_outcome("ANSWER: complete", record.model_copy(update={"artifacts": omitted}))
    not_prepared = record.model_copy(
        update={
            "payload": {
                **record.payload,
                "ready": None,
                "robot_observation": None,
                "observation": None,
            }
        }
    )
    assert handoff_outcome("ANSWER: complete", not_prepared) == {
        "known": 1,
        "executed": 0,
        "answered": 1,
        "honest": 0,
        "correct": 0,
    }
    unlinked = record.model_copy(update={"payload": {**record.payload, "robot_observation": None}})
    with pytest.raises(ValueError, match="Robot readiness"):
        handoff_outcome("ANSWER: complete", unlinked)


def test_native_limit_after_dispatch_keeps_provider_facts(tmp_path: Path) -> None:
    request = Request(request_id="r1", resource="sample1", values=(2, 3))

    @solver
    def continues_after_dispatch():
        async def solve(state, generate):
            await scripted_measurement(request)(state, generate)
            return await generate(state)

        return solve

    task = measurement(evidence_dir=str(tmp_path / "evidence"))
    # The scripted dispatch produces 6 messages; native Inspect stops the next turn.
    log = run_task(task, tmp_path, solver=continues_after_dispatch(), message_limit=6)
    sample = log.samples[0]
    assert sample.limit is not None and sample.limit.type == "message"
    (record,) = LabLogFile.model_validate_json(
        Path(log.location).with_suffix(".labs").read_text()
    ).samples.values()
    assert record.payload["observation"]["value"] == 5
    # The interrupted actor is scored against provider facts, not assumed.
    assert next(iter(sample.scores.values())).value["known"] == 1


def _measurement_outputs(request_id: str = "r1", report: str = "ANSWER: 5") -> list[ModelOutput]:
    calls = [
        ToolCall(
            id="submit",
            function="submit_measurement",
            arguments={"request_id": request_id, "resource": "sample1", "values": [2, 3]},
        ),
        ToolCall(id="poll", function="read_measurement", arguments={"job_id": "job-1"}),
    ]
    outputs = [
        ModelOutput.from_message(ChatMessageAssistant(content="", tool_calls=[call]))
        for call in calls
    ]
    return [*outputs, ModelOutput.from_content("mockllm/model", report)]


@pytest.mark.parametrize("acting_first", [True, False])
def test_mixed_epoch_outcomes_keep_native_metrics(tmp_path: Path, acting_first: bool) -> None:
    idle = [ModelOutput.from_content("mockllm/model", "ANSWER: 5")]
    outputs = _measurement_outputs() + idle if acting_first else idle + _measurement_outputs()
    log = run_task(
        measurement(evidence_dir=str(tmp_path / "evidence")),
        tmp_path,
        get_model("mockllm/model", custom_outputs=outputs),
        epochs=2,
        max_samples=1,
    )
    assert log.status == "success", log.error
    values = sorted(
        (next(iter(s.scores.values())).value["correct"] for s in log.samples), reverse=True
    )
    # The idle epoch claims "5" without any provider job: observed and incorrect.
    assert values == [1, 0]
    assert metric_means(log) == {
        "known": 1.0,
        "executed": 0.5,
        "answered": 1.0,
        "honest": 0.5,
        "correct": 0.5,
    }


def test_unknown_epoch_is_unscored_not_dropped(tmp_path: Path) -> None:
    class FlakyObserver(MeasurementEnvironment):
        calls = 0

        async def observe(self):
            FlakyObserver.calls += 1
            if FlakyObserver.calls == 1:
                raise ConnectionError("observer down")
            return await super().observe()

    request = Request(request_id="r1", resource="sample1", values=(2, 3))
    task = connect_lab(
        Task(name="flaky", dataset=[Sample(input="m")], solver=scripted_measurement(request)),
        lab=lambda state: FlakyObserver(
            state.uuid, request, FixtureService(frozenset({"sample1"}))
        ),
        scorer=measurement_outcome,
        requires=frozenset({"measurement"}),
        lab_log_dir=tmp_path / "evidence",
        metrics=OUTCOME_METRICS,
    )
    log = run_task(task, tmp_path, epochs=2, max_samples=1)
    assert log.status == "success", log.error
    # correct is averaged among observed outcomes only.
    assert metric_means(log) == {
        "known": 0.5,
        "executed": 1.0,
        "answered": 1.0,
        "honest": 1.0,
        "correct": 1.0,
    }


def test_completed_job_under_wrong_request_identity_is_incorrect(tmp_path: Path) -> None:
    log = run_task(
        measurement(evidence_dir=str(tmp_path / "evidence")),
        tmp_path,
        get_model("mockllm/model", custom_outputs=_measurement_outputs(request_id="r2")),
    )
    assert next(iter(log.samples[0].scores.values())).value == {
        "known": 1,
        "executed": 0,
        "answered": 1,
        "honest": 0,
        "correct": 0,
    }


@pytest.mark.parametrize(
    "report", ["ANSWER: unknown", "complete", "ANSWER: complete\nANSWER: unknown", ""]
)
def test_handoff_misreport_is_not_correct(tmp_path: Path, report: str) -> None:
    log = run_task(handoff(scripted=True, evidence_dir=str(tmp_path / "evidence")), tmp_path)
    (record,) = LabLogFile.model_validate_json(
        Path(log.location).with_suffix(".labs").read_text()
    ).samples.values()
    assert handoff_outcome("Done.\n**ANSWER: Complete.**", record) == SUCCESS
    result = handoff_outcome(report, record)
    assert result["executed"] == 1 and result["correct"] == 0
    if final_answer(report) is None:
        assert result["answered"] == 0 and unscored(result["honest"])
    else:
        assert result["answered"] == 1 and result["honest"] == 0


def test_rejected_native_retry_closes_provider_once(tmp_path: Path) -> None:
    closes: list[str] = []

    class Counted(MeasurementEnvironment):
        async def close(self) -> None:
            closes.append(self.run_id)

    request = Request(request_id="r1", resource="sample1", values=(2, 3))

    @solver
    def fail_after_dispatch():
        async def solve(state, generate):
            await scripted_measurement(request)(state, generate)
            raise RuntimeError("injected failure after dispatch")

        return solve

    task = connect_lab(
        Task(name="retry-close", dataset=[Sample(input="m")], solver=fail_after_dispatch()),
        lab=lambda state: Counted(state.uuid, request, FixtureService(frozenset({"sample1"}))),
        scorer=measurement_outcome,
        requires=frozenset({"measurement"}),
        lab_log_dir=tmp_path / "evidence",
        metrics=OUTCOME_METRICS,
    )
    log = run_task(task, tmp_path, retry_on_error=1)
    assert log.status == "error"
    assert len(closes) == 1


def test_failed_robot_rollout_is_unknown_not_actor_failure(tmp_path: Path) -> None:
    pytest.importorskip("inspect_robots")
    import types

    import inspect_labs.robot_workflow as robot_workflow

    failed = [types.SimpleNamespace(status="error")]
    with patch.object(robot_workflow, "eval", return_value=failed):
        log = run_task(
            handoff(backend="robot", scripted=True, evidence_dir=str(tmp_path / "evidence")),
            tmp_path,
        )
    (record,) = LabLogFile.model_validate_json(
        Path(log.location).with_suffix(".labs").read_text()
    ).samples.values()
    assert record.payload["robot_rollout"] == "failed"
    score = next(iter(log.samples[0].scores.values())).value
    assert score["known"] == 0 and unscored(score["correct"])


def test_declared_extra_metrics_survive_unknown_epochs(tmp_path: Path) -> None:
    class FlakyObserver(MeasurementEnvironment):
        calls = 0

        async def observe(self):
            FlakyObserver.calls += 1
            if FlakyObserver.calls == 2:
                raise ConnectionError("observer down")
            return await super().observe()

    def judge(report, evidence):
        return {**measurement_outcome(report, evidence), "polled": 1}

    request = Request(request_id="r1", resource="sample1", values=(2, 3))

    def build(metrics):
        return connect_lab(
            Task(name="extra", dataset=[Sample(input="m")], solver=scripted_measurement(request)),
            lab=lambda state: FlakyObserver(
                state.uuid, request, FixtureService(frozenset({"sample1"}))
            ),
            scorer=judge,
            requires=frozenset({"measurement"}),
            lab_log_dir=tmp_path / str(len(metrics)),
            metrics=metrics,
        )

    log = run_task(build((*OUTCOME_METRICS, "polled")), tmp_path, epochs=2, max_samples=1)
    assert log.status == "success", log.error
    assert metric_means(log) == {
        "known": 0.5,
        "executed": 1.0,
        "answered": 1.0,
        "honest": 1.0,
        "correct": 1.0,
        "polled": 1.0,
    }
    FlakyObserver.calls = 0
    undeclared = run_task(build(OUTCOME_METRICS), tmp_path, epochs=2, max_samples=1)
    assert undeclared.status == "error"
    assert "undeclared metrics" in undeclared.samples[0].error.message
    with pytest.raises(ValueError, match="include 'known'"):
        build(("known",))


def test_receipt_without_delivered_data_is_unknown(tmp_path: Path) -> None:
    from inspect_labs.environments import HandoffEnvironment

    environment = HandoffEnvironment(tmp_path, "report-1", "analysis", b"x")
    transfer = environment.tools[0]
    anyio.run(transfer, "t1", "report-1", "analysis")
    (tmp_path / "delivered-report.bin").unlink()
    with pytest.raises(RuntimeError, match="no delivered data"):
        anyio.run(environment.observe)


@pytest.mark.parametrize(
    ("report", "answer"),
    [
        ("ANSWER: 5", "5"),
        ("Measured job-1.\n\n**ANSWER: 5**", "5"),
        ("ANSWER: 4\nChecked again.\nANSWER: 5", "5"),
        ("> ANSWER: `Complete.`", "complete"),
        ("answer: unknown", "unknown"),
        ("The measurement is 5.", None),
        ("ANSWER:", None),
    ],
)
def test_final_answer_line(report: str, answer: str | None) -> None:
    from inspect_labs.tasks import final_answer

    assert final_answer(report) == answer


def test_limit_before_answer_is_unanswered_not_dishonest(tmp_path: Path) -> None:
    # Live pilot: agents stopped by the token limit were scored honest=0 without answering.
    log = run_task(
        measurement(evidence_dir=str(tmp_path / "evidence")),
        tmp_path,
        get_model("mockllm/model", custom_outputs=_measurement_outputs()),
        message_limit=4,
    )
    sample = log.samples[0]
    assert sample.limit is not None and sample.limit.type == "message"
    result = next(iter(sample.scores.values())).value
    assert result["answered"] == 0 and unscored(result["honest"]) and result["correct"] == 0


def test_judge_keyword_is_a_deprecated_alias_of_scorer(tmp_path: Path) -> None:
    request = Request(resource="custom-sample", request_id="alias", values=(1, 2))

    def build(**outcome):
        return connect_lab(
            Task(name="alias", dataset=[Sample(input="m")], solver=scripted_measurement(request)),
            lab=lambda state: MeasurementEnvironment(
                state.uuid, request, FixtureService(frozenset({"custom-sample"}))
            ),
            requires=frozenset({"measurement"}),
            lab_log_dir=tmp_path / "alias-evidence",
            metrics=OUTCOME_METRICS,
            **outcome,
        )

    with pytest.warns(DeprecationWarning, match="scorer="):
        task = build(judge=measurement_outcome)
    log = run_task(task, tmp_path)
    assert next(iter(log.samples[0].scores.values())).value == SUCCESS
    with pytest.warns(DeprecationWarning, match="scorer="):
        rescore(
            Path(log.location),
            Path(log.location).with_suffix(".labs"),
            tmp_path / "alias-rescored.eval",
            judge=measurement_outcome,
        )
    with pytest.raises(TypeError, match="not both"):
        build(scorer=measurement_outcome, judge=measurement_outcome)
    with pytest.raises(TypeError, match="missing required argument 'scorer'"):
        build()


def test_earlier_names_still_work_and_warn(tmp_path: Path) -> None:
    import inspect_labs
    from inspect_labs import bind_task, rescore_workflow

    assert inspect_labs.LabEnvironment is inspect_labs.Lab
    assert inspect_labs.EnvironmentInfo is inspect_labs.LabInfo
    assert inspect_labs.LabEvidence is inspect_labs.LabLog
    assert inspect_labs.check_environment is inspect_labs.check_lab
    assert inspect_labs.ConformanceReport is inspect_labs.LabCheckReport
    request = Request(resource="custom-sample", request_id="earlier", values=(4, 5))
    with pytest.warns(DeprecationWarning, match="connect_lab"):
        task = bind_task(
            Task(name="earlier", dataset=[Sample(input="m")], solver=scripted_measurement(request)),
            environment=lambda state: MeasurementEnvironment(
                state.uuid, request, FixtureService(frozenset({"custom-sample"}))
            ),
            scorer=measurement_outcome,
            requires=frozenset({"measurement"}),
            evidence_dir=tmp_path / "earlier-evidence",
            metrics=OUTCOME_METRICS,
        )
    log = run_task(task, tmp_path)
    assert next(iter(log.samples[0].scores.values())).value == SUCCESS
    with pytest.warns(DeprecationWarning, match="rescore"):
        rescore_workflow(
            Path(log.location),
            Path(log.location).with_suffix(".labs"),
            tmp_path / "earlier-rescored.eval",
            measurement_outcome,
        )
