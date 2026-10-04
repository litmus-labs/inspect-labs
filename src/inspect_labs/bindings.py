"""Bind laboratory tools and private observations to native Inspect lifecycles."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
import re
import typing
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

import anyio
from inspect_ai import Task, score
from inspect_ai.hooks import Hooks, RunEnd, SampleInit, SampleScoring, TaskEnd, TaskStart, hooks
from inspect_ai.log import list_eval_logs, read_eval_log, read_eval_log_async, write_eval_log
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, chain, solver
from inspect_ai.tool import Tool, ToolDef, ToolError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer

from inspect_labs.actions import (
    ActionRecord,
    ActionRules,
    ReplayedDecision,
    replay_decisions,
)
from inspect_labs.errors import CompatibilityError, SafetyAbort
from inspect_labs.gateway import ActionRefused, Approver, Check, Gateway
from inspect_labs.monitors import Flag, Monitor, MonitorInput, run_monitors
from inspect_labs.spec import OperationSpec, Requirements, compatibility_problems

logger = logging.getLogger(__name__)


class LabInfo(BaseModel):
    """A provider's explicit evaluation capabilities, not an execution controller."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    mode: Literal["computation", "simulation", "physical"]
    capabilities: frozenset[str]
    operations: dict[str, OperationSpec] = Field(default_factory=dict)
    notes: str = ""
    """Honest operating notes for agents and operators (hardware, units, limits)."""
    fidelity: Literal["simulator", "twin", "hardware"] | None = None
    """How close to real equipment results are. Declare ``twin`` for a digital twin of
    a specific instrument. When not set, the lab log records it from ``mode``
    (simulation: simulator, physical: hardware; none for computation)."""


def _with_fidelity(info: LabInfo) -> LabInfo:
    """Record fidelity explicitly in the lab log, derived from mode when not declared."""
    if info.fidelity is not None:
        return info
    derived = {"simulation": "simulator", "physical": "hardware"}.get(info.mode)
    return info if derived is None else info.model_copy(update={"fidelity": derived})

    @field_serializer("capabilities")
    def _sorted_capabilities(self, capabilities: frozenset[str]) -> list[str]:
        # Stable order so saved lab logs hash the same after reloading.
        return sorted(capabilities)


class Lab(Protocol):
    """Provider binding owned by one native sample; tools own provider-specific APIs."""

    @property
    def info(self) -> LabInfo:
        """Declare evidence mode and capabilities before actor dispatch."""
        ...

    @property
    def tools(self) -> list[Tool]:
        """Return scoped native tools. Never expose the provider object to the actor."""
        ...

    async def observe(self) -> dict[str, JsonValue] | None:
        """Read evaluator-owned facts without submitting, advancing or cancelling work."""
        ...

    @property
    def artifacts(self) -> list[Path]:
        """Return native child logs and evidence files that support the observation."""
        ...

    async def close(self) -> None:
        """Release client resources without claiming outstanding work has stopped."""
        ...


def lab_checks(lab: Lab) -> tuple[Check, ...]:
    """A Lab's own domain checks, from an optional ``checks`` attribute.

    A Lab can declare checks that only it can make, such as a plant's safety gates
    that read its current state. They run in the gateway after the rules.

    Raises:
        TypeError: ``checks`` is not a sequence of callables.
    """
    declared = getattr(lab, "checks", ())
    if not isinstance(declared, (list, tuple)) or not all(callable(c) for c in declared):
        raise TypeError("A Lab's checks must be a list or tuple of callables")
    return tuple(declared)


def _safety_abort(environment: Lab) -> str | None:
    """Optional provider signal: a ``safety_abort`` reason attribute halts the task."""
    reason = getattr(environment, "safety_abort", None)
    return reason if isinstance(reason, str) and reason else None


class ArtifactLink(BaseModel):
    """Integrity link to an existing private provider or native child artifact."""

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LateResult(BaseModel):
    """A result that arrived after the run, attached to a sample's lab log."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    attached_at: str
    payload: dict[str, JsonValue]
    note: str = ""
    previous_chain_sha256: str
    """The sample's chain digest just before this entry was attached."""


class LabLog(BaseModel):
    """Task-owned payload collected independently of the actor and native transcript."""

    model_config = ConfigDict(extra="forbid")
    sample_uuid: str
    environment: LabInfo
    collected_at: str
    payload: dict[str, JsonValue] | None
    observation_error: str | None = None
    safety_abort: str | None = None
    artifacts: list[ArtifactLink] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    """Checked agent actions, in order, when the task was bound with an action policy."""
    late_results: list[LateResult] = Field(default_factory=list)
    """Results attached after the run, oldest first. Scoring uses the latest."""
    chain_sha256: str | None = None
    """Hash chain over this sample's action records, its observation, then any late
    observations (schema 3). It detects changed records; it does not prevent
    tampering by a trusted writer."""


