"""Opentrons OT-2 backends for Inspect Labs, built on PyLabRobot.

``opentrons-ot2-simulator`` runs PyLabRobot's OT-2 simulator: the OT-2 backend's
mount and pipette logic with no hardware and no vendor client. ``opentrons-ot2``
drives a real robot over its HTTP API. It is physical: `bind_task` refuses it unless
the host passes ``allow_physical=True``, and it claims the robot's address so two
evaluations cannot drive it at once. Neither flag replaces facility authorization,
interlocks or an operator.

Construction performs no network I/O; the connection opens on the first operation.
"""

from __future__ import annotations

from inspect_labs.plugins import BackendBinding, DeviceSlot

RUNTIME_REQUIREMENTS = {"pylabrobot": 'uv pip install "inspect-labs[pylabrobot]"'}

NOTES = (
    "Opentrons OT-2. Left mount {left}, right mount {right}. Single-channel operations "
    "only; the backend selects the mount by volume. Verify pipette mounts, labware "
    "definitions and deck calibration against the physical robot before any run."
)


def opentrons_ot2_simulator(
    left_pipette: str = "p300_single_gen2", right_pipette: str = "p20_single_gen2"
) -> BackendBinding:
    """Offline OT-2 simulator with the robot backend's pipette and mount semantics."""
    from pylabrobot.liquid_handling.backends import OpentronsOT2Simulator

    return BackendBinding(
        backend=OpentronsOT2Simulator(
            left_pipette_name=left_pipette, right_pipette_name=right_pipette
        ),
        mode="simulation",
        deck="ot2",
        notes=NOTES.format(left=left_pipette, right=right_pipette) + " Simulated: no robot.",
    )


def opentrons_ot2(host: str, port: int = 31950) -> BackendBinding:
    """A real OT-2 at ``host:port`` (physical; requires explicit authorization)."""
    from pylabrobot.liquid_handling.backends import OpentronsOT2Backend

    return BackendBinding(
        backend=OpentronsOT2Backend(host=host, port=port),
        mode="physical",
        deck="ot2",
        devices=(("http", f"{host}:{port}"),),
        notes=NOTES.format(left="per robot", right="per robot"),
    )


opentrons_ot2.RUNTIME_REQUIREMENTS = {  # type: ignore[attr-defined]
    **RUNTIME_REQUIREMENTS,
    "ot_api": 'uv pip install "inspect-labs-opentrons[robot]"',
}
opentrons_ot2.DEVICE_SLOTS = (  # type: ignore[attr-defined]
    DeviceSlot(arg="host", kind="http", label="OT-2 robot address"),
)

__all__ = ["opentrons_ot2", "opentrons_ot2_simulator"]
