"""The gateway: one place where every agent action is checked, approved and recorded.

Both modes use the same `Gateway`:

- the evaluation harness (`connect_lab(rules=...)`) routes each Lab tool call
  through it;
- the deployment server (`inspect_labs.gateway_server`) exposes a Lab's tools to any
  agent over MCP and routes each call through it.

So a rule file gives the same decisions in an evaluation and in real use.

For each action the gateway decides (allow, refuse, or hold for a person's
approval), records the decision, and only then lets the call reach the Lab. It
never operates equipment itself: the Lab's own software does. `stop` refuses every
later action, so people can halt an agent at any time.

This module is pure apart from awaiting the approver and the Lab call it is given.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, TypeVar

from pydantic import JsonValue

from inspect_labs.actions import Action, ActionRecord, ActionRules, Decision
from inspect_labs.spec import OperationSpec

T = TypeVar("T")

Approver = Callable[[Action], bool | Awaitable[bool]]
"""Called for a held action; returns True only if a person approved it."""


class ActionRefused(Exception):
    """An action was refused, or held and not approved. It never reached the Lab."""

    def __init__(self, record: ActionRecord) -> None:
        decision = record.decision
        why = "needs a person's approval" if decision.outcome == "hold" else "refused"
        super().__init__(f"Action {why} by lab rules ({decision.rule}): {decision.reason}")
        self.record = record


class Gateway:
    """Check, approve, record and run agent actions for one Lab session.

    Args:
        operations: The Lab's declared operations, matched to tools by name.
        rules: The action rules (the rule file).
        approver: Called for held actions. Without one, held actions are refused.
        records: Where to append action records; a new list when omitted.
    """

    def __init__(
        self,
        operations: dict[str, OperationSpec],
        rules: ActionRules,
        approver: Approver | None = None,
        records: list[ActionRecord] | None = None,
    ) -> None:
        self.operations = operations
        self.rules = rules
        self.approver = approver
        self.records: list[ActionRecord] = records if records is not None else []
        self.stopped: str | None = None

    def stop(self, reason: str) -> None:
        """Refuse every later action. Actions already running are not interrupted."""
        self.stopped = reason

    def decide(self, action: Action) -> Decision:
        """The decision for one action, including a stop."""
        if self.stopped is not None:
            return Decision(outcome="deny", rule="stopped", reason=self.stopped)
        return self.rules.decide(action, self.operations)

    async def run(
        self,
        tool: str,
        arguments: dict[str, JsonValue],
        call: Callable[[], Awaitable[T]],
    ) -> T:
        """Check one action, ask for approval if held, record it, then run it.

        Raises:
            ActionRefused: The action was refused or not approved; ``call`` was not run.
        """
        requested_at = datetime.now(UTC).isoformat()
        action = Action.of(tool, arguments, self.operations)
        decision = self.decide(action)
        approved: bool | None = None
        if decision.outcome == "hold":
            granted = self.approver(action) if self.approver is not None else False
            approved = bool(await granted if inspect.isawaitable(granted) else granted)

        def record(status: Literal["ran", "refused", "error"]) -> ActionRecord:
            entry = ActionRecord(
                sequence=len(self.records) + 1,
                requested_at=requested_at,
                action=action,
                rules_version=self.rules.version,
                decision=decision,
                approved=approved,
                status=status,
            )
            self.records.append(entry)
            return entry

        if decision.outcome == "deny" or (decision.outcome == "hold" and not approved):
            raise ActionRefused(record("refused"))
        try:
            result = await call()
        except BaseException:
            record("error")
            raise
        record("ran")
        return result
