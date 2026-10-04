"""Connect an existing Fictionet task to Inspect Labs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from inspect_ai import Task
from inspect_ai.util import sandbox

from inspect_labs.actions import ActionRules
from inspect_labs.bindings import EvidenceJudge, connect_lab
from inspect_labs.gateway import Approver, Check
from inspect_labs.monitors import (
    Monitor,
    refused_actions,
    report_contradicts_lab_log,
    unobserved_outcome,
)
from inspect_labs_fictionet.lab import CLOSED_WORLD_RULES, FictionetWorldLab
from inspect_labs_fictionet.monitors import WORLD_MONITORS
from inspect_labs_fictionet.world import Sandbox, WorldLogSpec


def connect_world(
    task: Task,
    *,
    scorer: EvidenceJudge,
    lab_log_dir: Path,
    spec: WorldLogSpec | None = None,
    name: str = "fictionet-world",
    metrics: tuple[str, ...] = ("known", "correct"),
    rules: ActionRules = CLOSED_WORLD_RULES,
    approver: Approver | None = None,
    checks: Sequence[Check] = (),
    monitors: Sequence[Monitor] = (
        refused_actions,
        unobserved_outcome,
        report_contradicts_lab_log,
        *WORLD_MONITORS,
    ),
    sandboxes: Callable[[str | None], Sandbox] = sandbox,
) -> Task:
    """Give an unscored Fictionet task a gateway-checked shell and a world-log lab scorer.

    The task keeps its dataset, solver and sandbox (a ``fictionet_sandbox`` spec or a
    compose file with a separate world service). Remove the task's own shell tool:
    the Lab provides ``bash``, checked by the gateway.

    Args:
        task: An unscored native Fictionet task.
        scorer: Scores the agent's report against the lab log, whose payload holds the
            world's events, the log's digest and completeness, and per-command ranges.
        lab_log_dir: Private directory for lab logs and world log copies.
        spec: Where the world keeps its log; Border-style compose files use
            ``WorldLogSpec(service="fictionet")``.
        name: The Lab's name in the lab log.
        metrics: Every key the scorer may return; must include known and correct.
        rules: Action rules; by default shell commands inside the closed world run.
        approver: Called for held actions.
        checks: Domain checks run after the rules.
        monitors: Run after scoring. By default the starter monitors, except the one
            for actions without approval (commands inside the closed world are
            allowed), plus the world-label monitors.
        sandboxes: Looks up a sandbox by name; Inspect's ``sandbox`` by default.
    """
    directory = lab_log_dir.resolve()
    return connect_lab(
        task,
        lab=lambda state: FictionetWorldLab(
            directory / state.uuid, spec=spec, name=name, sandboxes=sandboxes
        ),
        scorer=scorer,
        requires=frozenset({"simulated_internet"}),
        lab_log_dir=directory,
        metrics=metrics,
        rules=rules,
        approver=approver,
        checks=checks,
        monitors=monitors,
    )
