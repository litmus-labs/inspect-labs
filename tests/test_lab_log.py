"""Tamper-evident lab logs (schema 3) and replaying a policy change without running anything."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, Model, ModelOutput, get_model
from inspect_ai.solver import generate
from inspect_ai.tool import ToolCall

from inspect_labs import rescore_workflow
from inspect_labs.actions import DEFAULT_ACTION_POLICY, ActionPolicy, Rule
from inspect_labs.bindings import (
    WorkflowEvidence,
    bind_task,
    lab_log_digest,
    read_lab_logs,
    replay_action_policy,
)
from inspect_labs.liquid import DeckLayout, Labware, WellContent

pytest.importorskip("pylabrobot")
from inspect_labs.liquid_handling import LiquidHandlingEnvironment  # noqa: E402

LAYOUT = DeckLayout(
    labware=(
        Labware(name="tips", kind="tiprack_300ul", slot=1),
        Labware(name="plate", kind="plate_96_360ul", slot=2),
    ),
    contents={"plate": {"A1": WellContent(volume_ul=200, solutes={"dye": 2000})}},
)
STEPS = [
    ("read_volume", {"labware": "plate", "well": "A1"}),
    ("pick_up_tip", {"rack": "tips", "position": "A1"}),
    ("aspirate", {"labware": "plate", "well": "A1", "volume_ul": 50}),
]
PERMISSIVE = ActionPolicy(
    version="permissive-test",
    rules=(
        Rule(
            name="allow-all-declared",
            action_types=frozenset({"read", "reversible", "irreversible", "external"}),
            outcome="allow",
            reason="Test policy that allows every declared action",
        ),
    ),
)


def scorer(report, lab_log):
    return {"known": 1, "correct": int(report.strip().endswith("complete"))}


def _model():
    outputs = [
        ModelOutput.from_message(
            ChatMessageAssistant(
                content="", tool_calls=[ToolCall(id=f"c{i}", function=fn, arguments=args)]
            )
        )
        for i, (fn, args) in enumerate(STEPS)
    ]
    outputs.append(ModelOutput.from_content("mockllm/model", "ANSWER: complete"))
    return get_model("mockllm/model", custom_outputs=outputs)


@pytest.fixture
def run(tmp_path):
    task = bind_task(
        Task(dataset=[Sample(input="Move 50 uL")], solver=generate()),
        environment=lambda state: LiquidHandlingEnvironment(tmp_path / state.uuid, LAYOUT),
        scorer=scorer,
        requires=frozenset({"liquid_handling"}),
        evidence_dir=tmp_path / "evidence",
        action_policy=DEFAULT_ACTION_POLICY,
    )
    log = eval(task, model=_model(), log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    native = Path(log.location)
    return native, native.with_suffix(".labs")


def _edit(labs: Path, change) -> None:
    document = json.loads(labs.read_text())
    (sample,) = document["samples"].values()
    change(sample)
    labs.write_text(json.dumps(document))


def test_new_lab_logs_are_chained_and_linked_from_the_native_log(run):
    native, labs = run
    bundle = read_lab_logs(labs)
    assert bundle.schema_version == 3
    (record,) = bundle.samples.values()
    assert record.chain_sha256 == lab_log_digest(record)
    assert len(record.actions) == 3
    scores = read_eval_log(str(native)).samples[0].scores
    assert {s.metadata["lab_log_sha256"] for s in scores.values()} == {record.chain_sha256}


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["actions"][2].update(status="ran"),
        lambda s: s["actions"].pop(0),
        lambda s: s["payload"].update(tampered=True),
    ],
    ids=["edited-action", "removed-action", "edited-observation"],
)
def test_editing_a_lab_log_makes_rescoring_refuse_it(run, tmp_path, change):
    native, labs = run
    _edit(labs, change)
    with pytest.raises(ValueError, match="Lab log hash mismatch"):
        rescore_workflow(native, labs, tmp_path / "rescored.eval", scorer)


def test_a_rewritten_chain_still_disagrees_with_the_native_log(run, tmp_path):
    native, labs = run

    def rewrite(sample):
        sample["actions"][2]["status"] = "ran"

    _edit(labs, rewrite)
    document = json.loads(labs.read_text())
    bundle = WorkflowEvidence.model_validate(document)
    (uuid, record) = next(iter(bundle.samples.items()))
    document["samples"][uuid]["chain_sha256"] = lab_log_digest(record)
    labs.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="does not match the digest in the native log"):
        rescore_workflow(native, labs, tmp_path / "rescored.eval", scorer)


def test_schema_2_lab_logs_still_rescore(run, tmp_path):
    native, labs = run
    document = json.loads(labs.read_text())
    document["schema_version"] = 2
    for sample in document["samples"].values():
        sample.pop("chain_sha256")
        sample.pop("actions")
    labs.write_text(json.dumps(document))
    rescore_workflow(native, labs, tmp_path / "rescored.eval", scorer)
    assert read_eval_log(str(tmp_path / "rescored.eval")).status == "success"


def test_replaying_a_policy_change_runs_nothing(run):
    _, labs = run
    with (
        patch.object(Model, "generate", side_effect=AssertionError("model dispatched")),
        patch.object(LiquidHandlingEnvironment, "__init__", side_effect=AssertionError("Lab")),
    ):
        (decisions,) = replay_action_policy(labs, PERMISSIVE).values()
    assert [(d.tool, d.recorded.outcome, d.replayed.outcome, d.changed) for d in decisions] == [
        ("read_volume", "allow", "allow", False),
        ("pick_up_tip", "allow", "allow", False),
        ("aspirate", "hold", "allow", True),
    ]
    assert decisions[2].recorded_policy_version == DEFAULT_ACTION_POLICY.version


def test_cli_replay_policy_reports_changes_and_refuses_tampered_logs(run, tmp_path):
    _, labs = run
    policy = tmp_path / "permissive.json"
    policy.write_text(PERMISSIVE.model_dump_json())
    command = [sys.executable, "-m", "inspect_labs.cli", "replay-policy", str(labs)]
    result = subprocess.run(
        [*command, "--policy", str(policy)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert (report["actions"], report["changed"], report["replay_only"]) == (3, 1, True)
    _edit(labs, lambda s: s["actions"][0].update(status="error"))
    refused = subprocess.run(
        [*command, "--policy", str(policy)], capture_output=True, text=True, check=False
    )
    assert refused.returncode == 2 and "Lab log hash mismatch" in refused.stderr