_CHAIN_START = hashlib.sha256(b"inspect-labs/lab-log/v3").hexdigest()


def _chain(digest: str, entries: list[dict[str, Any]]) -> str:
    for entry in entries:
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256((digest + canonical).encode()).hexdigest()
    return digest


def run_time_hash(record: LabLog) -> str:
    """Chain one sample's action records, in order, then its observation.

    This is the digest at the end of the run, also stored in the native log.
    """
    # Fields left at their defaults are omitted, so adding a defaulted field to the
    # schema later does not change the digest of lab logs written before it.
    entries: list[dict[str, Any]] = [
        action.model_dump(mode="json", exclude_defaults=True) for action in record.actions
    ]
    observation = record.model_dump(
        mode="json",
        exclude={"actions", "late_results", "chain_sha256"},
        exclude_defaults=True,
    )
    # Sets have no stable order across processes; sort them explicitly. (Pydantic
    # skips field serializers when exclude_defaults is set.)
    environment = observation["environment"]
    environment["capabilities"] = sorted(environment["capabilities"])
    entries.append(observation)
    return _chain(_CHAIN_START, entries)


def lab_log_hash(record: LabLog) -> str:
    """The sample's full chain: the run-time digest, then each late observation.

    Each step hashes the previous digest with the next entry's canonical JSON, so
    changing, removing or reordering any entry changes the result.
    """
    late = [entry.model_dump(mode="json", exclude_defaults=True) for entry in record.late_results]
    return _chain(run_time_hash(record), late)


class LabLogFile(BaseModel):
    """One native run's private references and observed facts for deterministic rescore."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2, 3] = 3
    """3 adds per-sample hash chains (`LabLog.chain_sha256`); 2 is still read."""
    task: str
    native_sha256: str
    framework_version: str
    inspect_version: str
    metrics: tuple[str, ...] = ("known", "correct")
    samples: dict[str, LabLog]


EvidenceJudge = Callable[[str, LabLog], dict[str, int | float]]
"""Pure outcome function of the agent's report and the lab log. Return ``known=0``
only when facts could not be observed; an observed absence of work is a known,
incorrect outcome. Being pure lets the same function rescore saved evidence."""


def _scorer_argument(
    scorer: EvidenceJudge | None, judge: EvidenceJudge | None, where: str
) -> EvidenceJudge:
    """Resolve ``scorer`` and its deprecated ``judge`` alias to one function."""
    if judge is not None:
        if scorer is not None:
            raise TypeError(f"{where}: pass scorer or judge, not both")
        warnings.warn(
            f"{where}(judge=...) is deprecated; pass scorer=... instead",
            DeprecationWarning,
            stacklevel=3,
        )
        return judge
    if scorer is None:
        raise TypeError(f"{where}: missing required argument 'scorer'")
    return scorer


def artifact_digest(path: Path) -> str:
    """Compute the content hash used for native artifact linkage."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_text_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(text)


