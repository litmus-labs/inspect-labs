"""Inspect AI tool binding for a bounded native Inspect Robots readiness rollout."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

import anyio
from inspect_ai.tool import Tool, tool
from inspect_robots import Scene, Task, eval, success_at_end
from inspect_robots.mock import CubePickEmbodiment, ScriptedPolicy
from inspect_robots.rollout import TrialRecord
from pydantic import JsonValue

from inspect_labs.bindings import EnvironmentInfo
from inspect_labs.environments import HandoffEnvironment


class RobotHandoffEnvironment(HandoffEnvironment):
    """Gate report transfer on observed mock station readiness, retaining native child logs.

    CubePick moves an end effector toward a target. It does not move containers.
    Inspect Robots owns the policy/embodiment loop and its native action records.
    """

    info = EnvironmentInfo(
        name="litmus-robot-report-handoff",
        version="1",
        mode="simulation",
        capabilities=frozenset({"handoff", "content_identity", "robot_readiness"}),
    )

    def __init__(self, directory: Path, artifact: str, destination: str, content: bytes) -> None:
        super().__init__(directory, artifact, destination, content)
        self.ready = None
        self._attempted = False
        self._child_dir = directory / "robot"
        self._readiness: dict[str, JsonValue] | None = None
        self._rollout: Literal["not_attempted", "verified", "failed"] = "not_attempted"

    @property
    def tools(self) -> list[Tool]:
        """Offer readiness as a native Inspect tool; transfer remains separately authorized."""

        @tool
        def prepare_station() -> Tool:
            async def execute() -> str:
                """Run one bounded mock robot readiness rollout, or return its prior observation."""
                if not self._attempted:
                    self._attempted = True
                    # Native rollout is synchronous. Wait for its bounded completion
                    # even if the parent is cancelled; do not abandon a running thread.
                    await anyio.to_thread.run_sync(self._prepare)
                return f"observed_ready={self.ready}"

            return execute

        return [prepare_station(), *super().tools]

    def _prepare(self) -> None:
        self._child_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        measured: dict[str, list[float]] = {}

        def collect(record: TrialRecord, scene: Scene) -> None:
            if record.status == "success" and record.steps:
                observed = record.steps[-1].result.observation.state
                measured["effector"] = [float(value) for value in observed["eef_pos"]]
                measured["target"] = [float(value) for value in observed["cube_pos"]]

        embodiment = CubePickEmbodiment()
        try:
            log = eval(
                Task(
                    name="lab-station-readiness",
                    scenes=[Scene(id="station", instruction="reach the cube", init_seed=0)],
                    scorer=success_at_end(),
                    max_steps=80,
                ),
                ScriptedPolicy(),
                embodiment,
                log_dir=str(self._child_dir),
                seed=0,
                before_scoring=collect,
            )[0]
            # Compute locally and publish once: a parallel transfer call must never
            # observe readiness before its native child records are verified.
            ready: bool | None = None
            if log.status == "success" and measured:
                ready = math.dist(measured["effector"], measured["target"]) <= 0.05
            evidence = self._child_dir / "readiness.json"
            evidence.write_text(
                json.dumps(
                    {"source": "cubepick-observation/v1", "positions": measured, "ready": ready}
                )
                + "\n"
            )
            native_logs = [path for path in self._child_dir.glob("*.json") if path != evidence]
            actions: list[JsonValue] = [str(p.resolve()) for p in self._child_dir.rglob("*.jsonl")]
            if ready is not None and len(native_logs) == 1 and actions:
                self._readiness = {
                    "effector": list(measured["effector"]),
                    "target": list(measured["target"]),
                    "native_log": str(native_logs[0].resolve()),
                    "observation_file": str(evidence.resolve()),
                    "actions": actions,
                }
                self._rollout = "verified"
                self.ready = ready
            else:
                # The child rollout failed or lacks native records: an infrastructure
                # outcome, recorded separately so it is not scored as actor failure.
                self._rollout = "failed"
            # Child artifacts are evaluator records even when native defaults differ.
            for path in self._child_dir.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)
        finally:
            embodiment.close()

    @property
    def artifacts(self) -> list[Path]:
        """Link native robot JSON, action sidecars and independent readiness coordinates."""
        return [
            *super().artifacts,
            *sorted(path for path in self._child_dir.rglob("*") if path.is_file()),
        ]

    @property
    def dispatch_count(self) -> int:
        """Delivered reports plus native robot rollouts that were started."""
        return super().dispatch_count + int(self._attempted)

    async def observe(self) -> dict[str, JsonValue]:
        """Join authorized transfer with independent mock robot readiness coordinates."""
        facts = await super().observe()
        facts["robot_observation"] = self._readiness
        facts["robot_rollout"] = self._rollout
        return facts


def robot_handoff_environment(
    directory: Path,
    *,
    artifact: str = "report-1",
    destination: str = "analysis",
    content: str = "counts=5\n",
) -> RobotHandoffEnvironment:
    """Registered environment factory: report handoff gated on an Inspect Robots rollout."""
    return RobotHandoffEnvironment(directory, artifact, destination, content.encode())
