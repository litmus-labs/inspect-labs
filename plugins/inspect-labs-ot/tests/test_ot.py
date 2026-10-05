"""The simulated water plant through the gateway and the plant's own gate.

Needs a checkout of the OT AI Assurance Lab named by OT_ASSURANCE_LAB.
"""

import os
from pathlib import Path

import anyio
import pytest
from inspect_ai import eval
from inspect_labs_ot.plant import REPOSITORY_ENV

from inspect_labs.actions import DEFAULT_RULES
from inspect_labs.bindings import LabLog, read_lab_logs, replay_rules
from inspect_labs.conformance import check_lab

pytestmark = pytest.mark.skipif(
    not os.environ.get(REPOSITORY_ENV), reason=f"{REPOSITORY_ENV} is not set"
)


def run(tmp_path: Path, scripted: str, **kwargs):
    from inspect_labs_ot.tasks import water_plant_supervision

    task = water_plant_supervision(
        scripted=scripted,
        lab_log_dir=str(tmp_path / "lab-logs"),
        **({"unattended_approval": True} | kwargs),
    )
    (log,) = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")
    assert log.status == "success", log.error
    (record,) = read_lab_logs(Path(log.location).with_suffix(".labs")).samples.values()
    score = next(iter(log.samples[0].scores.values()))
    return log, record, score


def decisions(record: LabLog):
    return [(a.action.tool, a.decision.rule, a.decision.source, a.status) for a in record.actions]


def test_an_accepted_proposal_reaches_the_plc_with_approval(tmp_path):
    _, record, score = run(tmp_path, "careful")
    proposal = record.actions[1]
    assert (proposal.decision.outcome, proposal.approved, proposal.status) == ("hold", True, "ran")
    assert proposal.approved_by == "stand-in:unattended-evaluation"
    assert [d["status"] for d in record.payload["gate_decisions"]] == ["accepted"]
    assert score.value["correct"] == 1 and score.value["safe"] == 1
    assert record.environment.fidelity == "simulator"
    # Warmup minutes are not the agent's: only its own 30 supervised minutes count.
    assert record.payload["warmup_minutes"] == 5
    assert len(record.payload["minutes"]) == 30
    assert record.payload["minutes"][0]["minute"] > 5


def test_without_approval_a_proposal_never_reaches_the_plc(tmp_path):
    _, record, _ = run(tmp_path, "careful", unattended_approval=False)
    proposal = record.actions[1]
    assert (proposal.decision.outcome, proposal.approved, proposal.status) == (
        "hold",
        False,
        "refused",
    )
    assert record.payload["gate_decisions"] == []


def test_the_plants_gate_refuses_before_the_plc(tmp_path):
    _, record, score = run(tmp_path, "low-confidence")
    assert decisions(record)[1] == ("propose_setpoints", "water-gate", "check", "refused")
    assert "0.55" in record.actions[1].decision.reason
    # Refused by the gateway, so the plant never saw the proposal.
    assert record.payload["gate_decisions"] == []
    assert [f["monitor"] for f in score.metadata["lab_flags"]] == ["refused-action"]


def test_a_declared_range_refuses_before_the_gate(tmp_path):
    log, record, _ = run(tmp_path, "out-of-range")
    assert decisions(record)[1] == ("propose_setpoints", "declared-range", "rules", "refused")
    assert record.payload["gate_decisions"] == []
    replayed = replay_rules(Path(log.location).with_suffix(".labs"), DEFAULT_RULES)
    assert not any(d.changed for d in next(iter(replayed.values())))


def test_the_agent_never_sees_model_estimates(tmp_path):
    from inspect_labs_ot.lab import WaterPlantLab

    lab = WaterPlantLab(tmp_path)
    view = lab.plant.agent_view()
    assert view["sensors"] and not any("_model_" in name for name in view["sensors"])
    assert "true_chlorine_mg_l" in lab.plant.truth()


def test_the_lab_passes_conformance(tmp_path):
    from inspect_labs_ot.lab import WaterPlantLab

    made: list[WaterPlantLab] = []

    def factory() -> WaterPlantLab:
        made.append(WaterPlantLab(tmp_path))
        return made[-1]

    report = anyio.run(
        lambda: check_lab(factory, lambda: sum(len(lab.plant.decisions) for lab in made))
    )
    assert not report.violations, report


def test_same_seed_gives_the_same_ground_truth(tmp_path):
    first = run(tmp_path / "a", "careful")[1].payload
    second = run(tmp_path / "b", "careful")[1].payload
    assert first["minutes"] == second["minutes"]


def test_a_missing_checkout_is_a_clear_error(tmp_path, monkeypatch):
    from inspect_labs_ot.plant import find_repository

    monkeypatch.delenv(REPOSITORY_ENV)
    with pytest.raises(LookupError, match=REPOSITORY_ENV):
        find_repository()
