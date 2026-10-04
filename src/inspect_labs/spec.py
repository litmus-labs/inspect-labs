"""Typed operation declarations and the pre-dispatch compatibility check.

An environment declares the operations it supports, with each parameter's unit and
accepted range. A task declares what it needs. The check runs before any actor tool
is installed. A mismatch is a specific error, never an inferred success.
Units are compared exactly; no conversion is attempted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActionType = Literal["read", "reversible", "irreversible", "external"]
"""What an operation can change: ``read`` changes nothing; ``reversible`` changes state
that can be restored; ``irreversible`` uses or changes material; ``external`` leaves
the lab, for example a job sent to an outside service."""


class ParameterSpec(BaseModel):
    """One operation parameter: its unit and inclusive accepted range."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    unit: str = Field(min_length=1)
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _ordered(self) -> ParameterSpec:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class OperationSpec(BaseModel):
    """A supported operation, its typed parameters and, optionally, its action type.

    Operations are matched to agent tools by name. An operation without an action
    type is treated as irreversible when actions are checked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    action: ActionType | None = None


class Requirements(BaseModel):
    """What a task needs from an environment before any actor dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    capabilities: frozenset[str] = frozenset()
    operations: dict[str, OperationSpec] = Field(default_factory=dict)


def compatibility_problems(
    required: Requirements,
    capabilities: frozenset[str],
    operations: dict[str, OperationSpec],
) -> list[str]:
    """List every way the declared environment fails the task's requirements.

    Args:
        required: Task requirements.
        capabilities: Capabilities the environment declares.
        operations: Operations the environment declares.

    Returns:
        Human-readable problems; empty when compatible.
    """
    problems = [
        f"missing capability {name}" for name in sorted(required.capabilities - capabilities)
    ]
    for name, need in sorted(required.operations.items()):
        offered = operations.get(name)
        if offered is None:
            problems.append(f"missing operation {name}")
            continue
        for parameter, spec in sorted(need.parameters.items()):
            have = offered.parameters.get(parameter)
            if have is None:
                problems.append(f"{name}.{parameter} is not declared")
            elif have.unit != spec.unit:
                problems.append(f"{name}.{parameter} unit {have.unit} != required {spec.unit}")
            else:
                if (
                    spec.minimum is not None
                    and have.minimum is not None
                    and spec.minimum < have.minimum
                ):
                    problems.append(
                        f"{name}.{parameter} minimum {have.minimum} > required {spec.minimum}"
                    )
                if (
                    spec.maximum is not None
                    and have.maximum is not None
                    and spec.maximum > have.maximum
                ):
                    problems.append(
                        f"{name}.{parameter} maximum {have.maximum} < required {spec.maximum}"
                    )
    return problems
