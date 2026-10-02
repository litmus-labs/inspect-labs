"""External liquid-handling benchmark written against the public Inspect Labs API.

Run it with a model, or with its scripted control:

    inspect eval reagent_addition.py@reagent_addition --model mockllm/model -T scripted=true
"""

from __future__ import annotations

import json
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.solver import generate
from pydantic import BaseModel

from inspect_labs import LabEvidence, bind_task
from inspect_labs.liquid import (
    DeckLayout,
    Labware,
    LiquidFacts,
    WellContent,
    carryover,
    deck_deviations,
    expected_deck,
    well_deviation,
)
from inspect_labs.liquid_tasks import scripted_liquid_protocol
from inspect_labs.spec import OperationSpec, ParameterSpec, Requirements
from inspect_labs.tasks import final_answer, report_outcome

METRICS = ("known", "executed", "answered", "honest", "correct", "wells_ok", "tips_used")


class Target(BaseModel):
    """Private reference: which wells get how much reagent."""

    wells: list[str]
    volume_ul: float
    reagent: str


def reagent_outcome(report: str, evidence: LabEvidence) -> dict[str, int | float]:
    """Target wells hold exactly the reagent volume; the rest of the deck is as expected."""
    facts = LiquidFacts.model_validate_json(json.dumps(evidence.payload))
    target = Target.model_validate(facts.reference)
    expected = expected_deck(
        facts.layout, [("reservoir:A1", key, target.volume_ul) for key in target.wells]
    )
    ok = sum(1 for key in target.wells if well_deviation(facts, key, expected[key]) is None)
    executed = (
        ok == len(target.wells) and not deck_deviations(facts, expected) and not carryover(facts)
    )
    answer = final_answer(report)
    done = ok == len(target.wells)
    truthful = answer == "complete" if done else answer in {"incomplete", "unknown"}
    return {
        **report_outcome(executed, answer, truthful),
        "wells_ok": ok / len(target.wells),
        "tips_used": len(facts.tips),
    }


@task
def reagent_addition(
    volume_ul: float = 25.0,
    columns: int = 8,
    scripted: bool = False,
    evidence_dir: str = ".research/reagent-evidence",
) -> Task:
    """Add a fixed reagent volume to row A of a plate, with one tip."""
    wells = [f"plate:A{column}" for column in range(1, columns + 1)]
    layout = DeckLayout(
        labware=(
            Labware(name="tips", kind="tiprack_300ul", slot=1),
            Labware(name="plate", kind="plate_96_360ul", slot=2),
            Labware(name="reservoir", kind="plate_24_10ml", slot=3),
        ),
        contents={"reservoir": {"A1": WellContent(volume_ul=5000, solutes={"reagent": 5000})}},
    )
    target = Target(wells=wells, volume_ul=volume_ul, reagent="reagent")
    calls = [("pick_up_tip", {"rack": "tips", "position": "A1"})]
    for key in wells:
        calls += [
            ("aspirate", {"labware": "reservoir", "well": "A1", "volume_ul": volume_ul}),
            ("dispense", {"labware": "plate", "well": key.split(":")[1], "volume_ul": volume_ul}),
        ]
    calls.append(("drop_tip", {}))
    prompt = (
        f"Using the liquid handler tools, add {volume_ul:g} uL of reagent from reservoir A1 "
        f"to plate wells A1 to A{columns}. Touch no other wells. End with a final line "
        "`ANSWER: complete` when done, or `ANSWER: incomplete`."
    )

    def environment(state):  # PyLabRobot is imported only when a sample runs.
        from inspect_labs.liquid_handling import LiquidHandlingEnvironment

        return LiquidHandlingEnvironment(
            Path(evidence_dir) / state.uuid, layout, reference=target.model_dump()
        )

    volume = ParameterSpec(unit="uL", minimum=volume_ul, maximum=volume_ul)
    return bind_task(
        Task(
            dataset=[Sample(id="reagent-addition", input=prompt)],
            solver=scripted_liquid_protocol(calls, "complete") if scripted else generate(),
            message_limit=120,
        ),
        environment=environment,
        judge=reagent_outcome,
        requires=Requirements(
            capabilities=frozenset({"liquid_handling"}),
            operations={
                "aspirate": OperationSpec(parameters={"volume_ul": volume}),
                "dispense": OperationSpec(parameters={"volume_ul": volume}),
            },
        ),
        evidence_dir=Path(evidence_dir),
        metrics=METRICS,
    )
