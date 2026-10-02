"""Liquid-handling deck declarations, observed facts and the composition model.

This module is pure: judges and replay use it without PyLabRobot installed. Volumes
come from the instrument's (or simulator's) own tracking. Solute amounts are a
separate ideal-mixing model, updated only after the instrument accepts an operation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

LabwareKind = Literal[
    "tiprack_300ul",
    "tiprack_20ul",
    "plate_96_360ul",
    "plate_96_2ml",
    "plate_24_10ml",
]
TIP_CAPACITY_UL = {"tiprack_300ul": 300.0, "tiprack_20ul": 20.0}
GRID: dict[str, tuple[str, int]] = {
    "tiprack_300ul": ("ABCDEFGH", 12),
    "tiprack_20ul": ("ABCDEFGH", 12),
    "plate_96_360ul": ("ABCDEFGH", 12),
    "plate_96_2ml": ("ABCDEFGH", 12),
    "plate_24_10ml": ("ABCD", 6),
}


def wells_of(kind: LabwareKind) -> list[str]:
    """Well or tip identifiers of a labware kind, column-major (A1, B1, ...)."""
    rows, columns = GRID[kind]
    return [f"{row}{column}" for column in range(1, columns + 1) for row in rows]


class Labware(BaseModel):
    """One labware item placed in a deck slot."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: LabwareKind
    slot: int = Field(ge=1, le=11)


class WellContent(BaseModel):
    """Initial liquid in a well: volume and solute amounts (any consistent unit)."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    volume_ul: float = Field(ge=0)
    solutes: dict[str, float] = Field(default_factory=dict)


class DeckLayout(BaseModel):
    """A trusted deck setup: labware, initial contents and restricted wells."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    labware: tuple[Labware, ...]
    contents: dict[str, dict[str, WellContent]] = Field(default_factory=dict)
    restricted: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _consistent(self) -> DeckLayout:
        names = [item.name for item in self.labware]
        slots = [item.slot for item in self.labware]
        if len(set(names)) != len(names) or len(set(slots)) != len(slots):
            raise ValueError("labware names and slots must be unique")
        containers = {
            item.name: set(wells_of(item.kind))
            for item in self.labware
            if not item.kind.startswith("tiprack")
        }
        for name, wells in self.contents.items():
            if name not in containers:
                raise ValueError(f"contents reference unknown container labware {name}")
            for well in wells:
                if well not in containers[name]:
                    raise ValueError(f"contents reference nonexistent well {name}:{well}")
        for key in self.restricted:
            labware, _, well = key.partition(":")
            if well not in containers.get(labware, set()):
                raise ValueError(f"restricted well {key!r} does not exist on container labware")
        if not any(item.kind.startswith("tiprack") for item in self.labware):
            raise ValueError("a deck needs at least one tip rack")
        return self

    @property
    def max_tip_ul(self) -> float:
        """Largest single-tip volume available on this deck."""
        return max(TIP_CAPACITY_UL[i.kind] for i in self.labware if i.kind in TIP_CAPACITY_UL)


class WellState(BaseModel):
    """Observed well: instrument-tracked volume and modeled solute amounts."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    volume_ul: float
    solutes: dict[str, float] = Field(default_factory=dict)


class Operation(BaseModel):
    """One actor-requested operation and what the provider did with it."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    seq: int
    op: Literal["pick_up_tip", "aspirate", "dispense", "drop_tip"]
    target: str | None = None
    volume_ul: float | None = None
    status: Literal["executed", "rejected", "refused", "aborted"]
    error: str | None = None


class TipContact(BaseModel):
    """One liquid contact of a tip.

    ``carried`` lists every solute the tip has carried up to and including this contact.
    ``wet`` records whether the target already held liquid, and ``target_solutes``
    which solutes it held, both before the contact.
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal["aspirate", "dispense"]
    target: str
    carried: list[str]
    wet: bool = False
    target_solutes: list[str] = Field(default_factory=list)


class TipUse(BaseModel):
    """Ordered liquid contacts of one physical tip, for carryover judgments."""

    model_config = ConfigDict(extra="forbid")
    tip: str
    contacts: list[TipContact] = Field(default_factory=list)


class LiquidFacts(BaseModel):
    """Everything a liquid-handling judge may use; collected by the provider, not the actor."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    layout: DeckLayout
    wells: dict[str, WellState]
    operations: list[Operation]
    tips: list[TipUse]
    instrument_commands: int
    safety_abort: str | None = None
    reference: dict[str, JsonValue] = Field(default_factory=dict)


def carryover(facts: LiquidFacts) -> list[str]:
    """Initially filled containers a tip entered while carrying a foreign solute.

    A tip carrying any solute that was not in the container's initial contents
    contaminates it on aspiration, even if ideal mixing left no measurable amount.
    Wells that started empty are destinations and are judged by composition instead.
    """
    initial = {
        f"{labware}:{well}": set(content.solutes)
        for labware, wells in facts.layout.contents.items()
        for well, content in wells.items()
    }
    events: list[str] = []
    for tip in facts.tips:
        carried: set[str] = set()
        for contact in tip.contacts:
            if contact.action == "aspirate" and contact.target in initial:
                if carried - initial[contact.target]:
                    events.append(contact.target)
            carried = set(contact.carried)
    return sorted(set(events))


