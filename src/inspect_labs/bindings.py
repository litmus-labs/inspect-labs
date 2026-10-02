"""Bind laboratory tools and private observations to native Inspect lifecycles."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

import anyio
from inspect_ai import Task, score
from inspect_ai.hooks import Hooks, RunEnd, SampleInit, SampleScoring, TaskEnd, TaskStart, hooks
from inspect_ai.log import list_eval_logs, read_eval_log, read_eval_log_async, write_eval_log
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, chain, solver
from inspect_ai.tool import Tool
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from inspect_labs.errors import CompatibilityError, SafetyAbort
from inspect_labs.spec import OperationSpec, Requirements, compatibility_problems

logger = logging.getLogger(__name__)


class EnvironmentInfo(BaseModel):
    """A provider's explicit evaluation capabilities, not an execution controller."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    mode: Literal["computation", "simulation", "physical"]
    capabilities: frozenset[str]
    operations: dict[str, OperationSpec] = Field(default_factory=dict)
    notes: str = ""
    """Honest operating notes for agents and operators (hardware, units, limits)."""


class LabEnvironment(Protocol):
    """Provider binding owned by one native sample; tools own provider-specific APIs."""

    @property
    def info(self) -> EnvironmentInfo:
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


def _safety_abort(environment: LabEnvironment) -> str | None:
    """Optional provider signal: a ``safety_abort`` reason attribute halts the task."""
    reason = getattr(environment, "safety_abort", None)
    return reason if isinstance(reason, str) and reason else None


class ArtifactLink(BaseModel):
    """Integrity link to an existing private provider or native child artifact."""

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LabEvidence(BaseModel):
    """Task-owned payload collected independently of the actor and native transcript."""

    model_config = ConfigDict(extra="forbid")
    sample_uuid: str
    environment: EnvironmentInfo
    collected_at: str
    payload: dict[str, JsonValue] | None
    observation_error: str | None = None
    safety_abort: str | None = None
    artifacts: list[ArtifactLink] = Field(default_factory=list)


class WorkflowEvidence(BaseModel):
    """One native run's private references and observed facts for deterministic rescore."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    task: str
    native_sha256: str
    framework_version: str
    inspect_version: str
    metrics: tuple[str, ...] = ("known", "correct")
    samples: dict[str, LabEvidence]


Readout = LabEvidence
"""What the evaluator read from the Lab for one sample, independently of the agent."""

EvidenceJudge = Callable[[str, LabEvidence], dict[str, int | float]]
"""Pure outcome function of the agent's report and the Readout. Return ``known=0``
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
    environment: LabEnvironment
    directory: Path
    observation_timeout: float
    attempt: _Attempt
    info: EnvironmentInfo
    metrics: tuple[str, ...]
    evidence: LabEvidence | None = None
    closed: bool = False


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


async def _observe(sample_uuid: str) -> None:
    session = _sessions.get(sample_uuid)
    if session is None or session.evidence is not None:
        return
    error = None
    payload = None
    links: list[ArtifactLink] = []
    try:
        with anyio.fail_after(session.observation_timeout):
            payload = await session.environment.observe()
        links = [
            ArtifactLink(path=str(path.resolve()), sha256=artifact_digest(path))
            for path in session.environment.artifacts
        ]
        evidence = LabEvidence(
            sample_uuid=sample_uuid,
            environment=session.info,
            collected_at=datetime.now(UTC).isoformat(),
            payload=payload,
            artifacts=links,
            safety_abort=_safety_abort(session.environment),
        )
    except Exception as exc:
        # Provider observation is an external boundary. Keep unavailable facts
        # unknown, and keep potentially sensitive provider exception text private.
        error = type(exc).__name__
        evidence = LabEvidence(
            sample_uuid=sample_uuid,
            environment=session.info,
            collected_at=datetime.now(UTC).isoformat(),
            payload=None,
            observation_error=error,
            safety_abort=_safety_abort(session.environment),
        )
    _write_private(session.directory / f"{sample_uuid}.lab-sample", evidence)
    session.evidence = evidence


