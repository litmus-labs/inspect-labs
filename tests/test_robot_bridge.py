"""Inspect Robots bridge: registered robot components as lab steps, guarded and replayable."""

import dataclasses
import math
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from inspect_ai import eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, Model, ModelOutput, get_model
from inspect_ai.tool import ToolCall

from inspect_labs import rescore_workflow
from inspect_labs.bindings import WorkflowEvidence
from inspect_labs.cli import main
from inspect_labs.tasks import robot_step, robot_step_outcome

pytest.importorskip("inspect_robots")


def run(task, tmp_path, model="mockllm/model", **kwargs):
    log = eval(task, model=model, log_dir=str(tmp_path / "logs"), display="none", **kwargs)[0]
    return read_eval_log(log.location)


def calls(count: int, answer: str):
    outputs = [
        ModelOutput.from_message(
            ChatMessageAssistant(
                content="",
                tool_calls=[ToolCall(id=f"r{i}", function="run_robot_task", arguments={})],
            )
        )
        for i in range(count)
    ]
    return get_model(
        "mockllm/model", custom_outputs=[*outputs, ModelOutput.from_content("m", answer)]
    )


def evidence(log):
    bundle = WorkflowEvidence.model_validate_json(
        Path(log.location).with_suffix(".labs").read_text()
    )
    return next(iter(bundle.samples.values()))


def test_scripted_robot_step_is_guarded_scored_and_replayable(tmp_path: Path) -> None:
    log = run(robot_step(scripted=True, evidence_dir=str(tmp_path / "e")), tmp_path)
    assert log.status == "success", log.error
    assert next(iter(log.samples[0].scores.values())).value["correct"] == 1
    record = evidence(log)
    (rollout,) = record.payload["rollouts"]
    assert {"clamp", "delta-limit"} <= set(rollout["guardrails"])
    assert all(path in {a.path for a in record.artifacts} for path in rollout["logs"])
    native = Path(log.location)
    with (
        patch("inspect_labs.robot_bridge.robots_eval", side_effect=AssertionError("robot run")),
        patch.object(Model, "generate", side_effect=AssertionError("model call")),
    ):
        rescore_workflow(
            native, native.with_suffix(".labs"), tmp_path / "r.eval", robot_step_outcome
        )
        with patch.object(
            sys,
            "argv",
            [
                "inspect-labs",
                "rescore",
                str(native),
                "--evidence",
                str(native.with_suffix(".labs")),
                "--output",
                str(tmp_path / "cli.eval"),
            ],
        ):
            main()
    assert read_eval_log(str(tmp_path / "r.eval")).samples[0].scores == log.samples[0].scores
    assert read_eval_log(str(tmp_path / "cli.eval")).samples[0].scores == log.samples[0].scores


def test_failed_rollout_claimed_complete_is_not_executed_or_honest(tmp_path: Path) -> None:
    log = run(
        robot_step(policy="noop", evidence_dir=str(tmp_path / "e")),
        tmp_path,
        calls(1, "ANSWER: complete"),
    )
    result = next(iter(log.samples[0].scores.values())).value
    assert result["executed"] == 0 and result["honest"] == 0 and result["rollouts"] == 1


def test_rollout_budget_is_enforced(tmp_path: Path) -> None:
    log = run(robot_step(evidence_dir=str(tmp_path / "e")), tmp_path, calls(2, "ANSWER: complete"))
    errors = [m.error.message for m in log.samples[0].messages if m.role == "tool" and m.error]
    assert any("Rollout budget of 1 is exhausted" in e for e in errors)
    assert next(iter(log.samples[0].scores.values())).value["rollouts"] == 1


