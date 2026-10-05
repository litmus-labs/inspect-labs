"""Journal, live approvals, operator control and tripwires on a served Lab."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import anyio
import pytest

from inspect_labs.actions import DEFAULT_RULES, Action
from inspect_labs.gateway import ActionRefused, Gateway
from inspect_labs.journal import Journal, check_witness, read_journal, witness_file
from inspect_labs.monitors import LIVE_MONITORS, repeated_refusals
from inspect_labs.operators import (
    ApprovalQueue,
    Control,
    ControlRequest,
    send_control,
    serve_control,
)
from inspect_labs.spec import OperationSpec

OPERATIONS = {
    "read": OperationSpec(action="read"),
    "dispense": OperationSpec(action="irreversible"),
}


@pytest.fixture
def short_dir():
    # Unix socket paths are limited to about 100 bytes, so keep them short.
    directory = Path(tempfile.mkdtemp(prefix="il-", dir="/tmp"))
    yield directory
    for path in sorted(directory.rglob("*"), reverse=True):
        path.unlink() if not path.is_dir() else path.rmdir()
    directory.rmdir()


async def done() -> str:
    return "done"


# Journal


def test_journal_entries_chain_and_check(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.append("started", {"lab": "x"})
    journal.append("ended", {})
    journal.close()
    checked = read_journal(tmp_path / "j.jsonl")
    assert [e.kind for e in checked.entries] == ["started", "ended"]
    assert checked.ended and not checked.torn_tail and checked.head == journal.head
    assert os.stat(tmp_path / "j.jsonl").st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        Journal(tmp_path / "j.jsonl")


def test_a_changed_entry_breaks_the_journal(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.append("started", {"lab": "x"})
    journal.append("ended", {})
    journal.close()
    lines = (tmp_path / "j.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    entry["body"]["lab"] = "y"
    (tmp_path / "j.jsonl").write_text(json.dumps(entry) + "\n" + lines[1] + "\n")
    with pytest.raises(ValueError, match="digest"):
        read_journal(tmp_path / "j.jsonl")


def test_a_crash_mid_write_leaves_a_readable_journal(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.append("started", {})
    journal.close()
    with (tmp_path / "j.jsonl").open("a") as stream:
        stream.write('{"sequence": 2, "at"')
    checked = read_journal(tmp_path / "j.jsonl")
    assert checked.torn_tail and not checked.ended and len(checked.entries) == 1


def test_a_witness_catches_a_rewritten_journal(tmp_path):
    witness = tmp_path / "elsewhere" / "witness.txt"
    journal = Journal(tmp_path / "j.jsonl", witness=witness_file(witness))
    journal.append("started", {"lab": "x"})
    journal.append("ended", {})
    journal.close()
    assert check_witness(read_journal(tmp_path / "j.jsonl"), witness) == []
    # Someone with write access rebuilds a consistent journal with different content.
    (tmp_path / "j.jsonl").unlink()
    forged = Journal(tmp_path / "j.jsonl")
    forged.append("started", {"lab": "other"})
    forged.append("ended", {})
    forged.close()
    problems = check_witness(read_journal(tmp_path / "j.jsonl"), witness)
    assert problems and "differs from its witnessed digest" in problems[0]


# Approval queue and gateway


def test_an_operator_approves_a_waiting_action(tmp_path):
    queue = ApprovalQueue(timeout=5)
    journal = Journal(tmp_path / "j.jsonl")
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, queue, journal=journal)

    async def scenario():
        async with anyio.create_task_group() as group:
            group.start_soon(gateway.run, "dispense", {"well": "A1"}, done)
            while not queue.pending():
                await anyio.sleep(0.01)
            (waiting,) = queue.pending()
            assert waiting.action.tool == "dispense"
            queue.answer(waiting.id, approved=True, by="alice")

    anyio.run(scenario)
    (record,) = gateway.records
    assert (record.status, record.approved, record.approved_by) == (
        "ran",
        True,
        "operator:alice",
    )
    journal.close()
    kinds = [e.kind for e in read_journal(tmp_path / "j.jsonl").entries]
    assert kinds == ["decided", "approval", "finished"]


def test_nobody_answering_refuses(tmp_path):
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, ApprovalQueue(timeout=0.05))
    with pytest.raises(ActionRefused):
        anyio.run(gateway.run, "dispense", {}, done)
    assert gateway.records[0].approved is False


def test_a_stop_while_waiting_wins_over_a_late_approval():
    queue = ApprovalQueue(timeout=5)
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, queue)
    outcome = {}

    async def attempt():
        try:
            await gateway.run("dispense", {}, done)
        except ActionRefused as exc:
            outcome["refused"] = str(exc)

    async def scenario():
        async with anyio.create_task_group() as group:
            group.start_soon(attempt)
            while not queue.pending():
                await anyio.sleep(0.01)
            (waiting,) = queue.pending()
            gateway.stop("Operator saw a problem")
            queue.answer(waiting.id, approved=True, by="bob")

    anyio.run(scenario)
    assert "stopped" in outcome["refused"]
    assert gateway.records[0].decision.rule == "stopped"


def test_a_failing_approver_refuses_and_is_recorded():
    def broken(action: Action) -> bool:
        raise RuntimeError("approval service down")

    gateway = Gateway(OPERATIONS, DEFAULT_RULES, broken)
    with pytest.raises(ActionRefused):
        anyio.run(gateway.run, "dispense", {}, done)
    assert gateway.records[0].status == "refused"


def test_answers_name_the_operator():
    queue = ApprovalQueue()
    with pytest.raises(ValueError):
        queue.answer("x", approved=True, by=" ")
    with pytest.raises(KeyError):
        queue.answer("missing", approved=True, by="alice")


# Control channel


def test_operators_use_the_control_socket(short_dir):
    queue = ApprovalQueue(timeout=5)
    gateway = Gateway(OPERATIONS, DEFAULT_RULES, queue)
    control = Control(gateway, queue, lambda: {"lab": "demo"})
    socket_path = short_dir / "control.sock"
    replies = {}

    def ask(**request):
        return send_control(socket_path, ControlRequest(**request))

    async def scenario():
        async with anyio.create_task_group() as group:
            group.start_soon(serve_control, control, socket_path)
            while not socket_path.exists():
                await anyio.sleep(0.01)
            assert os.stat(socket_path).st_mode & 0o777 == 0o600
            group.start_soon(gateway.run, "dispense", {"well": "B1"}, done)
            while not queue.pending():
                await anyio.sleep(0.01)
            pending = await anyio.to_thread.run_sync(lambda: ask(command="pending"))
            (waiting,) = pending["pending"]
            replies["anonymous"] = await anyio.to_thread.run_sync(
                lambda: ask(command="approve", id=waiting["id"])
            )
            replies["approve"] = await anyio.to_thread.run_sync(
                lambda: ask(command="approve", id=waiting["id"], by="carol")
            )
            while not gateway.records:
                await anyio.sleep(0.01)
            replies["stop"] = await anyio.to_thread.run_sync(
                lambda: ask(command="stop", by="carol", reason="End of shift")
            )
            replies["status"] = await anyio.to_thread.run_sync(lambda: ask(command="status"))
            group.cancel_scope.cancel()

    anyio.run(scenario)
    assert not replies["anonymous"]["ok"] and "by" in replies["anonymous"]["error"]
    assert replies["approve"]["ok"]
    assert gateway.records[0].approved_by == "operator:carol"
    assert replies["status"] | {} == {
        "ok": True,
        "lab": "demo",
        "stopped": "End of shift",
        "actions": 1,
        "refused": 0,
        "pending": 0,
    }
    assert not socket_path.exists()


def test_the_control_socket_needs_a_private_directory(short_dir):
    shared = short_dir / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    control = Control(Gateway(OPERATIONS, DEFAULT_RULES))
    with pytest.raises(PermissionError):
        anyio.run(serve_control, control, shared / "control.sock")


# Served sessions

pytest.importorskip("mcp")
pytest.importorskip("pylabrobot")

from inspect_labs.journal import read_journal as _read_journal  # noqa: E402
from inspect_labs.liquid import DeckLayout, Labware, WellContent  # noqa: E402
from inspect_labs.liquid_handling import LiquidHandlingEnvironment  # noqa: E402
from inspect_labs.serve import LabSession  # noqa: E402

LAYOUT = DeckLayout(
    labware=(
        Labware(name="tips", kind="tiprack_300ul", slot=1),
        Labware(name="plate", kind="plate_96_360ul", slot=2),
    ),
    contents={"plate": {"A1": WellContent(volume_ul=200, solutes={"dye": 2000})}},
)


def test_a_tripwire_stops_the_session_and_everything_is_journaled(tmp_path):
    journal = Journal(tmp_path / "session.journal.jsonl")
    session = LabSession(
        LiquidHandlingEnvironment(tmp_path / "lab", LAYOUT),
        DEFAULT_RULES,
        journal=journal,
        live_monitors=(*LIVE_MONITORS, repeated_refusals(2)),
        stop_on=frozenset({"repeated-refusals"}),
    )

    async def scenario():
        for _ in range(2):
            with pytest.raises(ActionRefused):
                await session.call("aspirate", {"labware": "plate", "well": "A1", "volume_ul": 5})
        with pytest.raises(ActionRefused, match="repeated-refusals"):
            await session.call("read_volume", {"labware": "plate", "well": "A1"})
        return await session.finish(tmp_path / "session.json")

    log = anyio.run(scenario)
    assert log.stopped and "repeated-refusals" in log.stopped
    assert "repeated-refusals" in [flag.monitor for flag in log.flags]
    checked = _read_journal(tmp_path / "session.journal.jsonl")
    kinds = [entry.kind for entry in checked.entries]
    assert kinds[0] == "started" and kinds[-1] == "ended" and "stopped" in kinds
    assert kinds.count("decided") == 3 and kinds.count("flag") >= 3
    # The lab log points at the journal as it was just before the lab log was written.
    assert log.journal_sha256 == checked.entries[-2].sha256


def test_cli_journals_a_served_session(tmp_path):
    from mcp import Client, StdioServerParameters

    lab_log = tmp_path / "cli-session.json"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "inspect_labs.cli", "serve", "--lab", "liquid-handler"]
        + ["--lab-dir", str(tmp_path / "lab"), "--lab-log", str(lab_log)]
        + ["--refusal-limit", "1", "--witness-file", str(tmp_path / "witness.txt")],
    )

    async def scenario():
        async with Client(parameters) as client:
            refused = await client.call_tool(
                "aspirate", {"labware": "plate", "well": "A1", "volume_ul": 10}
            )
            stopped = await client.call_tool("read_volume", {"labware": "plate", "well": "A1"})
        return refused, stopped

    refused, stopped = anyio.run(scenario)
    assert refused.is_error and stopped.is_error and "stopped" in stopped.content[0].text.lower()
    result = subprocess.run(
        [sys.executable, "-m", "inspect_labs.cli", "journal"]
        + [str(lab_log.with_suffix(".journal.jsonl")), "--witness", str(tmp_path / "witness.txt")],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["ended"] and report["witness_problems"] == []