@hooks(name="lab_evidence", description="Collect and link private laboratory workflow evidence")
class LabEvidenceHooks(Hooks):
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
            WorkflowEvidence(
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


def lab_scorer(
    judge: EvidenceJudge,
    evidence: dict[str, LabEvidence] | None = None,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
) -> Scorer:
    """Build a native Inspect scorer from a pure lab scoring function.

    The function receives the agent's report and the Readout. An unknown outcome
    (failed or empty observation) scores ``known=0`` with every other metric NaN,
    without calling the function.

    Args:
        judge: Pure report/Readout comparison, without provider or model access.
        evidence: Persisted Readouts for replay; omitted during native execution.
        metrics: Every key the function may return. Unknown outcomes and omitted keys
            are NaN (unscored), so every sample and epoch has the same keys.

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
            unscored: dict[str, float] = {key: math.nan for key in keys}
            if record.observation_error is not None or record.payload is None:
                return Score(value={**unscored, "known": 0})
            outcome = judge(state.output.completion, record)
            undeclared = set(outcome) - set(keys)
            if undeclared:
                raise ValueError(f"Scorer returned undeclared metrics: {sorted(undeclared)}")
            return Score(value={**unscored, **outcome})

        return assess

    return bound()


evidence_scorer = lab_scorer
"""Earlier name of `lab_scorer`."""


def bind_task(
    task: Task,
    *,
    environment: Callable[[TaskState], LabEnvironment],
    scorer: EvidenceJudge | None = None,
    requires: frozenset[str] | Requirements,
    evidence_dir: Path,
    allow_physical: bool = False,
    observation_timeout: float = 30,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    judge: EvidenceJudge | None = None,
) -> Task:
    """Attach lab tools/evidence to an existing native Task without replacing its solver.

    Args:
        task: Native Task containing the dataset, solver, approvals and budgets.
        environment: Trusted factory returning a Lab (scoped provider client) per sample.
        scorer: Pure lab scoring function of the report and the Readout, installed as
            a native Inspect scorer and reused unchanged for saved-evidence rescore.
        requires: Capabilities, or typed operation requirements, checked against the
            environment declaration before any actor dispatch.
        evidence_dir: Private directory retaining sample evidence even on failed runs.
        allow_physical: Host authorization for a physical provider; defaults to denied.
        observation_timeout: Bound for read-only provider observation, in seconds.
        metrics: Every key the scorer may return; must include known and correct.
        judge: Deprecated alias of ``scorer``.

    Returns:
        The native Task, with setup, scorer and cleanup bindings installed.

    Raises:
        ValueError: The task already has a scorer; outcome ownership must be explicit.
        TypeError: Neither or both of ``scorer`` and ``judge`` were passed.

    Samples raise ``CompatibilityError`` before dispatch when the environment does not
    satisfy ``requires`` or is physical without ``allow_physical``. A provider that sets
    ``safety_abort`` stops later samples of the same task execution with ``SafetyAbort``.
    """
    required = (
        requires if isinstance(requires, Requirements) else Requirements(capabilities=requires)
    )
    outcome = _scorer_argument(scorer, judge, "bind_task")
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
            latch = _latch(evidence_dir, attempt.task_name)
            if str(latch) in _unpersisted_latches:
                reason = _unpersisted_latches[str(latch)]
                raise SafetyAbort(f"Task halted by an earlier safety abort: {reason}")
            if latch.exists():
                raise SafetyAbort(
                    f"Task halted by an earlier safety abort: {latch.read_text().strip()}"
                )
            evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with os.fdopen(
                    os.open(
                        evidence_dir / f"{attempt.scope}.attempt",
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
            provider = environment(state)
            info = provider.info
            problems = compatibility_problems(required, info.capabilities, info.operations)
            if info.mode == "physical" and not allow_physical:
                problems.append("physical mode requires explicit host authorization")
            if problems:
                await provider.close()
                raise CompatibilityError("Unsupported lab requirements: " + "; ".join(problems))
            _sessions[state.uuid] = _Session(
                provider, evidence_dir, observation_timeout, attempt, provider.info, keys
            )
            state.tools = [*state.tools, *provider.tools]
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
    task.scorer = [lab_scorer(outcome, metrics=keys)]
    task.cleanup = cleanup
    return task


def rescore_workflow(
    native_log: Path,
    evidence_file: Path,
    output: Path,
    scorer: EvidenceJudge | None = None,
    metrics: tuple[str, ...] | None = None,
    *,
    judge: EvidenceJudge | None = None,
) -> None:
    """Rescore linked workflow evidence with no environment or robot construction.

    Args:
        native_log: Original native Inspect artifact.
        evidence_file: Its private .labs companion (JSON content, not a native log).
        output: New native log destination; existing files are never overwritten.
        scorer: Explicit trusted lab scoring function, never imported from the artifact.
        metrics: Override the metric keys recorded in the evidence at bind time.
        judge: Deprecated alias of ``scorer``.

    Raises:
        ValueError: The native or child artifacts do not match recorded provenance.
        OSError: Evidence is unavailable or the output already exists.
        TypeError: Neither or both of ``scorer`` and ``judge`` were passed.
    """
    outcome = _scorer_argument(scorer, judge, "rescore_workflow")
    bundle = WorkflowEvidence.model_validate_json(evidence_file.read_text())
    if artifact_digest(native_log) != bundle.native_sha256:
        raise ValueError("Native artifact hash mismatch")
    log = read_eval_log(str(native_log))
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
    for sample_uuid, record in bundle.samples.items():
        if record.sample_uuid != sample_uuid:
            raise ValueError("Observation identity mismatch")
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
            lab_scorer(outcome, bundle.samples, metrics or bundle.metrics),
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