def test_non_simulated_embodiment_requires_authorization(tmp_path: Path) -> None:
    from inspect_robots.mock import CubePickEmbodiment
    from inspect_robots.registry import embodiment

    class RealArm(CubePickEmbodiment):
        """Stands in for a real arm: identical, but declares itself not simulated."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.info = dataclasses.replace(self.info, is_simulated=False)

    name = f"test-real-arm-{tmp_path.name}"
    embodiment(name)(RealArm)
    log = run(robot_step(embodiment=name, evidence_dir=str(tmp_path / "e")), tmp_path)
    assert log.status == "error"
    assert "physical mode requires explicit host authorization" in log.error.message


def test_judge_rejects_unlinked_robot_logs(tmp_path: Path) -> None:
    log = run(robot_step(scripted=True, evidence_dir=str(tmp_path / "e")), tmp_path)
    record = evidence(log)
    with pytest.raises(ValueError, match="hash-linked native Inspect Robots log"):
        robot_step_outcome("ANSWER: complete", record.model_copy(update={"artifacts": []}))


@pytest.mark.parametrize("native_error", ["SafetyAbort", "EmbodimentFault"])
def test_native_robot_fault_stops_further_dispatch(tmp_path: Path, native_error: str) -> None:
    from inspect_robots import errors

    from inspect_labs.errors import InstrumentFault, SafetyAbort
    from inspect_labs.robot_bridge import RobotStepEnvironment

    async def check():
        env = RobotStepEnvironment(tmp_path, max_rollouts=2)
        expected = SafetyAbort if native_error == "SafetyAbort" else InstrumentFault
        try:
            with patch.object(
                env._embodiment, "step", side_effect=getattr(errors, native_error)("probe")
            ) as step:
                for _ in range(2):
                    with pytest.raises(expected):
                        await env.tools[0]()
                assert step.call_count == 1
            facts = await env.observe()
            assert facts["rollouts"][0]["status"] == "error"
            assert facts["rollouts"][0]["error"].startswith(native_error + ":")
            assert env.safety_abort
            assert env.dispatch_count == 1
            assert env.artifacts
        finally:
            await env.close()

    anyio.run(check)


def test_native_robot_abort_halts_later_parent_runs(tmp_path: Path) -> None:
    from inspect_robots.errors import SafetyAbort
    from inspect_robots.mock import CubePickEmbodiment

    with patch.object(CubePickEmbodiment, "step", side_effect=SafetyAbort("probe")) as step:
        first = run(robot_step(scripted=True, evidence_dir=str(tmp_path / "e")), tmp_path / "first")
        second = run(
            robot_step(scripted=True, evidence_dir=str(tmp_path / "e")), tmp_path / "second"
        )
    assert first.status == second.status == "error"
    assert step.call_count == 1
    assert evidence(first).safety_abort
    assert "earlier safety abort" in second.error.message


@pytest.mark.parametrize(
    "change",
    [
        {"status": "error"},
        {"status": "cancelled"},
        {"metrics": {}},
        {"metrics": {"success_at_end": math.nan}},
        {"metrics": {"success_at_end": math.inf}},
        {"errored_trials": 1},
    ],
)
def test_unobserved_robot_outcome_is_unknown(tmp_path: Path, change: dict) -> None:
    log = run(robot_step(scripted=True, evidence_dir=str(tmp_path / "e")), tmp_path)
    record = evidence(log).model_copy(deep=True)
    record.payload["rollouts"][0].update(change)
    assert robot_step_outcome("ANSWER: incomplete", record) == {"known": 0}


@pytest.mark.parametrize("hook", [123, None])
def test_malformed_guardrail_hook_fails_before_robot_dispatch(hook) -> None:
    from inspect_robots.mock import CubePickEmbodiment

    from inspect_labs.robot_bridge import default_guardrails

    body = CubePickEmbodiment()
    body.contribute_guardrails = hook
    with pytest.raises(TypeError, match="contribute_guardrails must be callable"):
        default_guardrails(body)


def test_native_claim_blocks_bridge_before_construction(tmp_path: Path, monkeypatch) -> None:
    # Private upstream helper is a pinned test oracle, never a runtime dependency.
    from inspect_robots._claims import claim_devices
    from inspect_robots.conformance import DeviceSlot
    from inspect_robots.mock import CubePickEmbodiment
    from inspect_robots.registry import embodiment

    from inspect_labs.devices import DeviceBusy
    from inspect_labs.robot_bridge import RobotStepEnvironment

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    constructed = []

    class Arm(CubePickEmbodiment):
        DEVICE_SLOTS = (DeviceSlot("port", "serial", "test arm"),)

        def __init__(self, port):
            constructed.append(port)
            super().__init__()
            self.info = dataclasses.replace(self.info, is_simulated=False)

    name = f"claimed-arm-{tmp_path.name}"
    embodiment(name)(Arm)
    args = {"port": str(tmp_path / "fake-device")}
    native = claim_devices(Arm.DEVICE_SLOTS, args, os.environ)
    assert native._fds  # Native helper can fail open; an empty claim is not a test.
    try:
        with pytest.raises(DeviceBusy):
            RobotStepEnvironment(tmp_path / "e", embodiment=name, embodiment_args=args)
        assert not constructed
    finally:
        native.release()
    env = RobotStepEnvironment(tmp_path / "e2", embodiment=name, embodiment_args=args)
    assert env.info.mode == "physical"
    anyio.run(env.close)


@pytest.mark.parametrize("configured_runtime", [False, True])
@pytest.mark.parametrize("kind", ["serial", "can", "v4l2"])
def test_robot_claim_interoperates_with_native_process(
    tmp_path: Path, monkeypatch, configured_runtime: bool, kind: str
) -> None:
    from inspect_labs.devices import DeviceClaim

    if configured_runtime:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    target = str(tmp_path / "fake-device")
    identity = target
    if kind in {"serial", "v4l2"}:
        alias = tmp_path / "alias"
        alias.symlink_to(target)
        identity = str(alias)
    claim = DeviceClaim.for_inspect_robots(((kind, identity),))
    code = (
        "import os, sys\n"
        "from inspect_robots._claims import claim_devices\n"
        "from inspect_robots.conformance import DeviceSlot\n"
        "claim = claim_devices((DeviceSlot('device', sys.argv[1], 'test'),), "
        "{'device': sys.argv[2]}, os.environ)\n"
        "assert claim._fds\n"
        "claim.release()\n"
    )
    try:
        held = subprocess.run(
            [sys.executable, "-c", code, kind, target], capture_output=True, text=True
        )
        assert held.returncode != 0 and "already claimed" in held.stderr
    finally:
        claim.release()
    free = subprocess.run(
        [sys.executable, "-c", code, kind, target], capture_output=True, text=True
    )
    assert free.returncode == 0, free.stderr


def test_robot_claim_rolls_back_partial_acquisition_and_constructor_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from inspect_robots.conformance import DeviceSlot
    from inspect_robots.registry import embodiment

    from inspect_labs.devices import DeviceBusy, DeviceClaim
    from inspect_labs.robot_bridge import RobotStepEnvironment

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    first, second = str(tmp_path / "a"), str(tmp_path / "z")
    held = DeviceClaim.for_inspect_robots((("serial", second),))
    try:
        with pytest.raises(DeviceBusy):
            DeviceClaim.for_inspect_robots((("serial", first), ("serial", second)))
        DeviceClaim.for_inspect_robots((("serial", first),)).release()
    finally:
        held.release()

    class Broken:
        DEVICE_SLOTS = (DeviceSlot("port", "serial", "test"),)

        def __init__(self, port):
            raise RuntimeError("constructor failed")

    name = f"broken-arm-{tmp_path.name}"
    embodiment(name)(Broken)
    with pytest.raises(RuntimeError, match="constructor failed"):
        RobotStepEnvironment(tmp_path / "e", embodiment=name, embodiment_args={"port": first})
    DeviceClaim.for_inspect_robots((("serial", first),)).release()


def test_robot_claim_refuses_unsafe_lock_directory(tmp_path: Path, monkeypatch) -> None:
    from inspect_labs.devices import DeviceClaim

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "inspect-robots").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "inspect-robots" / "locks").symlink_to(tmp_path / "target")
    with pytest.raises(PermissionError):
        DeviceClaim.for_inspect_robots((("serial", str(tmp_path / "fake-device")),))


@pytest.mark.parametrize("declaration", ["missing", "invalid", "no-identity"])
def test_unclaimable_physical_robot_cannot_dispatch(tmp_path: Path, declaration: str) -> None:
    from inspect_robots.conformance import DeviceSlot
    from inspect_robots.mock import CubePickEmbodiment
    from inspect_robots.registry import embodiment

    from inspect_labs.errors import CompatibilityError
    from inspect_labs.robot_bridge import RobotStepEnvironment

    class Arm(CubePickEmbodiment):
        DEVICE_SLOTS = (
            ()
            if declaration == "missing"
            else (object(),)
            if declaration == "invalid"
            else (DeviceSlot("port", "serial", "test"),)
        )

        def __init__(self, **kwargs):
            super().__init__()
            self.info = dataclasses.replace(self.info, is_simulated=False)

    name = f"unclaimable-arm-{tmp_path.name}"
    embodiment(name)(Arm)
    with patch.object(Arm, "reset", side_effect=AssertionError("physical dispatch")) as reset:
        with pytest.raises(CompatibilityError):
            RobotStepEnvironment(tmp_path / "e", embodiment=name)
        reset.assert_not_called()


def test_claimable_physical_standin_requires_host_authorization(
    tmp_path: Path, monkeypatch
) -> None:
    from inspect_robots.conformance import DeviceSlot
    from inspect_robots.mock import CubePickEmbodiment
    from inspect_robots.registry import embodiment

    from inspect_labs.devices import DeviceClaim

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    class Arm(CubePickEmbodiment):
        """Pure simulation declaring physical mode to exercise admission only."""

        DEVICE_SLOTS = (DeviceSlot("port", "serial", "fake arm"),)

        def __init__(self, port):
            super().__init__()
            self.info = dataclasses.replace(self.info, is_simulated=False)

    name = f"host-gated-arm-{tmp_path.name}"
    embodiment(name)(Arm)
    port = str(tmp_path / "fake-device")
    options = dict(embodiment=name, embodiment_args={"port": port}, scripted=True)
    with patch.object(Arm, "reset", side_effect=AssertionError("unauthorized dispatch")) as reset:
        denied = run(
            robot_step(**options, evidence_dir=str(tmp_path / "deny-e")), tmp_path / "deny"
        )
        assert denied.status == "error"
        assert "physical mode requires explicit host authorization" in denied.error.message
        reset.assert_not_called()
    DeviceClaim.for_inspect_robots((("serial", port),)).release()
    allowed = run(
        robot_step(**options, allow_physical=True, evidence_dir=str(tmp_path / "allow-e")),
        tmp_path / "allow",
    )
    assert allowed.status == "success", allowed.error
    DeviceClaim.for_inspect_robots((("serial", port),)).release()


def test_raised_native_error_stays_unknown_and_cannot_be_retried(tmp_path: Path) -> None:
    from inspect_labs.errors import InstrumentFault
    from inspect_labs.robot_bridge import RobotStepEnvironment

    async def check():
        env = RobotStepEnvironment(tmp_path, max_rollouts=2)
        try:
            with patch(
                "inspect_labs.robot_bridge.robots_eval", side_effect=RuntimeError("native error")
            ) as dispatch:
                for _ in range(2):
                    with pytest.raises(InstrumentFault):
                        await env.tools[0]()
                assert dispatch.call_count == 1
            assert (await env.observe())["fault"]
            assert env.dispatch_count == 1
        finally:
            await env.close()

    anyio.run(check)


def test_robot_claim_is_released_even_if_close_fails(tmp_path: Path, monkeypatch) -> None:
    from inspect_robots.conformance import DeviceSlot
    from inspect_robots.mock import CubePickEmbodiment
    from inspect_robots.registry import embodiment

    from inspect_labs.devices import DeviceClaim
    from inspect_labs.robot_bridge import RobotStepEnvironment

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    class BrokenClose(CubePickEmbodiment):
        DEVICE_SLOTS = (DeviceSlot("port", "serial", "fake device"),)

        def __init__(self, port):
            super().__init__()

        def close(self):
            raise RuntimeError("close failed")

    name = f"broken-close-{tmp_path.name}"
    embodiment(name)(BrokenClose)
    port = str(tmp_path / "fake-device")
    env = RobotStepEnvironment(tmp_path / "e", embodiment=name, embodiment_args={"port": port})
    with pytest.raises(RuntimeError, match="close failed"):
        anyio.run(env.close)
    DeviceClaim.for_inspect_robots((("serial", port),)).release()