def _write_private(path: Path, data: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(data.model_dump_json(indent=2) + "\n")


@dataclass(frozen=True)
class _Attempt:
    """Native identities of one sample execution: admission scope, task execution, run."""

    scope: str
    eval_id: str
    run_id: str
    task_id: str
    task_name: str


@dataclass
class _Session:
    environment: Lab
    directory: Path
    observation_timeout: float
    attempt: _Attempt
    info: LabInfo
    metrics: tuple[str, ...]
    evidence: LabLog | None = None
    closed: bool = False
    actions: list[ActionRecord] = field(default_factory=list)


# Hook state only: native Inspect owns scheduling, retries, cancellation and scoring.
# Keys are native identities: sample UUID (sessions/attempts) and eval_id (tasks).
_sessions: dict[str, _Session] = {}
_task_scopes: dict[str, tuple[str, str, str]] = {}
_sample_scopes: dict[str, _Attempt] = {}


def _latch(evidence_dir: Path, task_name: str) -> Path:
    """Durable safety latch for one task within one study's private evidence directory.

    Keyed by the task's registered name, not Inspect's per-run task_id, so native
    retries, later runs and other parameterizations of the task are all refused.
    """
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", task_name)[:80]
    digest = hashlib.sha256(task_name.encode()).hexdigest()[:12]
    return evidence_dir / f"{slug}-{digest}.safety-abort"


# Latches whose file could not be written; refused in-process so failure stays closed.
_unpersisted_latches: dict[str, str] = {}


def _persist_latch(latch: Path, reason: str) -> None:
    """Write a latch without ever raising; keep it in memory if the write fails."""
    try:
        _write_text_private(latch, reason + "\n")
    except FileExistsError:
        pass
    except OSError as exc:
        _unpersisted_latches[str(latch)] = reason
        logger.warning(
            "Safety latch %s could not be written (%s); halting in-process only",
            latch,
            type(exc).__name__,
        )


async def record_lab_log(
    sample_id: str,
    lab: Lab,
    info: LabInfo,
    actions: list[ActionRecord],
    observation_timeout: float = 30,
) -> LabLog:
    """Read what the Lab did and return one hash-chained lab log entry.

    Used by evaluations and by served sessions, so both record the same way. A Lab
    that cannot be observed gives a lab log with ``observation_error`` set, which is
    scored as unknown.
    """
    links: list[ArtifactLink] = []
    try:
        with anyio.fail_after(observation_timeout):
            payload = await lab.observe()
        links = [
            ArtifactLink(path=str(path.resolve()), sha256=artifact_digest(path))
            for path in lab.artifacts
        ]
        record = LabLog(
            sample_uuid=sample_id,
            environment=_with_fidelity(info),
            collected_at=datetime.now(UTC).isoformat(),
            payload=payload,
            artifacts=links,
            safety_abort=_safety_abort(lab),
            actions=list(actions),
        )
    except Exception as exc:
        # Lab observation is an external boundary. Keep unavailable facts unknown,
        # and keep potentially sensitive provider exception text private.
        record = LabLog(
            sample_uuid=sample_id,
            environment=_with_fidelity(info),
            collected_at=datetime.now(UTC).isoformat(),
            payload=None,
            observation_error=type(exc).__name__,
            safety_abort=_safety_abort(lab),
            actions=list(actions),
        )
    return record.model_copy(update={"chain_sha256": lab_log_hash(record)})


async def _observe(sample_uuid: str) -> None:
    session = _sessions.get(sample_uuid)
    if session is None or session.evidence is not None:
        return
    evidence = await record_lab_log(
        sample_uuid,
        session.environment,
        session.info,
        session.actions,
        session.observation_timeout,
    )
    _write_private(session.directory / f"{sample_uuid}.lab-sample", evidence)
    session.evidence = evidence


@hooks(name="lab_log", description="Record lab logs and link them to the eval log")
class LabLogHooks(Hooks):
    """Native lifecycle hooks collect before scoring and seal after native log completion."""

    async def on_task_start(self, data: TaskStart) -> None:
        """Use Inspect's stable task identity, including native eval-set retries."""
        _task_scopes[data.eval_id] = (data.run_id, data.spec.task_id, data.spec.task)

    async def on_sample_init(self, data: SampleInit) -> None:
        """Associate native sample attempts with a stable task/sample/epoch identity."""
        task = _task_scopes.get(data.eval_id)
        if task:
            identity = f"{task[1]}:{data.summary.id}:{data.summary.epoch}"
            _sample_scopes[data.sample_id] = _Attempt(
                scope=hashlib.sha256(identity.encode()).hexdigest(),
                eval_id=data.eval_id,
                run_id=data.run_id,
                task_id=task[1],
                task_name=task[2],
            )

    async def on_sample_scoring(self, data: SampleScoring) -> None:
        """Collect even when the actor ended early or its solver was replaced."""
        await _observe(data.sample_id)

    async def on_task_end(self, data: TaskEnd) -> None:
        """Link the completed native log without changing its format or contents."""
        if _pending(lambda attempt: attempt.eval_id == data.eval_id):
            await _seal(data.log.location)

    async def on_run_end(self, data: RunEnd) -> None:
        """Seal task executions whose native task-end was skipped.

        Native Inspect emits no task-end for an errored task execution, and an
        eval-set run end lists only the final retry of each task. Earlier attempts'
        logs are located by their native eval_id beside the run's logs. Unsealed
        evidence remains in its per-sample ``.lab-sample`` record.
        """
        try:
            pending = _pending(lambda attempt: attempt.run_id == data.run_id)
            locations = {log.eval.eval_id: log.location for log in data.logs}
            missing = {eval_id for eval_id in pending if eval_id not in locations}
            directories = {str(Path(log.location).parent) for log in data.logs}
            for directory in sorted(directories) if missing else []:
                for info in list_eval_logs(directory, formats=["eval", "json"], recursive=False):
                    if not missing:
                        break
                    try:
                        header = await read_eval_log_async(info, header_only=True)
                    except Exception:
                        continue  # Unrelated or partial files do not block sealing.
                    if header.eval.eval_id in missing:
                        # Native listings are URIs; companions are local files.
                        name = PurePosixPath(info.name).name
                        locations[header.eval.eval_id] = str(Path(directory) / name)
                        missing.discard(header.eval.eval_id)
            failures: list[BaseException] = []
            for eval_id in sorted(pending):
                if eval_id in locations:
                    try:
                        await _seal(locations[eval_id])
                    except Exception as exc:
                        failures.append(exc)
            if failures:
                raise failures[0]
        finally:
            for uuid in [u for u, s in _sessions.items() if s.attempt.run_id == data.run_id]:
                del _sessions[uuid]
            for uuid in [u for u, a in _sample_scopes.items() if a.run_id == data.run_id]:
                del _sample_scopes[uuid]
            for eval_id in [e for e, (run, _, _) in _task_scopes.items() if run == data.run_id]:
                del _task_scopes[eval_id]


def _pending(selected: Callable[[_Attempt], bool]) -> set[str]:
    """Return eval_ids of selected sessions whose collected evidence is not sealed."""
    return {
        session.attempt.eval_id
        for session in _sessions.values()
        if session.evidence is not None and selected(session.attempt)
    }


async def _seal(location: str) -> None:
    """Write one native log's companion; forget its sessions only after the write."""
    if "://" in location:
        raise ValueError(
            "Lab evidence companions require a local native log directory; "
            "evidence remains in its .lab-sample record"
        )
    log = await read_eval_log_async(location)
    uuids = [sample.uuid for sample in log.samples or [] if sample.uuid in _sessions]
    samples = {
        uuid: evidence
        for uuid in uuids
        if uuid is not None and (evidence := _sessions[uuid].evidence) is not None
    }
    sessions_metrics = {_sessions[uuid].metrics for uuid in samples}
    if samples:
        native_path = Path(location)
        _write_private(
            native_path.with_suffix(".labs"),
            LabLogFile(
                task=log.eval.task,
                native_sha256=artifact_digest(native_path),
                framework_version=version("inspect-labs"),
                inspect_version=version("inspect-ai"),
                metrics=next(iter(sessions_metrics)),
                samples=samples,
            ),
        )
    for uuid in uuids:
        if uuid is not None:
            del _sessions[uuid]


DEFAULT_METRICS = ("known", "correct")


def _metric_keys(metrics: tuple[str, ...]) -> tuple[str, ...]:
    if not {"known", "correct"} <= set(metrics) or len(set(metrics)) != len(metrics):
        raise ValueError("metrics must be unique and include 'known' and 'correct'")
    return metrics


def _monitor_input(
    sample_uuid: str, record: LabLog, report: str | None, scores: dict[str, Any]
) -> MonitorInput:
    observed = bool(record.late_results) or (
        record.observation_error is None and record.payload is not None
    )
    return MonitorInput(
        sample=sample_uuid,
        actions=record.actions,
        observed=observed,
        report=report,
        scores=scores,
        observation=record.late_results[-1].payload if record.late_results else record.payload,
    )


def lab_scorer(
    judge: EvidenceJudge,
    evidence: dict[str, LabLog] | None = None,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    monitors: Sequence[Monitor] = (),
) -> Scorer:
    """Build a native Inspect scorer from a pure lab scoring function.

    The function receives the agent's report and the lab log. An unknown outcome
    (failed or empty observation) scores ``known=0`` with every other metric NaN,
    without calling the function.

    Args:
        judge: Pure report/lab-log comparison, without provider or model access.
        evidence: Saved lab logs for replay; omitted during native execution.
        metrics: Every key the function may return. Unknown outcomes and omitted keys
            are NaN (unscored), so every sample and epoch has the same keys.
        monitors: Run on each sample after scoring; their flags go into the score's
            metadata as ``lab_flags``, so they appear in Inspect's log viewer.

    Returns:
        Native scorer. Private payloads are never registered scorer arguments.

    Raises:
        ValueError: ``metrics`` lacks ``known``/``correct`` or repeats a key.
    """
    keys = _metric_keys(metrics)

    @scorer(name="lab_outcome", metrics={key: [mean()] for key in keys})
    def bound() -> Scorer:
        async def assess(state: TaskState, target: Target) -> Score:
            if evidence is None:
                session = _sessions.get(state.uuid)
                record = session.evidence if session else None
            else:
                record = evidence.get(state.uuid)
            if record is None:
                raise ValueError(
                    "Missing lab evidence record; collection or artifact is incomplete"
                )
            # Native metrics and epoch reduction need identical keys on every sample.
            # NaN marks a value as unscored; a missing key breaks or drops the metric.
            if record.late_results:
                # A result that arrived after the run replaces the run-time observation
                # for scoring; the saved entries themselves are never changed.
                record = record.model_copy(
                    update={
                        "payload": record.late_results[-1].payload,
                        "observation_error": None,
                    }
                )
            unscored: dict[str, float] = {key: math.nan for key in keys}
            # Cross-link: the native log keeps this sample's lab log digest, so a
            # changed .labs file also disagrees with the hash-linked native log.
            metadata: dict[str, Any] = {}
            if record.chain_sha256:
                metadata["lab_log_sha256"] = record.chain_sha256
            if record.observation_error is not None or record.payload is None:
                value: dict[str, float | int] = {**unscored, "known": 0}
            else:
                outcome = judge(state.output.completion, record)
                undeclared = set(outcome) - set(keys)
                if undeclared:
                    raise ValueError(f"Scorer returned undeclared metrics: {sorted(undeclared)}")
                value = {**unscored, **outcome}
            if monitors:
                entry = _monitor_input(state.uuid, record, state.output.completion, value)
                metadata["lab_flags"] = [
                    flag.model_dump(mode="json") for flag in run_monitors(entry, monitors)
                ]
            return Score(value=value, metadata=metadata or None)

        return assess

    return bound()


evidence_scorer = lab_scorer
"""Earlier name of `lab_scorer`."""


def _checked_tool(tool: Tool, gateway: Gateway) -> Tool:
    """Wrap a Lab tool so each call goes through the gateway before it reaches the Lab."""
    definition = ToolDef(tool)

    async def run(**arguments: Any) -> Any:
        try:
            return await gateway.run(definition.name, arguments, lambda: tool(**arguments))
        except ActionRefused as exc:
            raise ToolError(str(exc)) from exc

    # Inspect maps tool-call arguments by the function's signature, so the wrapper
    # presents the original tool's parameters rather than **arguments.
    run.__signature__ = inspect.signature(tool)  # type: ignore[attr-defined]
    run.__annotations__ = typing.get_type_hints(tool)
    return ToolDef(
        run,
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
    ).as_tool()


def connect_lab(
    task: Task,
    *,
    lab: Callable[[TaskState], Lab],
    scorer: EvidenceJudge | None = None,
    requires: frozenset[str] | Requirements,
    lab_log_dir: Path,
    allow_physical: bool = False,
    observation_timeout: float = 30,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    judge: EvidenceJudge | None = None,
    rules: ActionRules | None = None,
    approver: Approver | None = None,
    monitors: Sequence[Monitor] = (),
    checks: Sequence[Check] = (),
) -> Task:
    """Connect a native Inspect task to a Lab, keeping its own solver.

    The agent gets the Lab's tools; the evaluator records a lab log of what the Lab
    did, and the scorer judges the agent's report against that log.

    Args:
        task: Native Task containing the dataset, solver, approvals and budgets.
        lab: Trusted factory returning a fresh Lab for each sample.
        scorer: Pure lab scoring function of the report and the lab log, installed as
            a native Inspect scorer and reused unchanged for saved-evidence rescore.
        requires: Capabilities, or typed operation requirements, checked against the
            Lab's declarations before the agent gets any tool.
        lab_log_dir: Private directory for lab logs, kept even on failed runs.
        allow_physical: Host authorization for a physical provider; defaults to denied.
        observation_timeout: Bound for read-only provider observation, in seconds.
        metrics: Every key the scorer may return; must include known and correct.
        judge: Deprecated alias of ``scorer``.
        rules: Check every Lab tool call before it runs, and record the decision in
            the lab log. Off when None.
        approver: Called for actions the rules hold; returns True only if a person
            approved. Without one, held actions are refused.
        monitors: Run after scoring each sample; flags go into the score's metadata.
        checks: Domain checks run after ``rules``, together with the Lab's own
            ``checks``. They can make a decision stricter, never looser. Need ``rules``.

    Returns:
        The native Task, with setup, scorer and cleanup bindings installed.

    Raises:
        ValueError: The task already has a scorer; outcome ownership must be explicit.
        TypeError: Neither or both of ``scorer`` and ``judge`` were passed.
        ValueError: ``checks`` were passed without ``rules``.

    Samples raise ``CompatibilityError`` before dispatch when the Lab does not
    satisfy ``requires`` or is physical without ``allow_physical``. A provider that sets
    ``safety_abort`` stops later samples of the same task execution with ``SafetyAbort``.
    """
    required = (
        requires if isinstance(requires, Requirements) else Requirements(capabilities=requires)
    )
    outcome = _scorer_argument(scorer, judge, "connect_lab")
    if checks and rules is None:
        raise ValueError("Domain checks run in the gateway; pass rules to turn it on")
    if task.scorer:
        raise ValueError("Bind an unscored Task; the lab scorer owns the outcome")
    keys = _metric_keys(metrics)
    if not math.isfinite(observation_timeout) or observation_timeout <= 0:
        raise ValueError("observation_timeout must be positive and finite")
    previous_setup = task.setup
    previous_cleanup = task.cleanup

    @solver
    def setup_lab() -> Solver:
        async def setup(state: TaskState, generate: Generate) -> TaskState:
            if state.uuid in _sessions:
                raise ValueError("Automatic re-execution of an existing lab sample is unsupported")
            if previous_setup:
                state = await chain(previous_setup)(state, generate)
            attempt = _sample_scopes.get(state.uuid)
            if attempt is None:
                raise ValueError("Native lab lifecycle hooks did not initialize this sample")
            latch = _latch(lab_log_dir, attempt.task_name)
            if str(latch) in _unpersisted_latches:
                reason = _unpersisted_latches[str(latch)]
                raise SafetyAbort(f"Task halted by an earlier safety abort: {reason}")
            if latch.exists():
                raise SafetyAbort(
                    f"Task halted by an earlier safety abort: {latch.read_text().strip()}"
                )
            lab_log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with os.fdopen(
                    os.open(
                        lab_log_dir / f"{attempt.scope}.attempt",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    ),
                    "w",
                ) as fence:
                    fence.write(state.uuid + "\n")
            except FileExistsError as exc:
                raise ValueError(
                    "Lab sample was already attempted; reconcile its evidence before "
                    "authorizing a new execution"
                ) from exc
            provider = lab(state)
            info = provider.info
            problems = compatibility_problems(required, info.capabilities, info.operations)
            if info.mode == "physical" and not allow_physical:
                problems.append("physical mode requires explicit host authorization")
            if problems:
                await provider.close()
                raise CompatibilityError("Unsupported lab requirements: " + "; ".join(problems))
            session = _Session(
                provider, lab_log_dir, observation_timeout, attempt, provider.info, keys
            )
            _sessions[state.uuid] = session
            lab_tools = provider.tools
            if rules is not None:
                gateway = Gateway(
                    session.info.operations,
                    rules,
                    approver,
                    records=session.actions,
                    checks=(*lab_checks(provider), *checks),
                )
                lab_tools = [_checked_tool(lab_tool, gateway) for lab_tool in lab_tools]
            state.tools = [*state.tools, *lab_tools]
            return state

        return setup

    async def cleanup(state: TaskState) -> None:
        session = _sessions.get(state.uuid)
        try:
            await _observe(state.uuid)
        finally:
            if session and (reason := _safety_abort(session.environment)):
                # Persisted so retries and later runs stay halted until an operator who
                # has reviewed the abort removes it. Never raises: native Inspect runs
                # cleanup after scoring and only logs its exceptions.
                _persist_latch(_latch(session.directory, session.attempt.task_name), reason)
            try:
                # A rejected native retry reuses the sample UUID; close each provider once.
                if session and not session.closed:
                    session.closed = True
                    await session.environment.close()
            finally:
                if previous_cleanup:
                    await previous_cleanup(state)

    task.setup = setup_lab()
    task.scorer = [lab_scorer(outcome, metrics=keys, monitors=monitors)]
    task.cleanup = cleanup
    return task


def rescore(
    eval_log: Path,
    lab_log_file: Path,
    output: Path,
    scorer: EvidenceJudge | None = None,
    metrics: tuple[str, ...] | None = None,
    *,
    judge: EvidenceJudge | None = None,
    monitors: Sequence[Monitor] = (),
) -> None:
    """Rescore linked workflow evidence with no environment or robot construction.

    Args:
        eval_log: The run's native Inspect eval log.
        lab_log_file: The run's ``.labs`` lab log file.
        output: New native log destination; existing files are never overwritten.
        scorer: Explicit trusted lab scoring function, never imported from the artifact.
        metrics: Override the metric keys recorded in the evidence at bind time.
        judge: Deprecated alias of ``scorer``.
        monitors: Run on each rescored sample; flags go into the score's metadata.

    Raises:
        ValueError: The native or child artifacts do not match recorded provenance.
        OSError: Evidence is unavailable or the output already exists.
        TypeError: Neither or both of ``scorer`` and ``judge`` were passed.
    """
    outcome = _scorer_argument(scorer, judge, "rescore")
    bundle = read_lab_logs(lab_log_file)
    if artifact_digest(eval_log) != bundle.native_sha256:
        raise ValueError("Native artifact hash mismatch")
    log = read_eval_log(str(eval_log))
    samples = log.samples or []
    uuids = {sample.uuid for sample in samples}
    # Samples halted or rejected before dispatch have no evidence; they must have errored.
    unevidenced = [s for s in samples if s.uuid not in bundle.samples]
    if (
        log.eval.task != bundle.task
        or not set(bundle.samples) <= uuids
        or any(sample.error is None for sample in unevidenced)
    ):
        raise ValueError("Task/sample evidence binding mismatch")
    native_by_uuid = {sample.uuid: sample for sample in samples}
    for sample_uuid, record in bundle.samples.items():
        if record.sample_uuid != sample_uuid:
            raise ValueError("Observation identity mismatch")
        if bundle.schema_version >= 3:
            native_scores = native_by_uuid[sample_uuid].scores or {}
            linked = {
                (score.metadata or {}).get("lab_log_sha256") for score in native_scores.values()
            } - {None}
            if linked and linked != {run_time_hash(record)}:
                raise ValueError("Lab log does not match the digest in the native log")
        for link in record.artifacts:
            if artifact_digest(Path(link.path)) != link.sha256:
                raise ValueError("Child artifact hash mismatch")
    # Native errored samples were never scored; replay leaves them exactly as recorded.
    scorable = [sample for sample in samples if sample.error is None]
    result = log.model_copy(deep=True)
    if scorable:
        subset = log.model_copy(update={"samples": scorable}, deep=True)
        subset.eval.model_roles = None
        scored = score(
            subset,
            lab_scorer(outcome, bundle.samples, metrics or bundle.metrics, monitors),
            model="mockllm/model",
            action="overwrite",
            display="none",
        )
        rescored = {sample.uuid: sample for sample in scored.samples or []}
        result.samples = [rescored.get(sample.uuid, sample) for sample in samples]
        # Recompute aggregates only where the native run produced them.
        if log.results is None:
            result.results = None
        else:
            result.results = scored.results
            if result.results is not None:
                result.results.total_samples = log.results.total_samples
                result.results.completed_samples = log.results.completed_samples
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"):
        pass
    write_eval_log(result, str(output))


def read_lab_logs(evidence_file: Path) -> LabLogFile:
    """Read a run's ``.labs`` file and check each sample's hash chain (schema 3).

    Raises:
        ValueError: A sample's records do not match its recorded hash chain.
        OSError: The file is unavailable.
    """
    bundle = LabLogFile.model_validate_json(evidence_file.read_text())
    if bundle.schema_version >= 3:
        _check_chains(bundle.samples)
    return bundle


class LabSessionLog(BaseModel):
    """The lab log file of one served session (`inspect-labs serve`)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["session"] = "session"
    schema_version: Literal[1] = 1
    lab: str
    rules_version: str
    framework_version: str
    started_at: str
    ended_at: str
    stopped: str | None = None
    """Why the session was stopped, if an operator stopped it."""
    samples: dict[str, LabLog]
    """One lab log per session, keyed by session id."""
    flags: list[Flag] = Field(default_factory=list)


def _check_chains(samples: dict[str, LabLog]) -> None:
    for record in samples.values():
        if record.chain_sha256 is None or lab_log_hash(record) != record.chain_sha256:
            raise ValueError("Lab log hash mismatch")


def read_session_log(path: Path) -> LabSessionLog:
    """Read a served session's lab log file and check its hash chain.

    Raises:
        ValueError: A record does not match its hash chain.
        OSError: The file is unavailable.
    """
    log = LabSessionLog.model_validate_json(path.read_text())
    _check_chains(log.samples)
    return log


def _read_any_lab_logs(path: Path) -> dict[str, LabLog]:
    """Lab logs from an evaluation's ``.labs`` file or a served session's log file."""
    document = json.loads(path.read_text())
    if isinstance(document, dict) and document.get("kind") == "session":
        return read_session_log(path).samples
    return read_lab_logs(path).samples


def replay_rules(lab_log_file: Path, rules: ActionRules) -> dict[str, list[ReplayedDecision]]:
    """Re-decide every saved action under other ``rules``, running nothing.

    Args:
        lab_log_file: An evaluation's ``.labs`` file or a served session's lab log file.
        rules: The changed rules to test against past actions.

    Returns:
        Replayed decisions per sample or session, in action order.

    Raises:
        ValueError: The lab log fails its hash check.
    """
    return {
        key: replay_decisions(record.actions, rules, record.environment.operations)
        for key, record in _read_any_lab_logs(lab_log_file).items()
    }


def attach_late_result(
    evidence_file: Path,
    sample_uuid: str,
    payload: dict[str, JsonValue],
    output: Path,
    *,
    note: str = "",
) -> LabLogFile:
    """Attach a result that arrived after the run, writing a new lab log file.

    The original file is not changed. The new entry is chained after the existing
    ones, and rescoring the new file uses it in place of the run-time observation.

    Args:
        evidence_file: The run's ``.labs`` file (schema 3).
        sample_uuid: The sample the result belongs to.
        payload: The late observation, in the same shape the Lab's ``observe()`` returns.
        output: New lab log path; an existing file is never overwritten.
        note: Where the result came from, for reviewers.

    Returns:
        The new lab logs.

    Raises:
        ValueError: The lab log fails its hash check, predates schema 3, or has no
            such sample.
        OSError: The output already exists or cannot be written.
    """
    bundle = read_lab_logs(evidence_file)
    if bundle.schema_version < 3:
        raise ValueError("Late results need a schema 3 lab log")
    record = bundle.samples.get(sample_uuid)
    if record is None or record.chain_sha256 is None:
        raise ValueError(f"No sample {sample_uuid!r} in this lab log")
    late = LateResult(
        attached_at=datetime.now(UTC).isoformat(),
        payload=payload,
        note=note,
        previous_chain_sha256=record.chain_sha256,
    )
    updated = record.model_copy(update={"late_results": [*record.late_results, late]})
    updated = updated.model_copy(update={"chain_sha256": lab_log_hash(updated)})
    result = bundle.model_copy(update={"samples": {**bundle.samples, sample_uuid: updated}})
    _write_private(output, result)
    return result


def monitor_saved_run(
    native_log: Path, evidence_file: Path, monitors: Sequence[Monitor]
) -> dict[str, list[Flag]]:
    """Run monitors on a saved run, offline. Nothing is dispatched.

    Args:
        native_log: The run's native ``.eval`` log (for each sample's report and scores).
        evidence_file: The run's ``.labs`` file; its hash chain is checked first.
        monitors: Monitors to run on every sample in the lab log.

    Returns:
        Flags per sample, in sample order.

    Raises:
        ValueError: The lab log fails its hash check.
    """
    bundle = read_lab_logs(evidence_file)
    native = {sample.uuid: sample for sample in read_eval_log(str(native_log)).samples or []}
    flags: dict[str, list[Flag]] = {}
    for uuid, record in bundle.samples.items():
        sample = native.get(uuid)
        report = sample.output.completion if sample is not None else None
        scores: dict[str, Any] = {}
        if sample is not None and sample.scores:
            value = next(iter(sample.scores.values())).value
            scores = value if isinstance(value, dict) else {}
        flags[uuid] = run_monitors(_monitor_input(uuid, record, report, scores), monitors)
    return flags


# Earlier names, kept for one release.
LabEnvironment = Lab
"""Earlier name of `Lab`."""
EnvironmentInfo = LabInfo
"""Earlier name of `LabInfo`."""
LabEvidence = LabLog
"""Earlier name of `LabLog`."""
WorkflowEvidence = LabLogFile
"""Earlier name of `LabLogFile`."""


def bind_task(
    task: Task,
    *,
    environment: Callable[[TaskState], Lab],
    evidence_dir: Path,
    requires: frozenset[str] | Requirements,
    scorer: EvidenceJudge | None = None,
    judge: EvidenceJudge | None = None,
    allow_physical: bool = False,
    observation_timeout: float = 30,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    action_policy: ActionRules | None = None,
    approver: Approver | None = None,
    monitors: Sequence[Monitor] = (),
) -> Task:
    """Deprecated: use `connect_lab` (``environment`` is ``lab``, ``evidence_dir`` is
    ``lab_log_dir`` and ``action_policy`` is ``rules``)."""
    warnings.warn(
        "bind_task is deprecated; use connect_lab(task, lab=..., lab_log_dir=..., rules=...)",
        DeprecationWarning,
        stacklevel=2,
    )
    return connect_lab(
        task,
        lab=environment,
        lab_log_dir=evidence_dir,
        requires=requires,
        scorer=scorer,
        judge=judge,
        allow_physical=allow_physical,
        observation_timeout=observation_timeout,
        metrics=metrics,
        rules=action_policy,
        approver=approver,
        monitors=monitors,
    )


def rescore_workflow(
    native_log: Path,
    evidence_file: Path,
    output: Path,
    scorer: EvidenceJudge | None = None,
    metrics: tuple[str, ...] | None = None,
    *,
    judge: EvidenceJudge | None = None,
    monitors: Sequence[Monitor] = (),
) -> None:
    """Deprecated: use `rescore`."""
    warnings.warn(
        "rescore_workflow is deprecated; use rescore(eval_log, lab_log_file, output, scorer)",
        DeprecationWarning,
        stacklevel=2,
    )
    rescore(native_log, evidence_file, output, scorer, metrics, judge=judge, monitors=monitors)
