"""The gateway: one place where every agent action is checked, approved and recorded.

Both modes use the same `Gateway`:

- the evaluation harness (`connect_lab(rules=...)`) routes each Lab tool call
  through it;
- the deployment server (`inspect_labs.gateway_server`) exposes a Lab's tools to any
  agent over MCP and routes each call through it.

So a rule file gives the same decisions in an evaluation and in real use.

For each action the gateway decides (allow, refuse, or hold for a person's
approval), records the decision, and only then lets the call reach the Lab. Domain
checks, such as a water plant's safety gates or a sequence screen, can make a
decision stricter but never looser; a check that fails refuses the action. It
never operates equipment itself: the Lab's own software does. `stop` refuses every
later action, so people can halt an agent at any time.

This module is pure apart from awaiting the approver and the Lab call it is given.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from inspect_labs.actions import Action, ActionRecord, ActionRules, Decision, stricter
from inspect_labs.journal import Journal
from inspect_labs.spec import OperationSpec

T = TypeVar("T")
logger = logging.getLogger(__name__)


class Approval(BaseModel):
    """A person's answer for one held action, and who or what gave it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    approved: bool
    by: str | None = None


Approver = Callable[[Action], bool | Approval | Awaitable[bool | Approval]]
"""Called for a held action; approves only if a person approved it."""

Check = Callable[[Action], Decision | None | Awaitable[Decision | None]]
"""A domain check, such as a plant's safety gate. Returns a decision, or None when it
has no view on the action. It can make the gateway's decision stricter, never looser."""


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
        checks: Domain checks run in order after the rules, unless the rules refuse.
        journal: Where to write each step as it happens, before the next one.
    """

    def __init__(
        self,
        operations: dict[str, OperationSpec],
        rules: ActionRules,
        approver: Approver | None = None,
        records: list[ActionRecord] | None = None,
        checks: Sequence[Check] = (),
        journal: Journal | None = None,
    ) -> None:
        self.operations = operations
        self.rules = rules
        self.approver = approver
        self.checks = tuple(checks)
        self.records: list[ActionRecord] = records if records is not None else []
        self.stopped: str | None = None
        self.journal = journal
        self.listeners: list[Callable[[ActionRecord], None]] = []
        """Called with each action record as soon as it is made, for live monitors."""
        self._requests = 0

    def _write(
        self,
        kind: Literal["decided", "approval", "finished", "stopped"],
        body: dict[str, JsonValue],
    ) -> None:
        if self.journal is not None:
            self.journal.append(kind, body)

    def stop(self, reason: str, *, by: str = "operator") -> None:
        """Refuse every later action. Actions already running are not interrupted.

        A later stop keeps the first reason.
        """
        if self.stopped is None:
            self.stopped = reason
            self._write("stopped", {"reason": reason, "by": by})

    def decide(self, action: Action) -> Decision:
        """The rules' decision for one action, including a stop, before domain checks."""
        if self.stopped is not None:
            return Decision(outcome="deny", rule="stopped", reason=self.stopped)
        return self.rules.decide(action, self.operations)

    async def decide_with_checks(self, action: Action) -> Decision:
        """The full decision: the rules, then each domain check unless already refused.

        A check that raises refuses the action, so a broken check fails closed.
        """
        decision = self.decide(action)
        for check in self.checks:
            if decision.outcome == "deny":
                break
            try:
                result = check(action)
                found = await result if inspect.isawaitable(result) else result
            except Exception as exc:
                logger.warning("Domain check failed for %s", action.tool, exc_info=True)
                found = Decision(
                    outcome="deny",
                    rule="check-error",
                    reason=f"A domain check failed ({type(exc).__name__}), so the action "
                    "is refused",
                )
            if found is not None:
                decision = stricter(decision, found.model_copy(update={"source": "check"}))
        return decision

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
        self._requests += 1
        request = self._requests
        action = Action.of(tool, arguments, self.operations)
        decision = await self.decide_with_checks(action)
        self._write(
            "decided",
            {
                "request": request,
                "requested_at": requested_at,
                "action": action.model_dump(mode="json", exclude_defaults=True),
                "decision": decision.model_dump(mode="json", exclude_defaults=True),
            },
        )
        approved: bool | None = None
        approved_by: str | None = None
        if decision.outcome == "hold" and self.approver is not None:
            try:
                granted = self.approver(action)
                answer = await granted if inspect.isawaitable(granted) else granted
            except Exception:
                # An approver that fails has not approved: fail closed.
                logger.warning("Approver failed for %s", action.tool, exc_info=True)
                answer = Approval(approved=False)
            if isinstance(answer, Approval):
                approved, approved_by = answer.approved, answer.by if answer.approved else None
            else:
                approved = answer is True
            if approved and self.stopped is not None:
                # Stopped while waiting for approval: the stop wins.
                approved, approved_by = False, None
                decision = Decision(outcome="deny", rule="stopped", reason=self.stopped)
            self._write(
                "approval",
                {"request": request, "approved": approved}
                | ({"by": approved_by} if approved_by else {}),
            )
        elif decision.outcome == "hold":
            approved = False

        def record(status: Literal["ran", "refused", "error"]) -> ActionRecord:
            entry = ActionRecord(
                sequence=len(self.records) + 1,
                requested_at=requested_at,
                action=action,
                rules_version=self.rules.version,
                decision=decision,
                approved=approved,
                approved_by=approved_by,
                status=status,
            )
            self.records.append(entry)
            self._write(
                "finished",
                {"request": request, "sequence": entry.sequence, "status": status},
            )
            for listener in self.listeners:
                try:
                    listener(entry)
                except Exception:
                    logger.warning("Action listener failed", exc_info=True)
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


