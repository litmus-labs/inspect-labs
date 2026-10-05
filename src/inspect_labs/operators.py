"""People in the loop: a live approval queue and a local control channel.

When the rules hold an action, `ApprovalQueue` keeps it waiting until an operator
approves or refuses it, or until a time limit passes, which refuses it. Operators
answer over a control channel that only they can reach: a Unix socket in a private
directory, readable and writable by the operator's own OS account. The agent's
tools (MCP or Inspect) never include the channel, so an agent can't approve its
own actions or lift a stop.

The channel speaks one JSON object per line. Requests: ``status``, ``pending``,
``approve``, ``refuse``, ``stop``. Each answer records the operator's name and note.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from anyio.abc import SocketStream
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from inspect_labs.actions import Action
from inspect_labs.gateway import Approval, Gateway

MAX_REQUEST_BYTES = 64 * 1024


class PendingAction(BaseModel):
    """An action waiting for an operator."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    action: Action
    requested_at: str
    expires_at: str


class ApprovalQueue:
    """An approver that waits for an operator's answer.

    Args:
        timeout: Seconds to wait before refusing; nobody answering is not approval.
    """

    def __init__(self, timeout: float = 300) -> None:
        if not timeout > 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self._pending: dict[str, tuple[PendingAction, anyio.Event]] = {}
        self._answers: dict[str, Approval] = {}

    def pending(self) -> list[PendingAction]:
        """Actions waiting for an answer, oldest first."""
        return [entry for entry, _ in self._pending.values()]

    def answer(self, id: str, *, approved: bool, by: str) -> None:
        """Approve or refuse one waiting action.

        Raises:
            KeyError: No action with this id is waiting (it was answered or expired).
            ValueError: ``by`` is empty; every answer names who gave it.
        """
        if not by.strip():
            raise ValueError("Name the operator giving the answer")
        _, event = self._pending[id]
        self._answers[id] = Approval(approved=approved, by=f"operator:{by.strip()}")
        event.set()

    def refuse_all(self) -> None:
        """Refuse everything waiting, for example when the session stops."""
        for id, (_, event) in list(self._pending.items()):
            self._answers.setdefault(id, Approval(approved=False))
            event.set()

    async def __call__(self, action: Action) -> Approval:
        now = datetime.now(UTC)
        id = uuid.uuid4().hex[:12]
        expires = datetime.fromtimestamp(now.timestamp() + self.timeout, UTC)
        entry = PendingAction(
            id=id, action=action, requested_at=now.isoformat(), expires_at=expires.isoformat()
        )
        event = anyio.Event()
        self._pending[id] = (entry, event)
        try:
            with anyio.move_on_after(self.timeout):
                await event.wait()
        finally:
            del self._pending[id]
        return self._answers.pop(id, Approval(approved=False))


class ControlRequest(BaseModel):
    """One operator request over the control channel."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    command: str = Field(pattern=r"^(status|pending|approve|refuse|stop)$")
    id: str | None = None
    by: str | None = None
    reason: str | None = None


class Control:
    """What operators can see and do for one session.

    Args:
        gateway: The session's gateway, which operators can stop.
        queue: The session's approval queue, if held actions wait for operators.
        describe: Extra status for operators, such as the Lab's name.
    """

    def __init__(
        self,
        gateway: Gateway,
        queue: ApprovalQueue | None = None,
        describe: Callable[[], dict[str, JsonValue]] | None = None,
    ) -> None:
        self.gateway = gateway
        self.queue = queue
        self.describe = describe

    def handle(self, request: ControlRequest) -> dict[str, Any]:
        """Answer one request. Errors come back as ``{"ok": false, "error": ...}``."""
        try:
            return {"ok": True} | self._handle(request)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc).strip("'\"")}

    def _handle(self, request: ControlRequest) -> dict[str, Any]:
        if request.command == "status":
            records = self.gateway.records
            return (self.describe() if self.describe else {}) | {
                "stopped": self.gateway.stopped,
                "actions": len(records),
                "refused": sum(1 for r in records if r.status == "refused"),
                "pending": len(self.queue.pending()) if self.queue else 0,
            }
        if request.command == "pending":
            waiting = self.queue.pending() if self.queue else []
            return {"pending": [entry.model_dump(mode="json") for entry in waiting]}
        if not request.by or not request.by.strip():
            raise ValueError("Name the operator with 'by'")
        if request.command == "stop":
            self.gateway.stop(request.reason or "Stopped by an operator", by=request.by)
            if self.queue:
                self.queue.refuse_all()
            return {"stopped": self.gateway.stopped}
        if self.queue is None:
            raise ValueError("This session has no approval queue")
        if request.id is None:
            raise ValueError("Give the pending action's id")
        self.queue.answer(request.id, approved=request.command == "approve", by=request.by)
        return {"answered": request.id}


def _private_socket_path(path: Path) -> None:
    """Make sure the socket's directory is private and no stale socket is in the way."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(path.parent.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"{path.parent} must be private (mode 700), not {oct(mode)}")
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise FileExistsError(f"{path} exists and is not a socket")
        path.unlink()


async def serve_control(control: Control, path: Path) -> None:
    """Serve the control channel on a Unix socket until cancelled.

    Raises:
        PermissionError: The socket's directory is readable by other users.
    """
    _private_socket_path(path)
    listener = await anyio.create_unix_listener(path)
    os.chmod(path, 0o600)

    async def serve(stream: SocketStream) -> None:
        async with stream:
            buffer = b""
            while b"\n" not in buffer and len(buffer) <= MAX_REQUEST_BYTES:
                try:
                    chunk = await stream.receive(4096)
                except (anyio.EndOfStream, anyio.BrokenResourceError):
                    return
                buffer += chunk
            line = buffer.split(b"\n", 1)[0]
            try:
                reply = control.handle(ControlRequest.model_validate_json(line))
            except ValueError as exc:
                reply = {"ok": False, "error": f"Bad request: {str(exc).splitlines()[0]}"}
            await stream.send(json.dumps(reply).encode() + b"\n")

    try:
        async with listener:
            await listener.serve(serve)
    finally:
        path.unlink(missing_ok=True)


def send_control(path: Path, request: ControlRequest, timeout: float = 10) -> dict[str, Any]:
    """Send one request to a session's control channel and return its answer.

    Raises:
        OSError: No session is listening at ``path``.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        client.sendall(request.model_dump_json(exclude_none=True).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    answer: dict[str, Any] = json.loads(data)
    return answer
