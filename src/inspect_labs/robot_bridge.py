"""Inspect Robots bridge: any registered robot task, policy and embodiment as a lab step.

A lab agent (native Inspect AI) calls one tool that runs a bounded child evaluation in
Inspect Robots. Inspect Robots owns the task, policy, embodiment, control loop, logs
and scoring. Registered policies and simulated embodiments are used directly;
physical embodiments additionally need explicit supported native device slots.

Safety mirrors the Inspect Robots CLI rather than its bare programmatic default: the
bounds clamp, per-step delta limit and any embodiment-contributed guardrails are
installed from public approvers. An embodiment that is not simulated is ``physical``
and is refused unless the host authorizes it.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import anyio
from inspect_ai.tool import Tool, ToolError, tool
from inspect_robots import eval as robots_eval
from inspect_robots import registered
from inspect_robots.approver import (
    Approver,
    AutoApprover,
    ChainApprover,
    ClampApprover,
    DeltaLimitApprover,
    GuardrailContribution,
)
from inspect_robots.conformance import DeviceSlot
from inspect_robots.errors import SafetyAbort as NativeSafetyAbort
from inspect_robots.registry import resolve as robots_resolve
from pydantic import JsonValue

from inspect_labs.bindings import EnvironmentInfo
from inspect_labs.devices import DeviceClaim
from inspect_labs.errors import CompatibilityError, InstrumentFault, SafetyAbort

RUNTIME_REQUIREMENTS = {"inspect_robots": 'uv pip install "inspect-labs[robots]"'}


def default_guardrails(embodiment: Any) -> tuple[Approver, list[str], list[str]]:
    """Inspect Robots' default CLI safety chain, built from its public approvers.

    Returns:
        ``(approver, active guardrail names, warnings)``. Components that cannot apply
        to the action space are skipped with a warning, never silently.
    """
    space = embodiment.info.action_space
    parts: list[Approver] = []
    active: list[str] = []
    warnings: list[str] = []
    if space.low is None and space.high is None:
        warnings.append("bounds clamp skipped: the action space declares no bounds")
    else:
        parts.append(ClampApprover(space))
        active.append("clamp")
    try:
        parts.append(DeltaLimitApprover(space))
        active.append("delta-limit")
    except ValueError as exc:
        warnings.append(f"delta limit skipped: {exc}")
    absent = object()
    hook = getattr(embodiment, "contribute_guardrails", absent)
    if hook is not absent:
        if not callable(hook):
            raise TypeError("contribute_guardrails must be callable when declared")
        contribution = hook(space)
        if not isinstance(contribution, GuardrailContribution):
            raise TypeError("contribute_guardrails must return a GuardrailContribution")
        for name, approver in contribution.approvers:
            parts.append(approver)
            active.append(name)
        warnings.extend(contribution.warnings)
    if not parts:
        warnings.append("no guardrails are active for this action space")
        return AutoApprover(), active, warnings
    return ChainApprover(*parts), active, warnings


class RobotStepEnvironment:
    """One sample's robot step: a bounded Inspect Robots evaluation, run on request.

    Args:
        directory: Private per-sample evidence directory for child logs.
        task: Registered Inspect Robots task name.
        policy: Registered Inspect Robots policy name.
        embodiment: Registered Inspect Robots embodiment name. Its constructor must be
            hardware-free (Inspect Robots' adapter contract); it connects on reset.
        policy_args: Policy constructor arguments.
        embodiment_args: Embodiment constructor arguments.
        max_rollouts: Rollouts the agent may start in one sample.
        seed: Child evaluation seed.
        success_metric: The Inspect Robots metric that defines task success.
    """

    def __init__(
        self,
        directory: Path,
        *,
        task: str = "cubepick-reach",
        policy: str = "scripted",
        embodiment: str = "cubepick",
        policy_args: dict[str, Any] | None = None,
        embodiment_args: dict[str, Any] | None = None,
        max_rollouts: int = 1,
        seed: int = 0,
        success_metric: str = "success_at_end",
    ) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.task, self.policy, self.embodiment_name = task, policy, embodiment
        self.policy_args = policy_args or {}
        self.max_rollouts = max_rollouts
        self.seed = seed
        self.success_metric = success_metric
        factory = registered("embodiment").get(embodiment)
        if factory is None:
            raise CompatibilityError(f"No registered robot embodiment {embodiment!r}")
        args = embodiment_args or {}
        slots = getattr(factory, "DEVICE_SLOTS", ())
        if not isinstance(slots, tuple | list) or any(
            not isinstance(slot, DeviceSlot) or slot.kind not in {"serial", "v4l2", "can"}
            for slot in slots
        ):
            raise CompatibilityError("Robot DEVICE_SLOTS must declare valid native device slots")
        if len({slot.arg for slot in slots}) != len(slots):
            raise CompatibilityError("Robot DEVICE_SLOTS argument names must be unique")
        devices: list[tuple[str, str]] = []
        for slot in slots:
            value = args.get(slot.arg)
            if not isinstance(value, str) or not value.strip():
                raise CompatibilityError(f"Explicit robot device identity required: {slot.arg}")
            devices.append((slot.kind, value))
        self._claim = DeviceClaim.for_inspect_robots(tuple(devices)) if devices else None
        self._embodiment: Any = None
        try:
            self._embodiment = factory(**args)
            body = self._embodiment.info
            if not body.is_simulated and self._claim is None:
                raise CompatibilityError(
                    "physical mode requires explicit host authorization and claimed native "
                    "DEVICE_SLOTS; this embodiment has no claimable device identities"
                )
            self.info = EnvironmentInfo(
                name=f"inspect-robots:{body.name}",
                version=str(body.environment_revision or "unversioned"),
                mode="simulation" if body.is_simulated else "physical",
                capabilities=frozenset({"robot_rollout"}),
                notes=str(body.docs or ""),
            )
        except BaseException:
            try:
                close = getattr(self._embodiment, "close", None)
                if callable(close):
                    close()
            finally:
                if self._claim:
                    self._claim.release()
            raise
        self._rollouts: list[dict[str, JsonValue]] = []
        self._lock = anyio.Lock()
        self._attempts = 0
        self._failure: InstrumentFault | SafetyAbort | None = None
        self.safety_abort: str | None = None

    @property
    def tools(self) -> list[Tool]:
        """One tool that runs the configured robot task and reports its recorded outcome."""

        @tool(parallel=False)
        def run_robot_task() -> Tool:
            async def execute() -> str:
                """Run the configured robot task once under guardrails and report the result."""
                async with self._lock:
                    if self._failure is not None:
                        raise self._failure
                    if self._attempts >= self.max_rollouts:
                        raise ToolError(f"Rollout budget of {self.max_rollouts} is exhausted")
                    self._attempts += 1
                    child = self.directory / f"rollout-{self._attempts}"
                    # The native rollout is synchronous and bounded; wait for it to finish
                    # even if the parent is cancelled, rather than abandon a running robot.
                    try:
                        record = await anyio.to_thread.run_sync(self._rollout, child)
                    except BaseException as exc:
                        self.safety_abort = (
                            f"Robot execution raised {type(exc).__name__}; reconcile before retry"
                        )
                        self._failure = (
                            SafetyAbort(self.safety_abort)
                            if isinstance(exc, NativeSafetyAbort)
                            else InstrumentFault(self.safety_abort)
                        )
                        if not isinstance(exc, Exception):
                            raise  # Preserve cancellation/interrupt semantics while latching.
                        raise self._failure from exc
                    self._rollouts.append(record)
                    metrics = record["metrics"]
                    value = metrics.get(self.success_metric) if isinstance(metrics, dict) else None
                    if (
                        record["status"] != "success"
                        or record["errored_trials"] != 0
                        or not isinstance(value, int | float)
                        or isinstance(value, bool)
                        or not math.isfinite(value)
                    ):
                        self.safety_abort = (
                            "Robot outcome unavailable; reconcile child records before retry"
                        )
                        # The pinned native schema has string errors, not a typed category.
                        # Recognize its SafetyAbort prefix; all other failures stay faults.
                        error = record.get("error")
                        self._failure = (
                            SafetyAbort(self.safety_abort)
                            if isinstance(error, str) and error.startswith("SafetyAbort:")
                            else InstrumentFault(self.safety_abort)
                        )
                        raise self._failure
                    return f"status={record['status']}; metrics={record['metrics']}"

            return execute

        return [run_robot_task()]

    def _rollout(self, child: Path) -> dict[str, JsonValue]:
        child.mkdir(parents=True, mode=0o700, exist_ok=True)
        approver, active, warnings = default_guardrails(self._embodiment)
        policy = robots_resolve("policy", self.policy, **self.policy_args)
        log = robots_eval(
            self.task,
            policy,
            self._embodiment,
            log_dir=str(child),
            seed=self.seed,
            approver=approver,
        )[0]
        for path in child.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
        logs: list[JsonValue] = [str(p.resolve()) for p in sorted(child.glob("*.json"))]
        results = log.results
        metrics = {k: float(v) for k, v in (getattr(results, "metrics", None) or {}).items()}
        return {
            "task": self.task,
            "policy": self.policy,
            "embodiment": self.embodiment_name,
            "status": str(log.status),
            "error": log.error,
            "errored_trials": int(getattr(results, "errored_trials", 0) or 0),
            "trials": int(getattr(results, "total_trials", 0) or 0),
            "metrics": dict(metrics),
            "guardrails": list(active),
            "guardrail_warnings": list(warnings),
            "logs": logs,
        }

    async def observe(self) -> dict[str, JsonValue]:
        """Rollouts as recorded by Inspect Robots (status, metrics, guardrails, log paths)."""
        return {
            "rollouts": list(self._rollouts),
            "max_rollouts": self.max_rollouts,
            "success_metric": self.success_metric,
            "fault": str(self._failure) if self._failure else None,
        }

    @property
    def artifacts(self) -> list[Path]:
        """Native Inspect Robots logs and action records of every rollout."""
        return sorted(path for path in self.directory.rglob("*") if path.is_file())

    @property
    def dispatch_count(self) -> int:
        """Robot execution attempts admitted, including failed attempts."""
        return self._attempts

    async def close(self) -> None:
        """Release the embodiment; tracked records stay for evidence."""
        try:
            close = getattr(self._embodiment, "close", None)
            if callable(close):
                close()
        finally:
            if self._claim:
                self._claim.release()


def robot_step_environment(directory: Path, **options: Any) -> RobotStepEnvironment:
    """Registered environment factory for `RobotStepEnvironment`."""
    return RobotStepEnvironment(directory, **options)
