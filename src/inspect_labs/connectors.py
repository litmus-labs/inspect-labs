"""Connect an agent to outside services, such as biology databases and lab
software, through the gateway.

Many lab services are offered as MCP servers ("connectors"): literature and
sequence databases, structure databases, electronic lab notebooks, ordering. A
`ConnectorLab` puts one connector behind the Inspect Labs gateway, so every call is
checked, approved if needed, journaled and recorded like any other Lab action.

A connector is described by a `ConnectorProfile`, made once with
`snapshot_connector` and then reviewed by a person:

- Each tool's definition (description and input schema) is pinned by digest. If the
  server later changes a tool, calls to it are refused until a person reviews the
  change, so a connector can't quietly turn a search into a write.
- Each tool must be given an action type. Tools left without one are not offered to
  the agent at all (fail closed).
- Arguments that carry DNA or protein sequences can be named, so a sequence screen
  runs before anything leaves the lab.

What a connector returns is outside data: it can be wrong, and it can contain text
written to mislead the agent. The Lab records a digest of every result, and the
first part of its text, so reviewers can see what the agent was shown.

Needs the optional extra: ``pip install 'inspect-labs[serve]'``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from inspect_ai.tool import Tool, ToolDef, ToolError, ToolParams
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from inspect_labs.actions import Action, Decision
from inspect_labs.bindings import LabInfo
from inspect_labs.spec import ActionType, OperationSpec, ParameterSpec

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
RESULT_EXCERPT = 2000
"""Characters of each result kept in the lab log; the digest covers all of it."""


class ConnectorServer(BaseModel):
    """How to reach a connector: a local command or a remote URL.

    Credentials are never stored here. ``env`` names environment variables to pass
    to a local server; their values are read when the server starts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = Field(default=None, pattern=r"^https://")
    env: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _one_way(self) -> ConnectorServer:
        if (self.command is None) == (self.url is None):
            raise ValueError("Give either a command or an https URL")
        if bad := [name for name in self.env if not ENV_NAME.match(name)]:
            raise ValueError(f"Not environment variable names: {bad}")
        return self


class ConnectorTool(BaseModel):
    """One connector tool, pinned and classified."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    description: str
    input_schema: dict[str, JsonValue]
    definition_sha256: str
    """Digest of the tool's name, description and input schema when it was reviewed."""
    action: ActionType | None = None
    """None until a person classifies the tool; unclassified tools are not offered."""
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    sequence_arguments: tuple[str, ...] = ()
    """Arguments that carry DNA or protein sequences, screened before the call."""
    note: str = ""


class ConnectorProfile(BaseModel):
    """A reviewed description of one connector."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str = Field(min_length=1)
    server: ConnectorServer
    tools: dict[str, ConnectorTool]
    notes: str = ""


def definition_digest(name: str, description: str, input_schema: Mapping[str, Any]) -> str:
    """The pinned digest of a tool definition."""
    canonical = json.dumps(
        {"name": name, "description": description, "input_schema": input_schema},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class Classification(BaseModel):
    """A person's classification of one tool, for example from a connector template."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action: ActionType
    sequence_arguments: tuple[str, ...] = ()
    note: str = ""


TEMPLATES = Path(__file__).parent / "connector_templates"
"""Built-in classifications for common connectors, as reviewed starting points."""


def load_template(name_or_path: str) -> dict[str, Classification]:
    """Classifications from a built-in template name or a JSON file.

    Raises:
        LookupError: No built-in template or file has this name.
    """
    builtin = TEMPLATES / f"{name_or_path}.json"
    path = builtin if builtin.is_file() else Path(name_or_path)
    if not path.is_file():
        available = sorted(p.stem for p in TEMPLATES.glob("*.json"))
        raise LookupError(f"No template {name_or_path!r}; built-in: {available}")
    document = json.loads(path.read_text())
    return {name: Classification.model_validate(entry) for name, entry in document["tools"].items()}


