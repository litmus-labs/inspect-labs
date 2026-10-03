"""A Lab backed by a mock SiLA 2 instrument.

The agent gets one native Inspect tool that calls the instrument's
``AbsorbanceReader.ReadWell`` command over SiLA 2. The evaluator's lab log comes
from the instrument's own ``RunLog`` feature through a separate client, so the
outcome comes from the system that did the work, not from the agent's report.

Swapping the mock for a real SiLA 2 reader would change the server address, not the
task. That swap is not implemented or validated here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from inspect_ai.tool import Tool, ToolError, tool
from pydantic import JsonValue
from sila2.client import SilaClient

from inspect_labs.bindings import EnvironmentInfo
from inspect_labs.spec import OperationSpec
from inspect_labs_sila.instrument import SEEDED_ABSORBANCE, MockAbsorbanceReader


class SilaReaderLab:
    """One sample's mock SiLA 2 absorbance reader.

    Constructing the Lab starts the mock server and connects two clients; it sends
    no instrument command.

    Args:
        sample_uuid: Native Inspect sample identity.
        well: The well the task asks the agent to read.
        values: Seeded absorbance per well.
    """

    info = EnvironmentInfo(
        name="sila-mock-reader",
        version="1",
        mode="simulation",
        capabilities=frozenset({"absorbance_read"}),
        operations={"read_absorbance": OperationSpec()},
        notes="Mock SiLA 2 absorbance reader on localhost; seeded synthetic values, "
        "not measurements.",
    )

    def __init__(
        self,
        sample_uuid: str,
        *,
        well: str = "A2",
        values: dict[str, float] | None = None,
    ) -> None:
        self.sample_uuid = sample_uuid
        self.well = well
        self.instrument = MockAbsorbanceReader(values or SEEDED_ABSORBANCE)
        port = self.instrument.start()
        try:
            self._agent_client = SilaClient("127.0.0.1", port, insecure=True)
            self._evaluator_client = SilaClient("127.0.0.1", port, insecure=True)
        except Exception:
            self.instrument.stop()
            raise

    @property
    def tools(self) -> list[Tool]:
        """The agent's only tool: one SiLA ``ReadWell`` call per invocation."""
        # sila2 adds feature attributes to the client at runtime, from the server's FDL.
        reader: Any = getattr(self._agent_client, "AbsorbanceReader")  # noqa: B009

        @tool
        def read_absorbance() -> Tool:
            async def execute(well: str) -> str:
                """Read the absorbance of one well on the instrument.

                Args:
                    well: Well name, for example A2.
                """

                def call() -> float:
                    response: Any = reader.ReadWell(Well=well)
                    return float(response.Absorbance)

                try:
                    value = await anyio.to_thread.run_sync(call)
                except Exception as exc:
                    raise ToolError(f"Read unavailable: {type(exc).__name__}") from exc
                return f"{value:.3f}"

            return execute

        return [read_absorbance()]

    @property
    def artifacts(self) -> list[Path]:
        """No files; the lab log carries the instrument's run log."""
        return []

    async def observe(self) -> dict[str, JsonValue]:
        """Read the instrument's run log without sending an instrument command."""
        raw: str = await anyio.to_thread.run_sync(
            lambda: str(getattr(self._evaluator_client, "RunLog").Entries.get())  # noqa: B009
        )
        entries: list[JsonValue] = json.loads(raw)
        return {"expected_well": self.well, "reads": entries}

    async def close(self) -> None:
        """Stop the mock server."""
        await anyio.to_thread.run_sync(self.instrument.stop)


def sila_mock_reader(
    directory: Path, *, sample_uuid: str = "doctor", well: str = "A2"
) -> SilaReaderLab:
    """Discoverable Lab factory for declaration checks and direct use.

    Args:
        directory: Accepted for the registry's factory signature; this Lab writes no files.
        sample_uuid: Native Inspect sample identity.
        well: The well the task asks the agent to read.
    """
    del directory
    return SilaReaderLab(sample_uuid, well=well)
