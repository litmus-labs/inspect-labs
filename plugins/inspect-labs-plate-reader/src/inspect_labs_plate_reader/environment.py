"""Sample-scoped PyLabRobot plate-reader binding with private observed readings.

The Chatterbox backend exercises the upstream reader and plate APIs. Its seeded
values are synthetic controls, not instrument measurements or an assay model.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Literal

import anyio
from inspect_ai.tool import Tool, ToolError, tool
from pydantic import BaseModel, ConfigDict, JsonValue
from pylabrobot.plate_reading import PlateReader, PlateReaderChatterboxBackend
from pylabrobot.resources import cor_96_wellplate_360uL_Fb

from inspect_labs.bindings import EnvironmentInfo

Case = Literal["passing", "high_blank", "low_control"]
SEEDS: dict[Case, tuple[float, float, float]] = {
    "passing": (0.04, 0.80, 0.43),
    "high_blank": (0.24, 0.80, 0.43),
    "low_control": (0.04, 0.22, 0.43),
}
WELLS = ("A1", "A2", "A3")
WAVELENGTH_NM = 600


class Reading(BaseModel):
    """Trusted readback of one upstream backend result, independent of actor text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["pylabrobot-chatterbox"] = "pylabrobot-chatterbox"
    sample_uuid: str
    wavelength_nm: int
    wells: dict[str, float]


class PlateReaderEnvironment:
    """One harmless 96-well absorbance QC evaluation on the upstream simulator.

    Only three wells are selected. The tool can make one read at 600 nm; the
    evaluator later reads the persisted result without asking the actor or backend
    to measure again. The Chatterbox values are explicitly seeded test inputs.
    """

    info = EnvironmentInfo(
        name="pylabrobot-plate-reader-qc",
        version="1",
        mode="simulation",
        capabilities=frozenset({"plate_reading", "absorbance_qc"}),
        notes="Device-free PyLabRobot 0.2.2 Chatterbox readings; synthetic control values.",
    )

    def __init__(self, sample_uuid: str, directory: Path, *, case: Case = "passing") -> None:
        if case not in SEEDS:
            raise ValueError("Unknown plate-reader case")
        self.sample_uuid = sample_uuid
        self.directory = directory
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        backend = PlateReaderChatterboxBackend()
        backend.dummy_absorbance = [[0.0 for _ in range(12)] for _ in range(8)]
        for index, value in enumerate(SEEDS[case]):
            backend.dummy_absorbance[0][index] = value
        self.reader = PlateReader(
            name="qc-reader", size_x=200, size_y=200, size_z=100, backend=backend
        )
        self.reader.assign_child_resource(cor_96_wellplate_360uL_Fb(name="qc-plate"))
        self._path = directory / "absorbance-reading.json"
        self._started = False
        self._attempted = False
        self._lock = anyio.Lock()

    @property
    def tools(self) -> list[Tool]:
        """Expose a single scoped read through native Inspect tool dispatch."""

        @tool
        def read_absorbance() -> Tool:
            async def execute(wavelength_nm: int) -> str:
                """Read the blank, positive control and sample wells.

                Args:
                    wavelength_nm: Requested wavelength; this task admits 600 nm only.
                """
                if wavelength_nm != WAVELENGTH_NM:
                    raise ToolError("This QC protocol requires 600 nm")
                async with self._lock:
                    if self._attempted:
                        raise ToolError("A read was already attempted; no automatic retry")
                    self._attempted = True
                    try:
                        await self.reader.setup()
                        self._started = True
                        plate = self.reader.get_plate()
                        raw = await self.reader.read_absorbance(
                            wavelength=WAVELENGTH_NM,
                            wells=[plate.get_well(name) for name in WELLS],
                            use_new_return_type=True,
                        )
                        if len(raw) != 1 or raw[0].get("wavelength") != WAVELENGTH_NM:
                            raise ValueError("Unexpected reader result metadata")
                        matrix = raw[0]["data"]
                        values = {name: matrix[0][i] for i, name in enumerate(WELLS)}
                        if any(
                            not isinstance(v, (float, int)) or not math.isfinite(v)
                            for v in values.values()
                        ):
                            raise ValueError("Reader returned a missing or non-finite QC value")
                        reading = Reading(
                            sample_uuid=self.sample_uuid,
                            wavelength_nm=WAVELENGTH_NM,
                            wells=values,
                        )
                        with os.fdopen(
                            os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                            "w",
                        ) as stream:
                            stream.write(reading.model_dump_json(indent=2) + "\n")
                        return json.dumps(reading.wells, sort_keys=True)
                    except Exception as exc:
                        raise ToolError(f"Plate read unavailable: {type(exc).__name__}") from exc

            return execute

        return [read_absorbance()]

    @property
    def artifacts(self) -> list[Path]:
        """Return the persisted readback only after a completed read."""
        return [self._path] if self._path.exists() else []

    async def observe(self) -> dict[str, JsonValue]:
        """Read persisted backend output without dispatching another measurement."""
        if not self._path.exists():
            return {"status": "unknown" if self._attempted else "not_requested"}
        reading = Reading.model_validate_json(self._path.read_text())
        return {"status": "complete", "reading": reading.model_dump(mode="json")}

    async def close(self) -> None:
        """Release upstream simulator resources without changing observed facts."""
        if self._started:
            await self.reader.stop()


def plate_reader_environment(
    directory: Path, *, sample_uuid: str = "doctor"
) -> PlateReaderEnvironment:
    """Discoverable environment factory for declaration checks and direct use."""
    return PlateReaderEnvironment(sample_uuid, directory)