def _client(server: ConnectorServer) -> Any:
    try:
        from mcp import Client, StdioServerParameters
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Connectors need the optional extra: pip install 'inspect-labs[serve]'"
        ) from exc
    if server.url is not None:
        return Client(server.url)
    environment = {name: os.environ[name] for name in server.env if name in os.environ}
    if "PATH" in os.environ:
        environment.setdefault("PATH", os.environ["PATH"])
    return Client(
        StdioServerParameters(command=server.command or "", args=list(server.args), env=environment)
    )


async def _list_tools(server: ConnectorServer) -> dict[str, tuple[str, dict[str, Any]]]:
    async with _client(server) as client:
        listed = await client.list_tools()
    return {tool.name: (tool.description or "", dict(tool.input_schema)) for tool in listed.tools}


async def snapshot_connector(
    name: str,
    server: ConnectorServer,
    *,
    version: str = "1",
    template: Mapping[str, Classification] | None = None,
) -> ConnectorProfile:
    """List a connector's tools and pin them, ready for a person to review.

    Tools named in ``template`` get its classification; every other tool is left
    unclassified, so it is not offered until someone classifies it.
    """
    template = template or {}
    tools = {}
    for tool_name, (description, schema) in sorted((await _list_tools(server)).items()):
        known = template.get(tool_name)
        tools[tool_name] = ConnectorTool(
            description=description,
            input_schema=schema,
            definition_sha256=definition_digest(tool_name, description, schema),
            action=known.action if known else None,
            sequence_arguments=known.sequence_arguments if known else (),
            note=known.note if known else "",
        )
    return ConnectorProfile(name=name, version=version, server=server, tools=tools)