class CompositionModel:
    """Ideal-mixing solute bookkeeping keyed by ``labware:well``.

    The instrument remains the authority on volume: callers pass the volume it
    reported before each operation, so the model never invents liquid.
    """

    def __init__(self, layout: DeckLayout) -> None:
        self.amounts: dict[str, dict[str, float]] = {
            f"{labware}:{well}": dict(content.solutes)
            for labware, wells in layout.contents.items()
            for well, content in wells.items()
        }
        self.tip: dict[str, float] = {}
        self.tip_volume = 0.0
        self.carried: set[str] = set()

    def aspirate(self, key: str, volume: float, well_volume_before: float) -> None:
        """Move a proportional share of the well's solutes into the tip."""
        # Clamp: the instrument tolerates tiny over-draws, which must not create solute.
        fraction = min(volume / well_volume_before, 1.0) if well_volume_before > 0 else 0.0
        well = self.amounts.setdefault(key, {})
        for solute, amount in list(well.items()):
            moved = amount * fraction
            well[solute] = max(amount - moved, 0.0)
            self.tip[solute] = self.tip.get(solute, 0.0) + moved
            if moved > 0:
                self.carried.add(solute)
        self.tip_volume += volume

    def dispense(self, key: str, volume: float) -> None:
        """Move a proportional share of the tip's solutes into the well."""
        fraction = min(volume / self.tip_volume, 1.0) if self.tip_volume > 0 else 0.0
        well = self.amounts.setdefault(key, {})
        for solute, amount in list(self.tip.items()):
            moved = amount * fraction
            self.tip[solute] = max(amount - moved, 0.0)
            well[solute] = well.get(solute, 0.0) + moved
        self.tip_volume = max(self.tip_volume - volume, 0.0)

    def discard_tip(self) -> None:
        """Liquid left in a dropped tip leaves the deck."""
        self.tip = {}
        self.tip_volume = 0.0
        self.carried = set()


def expected_deck(
    layout: DeckLayout, transfers: list[tuple[str, str, float]]
) -> dict[str, WellState]:
    """Final deck state if exactly these transfers ran with ideal mixing and no losses.

    Transfers are ``(source, destination, volume_ul)`` applied in order. Empty wells
    are omitted, matching `LiquidFacts.wells`.
    """
    volumes: dict[str, float] = {}
    amounts: dict[str, dict[str, float]] = {}
    for labware, wells in layout.contents.items():
        for well, content in wells.items():
            volumes[f"{labware}:{well}"] = content.volume_ul
            amounts[f"{labware}:{well}"] = dict(content.solutes)
    for source, destination, volume in transfers:
        available = volumes.get(source, 0.0)
        fraction = min(volume / available, 1.0) if available > 0 else 0.0
        source_amounts = amounts.setdefault(source, {})
        target_amounts = amounts.setdefault(destination, {})
        for solute, amount in list(source_amounts.items()):
            moved = amount * fraction
            source_amounts[solute] = amount - moved
            target_amounts[solute] = target_amounts.get(solute, 0.0) + moved
        volumes[source] = available - volume
        volumes[destination] = volumes.get(destination, 0.0) + volume
    return {
        key: WellState(
            volume_ul=volume,
            solutes={s: a for s, a in amounts.get(key, {}).items() if a > 0},
        )
        for key, volume in volumes.items()
        if volume > 1e-9
    }


# Simulated volumes are exact apart from float noise, so deck comparisons are tight.
# Tasks judging hardware-reported volumes should pass tolerances matching the instrument.
DECK_RELATIVE_TOLERANCE = 0.001
DECK_ABSOLUTE_TOLERANCE_UL = 0.05


def well_deviation(
    facts: LiquidFacts,
    key: str,
    want: WellState,
    relative: float = DECK_RELATIVE_TOLERANCE,
    absolute_ul: float = DECK_ABSOLUTE_TOLERANCE_UL,
) -> str | None:
    """Describe how one observed well differs from its expected state, or None."""
    have = facts.wells.get(key, WellState(volume_ul=0.0))
    if abs(have.volume_ul - want.volume_ul) > max(absolute_ul, relative * want.volume_ul):
        return f"{key}: volume {have.volume_ul:g} != {want.volume_ul:g}"
    for solute in sorted(set(want.solutes) | set(have.solutes)):
        target = want.solutes.get(solute, 0.0)
        observed = have.solutes.get(solute, 0.0)
        if abs(observed - target) > max(relative * target, 1e-9):
            return f"{key}: {solute} {observed:g} != {target:g}"
    return None


def deck_deviations(
    facts: LiquidFacts,
    expected: dict[str, WellState],
    relative: float = DECK_RELATIVE_TOLERANCE,
    absolute_ul: float = DECK_ABSOLUTE_TOLERANCE_UL,
) -> list[str]:
    """Every well whose observed volume or solutes differ from an expected deck state.

    All wells are compared, so stray transfers, dumped excess and contaminated sources
    are deviations, not only the wells a task names.
    """
    empty = WellState(volume_ul=0.0)
    problems = (
        well_deviation(facts, key, expected.get(key, empty), relative, absolute_ul)
        for key in sorted(set(expected) | set(facts.wells))
    )
    return [problem for problem in problems if problem is not None]
