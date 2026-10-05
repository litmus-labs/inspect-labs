"""Domain checks and leases in the shared gateway."""

from datetime import UTC, datetime, timedelta

import anyio
import pytest

from inspect_labs.actions import (
    DEFAULT_RULES,
    Action,
    ActionRecord,
    Decision,
    replay_decisions,
)
from inspect_labs.gateway import (
    ActionRefused,
    Approval,
    Gateway,
    Lease,
    first_approval,
    leased_actions,
)
from inspect_labs.spec import OperationSpec, ParameterSpec

OPERATIONS = {
    "read_level": OperationSpec(action="read"),
    "set_pump": OperationSpec(
        action="irreversible",
        parameters={"speed": ParameterSpec(unit="percent", minimum=0, maximum=100)},
    ),
}
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def max_step(action: Action) -> Decision | None:
    """A plant-style gate: refuse pump changes above 80 percent."""
    if action.tool != "set_pump":
        return None
    speed = action.arguments.get("speed")
    if isinstance(speed, (int, float)) and speed > 80:
        return Decision(outcome="deny", rule="water:max-pump-speed", reason="Above 80 percent")
    return None


def run(gateway: Gateway, tool: str, **arguments: object) -> str:
    async def call() -> str:
        return "done"

    return anyio.run(gateway.run, tool, arguments, call)


def test_a_check_refuses_and_the_record_says_it_was_a_check():
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, lambda a: True, checks=[max_step])
    with pytest.raises(ActionRefused, match="water:max-pump-speed"):
        run(gateway, "set_pump", speed=90)
    assert run(gateway, "set_pump", speed=50) == "done"
    refused, ran = gateway.records
    assert refused.decision.source == "check" and refused.status == "refused"
    assert ran.decision.source == "rules" and ran.approved is True


def test_a_check_cannot_loosen_a_decision():
    def allow_everything(action: Action) -> Decision:
        return Decision(outcome="allow", rule="loose", reason="anything goes")

    gateway = Gateway(OPERATIONS, DEFAULT_RULES, checks=[allow_everything])
    with pytest.raises(ActionRefused, match="needs a person's approval"):
        run(gateway, "set_pump", speed=10)
    with pytest.raises(ActionRefused, match="declared-range"):
        run(gateway, "set_pump", speed=150)


def test_a_failing_check_refuses_the_action():
    def broken(action: Action) -> Decision | None:
        raise RuntimeError("sensor offline")

    async def async_hold(action: Action) -> Decision:
        return Decision(outcome="hold", rule="async", reason="later")

    gateway = Gateway(OPERATIONS, DEFAULT_RULES, checks=[broken])
    with pytest.raises(ActionRefused, match="check-error"):
        run(gateway, "read_level")
    held = Gateway(OPERATIONS, DEFAULT_RULES, checks=[async_hold])
    with pytest.raises(ActionRefused, match="async"):
        run(held, "read_level")


def test_replay_keeps_a_recorded_check_decision():
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, lambda a: True, checks=[max_step])
    with pytest.raises(ActionRefused):
        run(gateway, "set_pump", speed=90)
    (replayed,) = replay_decisions(gateway.records, DEFAULT_RULES, OPERATIONS)
    assert replayed.replayed.rule == "water:max-pump-speed" and not replayed.changed


def test_an_old_record_without_new_fields_hashes_the_same():
    gateway = Gateway(OPERATIONS, DEFAULT_RULES)
    run(gateway, "read_level")
    (record,) = gateway.records
    dumped = record.model_dump(mode="json", exclude_defaults=True)
    assert "approved_by" not in dumped and "source" not in dumped["decision"]
    assert ActionRecord.model_validate(dumped) == record


def lease(**changes: object) -> Lease:
    fields: dict[str, object] = {
        "name": "morning-shift",
        "tool": "set_pump",
        "match": {"pump": "P1"},
        "expires_at": NOW + timedelta(minutes=5),
        "uses": 2,
        "granted_by": "operator",
    }
    return Lease.model_validate(fields | changes)


def test_a_lease_approves_matching_actions_until_it_runs_out():
    clock = [NOW]
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, leased_actions([lease()], lambda: clock[0]))
    assert run(gateway, "set_pump", pump="P1", speed=20) == "done"
    with pytest.raises(ActionRefused):
        run(gateway, "set_pump", pump="P2", speed=20)
    assert run(gateway, "set_pump", pump="P1", speed=30) == "done"
    with pytest.raises(ActionRefused):
        run(gateway, "set_pump", pump="P1", speed=40)
    assert gateway.records[0].approved_by == "lease:morning-shift (operator)"
    assert gateway.records[1].approved is False and gateway.records[1].approved_by is None


def test_a_lease_expires():
    clock = [NOW + timedelta(minutes=5)]
    gateway = Gateway(
        OPERATIONS, DEFAULT_RULES, leased_actions([lease(uses=None)], lambda: clock[0])
    )
    with pytest.raises(ActionRefused):
        run(gateway, "set_pump", pump="P1", speed=20)


def test_a_lease_needs_a_timezone():
    with pytest.raises(ValueError):
        lease(expires_at=datetime(2026, 10, 3, 12))


def test_first_approval_asks_each_approver_in_order():
    def no(action: Action) -> bool:
        return False

    async def named(action: Action) -> Approval:
        return Approval(approved=True, by="desk")

    gateway = Gateway(OPERATIONS, DEFAULT_RULES, first_approval(no, named))
    run(gateway, "set_pump", speed=10)
    assert gateway.records[0].approved_by == "desk"
    nobody = Gateway(OPERATIONS, DEFAULT_RULES, first_approval(no))
    with pytest.raises(ActionRefused):
        run(nobody, "set_pump", speed=10)


def test_a_lab_with_malformed_checks_is_closed_and_refused(tmp_path):
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import generate

    from inspect_labs.bindings import LabInfo, connect_lab

    closed = []

    class BadChecksLab:
        info = LabInfo(name="bad", version="1", mode="computation", capabilities=frozenset())
        tools: list = []
        artifacts: list = []
        checks = "not a list of callables"

        async def observe(self):
            return {}

        async def close(self):
            closed.append(True)

    task = connect_lab(
        Task(dataset=[Sample(input="x")], solver=generate()),
        lab=lambda state: BadChecksLab(),
        scorer=lambda report, lab_log: {"known": 1, "correct": 1},
        requires=frozenset(),
        lab_log_dir=tmp_path / "lab-logs",
        rules=DEFAULT_RULES,
    )
    (log,) = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")
    assert log.status == "error" and "checks must be a list" in log.samples[0].error.message
    assert closed == [True]
