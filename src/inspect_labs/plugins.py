"""Registry of laboratory environments and instrument backends, with entry-point plugins.

Mirrors Inspect Robots' registry: components register by name, and packages publish
entry points so an installed adapter is discoverable without being imported first::

    [project.entry-points."inspect_labs.backends"]
    opentrons-ot2 = "inspect_labs_opentrons:opentrons_ot2"

Adapters declare what they need on the factory itself, so ``inspect-labs doctor`` can
check an installation without constructing anything or touching hardware:

- ``RUNTIME_REQUIREMENTS``: ``{"module": "install command"}`` for imports that package
  metadata cannot express (vendor SDKs, optional extras), on the factory or its module.
- ``DEVICE_SLOTS``: constructor arguments that name physical devices, used for advisory
  device claims so two evaluations never drive one instrument.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Literal

Kind = Literal["environment", "backend"]
GROUPS: dict[Kind, str] = {
    "environment": "inspect_labs.environments",
    "backend": "inspect_labs.backends",
}


@dataclass(frozen=True)
class DeviceSlot:
    """A constructor argument that identifies a physical device.

    Args:
        arg: Constructor keyword naming the device (for example ``host``).
        kind: ``http`` host, ``serial``/``usb`` device path, or ``can`` interface.
        label: Human-readable description for setup and doctor output.
    """

    arg: str
    kind: Literal["http", "serial", "usb", "can"]
    label: str


@dataclass(frozen=True)
class BackendBinding:
    """A constructed instrument backend and the evidence level it provides.

    Args:
        backend: The instrument backend object (for example a PyLabRobot backend).
        mode: ``simulation`` or ``physical``. Physical backends require explicit
            host authorization in `bind_task`.
        deck: Deck family the backend drives (for example ``ot2``).
        devices: Claimed device identities, keyed by device kind.
        notes: Operating notes shown to agents and operators.
    """

    backend: Any
    mode: Literal["simulation", "physical"]
    deck: str
    devices: tuple[tuple[str, str], ...] = ()
    notes: str = ""


_local: dict[Kind, dict[str, Callable[..., Any]]] = {kind: {} for kind in GROUPS}


def register(kind: Kind, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register an in-process component factory under a name.

    Raises:
        ValueError: The name is already registered for this kind.
    """

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        if name in _local[kind]:
            raise ValueError(f"{kind} {name!r} is already registered")
        _local[kind][name] = factory
        return factory

    return decorator


def environment(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a `LabEnvironment` factory taking ``directory`` plus keyword options."""
    return register("environment", name)


def backend(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a factory returning a `BackendBinding`."""
    return register("backend", name)


def _entry_points(kind: Kind) -> dict[str, EntryPoint]:
    return {point.name: point for point in entry_points(group=GROUPS[kind])}


def available(kind: Kind) -> list[str]:
    """Names of registered and installed components, without importing plugins."""
    return sorted(set(_local[kind]) | set(_entry_points(kind)))


def factory(kind: Kind, name: str) -> Callable[..., Any]:
    """Load one component factory by name.

    Raises:
        LookupError: No component of this kind has that name.
    """
    if name in _local[kind]:
        return _local[kind][name]
    points = _entry_points(kind)
    if name not in points:
        raise LookupError(f"No {kind} named {name!r}; installed: {available(kind)}")
    loaded: Callable[..., Any] = points[name].load()
    return loaded


def resolve(kind: Kind, name: str, /, **kwargs: Any) -> Any:
    """Construct a component by name, forwarding keyword arguments to its factory."""
    return factory(kind, name)(**kwargs)


def runtime_requirements(component: object) -> dict[str, str]:
    """``RUNTIME_REQUIREMENTS`` declared on a factory, or else on its module."""
    declared = getattr(component, "RUNTIME_REQUIREMENTS", None)
    if declared is None:
        module = sys.modules.get(getattr(component, "__module__", ""))
        declared = getattr(module, "RUNTIME_REQUIREMENTS", {})
    return dict(declared) if isinstance(declared, dict) else {}


def missing_runtime_requirements(component: object) -> dict[str, str]:
    """Declared modules that are not importable, mapped to their install commands.

    Uses ``importlib.util.find_spec`` without importing the modules.
    """
    missing = {}
    for module, command in runtime_requirements(component).items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing[module] = command
    return missing


def device_slots(component: object) -> tuple[DeviceSlot, ...]:
    """A factory's declared ``DEVICE_SLOTS``."""
    declared = getattr(component, "DEVICE_SLOTS", ())
    return tuple(slot for slot in declared if isinstance(slot, DeviceSlot))