class ApprovedAction(BaseModel):
    """One action a person approved in advance: a tool and the exact arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool: str
    arguments: dict[str, JsonValue]


def approved_actions(approved: list[ApprovedAction]) -> Approver:
    """Approve held actions that exactly match one approved in advance, and nothing else."""

    def approve(action: Action) -> bool:
        return any(
            entry.tool == action.tool and entry.arguments == action.arguments for entry in approved
        )

    return approve


class Lease(BaseModel):
    """Permission, given in advance, for one tool until a deadline.

    A lease approves held calls to ``tool`` whose arguments include every key and
    value in ``match`` (other arguments may vary; declared ranges and domain checks
    still apply). It ends at ``expires_at`` or after ``uses`` approvals.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    match: dict[str, JsonValue] = Field(default_factory=dict)
    expires_at: AwareDatetime
    uses: int | None = Field(default=None, ge=1)
    granted_by: str = Field(min_length=1)
    reason: str = ""


def leased_actions(
    leases: Sequence[Lease], clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> Approver:
    """Approve held actions covered by a current lease, recording which lease approved.

    The first current, matching lease with uses left approves the action and uses
    one of its uses.
    """
    used: dict[str, int] = {}

    def approve(action: Action) -> Approval:
        now = clock()
        for lease in leases:
            if lease.tool != action.tool or now >= lease.expires_at:
                continue
            if any(action.arguments.get(key) != value for key, value in lease.match.items()):
                continue
            if lease.uses is not None and used.get(lease.name, 0) >= lease.uses:
                continue
            used[lease.name] = used.get(lease.name, 0) + 1
            return Approval(approved=True, by=f"lease:{lease.name} ({lease.granted_by})")
        return Approval(approved=False)

    return approve


def first_approval(*approvers: Approver) -> Approver:
    """Ask each approver in order; the first approval wins. Refuses if none approve."""

    async def approve(action: Action) -> Approval:
        for approver in approvers:
            granted = approver(action)
            answer = await granted if inspect.isawaitable(granted) else granted
            if isinstance(answer, Approval) and answer.approved:
                return answer
            if answer is True:
                return Approval(approved=True)
        return Approval(approved=False)

    return approve
