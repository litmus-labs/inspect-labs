"""Check a provider binding against the Lab contract before evaluation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from inspect_ai.tool import ToolDef
from pydantic import BaseModel, ConfigDict

from inspect_labs import plugins
from inspect_labs.bindings import Lab, LabInfo


class LabCheckReport(BaseModel):
    """Contract violations found for one provider binding; empty means none found."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: str
    violations: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """True when no checked contract clause was violated."""
        return not self.violations


async def check_lab(
    factory: Callable[[], Lab],
    dispatches: Callable[[], int],
    *,
    volatile: frozenset[str] = frozenset(),
    observation_timeout: float = 30,
) -> LabCheckReport:
    """Exercise construction, tool declaration, observation and close without actor calls.

    Actor tools are never invoked, so running the check dispatches nothing itself.
    Provider exceptions are reported as violations, not raised. A pass is evidence
    about these clauses only, not about physical behavior, provider enforcement,
    record completeness or the truth of declared capabilities.

    Args:
        factory: Builds one fresh binding, as `bind_task` would per native sample.
        dispatches: Trusted provider-side count of accepted work, for example
            submitted jobs or delivered artifacts. It must not come from the binding.
        volatile: Top-level observation keys that may legitimately change between
            reads without actor action, such as timestamps or live sensor values.
        observation_timeout: Bound for each observation, in seconds.

    Returns:
        Report naming each violated clause.
    """
    violations: list[str] = []
    before = dispatches()
    try:
        environment = factory()
    except Exception as exc:
        return LabCheckReport(
            environment="unconstructed",
            violations=(f"construction raised {type(exc).__name__}",),
        )
    if dispatches() != before:
        violations.append("construction dispatched provider work")
    name = type(environment).__name__
    try:
        info = environment.info
        if isinstance(info, LabInfo):
            name = info.name
        else:
            violations.append("info is not an LabInfo declaration")
    except Exception as exc:
        violations.append(f"info raised {type(exc).__name__}")
    try:
        tools = list(environment.tools)
    except Exception as exc:
        tools = []
        violations.append(f"tools raised {type(exc).__name__}")
    if not tools:
        violations.append("no actor tools are declared")
    names: list[str] = []
    for index, candidate in enumerate(tools):
        try:
            definition = ToolDef(candidate)
        except Exception as exc:
            # Native ToolDef rejects functions without a model-facing docstring.
            violations.append(
                f"tool {index} is not a documented native Inspect tool ({type(exc).__name__})"
            )
            continue
        names.append(definition.name)
        if not definition.description.strip():
            violations.append(f"tool {definition.name} has no description for the model")
    if len(set(names)) != len(names):
        violations.append("tool names are not unique")
    observations: list[object] = []
    for _ in range(2):
        mark = dispatches()
        try:
            with anyio.fail_after(observation_timeout):
                observations.append(await environment.observe())
        except Exception as exc:
            violations.append(f"observe raised {type(exc).__name__} before any actor action")
            break
        if dispatches() != mark:
            violations.append("observe dispatched provider work")
    for observation in observations:
        if observation is not None and not isinstance(observation, dict):
            violations.append("observe must return a JSON object or None")
            break
        try:
            json.dumps(observation, allow_nan=False)
        except (TypeError, ValueError):
            violations.append("observation is not strict JSON")
            break
    if len(observations) == 2 and _stable(observations[0], volatile) != _stable(
        observations[1], volatile
    ):
        violations.append("repeated observation changed without actor action")
    try:
        for artifact in environment.artifacts:
            if not isinstance(artifact, Path) or not artifact.is_file():
                violations.append(f"artifact is not an existing file: {artifact}")
    except Exception as exc:
        violations.append(f"artifacts raised {type(exc).__name__}")
    mark = dispatches()
    try:
        await environment.close()
    except Exception as exc:
        violations.append(f"close raised {type(exc).__name__}")
    if dispatches() != mark:
        violations.append("close dispatched provider work")
    return LabCheckReport(environment=name, violations=tuple(violations))


def _stable(observation: object, volatile: frozenset[str]) -> object:
    if isinstance(observation, dict):
        return {key: value for key, value in observation.items() if key not in volatile}
    return observation


__all__ = ["LabCheckReport", "check_lab", "diagnose"]


async def diagnose(kind: str, name: str) -> dict[str, Any]:
    """Inspect component declarations without constructing a provider or backend.

    Loads the component by name, reports missing runtime requirements with their
    install commands and declared device slots. Importing an installed plugin is
    trusted code; it must not perform I/O at import time. Lifecycle conformance is
    separate: call `check_lab` explicitly with an appropriate test instance.

    Args:
        kind: ``lab`` or ``backend`` (``environment`` is accepted as a legacy alias).
        name: Registered component name.

    Returns:
        JSON-serializable report; ``ok`` is False when any problem was found.
    """
    report: dict[str, Any] = {"kind": kind, "name": name, "problems": [], "warnings": []}
    try:
        component_kind = plugins.canonical(kind)
    except ValueError:
        report["problems"].append(f"unknown kind {kind!r}")
        return {**report, "ok": False}
    report["kind"] = component_kind
    try:
        component = plugins.factory(component_kind, name)
    except LookupError as exc:
        report["problems"].append(str(exc))
        return {**report, "ok": False}
    except ImportError as exc:
        report["problems"].append(f"cannot load: {type(exc).__name__}: {exc}")
        return {**report, "ok": False}
    if not callable(component):
        report["problems"].append("component is not a callable factory")
        return {**report, "ok": False}
    missing = plugins.missing_runtime_requirements(component)
    report["missing_requirements"] = missing
    report["device_slots"] = [
        {"arg": slot.arg, "kind": slot.kind, "label": slot.label}
        for slot in plugins.device_slots(component)
    ]
    report["problems"] += [f"missing module {m}: {cmd}" for m, cmd in missing.items()]
    report["conformance"] = "not_run"
    report["warnings"].append(
        "Declaration checks only; use check_lab with an explicitly selected "
        "test instance for lifecycle conformance"
    )
    return {**report, "ok": not report["problems"]}


# Earlier names, kept for one release.
ConformanceReport = LabCheckReport
"""Earlier name of `LabCheckReport`."""
check_environment = check_lab
"""Earlier name of `check_lab`."""
