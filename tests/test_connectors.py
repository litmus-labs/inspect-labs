"""Connectors behind the gateway: pinned definitions, classification and screening."""

import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")

from inspect_ai import Task, eval  # noqa: E402
from inspect_ai.dataset import Sample  # noqa: E402
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model  # noqa: E402
from inspect_ai.solver import generate  # noqa: E402
from inspect_ai.tool import ToolCall  # noqa: E402

from inspect_labs.actions import DEFAULT_RULES  # noqa: E402
from inspect_labs.bindings import connect_lab, read_lab_logs  # noqa: E402
from inspect_labs.connectors import (  # noqa: E402
    Classification,
    ConnectorLab,
    ConnectorServer,
    ScreenVerdict,
    snapshot_connector,
)
from inspect_labs.gateway import ActionRefused  # noqa: E402
from inspect_labs.serve import LabSession  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "fake_connector.py"
SERVER = ConnectorServer(
    command=sys.executable,
    args=(str(FIXTURE),),
    env=("FAKE_CONNECTOR_CHANGED", "FAKE_CONNECTOR_CHANGE_FLAG"),
)
TEMPLATE = {
    "search_literature": Classification(action="read", note="Queries only"),
    "get_figure": Classification(action="read"),
    "lookup_record": Classification(action="read"),
    "order_sequence": Classification(
        action="external", sequence_arguments=("sequence",), note="Places an order"
    ),
}
SEQUENCE = "ATGGCTAGCAAAGGAGAAGAACTTTTCACTGGAGTTGTCCCAATTCTTGTTGAATTAGATGG"


@pytest.fixture(scope="module")
def profile():
    return anyio.run(lambda: snapshot_connector("fake-bio", SERVER, template=TEMPLATE))


def screener(outcome):
    async def screen(sequence: str) -> ScreenVerdict:
        return ScreenVerdict(outcome=outcome, screener="test-screen", detail=f"{len(sequence)} nt")

    return screen


def test_a_snapshot_pins_and_classifies_tools(profile):
    assert set(profile.tools) == {
        "search_literature",
        "order_sequence",
        "get_figure",
        "lookup_record",
        "write_notebook_entry",
    }
    assert profile.tools["write_notebook_entry"].action is None
    assert all(len(tool.definition_sha256) == 64 for tool in profile.tools.values())


def test_unclassified_tools_are_not_offered(profile, tmp_path):
    lab = ConnectorLab(profile, tmp_path)
    assert set(lab.info.operations) == {
        "search_literature",
        "order_sequence",
        "get_figure",
        "lookup_record",
    }
    assert len(lab.tools) == 4
    assert "write_notebook_entry" in lab.info.notes


def session(profile, tmp_path, **kwargs):
    lab = ConnectorLab(profile, tmp_path / "lab", **kwargs)
    return LabSession(lab, DEFAULT_RULES, approver=lambda action: True)


def test_a_read_runs_and_is_recorded(profile, tmp_path):
    served = session(profile, tmp_path)

    async def scenario():
        text = await served.call("search_literature", {"query": "GFP folding"})
        log = await served.finish(tmp_path / "session.json")
        return text, log

    text, log = anyio.run(scenario)
    assert "PMID 1" in text
    (record,) = log.samples.values()
    (call,) = record.payload["calls"]
    assert call["tool"] == "search_literature" and call["result_excerpt"] == text
    assert len(call["result_sha256"]) == 64


@pytest.mark.parametrize(
    ("outcome", "rule"),
    [("flagged", "sequence-screen:flagged"), ("unknown", "sequence-screen:unknown")],
)
def test_a_sequence_that_is_not_clear_never_leaves(profile, tmp_path, outcome, rule):
    served = session(profile, tmp_path, screener=screener(outcome))
    with pytest.raises(ActionRefused, match=rule):
        anyio.run(served.call, "order_sequence", {"sequence": SEQUENCE, "name": "frag-1"})
    assert served.lab.calls == []


def test_without_a_screen_sequences_never_leave(profile, tmp_path):
    served = session(profile, tmp_path)
    with pytest.raises(ActionRefused, match="sequence-screen:unavailable"):
        anyio.run(served.call, "order_sequence", {"sequence": SEQUENCE, "name": "frag-1"})


def test_a_clear_sequence_still_needs_approval(profile, tmp_path):
    lab = ConnectorLab(profile, tmp_path / "lab", screener=screener("clear"))
    refused = LabSession(lab, DEFAULT_RULES)
    with pytest.raises(ActionRefused, match="needs a person's approval"):
        anyio.run(refused.call, "order_sequence", {"sequence": SEQUENCE, "name": "frag-1"})
    approved = session(profile, tmp_path / "ok", screener=screener("clear"))
    text = anyio.run(approved.call, "order_sequence", {"sequence": SEQUENCE, "name": "frag-1"})
    assert text.startswith("Order placed")
    record = approved.gateway.records[-1]
    assert (record.action.action_type, record.approved, record.status) == ("external", True, "ran")


