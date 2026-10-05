"""Serve a Lab to any agent, through the gateway.

`LabSession` is one agent session on a Lab. Every tool call goes through the same
`Gateway` the evaluation harness uses, so the same rules decide the same way. When
the session ends, a hash-chained lab log is written, with monitor flags, that
`inspect-labs replay-rules` and reviewers can read.

`mcp_server` exposes a session over MCP, so any MCP agent can use the Lab's tools.
`inspect-labs serve` runs it over stdio. An operator can stop the session at any
time (`LabSession.stop`, or a stop file); every later action is then refused.

The MCP server needs the optional extra: ``pip install 'inspect-labs[serve]'``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from inspect_ai.tool import Tool, ToolDef
from pydantic import JsonValue

from inspect_labs.actions import ActionRecord, ActionRules
from inspect_labs.bindings import Lab, LabSessionLog, _write_private, lab_checks, record_lab_log
from inspect_labs.gateway import ActionRefused, Approver, Check, Gateway, first_approval
from inspect_labs.journal import Journal
from inspect_labs.monitors import (
    DEFAULT_MONITORS,
    LIVE_MONITORS,
    Flag,
    Monitor,
    MonitorInput,
    run_monitors,
)
from inspect_labs.operators import ApprovalQueue, Control, serve_control

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server


class LabSession:
    """One agent session on a Lab, with every action going through the gateway.

    Args:
        lab: The Lab to serve. The session closes it when it finishes.
        rules: The action rules (the same rule file the evaluation harness uses).
        approver: Called first for held actions, for approvals given in advance
            or leases. Without one and without a queue, held actions are refused.
        monitors: Run on the session's lab log when it finishes.
        stop_file: If this file exists before an action, the session stops; the
            file's text is recorded as the reason.
        checks: Domain checks run after the rules, together with the Lab's own.
        queue: Held actions the approver doesn't approve wait here for an operator.
        journal: Where each step is written as it happens, so a crash loses nothing.
        live_monitors: Run after every action while the session runs.
        stop_on: Names of live monitors whose flags stop the session at once.
    """

    def __init__(
        self,
        lab: Lab,
        rules: ActionRules,
        *,
        approver: Approver | None = None,
        monitors: Sequence[Monitor] = DEFAULT_MONITORS,
        stop_file: Path | None = None,
        checks: Sequence[Check] = (),
        queue: ApprovalQueue | None = None,
        journal: Journal | None = None,
        live_monitors: Sequence[Monitor] = LIVE_MONITORS,
        stop_on: frozenset[str] = frozenset(),
    ) -> None:
        self.lab = lab
        self.info = lab.info
        self.rules = rules
        self.monitors = monitors
        self.stop_file = stop_file
        self.queue = queue
        self.journal = journal
        self.live_monitors = tuple(live_monitors)
        self.stop_on = stop_on
        self.live_flags: list[Flag] = []
        self.session_id = str(uuid.uuid4())
        self.started_at = datetime.now(UTC).isoformat()
        approvers = [a for a in (approver, queue) if a is not None]
        combined = (
            None
            if not approvers
            else approvers[0]
            if len(approvers) == 1
            else first_approval(*approvers)
        )
        self.gateway = Gateway(
            self.info.operations,
            rules,
            combined,
            checks=(*lab_checks(lab), *checks),
            journal=journal,
        )
        self.gateway.listeners.append(self._watch)
        self.control = Control(self.gateway, queue, self._describe)
        self._tools: dict[str, Tool] = {ToolDef(tool).name: tool for tool in lab.tools}
        if journal is not None:
            journal.append(
                "started",
                {
                    "session": self.session_id,
                    "lab": self.info.name,
                    "lab_version": self.info.version,
                    "rules_version": rules.version,
                    "framework_version": version("inspect-labs"),
                },
            )

    def _describe(self) -> dict[str, JsonValue]:
        return {"session": self.session_id, "lab": self.info.name, "started_at": self.started_at}

    def _watch(self, record: ActionRecord) -> None:
        """Run live monitors after each action; stop at once on a tripwire."""
        entry = MonitorInput(
            sample=self.session_id,
            actions=list(self.gateway.records),
            observed=False,
            report=None,
            scores={},
        )
        seen = {(f.monitor, f.sequence, f.reason) for f in self.live_flags}
        for flag in run_monitors(entry, self.live_monitors):
            if (flag.monitor, flag.sequence, flag.reason) in seen:
                continue
            self.live_flags.append(flag)
            if self.journal is not None:
                self.journal.append("flag", flag.model_dump(mode="json", exclude_none=True))
            if flag.monitor in self.stop_on:
                self.stop(f"Stopped by the {flag.monitor} monitor: {flag.reason}", by="monitor")

    def stop(self, reason: str, *, by: str = "operator") -> None:
        """Refuse every later action in this session, and everything waiting."""
        self.gateway.stop(reason, by=by)
        if self.queue is not None:
            self.queue.refuse_all()

    def tool_list(self) -> list[dict[str, Any]]:
        """The Lab's tools as name, description and JSON Schema for their arguments."""
        listed = []
        for tool in self._tools.values():
            definition = ToolDef(tool)
            listed.append(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "input_schema": definition.parameters.model_dump(
                        mode="json", exclude_none=True
                    ),
                }
            )
        return listed

    async def call(self, tool: str, arguments: dict[str, JsonValue]) -> str:
        """Run one tool call through the gateway and return the Lab's result as text.

        Raises:
            ActionRefused: The action was refused or not approved; it never reached the Lab.
            KeyError: The Lab has no such tool.
        """
        if self.stop_file is not None and self.stop_file.exists() and not self.gateway.stopped:
            reason = self.stop_file.read_text().strip() or "Stopped by the operator"
            self.stop(reason, by="stop-file")
        if tool not in self._tools:
            raise KeyError(f"No tool named {tool!r}")
        lab_tool = self._tools[tool]

        async def run() -> Any:
            return await lab_tool(**arguments)

        result = await self.gateway.run(tool, arguments, run)
        return result if isinstance(result, str) else json.dumps(result)

    async def finish(self, lab_log_file: Path, observation_timeout: float = 30) -> LabSessionLog:
        """Read what the Lab did, write the session's lab log, and close the Lab.

        The file is never overwritten.

        Raises:
            OSError: The file already exists or cannot be written.
        """
        try:
            record = await record_lab_log(
                self.session_id, self.lab, self.info, self.gateway.records, observation_timeout
            )
        finally:
            await self.lab.close()
        entry = MonitorInput(
            sample=self.session_id,
            actions=record.actions,
            observed=record.observation_error is None and record.payload is not None,
            report=None,
            scores={},
            observation=record.payload,
        )
        flags = list(self.live_flags)
        seen = {(f.monitor, f.sequence, f.reason) for f in flags}
        flags += [
            f
            for f in run_monitors(entry, self.monitors)
            if (f.monitor, f.sequence, f.reason) not in seen
        ]
        log = LabSessionLog(
            lab=self.info.name,
            rules_version=self.rules.version,
            framework_version=version("inspect-labs"),
            started_at=self.started_at,
            ended_at=datetime.now(UTC).isoformat(),
            stopped=self.gateway.stopped,
            samples={self.session_id: record},
            flags=flags,
            journal_sha256=self.journal.head if self.journal is not None else None,
        )
        _write_private(lab_log_file, log)
        if self.journal is not None:
            self.journal.append(
                "ended",
                {
                    "lab_log": str(lab_log_file),
                    "chain_sha256": record.chain_sha256,
                    "stopped": self.gateway.stopped,
                },
            )
            self.journal.close()
        return log


