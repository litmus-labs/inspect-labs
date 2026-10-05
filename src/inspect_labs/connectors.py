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
import keyword
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


class ServerHints(BaseModel):
    """A server's own annotations for a tool (MCP tool annotations)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    read_only: bool | None = None
    destructive: bool | None = None
    open_world: bool | None = None

    def disagrees_with(self, action: ActionType | None) -> bool:
        """Whether the server's hints contradict a classification as ``read``."""
        return action == "read" and (self.read_only is False or self.destructive is True)


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
    server_hints: ServerHints | None = None
    """What the server says about the tool, for reviewers. Never trusted: a server's
    hints are its own claims, so the gateway decides by ``action`` alone."""


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
    return {name: (d, s) for name, (d, s, _) in (await _list_tools_with_hints(server)).items()}


async def _list_tools_with_hints(
    server: ConnectorServer,
) -> dict[str, tuple[str, dict[str, Any], ServerHints | None]]:
    async with _client(server) as client:
        listed = await client.list_tools()
    tools = {}
    for tool in listed.tools:
        notes = tool.annotations
        hints = (
            ServerHints(
                read_only=getattr(notes, "read_only_hint", None),
                destructive=getattr(notes, "destructive_hint", None),
                open_world=getattr(notes, "open_world_hint", None),
            )
            if notes is not None
            else None
        )
        tools[tool.name] = (tool.description or "", dict(tool.input_schema), hints)
    return tools


async def snapshot_connector(
    name: str,
    server: ConnectorServer,
    *,
    version: str = "1",
    template: Mapping[str, Classification] | None = None,
) -> ConnectorProfile:
    """List a connector's tools and pin them, ready for a person to review.

    Tools named in ``template`` get its classification; every other tool is left
    unclassified, so it is not offered until someone classifies it. The server's own
    hints are kept for reviewers but never decide anything.
    """
    template = template or {}
    tools = {}
    listed = await _list_tools_with_hints(server)
    for tool_name, (description, schema, hints) in sorted(listed.items()):
        known = template.get(tool_name)
        tools[tool_name] = ConnectorTool(
            description=description,
            input_schema=schema,
            definition_sha256=definition_digest(tool_name, description, schema),
            action=known.action if known else None,
            sequence_arguments=known.sequence_arguments if known else (),
            note=known.note if known else "",
            server_hints=hints,
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


class _Omitted:
    """Default for an optional argument the agent didn't give, unlike an explicit null."""

    inspect_labs_omitted = True
    """Tells the gateway wrapper to leave this argument out of the record and the call."""

    def __repr__(self) -> str:
        return "omitted"


OMITTED: Any = _Omitted()

_JSON_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _aliases(schema: Mapping[str, Any]) -> dict[str, str]:
    """Python-safe names for a tool's arguments, mapped to the connector's own names.

    JSON Schema allows names like ``query-string`` or ``class`` that can't be Python
    parameters; the agent sees a safe alias, translated back for the connector.
    """
    names = list((schema.get("properties") or {}).keys())
    aliases: dict[str, str] = {}
    for name in names:
        alias = name
        if not name.isidentifier() or keyword.iskeyword(name):
            alias = re.sub(r"\W", "_", name)
            if not alias or not (alias[0].isalpha() or alias[0] == "_"):
                alias = f"arg_{alias}"
            if keyword.iskeyword(alias):
                alias += "_"
            base, number = alias, 2
            while alias in aliases or (alias in names and alias != name):
                alias, number = f"{base}_{number}", number + 1
        aliases[alias] = name
    return aliases


def _alias_of(schema: Mapping[str, Any], original: str) -> str:
    """The agent-facing name of one of the connector's argument names."""
    return next((a for a, o in _aliases(schema).items() if o == original), original)


def _tool_params(schema: Mapping[str, Any]) -> ToolParams:
    aliases = {original: alias for alias, original in _aliases(schema).items()}
    properties = {
        aliases[name]: spec for name, spec in dict(schema.get("properties") or {}).items()
    }
    required = [aliases.get(name, name) for name in schema.get("required") or []]
    for name, spec in properties.items():
        if isinstance(spec, dict) and not spec.get("description"):
            properties[name] = {**spec, "description": name.replace("_", " ")}
    return ToolParams.model_validate(
        {"type": "object", "properties": properties, "required": required}
    )


def _signature(schema: Mapping[str, Any]) -> inspect.Signature:
    properties = schema.get("properties") or {}
    aliases = {original: alias for alias, original in _aliases(schema).items()}
    required = {aliases.get(name, name) for name in schema.get("required") or []}
    parameters = []
    for original, spec in properties.items():
        name = aliases[original]
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
                    default=OMITTED,
                    annotation=annotation | None,
                )
            )
    return inspect.Signature(parameters, return_annotation=str)