def test_a_changed_tool_definition_is_refused(profile, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CONNECTOR_CHANGED", "1")
    served = session(profile, tmp_path)
    with pytest.raises(ActionRefused, match="connector:definition-changed"):
        anyio.run(served.call, "search_literature", {"query": "GFP"})
    assert served.lab.calls == []


def test_connector_tools_work_in_an_evaluation(profile, tmp_path):
    outputs = [
        ModelOutput.from_message(
            ChatMessageAssistant(
                content="",
                tool_calls=[
                    ToolCall(id="c1", function="search_literature", arguments={"query": "GFP"})
                ],
            )
        ),
        ModelOutput.from_content("mockllm/model", "ANSWER: PMID 1"),
    ]
    task = connect_lab(
        Task(dataset=[Sample(input="Find a paper on GFP")], solver=generate()),
        lab=lambda state: ConnectorLab(profile, tmp_path / state.uuid),
        scorer=lambda report, lab_log: {"known": 1, "correct": int("PMID 1" in report)},
        requires=frozenset({"connector:fake-bio"}),
        lab_log_dir=tmp_path / "lab-logs",
        rules=DEFAULT_RULES,
    )
    (log,) = eval(
        task,
        model=get_model("mockllm/model", custom_outputs=outputs),
        log_dir=str(tmp_path / "logs"),
        display="none",
    )
    assert log.status == "success", log.error
    (record,) = read_lab_logs(Path(log.location).with_suffix(".labs")).samples.values()
    assert record.actions[0].status == "ran"
    assert record.payload["calls"][0]["tool"] == "search_literature"


def test_built_in_templates_load_and_flag_writes():
    from inspect_labs.connectors import TEMPLATES, load_template

    names = sorted(path.stem for path in TEMPLATES.glob("*.json"))
    assert {"pubmed", "chembl", "biorxiv", "protocols-io", "synapse"} <= set(names)
    for name in names:
        assert load_template(name)
    protocols = load_template("protocols-io")
    assert protocols["search_public_protocols"].action == "read"
    assert protocols["set_protocol_steps"].action == "irreversible"
    # A proxy tool hides the real tool in its arguments, so it fails closed.
    assert load_template("synapse")["call_read_tool"].action == "irreversible"
    # A tool that hands work to another agent may write, so it is held.
    assert load_template("benchling")["benchling_agent"].action == "irreversible"
    with pytest.raises(LookupError, match="built-in"):
        load_template("no-such-connector")


def test_cli_snapshots_a_connector_for_review(tmp_path):
    import json
    import subprocess

    template = tmp_path / "template.json"
    # Misclassify the notebook write as a read: the server's own hints disagree.
    template.write_text(
        json.dumps(
            {
                "tools": {
                    "search_literature": {"action": "read"},
                    "write_notebook_entry": {"action": "read"},
                }
            }
        )
    )
    result = subprocess.run(
        [sys.executable, "-m", "inspect_labs.cli", "connector", "snapshot", "fake-bio"]
        + ["--command", sys.executable, "--arg", str(FIXTURE)]
        + ["--template", str(template), "--output", str(tmp_path / "profile.json")],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["tools"] == 5
    assert report["unclassified"] == ["get_figure", "lookup_record", "order_sequence"]
    assert report["server_hints_disagree"] == ["write_notebook_entry"]
    profile = json.loads((tmp_path / "profile.json").read_text())
    assert profile["tools"]["write_notebook_entry"]["server_hints"]["destructive"] is True


def test_a_tool_changed_mid_session_is_refused(profile, tmp_path, monkeypatch):
    flag = tmp_path / "changed"
    monkeypatch.setenv("FAKE_CONNECTOR_CHANGE_FLAG", str(flag))
    served = session(profile, tmp_path)

    async def scenario():
        first = await served.call("search_literature", {"query": "GFP"})
        flag.write_text("now")  # The connector changes the tool after the first call.
        with pytest.raises(ActionRefused, match="connector:definition-changed"):
            await served.call("search_literature", {"query": "GFP"})
        return first

    assert "PMID 1" in anyio.run(scenario)
    assert len(served.lab.calls) == 1


def test_a_change_between_the_check_and_the_call_is_caught(profile, tmp_path, monkeypatch):
    from inspect_ai.tool import ToolError

    flag = tmp_path / "changed"
    monkeypatch.setenv("FAKE_CONNECTOR_CHANGE_FLAG", str(flag))
    lab = ConnectorLab(profile, tmp_path / "lab")
    from inspect_ai.tool import ToolDef

    (search,) = [t for t in lab.tools if ToolDef(t).name == "search_literature"]
    flag.write_text("now")  # Changed after any check, before the call's own connection.
    with pytest.raises(ToolError, match="changed since it was reviewed"):
        anyio.run(lambda: search(query="GFP"))
    assert lab.calls[0]["error"] == "definition-changed"


def test_non_text_results_are_kept(profile, tmp_path):
    served = session(profile, tmp_path)
    text = anyio.run(served.call, "get_figure", {"id": "fig-1"})
    assert '"type": "image"' in text and "image/png" in text
    assert served.lab.calls[0]["result_chars"] == len(text) > 0


def test_argument_names_that_are_not_python_names_work(profile, tmp_path):
    from inspect_ai.tool import ToolDef

    served = session(profile, tmp_path)
    (tool,) = [t for t in served.lab.tools if ToolDef(t).name == "lookup_record"]
    assert set(ToolDef(tool).parameters.properties) == {"query_string", "class_"}
    text = anyio.run(served.call, "lookup_record", {"query_string": "P12345", "class_": "x"})
    assert text == "received class=x, query-string=P12345"


def test_sequences_in_a_list_are_each_screened(profile, tmp_path):
    seen = []

    async def screen(sequence: str) -> ScreenVerdict:
        seen.append(sequence)
        outcome = "flagged" if sequence.startswith("GGG") else "clear"
        return ScreenVerdict(outcome=outcome, screener="test-screen")

    served = session(profile, tmp_path, screener=screen)
    with pytest.raises(ActionRefused, match="sequence-screen:flagged"):
        anyio.run(served.call, "order_sequence", {"sequence": [SEQUENCE, "GGGCCC"], "name": "x"})
    assert seen == [SEQUENCE, "GGGCCC"] and served.lab.calls == []


@pytest.mark.parametrize("value", [{"seq": SEQUENCE}, [SEQUENCE, 7], 42])
def test_a_sequence_argument_that_cant_be_screened_is_refused(profile, tmp_path, value):
    served = session(profile, tmp_path, screener=screener("clear"))
    with pytest.raises(ActionRefused, match="sequence-screen:unscreenable"):
        anyio.run(served.call, "order_sequence", {"sequence": value, "name": "x"})
    assert served.lab.calls == []


def test_cli_serves_a_connector_with_a_screener(profile, tmp_path):
    from mcp import Client, StdioServerParameters

    profile_file = tmp_path / "profile.json"
    profile_file.write_text(profile.model_dump_json())
    screen = tmp_path / "screen.py"
    screen.write_text(
        "from inspect_labs.connectors import ScreenVerdict\n"
        "async def screen(sequence):\n"
        "    return ScreenVerdict(outcome='flagged', screener='file-screen')\n"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "inspect_labs.cli", "serve", "--connector", str(profile_file)]
        + ["--screener", f"{screen}:screen", "--lab-dir", str(tmp_path / "lab")]
        + ["--lab-log", str(tmp_path / "session.json")],
    )

    async def scenario():
        async with Client(parameters) as client:
            return await client.call_tool("order_sequence", {"sequence": SEQUENCE, "name": "x"})

    refused = anyio.run(scenario)
    assert refused.is_error and "sequence-screen:flagged" in refused.content[0].text


def test_an_explicit_null_is_passed_on_and_an_omitted_argument_is_not(profile, tmp_path):
    served = session(profile, tmp_path)
    with_null = anyio.run(served.call, "lookup_record", {"query_string": "P1", "class_": None})
    omitted = anyio.run(served.call, "lookup_record", {"query_string": "P1"})
    assert with_null == "received class=None, query-string=P1"
    assert omitted == "received query-string=P1"


def test_a_connector_is_refused_without_rules(profile, tmp_path):
    outputs = [
        ModelOutput.from_message(
            ChatMessageAssistant(
                content="",
                tool_calls=[
                    ToolCall(id="c1", function="search_literature", arguments={"query": "GFP"})
                ],
            )
        ),
        ModelOutput.from_content("mockllm/model", "done"),
    ]
    labs = []

    def make(state):
        labs.append(ConnectorLab(profile, tmp_path / state.uuid))
        return labs[-1]

    task = connect_lab(
        Task(dataset=[Sample(input="Find a paper")], solver=generate()),
        lab=make,
        scorer=lambda report, lab_log: {"known": 1, "correct": 1},
        requires=frozenset({"connector:fake-bio"}),
        lab_log_dir=tmp_path / "lab-logs",
    )
    (log,) = eval(
        task,
        model=get_model("mockllm/model", custom_outputs=outputs),
        log_dir=str(tmp_path / "logs"),
        display="none",
    )
    # Its own safety checks need the gateway, so it never runs without rules.
    assert log.status == "error"
    assert "own safety checks, which need rules" in log.samples[0].error.message
    assert all(lab.calls == [] for lab in labs)