class ScreenVerdict(BaseModel):
    """A sequence screen's answer for one sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["clear", "flagged", "unknown"]
    screener: str
    detail: str = ""


Screener = Callable[[str], Awaitable[ScreenVerdict]]
"""Screens one DNA or protein sequence, for example with a synthesis screening tool."""


_JSON_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _tool_params(schema: Mapping[str, Any]) -> ToolParams:
    properties = dict(schema.get("properties") or {})
    for name, spec in properties.items():
        if isinstance(spec, dict) and not spec.get("description"):
            properties[name] = {**spec, "description": name.replace("_", " ")}
    return ToolParams.model_validate(
        {"type": "object", "properties": properties, "required": list(schema.get("required") or [])}
    )


def _signature(schema: Mapping[str, Any]) -> inspect.Signature:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parameters = []
    for name, spec in properties.items():
        kind = spec.get("type") if isinstance(spec, dict) else None
        annotation = _JSON_TYPES.get(kind, Any) if isinstance(kind, str) else Any
        if name in required:
            parameters.append(
                inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation)
            )
        else:
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=annotation | None,
                )
            )
    return inspect.Signature(parameters, return_annotation=str)


class ConnectorLab:
    """One connector behind the gateway, as a Lab.

    Args:
        profile: The reviewed connector profile.
        directory: Private directory for this session's files.
        screener: Screens sequence arguments before a call. Without one, calls with
            sequence arguments are refused.
    """

    def __init__(
        self,
        profile: ConnectorProfile,
        directory: Path,
        *,
        screener: Screener | None = None,
    ) -> None:
        self.profile = profile
        self.directory = directory
        self.screener = screener
        self.calls: list[dict[str, JsonValue]] = []
        self._offered = {name: tool for name, tool in profile.tools.items() if tool.action}
        self._live: dict[str, str] | None = None
        hidden = sorted(set(profile.tools) - set(self._offered))
        self.info = LabInfo(
            name=f"connector-{profile.name}",
            version=profile.version,
            mode="computation",
            capabilities=frozenset({f"connector:{profile.name}"}),
            operations={
                name: OperationSpec(action=tool.action, parameters=tool.parameters)
                for name, tool in self._offered.items()
            },
            notes=(profile.notes + " " if profile.notes else "")
            + "Results come from an outside service and may be wrong or misleading."
            + (f" Not offered until classified: {', '.join(hidden)}." if hidden else ""),
        )
        self.checks = [self.pinned_definitions, self.screen_sequences]

    async def pinned_definitions(self, action: Action) -> Decision | None:
        """Refuse a call to a tool whose definition changed since it was reviewed."""
        tool = self._offered.get(action.tool)
        if tool is None:
            return None
        if self._live is None:
            listed = await _list_tools(self.profile.server)
            self._live = {
                name: definition_digest(name, description, schema)
                for name, (description, schema) in listed.items()
            }
        live = self._live.get(action.tool)
        if live == tool.definition_sha256:
            return None
        return Decision(
            outcome="deny",
            rule="connector:definition-changed",
            reason=f"{action.tool} is no longer offered by the connector"
            if live is None
            else f"{action.tool} changed since it was reviewed; review the connector again",
        )

    async def screen_sequences(self, action: Action) -> Decision | None:
        """Screen sequence arguments; refuse if any is flagged or can't be screened."""
        tool = self._offered.get(action.tool)
        if tool is None or not tool.sequence_arguments:
            return None
        sequences = [
            (name, value)
            for name in tool.sequence_arguments
            if isinstance(value := action.arguments.get(name), str) and value
        ]
        if not sequences:
            return None
        if self.screener is None:
            return Decision(
                outcome="deny",
                rule="sequence-screen:unavailable",
                reason="This call carries sequences and no sequence screen is configured",
            )
        for name, sequence in sequences:
            verdict = await self.screener(sequence)
            if verdict.outcome != "clear":
                return Decision(
                    outcome="deny",
                    rule=f"sequence-screen:{verdict.outcome}",
                    reason=f"The sequence in {name} was {verdict.outcome} by {verdict.screener}"
                    + (f": {verdict.detail}" if verdict.detail else ""),
                )
        return None

    @property
    def tools(self) -> list[Tool]:
        """The classified connector tools, each calling the connector once."""
        return [self._tool(name, tool) for name, tool in self._offered.items()]

    def _tool(self, name: str, spec: ConnectorTool) -> Tool:
        lab = self

        async def run(**arguments: Any) -> str:
            given = {key: value for key, value in arguments.items() if value is not None}
            try:
                async with _client(lab.profile.server) as client:
                    result = await client.call_tool(name, given)
            except Exception as exc:
                lab._record(name, given, None, error=type(exc).__name__)
                raise ToolError(
                    f"The connector could not be reached ({type(exc).__name__})"
                ) from exc
            text = "\n".join(
                part.text for part in result.content if getattr(part, "type", "") == "text"
            )
            lab._record(name, given, text, error="tool-error" if result.is_error else None)
            if result.is_error:
                raise ToolError(text or "The connector returned an error")
            return text

        run.__signature__ = _signature(spec.input_schema)  # type: ignore[attr-defined]
        run.__annotations__ = {
            parameter.name: parameter.annotation
            for parameter in run.__signature__.parameters.values()  # type: ignore[attr-defined]
        } | {"return": str}
        return ToolDef(
            run, name=name, description=spec.description, parameters=_tool_params(spec.input_schema)
        ).as_tool()

    def _record(
        self, tool: str, arguments: dict[str, Any], text: str | None, *, error: str | None
    ) -> None:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
        entry: dict[str, JsonValue] = {
            "call": len(self.calls) + 1,
            "at": datetime.now(UTC).isoformat(),
            "tool": tool,
            "arguments_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        }
        if text is not None:
            entry |= {
                "result_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "result_chars": len(text),
                "result_excerpt": text[:RESULT_EXCERPT],
            }
        if error is not None:
            entry["error"] = error
        self.calls.append(entry)

    @property
    def artifacts(self) -> list[Path]:
        """No files; the lab log carries each call's digests."""
        return []

    async def observe(self) -> dict[str, JsonValue]:
        """What the connector returned to the agent. Calls nothing."""
        return {
            "connector": self.profile.name,
            "profile_version": self.profile.version,
            "calls": list(self.calls),
        }

    async def close(self) -> None:
        """Nothing to release; each call opens and closes its own connection."""


def connector_lab(directory: Path, *, profile: Path | str) -> ConnectorLab:
    """Lab factory: a connector from a reviewed profile file.

    Args:
        directory: Private directory for this session's files.
        profile: Path to the connector profile JSON.
    """
    return ConnectorLab(ConnectorProfile.model_validate_json(Path(profile).read_text()), directory)
