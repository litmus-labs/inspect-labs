"""Checks before each action: pure decisions, and their records in the lab log."""

import math
from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.solver import generate
from inspect_ai.tool import ToolCall

from inspect_labs import rescore
from inspect_labs.actions import DEFAULT_RULES, Action, ActionRules, Rule
from inspect_labs.bindings import LabLogFile, connect_lab
from inspect_labs.liquid import DeckLayout, Labware, WellContent
from inspect_labs.spec import OperationSpec, ParameterSpec

pytest.importorskip("pylabrobot")
from inspect_labs.liquid_handling import LiquidHandlingEnvironment  # noqa: E402

VOLUME = ParameterSpec(unit="uL", minimum=1, maximum=300)
OPERATIONS = {
    "read_volume": OperationSpec(action="read"),
    "pick_up_tip": OperationSpec(action="reversible"),
    "dispense": OperationSpec(parameters={"volume_ul": VOLUME}, action="irreversible"),
    "submit_job": OperationSpec(action="external"),
    "mystery": OperationSpec(),
}
LAYOUT = DeckLayout(
    labware=(
        Labware(name="tips", kind="tiprack_300ul", slot=1),
        Labware(name="plate", kind="plate_96_360ul", slot=2),
    ),
    contents={"plate": {"A1": WellContent(volume_ul=200, solutes={"dye": 2000})}},
)


def decide(tool, **arguments):
    action = Action.of(tool, arguments, OPERATIONS)
    return action, DEFAULT_RULES.decide(action, OPERATIONS)


@pytest.mark.parametrize(
    "tool,arguments,outcome,rule",
    [
        ("read_volume", {}, "allow", "allow-read-and-reversible"),
        ("pick_up_tip", {}, "allow", "allow-read-and-reversible"),
        ("dispense", {"volume_ul": 50}, "hold", "approve-irreversible-and-external"),
        ("submit_job", {}, "hold", "approve-irreversible-and-external"),
        ("dispense", {"volume_ul": 900}, "deny", "declared-range"),
        ("dispense", {"volume_ul": math.nan}, "deny", "declared-range"),
        ("mystery", {}, "deny", "undeclared-operation"),
        ("not_declared_at_all", {}, "deny", "undeclared-operation"),
    ],
)
def test_default_policy_decisions(tool, arguments, outcome, rule):
    action, decision = decide(tool, **arguments)
    assert (decision.outcome, decision.rule) == (outcome, rule)
    assert decision.reason
    if rule == "undeclared-operation":
        assert action.action_type == "irreversible" and not action.declared


def test_no_matching_rule_is_refused_and_decisions_are_deterministic():
    reads_only = ActionRules(
        version="reads-only",
        rules=(
            Rule(
                name="reads",
                action_types=frozenset({"read"}),
                outcome="allow",
                reason="Reads only",
            ),
        ),
    )
    action = Action.of("pick_up_tip", {}, OPERATIONS)
    assert reads_only.decide(action, OPERATIONS).rule == "default"
    assert reads_only.decide(action, OPERATIONS) == reads_only.decide(action, OPERATIONS)


def _calls(steps):
    outputs = [
        ModelOutput.from_message(
            ChatMessageAssistant(
                content="", tool_calls=[ToolCall(id=f"c{i}", function=fn, arguments=args)]
            )
        )
        for i, (fn, args) in enumerate(steps)
    ]
    outputs.append(ModelOutput.from_content("mockllm/model", "ANSWER: complete"))
    return get_model("mockllm/model", custom_outputs=outputs)


STEPS = [
    ("read_volume", {"labware": "plate", "well": "A1"}),
    ("pick_up_tip", {"rack": "tips", "position": "A1"}),
    ("aspirate", {"labware": "plate", "well": "A1", "volume_ul": 50}),
]


def _run(tmp_path, approver):
    task = connect_lab(
        Task(dataset=[Sample(input="Move 50 uL")], solver=generate()),
        lab=lambda state: LiquidHandlingEnvironment(tmp_path / state.uuid, LAYOUT),
        scorer=lambda report, lab_log: {"known": 1, "correct": 1},
        requires=frozenset({"liquid_handling"}),
        lab_log_dir=tmp_path / "evidence",
        rules=DEFAULT_RULES,
        approver=approver,
    )
    log = eval(task, model=_calls(STEPS), log_dir=str(tmp_path / "logs"), display="none")[0]
    assert log.status == "success", log.error
    native = Path(log.location)
    bundle = LabLogFile.model_validate_json(native.with_suffix(".labs").read_text())
    (sample,) = bundle.samples.values()
    return read_eval_log(str(native)), native, sample.actions


def test_irreversible_action_is_refused_without_approval_and_recorded(tmp_path):
    log, _, actions = _run(tmp_path, approver=None)
    assert [(a.action.tool, a.decision.outcome, a.status) for a in actions] == [
        ("read_volume", "allow", "ran"),
        ("pick_up_tip", "allow", "ran"),
        ("aspirate", "hold", "refused"),
    ]
    assert actions[2].approved is False
    assert [a.sequence for a in actions] == [1, 2, 3]
    assert all(a.rules_version == DEFAULT_RULES.version for a in actions)
    tool_errors = [m.error for m in log.samples[0].messages if m.role == "tool" and m.error]
    assert len(tool_errors) == 1 and "approval" in tool_errors[0].message


def test_approved_irreversible_action_runs_and_records_the_approval(tmp_path):
    seen: list[str] = []

    async def approve(action):
        seen.append(action.tool)
        return True

    _, _, actions = _run(tmp_path, approver=approve)
    assert seen == ["aspirate"]
    assert (actions[2].decision.outcome, actions[2].approved, actions[2].status) == (
        "hold",
        True,
        "ran",
    )


def test_action_records_survive_rescoring(tmp_path):
    log, native, actions = _run(tmp_path, approver=lambda action: False)
    rescore(
        native,
        native.with_suffix(".labs"),
        tmp_path / "rescored.eval",
        lambda report, lab_log: {"known": 1, "correct": int(len(lab_log.actions) == 3)},
    )
    rescored = read_eval_log(str(tmp_path / "rescored.eval"))
    assert next(iter(rescored.samples[0].scores.values())).value["correct"] == 1
    assert len(actions) == 3


def test_without_a_policy_tools_are_unchanged_and_no_actions_are_recorded(tmp_path):
    task = connect_lab(
        Task(dataset=[Sample(input="Move 50 uL")], solver=generate()),
        lab=lambda state: LiquidHandlingEnvironment(tmp_path / state.uuid, LAYOUT),
        scorer=lambda report, lab_log: {"known": 1, "correct": 1},
        requires=frozenset({"liquid_handling"}),
        lab_log_dir=tmp_path / "evidence",
    )
    log = eval(task, model=_calls(STEPS), log_dir=str(tmp_path / "logs"), display="none")[0]
    bundle = LabLogFile.model_validate_json(Path(log.location).with_suffix(".labs").read_text())
    assert next(iter(bundle.samples.values())).actions == []
    assert not any(m.role == "tool" and m.error for m in log.samples[0].messages)
