"""Read a Fictionet world's own log out of its sandbox, completely or not at all.

A Fictionet world (https://github.com/amlalabs/fictionet-sdk, MIT or Apache-2.0)
runs a closed simulated internet in its own sandbox. The world process writes what
happened (DNS, TLS, HTTP and the world's own labels, such as a page that carries a
password) as JSON lines. The log lives in the world's sandbox and disappears at
teardown, so the Lab copies the episode's bytes out first.

Lines land asynchronously and there is no end marker, so a read waits until the
file stops growing. A short read, unparseable line or ``lost`` line makes the log
incomplete, and an incomplete log never counts as a complete record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

import anyio
from pydantic import BaseModel, ConfigDict, Field, JsonValue

SETTLE_INTERVAL = 0.2
SETTLE_ATTEMPTS = 50
COPY_PATH = "/tmp/inspect-labs-world-log.part"


class WorldLogSpec(BaseModel):
    """Where a world keeps its log.

    The SDK's ``fictionet_sandbox`` names the world's sandbox ``world``; hand-written
    compose files such as Border's name it ``fictionet``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    service: str = Field(default="world", min_length=1)
    log_path: str = Field(default="/var/lib/fictionet/log.jsonl", pattern=r"^/[A-Za-z0-9._/-]+$")
    """An absolute path of plain characters, since it is used in shell commands."""


class ExecResult(Protocol):
    """The part of Inspect's ``ExecResult`` this module reads."""

    @property
    def success(self) -> bool: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


class Sandbox(Protocol):
    """The part of Inspect's ``SandboxEnvironment`` this module uses."""

    async def exec(self, cmd: list[str], *, timeout: int | None = None) -> ExecResult: ...

    async def read_file(self, file: str, text: Literal[False]) -> bytes: ...


class WorldLogRead(BaseModel):
    """The episode's part of a world log, with its digest and completeness."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    start: int
    end: int
    sha256: str
    complete: bool
    problems: list[str]
    events: list[dict[str, JsonValue]]


async def log_size(world: Sandbox, spec: WorldLogSpec) -> int:
    """The log's current size in bytes; 0 when it does not exist yet.

    Raises:
        RuntimeError: The world's sandbox could not report the size.
    """
    result = await world.exec(
        [
            "sh",
            "-c",
            f"if [ -e '{spec.log_path}' ]; then wc -c < '{spec.log_path}'; else echo 0; fi",
        ],
        timeout=30,
    )
    if not result.success or not result.stdout.strip().isdigit():
        raise RuntimeError(f"Cannot read the world log's size: {result.stderr.strip()[:200]}")
    return int(result.stdout.strip())


async def settled_size(world: Sandbox, spec: WorldLogSpec) -> tuple[int, bool]:
    """Wait until the log stops growing. Returns its size and whether it settled."""
    size = await log_size(world, spec)
    for _ in range(SETTLE_ATTEMPTS):
        await anyio.sleep(SETTLE_INTERVAL)
        latest = await log_size(world, spec)
        if latest == size:
            return size, True
        size = latest
    return size, False


async def read_world_log(
    world: Sandbox, spec: WorldLogSpec, start: int
) -> tuple[WorldLogRead, bytes]:
    """Copy the log from ``start`` to its settled end, and check it.

    The bytes are copied to a file inside the world's sandbox and read back, so a
    large log is not cut short by command-output limits.

    Raises:
        RuntimeError: The world's sandbox could not be read.
    """
    end, settled = await settled_size(world, spec)
    problems: list[str] = [] if settled else ["the log was still growing"]
    if end < start:
        problems.append("the log shrank during the episode")
        start = 0
    count = end - start
    copy = await world.exec(
        ["sh", "-c", f"tail -c +{start + 1} '{spec.log_path}' | head -c {count} > {COPY_PATH}"],
        timeout=60,
    )
    if not copy.success:
        raise RuntimeError(f"Cannot copy the world log: {copy.stderr.strip()[:200]}")
    data = await world.read_file(COPY_PATH, text=False)
    if len(data) != count:
        problems.append(f"read {len(data)} of {count} bytes")
    events: list[dict[str, JsonValue]] = []
    for number, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number} is not JSON")
            continue
        if not isinstance(event, dict):
            problems.append(f"line {number} is not an object")
            continue
        if event.get("type") == "lost":
            problems.append(f"the world dropped {event.get('count', 'some')} lines")
        events.append(event)
    if data and not data.endswith(b"\n"):
        problems.append("the last line is incomplete")
    read = WorldLogRead(
        start=start,
        end=end,
        sha256=hashlib.sha256(data).hexdigest(),
        complete=not problems,
        problems=problems,
        events=events,
    )
    return read, data
