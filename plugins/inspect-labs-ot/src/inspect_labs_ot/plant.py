"""The OT AI Assurance Lab's water plant, run in-process with its own PLC and gate.

The OT AI Assurance Lab (https://github.com/xienanzheng/ot-ai-assurance-lab, MIT,
Nanzheng Xie) is not an installable package, so this module imports its simulator,
PLC and safety gate from a local checkout named by ``OT_ASSURANCE_LAB`` or passed
directly. It never imports the lab's web services.

`WaterPlant` closes the loop the lab's services close over the network: each
simulated minute the PLC reads the plant and sets its actuators. The supervisory
lease follows the lab's PLC service (``services/plc_control/app/main.py``): an
accepted change holds for a limited time, then the PLC restores the previous
setpoints.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import JsonValue

REPOSITORY_ENV = "OT_ASSURANCE_LAB"
"""Environment variable naming a local checkout of the OT AI Assurance Lab."""

TESTED_COMMIT = "c7361ceb812a8d92b45ebf5cdeb5be794f5bfe15"
"""The upstream commit this adapter was written and tested against."""

_import_lock = threading.Lock()


class Partner:
    """The upstream modules this adapter uses, loaded from one checkout."""

    def __init__(self, repository: Path) -> None:
        if not (repository / "services" / "plant_sim" / "app" / "simulator.py").is_file():
            raise FileNotFoundError(f"{repository} is not an OT AI Assurance Lab checkout")
        self.repository = repository
        with _import_lock:
            if str(repository) not in sys.path:
                sys.path.insert(0, str(repository))
            import importlib

            self.simulator: ModuleType = importlib.import_module("services.plant_sim.app.simulator")
            self.controller: ModuleType = importlib.import_module(
                "services.plc_control.app.controller"
            )
            self.models: ModuleType = importlib.import_module("shared.models")
            self.limits: ModuleType = importlib.import_module("shared.limits")
            self.supervision: ModuleType = importlib.import_module("shared.supervision")
        self.commit = _commit(repository)

    @property
    def setpoint_limits(self) -> dict[str, tuple[float, float]]:
        """Absolute setpoint ranges the upstream gate enforces, by setpoint name."""
        limits: dict[str, tuple[float, float]] = self.limits.SETPOINT_LIMITS
        return dict(limits)


def find_repository(repository: Path | str | None = None) -> Path:
    """The checkout to use: the argument, else ``OT_ASSURANCE_LAB``.

    Raises:
        LookupError: Neither names a checkout.
    """
    named = repository if repository is not None else os.environ.get(REPOSITORY_ENV)
    if not named:
        raise LookupError(
            f"Set {REPOSITORY_ENV} to a checkout of "
            "https://github.com/xienanzheng/ot-ai-assurance-lab"
        )
    return Path(named).expanduser().resolve()


def _commit(repository: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


HIDDEN_SENSORS = ("_model_",)
"""Sensor names containing these are process-model estimates (ground truth in the
simulator), so the agent never sees them."""


class WaterPlant:
    """One simulated water plant with its PLC and safety gate, stepped minute by minute.

    Args:
        partner: The loaded upstream modules.
        seed: Seed for the plant's sensor noise and scenario.
        scenario: An upstream water scenario, such as ``zone_leak``.
    """

    def __init__(self, partner: Partner, *, seed: int, scenario: str) -> None:
        self.partner = partner
        self.seed = seed
        self.scenario = scenario
        models = partner.models
        self.sim: Any = partner.simulator.WaterPlantSimulator(seed=seed)
        self.sim.reset(seed=seed, scenario=scenario)
        self.sim.controller_mode = models.ControlMode.GATED_AUTO
        self.plc: Any = partner.controller.BaselineController()
        self.gate: Any = partner.controller.SafetyGate(self.plc)
        self.minutes: list[dict[str, JsonValue]] = []
        self.decisions: list[dict[str, JsonValue]] = []
        self.hydraulic_fallbacks = 0

    def proposal(
        self, changes: dict[str, float | bool], *, confidence: float, explanation: str, effect: str
    ) -> Any:
        """An upstream ``ControlProposal``; raises ``ValueError`` on an invalid one."""
        models = self.partner.models
        return models.ControlProposal(
            changes=models.SetpointChanges(**changes),
            confidence=confidence,
            explanation=explanation,
            expected_effect=effect,
            source="manual",
        )

    def evaluate(self, proposal: Any) -> Any:
        """The upstream gate's decision on the current plant state. Changes nothing."""
        return self.gate.evaluate(proposal, self.sim.snapshot())

    def apply(self, proposal: Any, lease_minutes: int) -> Any:
        """Evaluate a proposal and, unless rejected, apply what the gate allows under a lease.

        Follows the upstream PLC service: the observation window and lease come
        from the upstream timing policy, capped by ``lease_minutes`` (1 to 30).
        """
        snapshot = self.sim.snapshot()
        decision = self.gate.evaluate(proposal, snapshot)
        if decision.status in {"accepted", "modified"}:
            changes = decision.applied_values.model_dump(exclude_none=True)
            lease = max(1, min(30, lease_minutes))
            window = self.partner.supervision.response_window("water", changes)
            self.plc.supervisory_timing = {
                "applied_minute": snapshot.elapsed_minutes,
                "observe_minutes": min(window["observe_minutes"], lease),
                "before_targets": self.plc.setpoint_dict(),
                "applied_targets": changes,
            }
            self.plc.apply_setpoint_changes(
                decision.applied_values,
                valid_until=snapshot.simulation_time + timedelta(minutes=lease),
                source=proposal.source,
            )
        self.decisions.append(
            {
                "minute": snapshot.elapsed_minutes,
                "status": decision.status,
                "violated": list(decision.violated_constraints),
                "modifications": list(decision.modifications),
                "applied": decision.applied_values.model_dump(mode="json", exclude_none=True),
            }
        )
        return decision

    def advance(self, minutes: int) -> None:
        """Let the PLC run the plant for ``minutes`` simulated minutes."""
        for _ in range(minutes):
            snapshot = self.sim.snapshot()
            command = self.plc.calculate(snapshot)
            self.sim.set_actuators(command.model_dump(exclude_none=True))
            self.sim.advance(1)
            if self.sim.hydraulic_error is not None:
                self.hydraulic_fallbacks += 1
            self.minutes.append(self.truth())

    def truth(self) -> dict[str, JsonValue]:
        """Ground truth for the evaluator, including values hidden from the agent."""
        snapshot = self.sim.snapshot()
        sensors = snapshot.sensors
        return {
            "minute": snapshot.elapsed_minutes,
            "safety_state": snapshot.safety_state,
            "emergency_stop": bool(snapshot.emergency_stop),
            "true_chlorine_mg_l": round(float(self.sim.true_chlorine_mg_l), 4),
            "clearwell_level_pct": round(float(self.sim.clearwell_level_pct), 4),
            "minimum_zone_pressure_m": round(
                min(float(sensors[f"zone_{zone}_pressure_m"].value) for zone in (1, 2, 3)), 4
            ),
            "critical_alarms": sorted(
                alarm.code for alarm in snapshot.active_alarms if alarm.severity == "critical"
            ),
        }

    def agent_view(self) -> dict[str, JsonValue]:
        """What the agent may see: sensors (without model estimates), setpoints and alarms."""
        snapshot = self.sim.snapshot()
        status = self.plc.status()
        return {
            "minute": snapshot.elapsed_minutes,
            "safety_state": snapshot.safety_state,
            "sensors": {
                name: {"value": round(float(value.value), 3), "unit": value.unit}
                | ({"quality": value.quality} if value.quality != "good" else {})
                for name, value in sorted(snapshot.sensors.items())
                if not any(hidden in name for hidden in HIDDEN_SENSORS)
            },
            "setpoints": {k: round(float(v), 3) for k, v in self.plc.setpoint_dict().items()},
            "lease_expires": status["setpoint_lease_expires"],
            "alarms": [
                {"code": alarm.code, "severity": alarm.severity, "message": alarm.message}
                for alarm in snapshot.active_alarms
            ],
        }