def mcp_server(session: LabSession) -> Server[Any]:
    """An MCP server exposing a session's Lab tools, each call through the gateway.

    A refused action comes back as an error result with the reason; it never reached
    the Lab. Stopping is not exposed to the agent.
    """
    try:
        import mcp.types as types
        from mcp.server.lowlevel import Server
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Serving a Lab over MCP needs the optional extra: pip install 'inspect-labs[serve]'"
        ) from exc

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool["name"],
                    description=tool["description"],
                    input_schema=tool["input_schema"],
                )
                for tool in session.tool_list()
            ]
        )

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            text = await session.call(params.name, dict(params.arguments or {}))
        except ActionRefused as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        except Exception as exc:
            # Lab errors stay private; the agent sees only their type.
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Tool error: {type(exc).__name__}")],
                is_error=True,
            )
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server(
        f"inspect-labs:{session.info.name}",
        version=version("inspect-labs"),
        instructions="Laboratory tools served through the Inspect Labs gateway. Each "
        "action is checked against the lab's rules before it runs; irreversible actions "
        "may need a person's approval.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_over_stdio(
    session: LabSession, lab_log_file: Path, control_socket: Path | None = None
) -> LabSessionLog:
    """Serve a session to one MCP agent over stdio until it disconnects, then write the
    session's lab log. With ``control_socket``, operators can watch, approve and stop
    the session over a private local socket while it runs."""
    try:
        from mcp.server.stdio import stdio_server
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Serving a Lab over MCP needs the optional extra: pip install 'inspect-labs[serve]'"
        ) from exc
    server = mcp_server(session)
    try:
        async with anyio.create_task_group() as group:
            if control_socket is not None:
                group.start_soon(serve_control, session.control, control_socket)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
            group.cancel_scope.cancel()
    finally:
        log = await session.finish(lab_log_file)
    return log