def _definition_problem(
    name: str, tool: ConnectorTool, listed: Mapping[str, tuple[str, dict[str, Any]]]
) -> Decision | None:
    """A refusal if the connector no longer offers ``name`` as it was reviewed."""
    if name not in listed:
        reason = f"{name} is no longer offered by the connector"
    elif definition_digest(name, *listed[name]) != tool.definition_sha256:
        reason = f"{name} changed since it was reviewed; review the connector again"
    else:
        return None
    return Decision(outcome="deny", rule="connector:definition-changed", reason=reason)


def _result_text(result: Any) -> str:
    """All of a tool result as text: text blocks as they are, anything else (images,
    resources, structured content) as JSON, so nothing is silently dropped."""
    parts = []
    for block in result.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
        else:
            dumped = block.model_dump(mode="json", exclude_none=True)
            parts.append(json.dumps(dumped, sort_keys=True))
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps({"structured_content": structured}, sort_keys=True))
    return "\n".join(parts)


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
        hidden = sorted(set(profile.tools) - set(self._offered))
        self.info = LabInfo(
            name=f"connector-{profile.name}",
            version=profile.version,
            mode="computation",
            capabilities=frozenset({f"connector:{profile.name}"}),
            operations={
                name: OperationSpec(
                    action=tool.action,
                    parameters={
                        _alias_of(tool.input_schema, key): spec
                        for key, spec in tool.parameters.items()
                    },
                )
                for name, tool in self._offered.items()
            },
            notes=(profile.notes + " " if profile.notes else "")
            + "Results come from an outside service and may be wrong or misleading."
            + (f" Not offered until classified: {', '.join(hidden)}." if hidden else ""),
        )
        self.checks = [self.pinned_definitions, self.screen_sequences]

    async def pinned_definitions(self, action: Action) -> Decision | None:
        """Refuse a call to a tool whose definition changed since it was reviewed.

        Checked before every call, and again on the same connection as the call.
        """
        tool = self._offered.get(action.tool)
        if tool is None:
            return None
        listed = await _list_tools(self.profile.server)
        return _definition_problem(action.tool, tool, listed)

    async def screen_sequences(self, action: Action) -> Decision | None:
        """Screen sequence arguments; refuse if any is flagged or can't be screened."""
        tool = self._offered.get(action.tool)
        if tool is None or not tool.sequence_arguments:
            return None
        sequences: list[tuple[str, str]] = []
        for name in tool.sequence_arguments:
            value = action.arguments.get(_alias_of(tool.input_schema, name))
            if value is None or value == "":
                continue
            if isinstance(value, str):
                sequences.append((name, value))
            elif isinstance(value, list) and all(isinstance(v, str) and v for v in value):
                sequences.extend((name, v) for v in value if isinstance(v, str))
            else:
                # Anything else could hide a sequence the screen can't read: refuse.
                return Decision(
                    outcome="deny",
                    rule="sequence-screen:unscreenable",
                    reason=f"{name} must be a sequence string or a list of them to be screened",
                )
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
        aliases = _aliases(spec.input_schema)

        async def run(**arguments: Any) -> str:
            # Omitted arguments are left out; an explicit null is passed on as null.
            given = {
                aliases.get(key, key): value
                for key, value in arguments.items()
                if value is not OMITTED
            }
            problem: Decision | None = None
            result: Any = None
            try:
                async with _client(lab.profile.server) as client:
                    # Check the definition on the same connection that makes the call,
                    # so a change after the gateway's check can't slip through.
                    listed = await client.list_tools()
                    problem = _definition_problem(
                        name,
                        spec,
                        {t.name: (t.description or "", dict(t.input_schema)) for t in listed.tools},
                    )
                    if problem is None:
                        result = await client.call_tool(name, given)
            except Exception as exc:
                lab._record(name, given, None, error=type(exc).__name__)
                reason = type(exc).__name__
                raise ToolError(f"The connector could not be reached ({reason})") from exc
            if problem is not None:
                lab._record(name, given, None, error="definition-changed")
                raise ToolError(problem.reason)
            text = _result_text(result)
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


def connector_lab(
    directory: Path, *, profile: Path | str, screener: Screener | None = None
) -> ConnectorLab:
    """Lab factory: a connector from a reviewed profile file.

    Args:
        directory: Private directory for this session's files.
        profile: Path to the connector profile JSON.
        screener: Screens sequence arguments; without one, sequence calls are refused.
    """
    profile_model = ConnectorProfile.model_validate_json(Path(profile).read_text())
    return ConnectorLab(profile_model, directory, screener=screener)
