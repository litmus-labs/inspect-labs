"""Checks that run before each agent action reaches a Lab.

Each operation a Lab declares can say what kind of action it is. Before a tool call
runs, an `ActionRules` decides whether to allow it, refuse it, or hold it for a
person's approval, and the decision is recorded in the lab log with its reason.

This module is pure: no Inspect, no Lab, no I/O. The same policy and records can be
used by the evaluation harness and, later, by checks that run during real use.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from inspect_labs.spec import ActionType, OperationSpec

Outcome = Literal["allow", "deny", "hold"]
"""``hold`` means the action runs only if a person approves it."""


class Action(BaseModel):
    """One agent tool call, described before it reaches the Lab.

    An operation the Lab did not declare, or declared without an action type, is
    treated as ``irreversible`` so that unclear actions fail closed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool: str
    arguments: dict[str, JsonValue]
    action_type: ActionType
    declared: bool

    @classmethod
    def of(
        cls, tool: str, arguments: dict[str, JsonValue], operations: dict[str, OperationSpec]
    ) -> Action:
        """Describe a tool call using the Lab's declared operations (matched by name)."""
        spec = operations.get(tool)
        declared = spec is not None and spec.action is not None
        action_type: ActionType = (
            spec.action if spec is not None and spec.action is not None else "irreversible"
        )
        return cls(tool=tool, arguments=arguments, action_type=action_type, declared=declared)


class Decision(BaseModel):
    """The policy's decision for one action, with the rule that made it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Outcome
    rule: str
    reason: str
    source: Literal["rules", "check"] = "rules"
    """``rules``: decided by the rule file. ``check``: decided by a domain check, such as
    a plant's safety gate, which may depend on the Lab's state at the time."""


_STRICTNESS: dict[Outcome, int] = {"allow": 0, "hold": 1, "deny": 2}


def stricter(first: Decision, second: Decision) -> Decision:
    """The stricter of two decisions (deny, then hold, then allow); ``first`` on a tie."""
    return second if _STRICTNESS[second.outcome] > _STRICTNESS[first.outcome] else first


class Rule(BaseModel):
    """Apply ``outcome`` to actions of the listed types."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    action_types: frozenset[ActionType]
    outcome: Outcome
    reason: str = Field(min_length=1)


class ActionRules(BaseModel):
    """An ordered, versioned list of rules. The first matching rule decides.

    Two checks always run first:

    - an argument outside a declared parameter range is refused;
    - an operation without a declared action type is refused, since its effect is
      unknown (fail closed).

    If no rule matches, the action is refused.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(min_length=1)
    rules: tuple[Rule, ...]

    def decide(self, action: Action, operations: dict[str, OperationSpec]) -> Decision:
        """Decide one action. Pure: the same inputs always give the same decision."""
        spec = operations.get(action.tool)
        if spec is not None:
            for name, parameter in sorted(spec.parameters.items()):
                value = action.arguments.get(name)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                low, high = parameter.minimum, parameter.maximum
                if not math.isfinite(value) or (
                    (low is not None and value < low) or (high is not None and value > high)
                ):
                    return Decision(
                        outcome="deny",
                        rule="declared-range",
                        reason=f"{name}={value} is outside the declared range "
                        f"[{low}, {high}] {parameter.unit}",
                    )
        if not action.declared:
            return Decision(
                outcome="deny",
                rule="undeclared-operation",
                reason=f"{action.tool} has no declared action type, so its effect is unknown",
            )
        for rule in self.rules:
            if action.action_type in rule.action_types:
                return Decision(outcome=rule.outcome, rule=rule.name, reason=rule.reason)
        return Decision(outcome="deny", rule="default", reason="No rule allows this type of action")


DEFAULT_RULES = ActionRules(
    version="1",
    rules=(
        Rule(
            name="allow-read-and-reversible",
            action_types=frozenset({"read", "reversible"}),
            outcome="allow",
            reason="Reads and reversible actions can run without approval",
        ),
        Rule(
            name="approve-irreversible-and-external",
            action_types=frozenset({"irreversible", "external"}),
            outcome="hold",
            reason="Irreversible actions and actions that leave the lab need a person's approval",
        ),
    ),
)
"""Allow reads and reversible actions; hold everything else for a person's approval."""


class ActionRecord(BaseModel):
    """What happened to one action: the decision, any approval, and the result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int = Field(ge=1)
    requested_at: str
    action: Action
    rules_version: str
    decision: Decision
    approved: bool | None = None
    """For a held action: whether a person approved it. None when not held."""
    approved_by: str | None = None
    """Who or what approved a held action, such as a lease's name, when known."""
    status: Literal["ran", "refused", "error"]
    """``ran``: the tool returned. ``refused``: blocked before reaching the Lab.
    ``error``: allowed, but the tool raised; the Lab may or may not have acted."""


class ReplayedDecision(BaseModel):
    """A saved action re-decided under another policy, without running anything."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int
    tool: str
    recorded: Decision
    recorded_rules_version: str
    replayed: Decision
    changed: bool


def replay_decisions(
    records: list[ActionRecord],
    policy: ActionRules,
    operations: dict[str, OperationSpec],
) -> list[ReplayedDecision]:
    """Re-decide saved actions under ``policy``. Pure: nothing is dispatched.

    Shows what a rule change would have done to a past run. It does not show what
    the agent would have done next, since the agent saw the original decisions.

    A domain check may have depended on the Lab's state, which can't be recomputed
    offline. Its recorded decision is kept, and the stricter of it and the new
    rules' decision is the replayed one.
    """
    replayed = []
    for record in records:
        decision = policy.decide(record.action, operations)
        if record.decision.source == "check":
            decision = stricter(decision, record.decision)
        replayed.append(
            ReplayedDecision(
                sequence=record.sequence,
                tool=record.action.tool,
                recorded=record.decision,
                recorded_rules_version=record.rules_version,
                replayed=decision,
                changed=decision.outcome != record.decision.outcome,
            )
        )
    return replayed
