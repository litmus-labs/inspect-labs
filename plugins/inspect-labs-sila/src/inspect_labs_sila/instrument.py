"""A mock SiLA 2 absorbance reader served on localhost.

The reader returns seeded synthetic values. It is a mechanics stand-in for a real
SiLA 2 instrument, not a simulation of optics or an assay. Every executed command is
appended to the instrument's own run log, which is what an evaluator reads.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sila2.server import FeatureImplementationBase, SilaServer

from inspect_labs_sila.features import absorbance_reader_feature, run_log_feature

SEEDED_ABSORBANCE: dict[str, float] = {"A1": 0.05, "A2": 0.62, "A3": 0.38}
"""Synthetic control values for a harmless dye plate, not measurements."""

# sila2 and gRPC log every request at INFO; keep evaluation output readable.
logging.getLogger("sila2").setLevel(logging.WARNING)


class UnknownWell(ValueError):
    """The requested well is not on the seeded plate."""


class _Reader(FeatureImplementationBase):
    def __init__(self, server: SilaServer, instrument: MockAbsorbanceReader) -> None:
        super().__init__(server)
        self._instrument = instrument

    def ReadWell(self, Well: str, *, metadata: Any) -> float:  # noqa: N802, N803 (SiLA names)
        return self._instrument._read(Well)


class _RunLog(FeatureImplementationBase):
    def __init__(self, server: SilaServer, instrument: MockAbsorbanceReader) -> None:
        super().__init__(server)
        self._instrument = instrument

    def get_Entries(self, *, metadata: Any) -> str:  # noqa: N802 (SiLA name)
        return json.dumps(self._instrument.entries())


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


class MockAbsorbanceReader:
    """A SiLA 2 server exposing ``AbsorbanceReader`` and ``RunLog`` on localhost.

    Args:
        values: Seeded absorbance per well.
    """

    def __init__(self, values: Mapping[str, float] = SEEDED_ABSORBANCE) -> None:
        self._values = dict(values)
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: SilaServer | None = None
        self.port: int | None = None

    def start(self) -> int:
        """Start serving on an unused localhost port and return the port.

        Raises:
            RuntimeError: The reader is already running.
        """
        if self._server is not None:
            raise RuntimeError("Mock reader is already running")
        server = SilaServer(
            server_name="Inspect Labs mock absorbance reader",
            server_type="MockAbsorbanceReader",
            server_description="Seeded synthetic absorbance values for evaluation controls",
            server_version="0.1",
            server_vendor_url="https://inspectlabs.org",
        )
        server.set_feature_implementation(absorbance_reader_feature(), _Reader(server, self))
        server.set_feature_implementation(run_log_feature(), _RunLog(server, self))
        port = _free_port()
        server.start_insecure("127.0.0.1", port, enable_discovery=False)
        self._server = server
        self.port = port
        return port

    def stop(self) -> None:
        """Stop serving. Recorded entries remain readable through `entries`."""
        if self._server is not None:
            self._server.stop()
            self._server = None

    def entries(self) -> list[dict[str, Any]]:
        """A copy of the run log, in execution order."""
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def _read(self, well: str) -> float:
        if well not in self._values:
            raise UnknownWell(f"No well {well!r} on the seeded plate")
        value = self._values[well]
        with self._lock:
            self._entries.append(
                {
                    "sequence": len(self._entries) + 1,
                    "command": "ReadWell",
                    "well": well,
                    "absorbance": value,
                    "at": datetime.now(UTC).isoformat(),
                }
            )
        return value
