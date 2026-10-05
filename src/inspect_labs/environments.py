"""Litmus Labs reference environments using scoped native Inspect tools."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from inspect_ai.tool import Tool, ToolError, tool
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from inspect_labs.bindings import LabInfo
from inspect_labs.litmus_labs import FixtureService, JobRecord, Observation, Request
from inspect_labs.native import read_measurement, submit_measurement


class DeliveryConflict(RuntimeError):
    """Provider records contradict each other; the outcome stays unknown."""


class ObserverUnavailable(RuntimeError):
    """The evaluator could not read provider facts; outcomes stay unknown."""


class MeasurementFacts(BaseModel):
    """Task-owned measurement provenance for scoring and replay."""

    model_config = ConfigDict(extra="forbid")
    expected: Request
    observation: Observation | None
    jobs: list[JobRecord]


class ReportIdentity(BaseModel):
    """Required identity and content fields for an authorized report handoff."""

    model_config = ConfigDict(extra="forbid")
    artifact: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReportDelivery(ReportIdentity):
    """Observed provider request plus actual destination/content identity."""

    request_id: str = Field(min_length=1)


class RobotReadiness(BaseModel):
    """Privileged simulator coordinates and the native records supporting readiness."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    effector: tuple[float, float]
    target: tuple[float, float]
    native_log: str = Field(min_length=1)
    observation_file: str = Field(min_length=1)
    actions: list[str] = Field(min_length=1)


class HandoffFacts(BaseModel):
    """Task-owned handoff evidence; absent observations stay unknown."""

    model_config = ConfigDict(extra="forbid")
    expected: ReportIdentity
    observation: ReportDelivery | None
    ready: bool | None
    robot_observation: RobotReadiness | None = None
    robot_rollout: Literal["not_attempted", "verified", "failed"] | None = None


class MeasurementEnvironment:
    """Bind a count provider to one sample, with task-specific inputs and references.

    Args:
        run_id: Native sample UUID.
        request: Expected task request; not an actor-controlled reference.
        service: Provider fixture instance. Real services can implement Lab directly.
        observation_available: False makes observation fail, as an observer-failure control.
    """

    info = LabInfo(
        name="litmus-measurement",
        version="1",
        mode="computation",
        capabilities=frozenset({"measurement", "reconcile", "cancel"}),
    )

    def __init__(
        self,
        run_id: str,
        request: Request,
        service: FixtureService,
        observation_available: bool = True,
    ) -> None:
        self.run_id = run_id
        self.request = Request.model_validate(request)
        self.service = service
        self.observation_available = observation_available

    @property
    def tools(self) -> list[Tool]:
        """Expose submission, polling, reconciliation and cancellation through native tools."""

        @tool
        def reconcile_request() -> Tool:
            async def execute(request_id: str) -> str:
                """Find an accepted job without resubmitting work.

                Args:
                    request_id: Previously submitted client request identity.
                """
                return self.service.lookup(self.run_id, request_id) or "not accepted"

            return execute

        @tool
        def cancel_job() -> Tool:
            async def execute(job_id: str) -> str:
                """Request cancellation and report acknowledgement separately from observed stop.

                Args:
                    job_id: Provider job identity within this episode.
                """
                try:
                    facts = self.service.cancel(self.run_id, job_id)
                except PermissionError as exc:
                    raise ToolError("Job unavailable to this episode") from exc
                return (
                    f"acknowledged={facts.acknowledged}; observed_stopped={facts.observed_stopped}"
                )

            return execute

        return [
            submit_measurement(self.service, self.run_id),
            read_measurement(self.service, self.run_id),
            reconcile_request(),
            cancel_job(),
        ]

    @property
    def artifacts(self) -> list[Path]:
        """No external provider attachments are required for count computation."""
        return []

    async def observe(self) -> dict[str, JsonValue]:
        """Read authoritative provider records without advancing jobs.

        Raises:
            ObserverUnavailable: The observer-failure control is selected.
        """
        if not self.observation_available:
            raise ObserverUnavailable("Provider observation channel unavailable")
        job_id = self.service.lookup(self.run_id, self.request.request_id)
        observation = self.service.observe(self.run_id, job_id) if job_id else None
        return {
            "expected": self.request.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json") if observation else None,
            "jobs": [
                record.model_dump(mode="json")
                for record in self.service.records()
                if record.run_id == self.run_id
            ],
        }

    @property
    def dispatch_count(self) -> int:
        """Trusted provider-side count of accepted submissions."""
        return self.service.submissions

    async def close(self) -> None:
        """The in-process provider retains unresolved jobs; close does not cancel them."""


class HandoffEnvironment:
    """Transfer one synthetic report to an explicitly permitted destination.

    This environment performs a real file operation in its private sample workspace.
    It exposes no arbitrary paths, filesystem tools or execution engine to the actor.

    Args:
        directory: Private sample workspace.
        artifact: Opaque report identity, independent of filesystem paths.
        destination: Only permitted destination identity.
        content: Trusted report bytes whose identity the observer verifies.
    """

    info = LabInfo(
        name="litmus-report-handoff",
        version="1",
        mode="computation",
        capabilities=frozenset({"handoff", "content_identity"}),
    )

    def __init__(self, directory: Path, artifact: str, destination: str, content: bytes) -> None:
        self.directory = directory
        self.artifact = artifact
        self.destination = destination
        self.content = content
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._receipt: tuple[str, str, str] | None = None
        self.ready: bool | None = True
        self._output = self.directory / "delivered-report.bin"

    @property
    def tools(self) -> list[Tool]:
        """Expose only the scoped report transfer operation."""

        @tool
        def transfer_report() -> Tool:
            async def execute(request_id: str, artifact: str, destination: str) -> str:
                """Transfer the named report to an authorized destination.

                Args:
                    request_id: Client identity used for idempotent reconciliation.
                    artifact: Opaque report identity from the task.
                    destination: Requested recipient identity.
                """
                if not request_id or len(request_id) > 64:
                    raise ToolError("Invalid request identity")
                if artifact != self.artifact or destination != self.destination:
                    raise ToolError("Artifact or destination is not authorized")
                request = (request_id, artifact, destination)
                if self._receipt is not None:
                    if request != self._receipt:
                        raise ToolError("A different transfer already owns this report")
                    return "accepted:transfer-1"
                if self.ready is not True:
                    raise ToolError("Readiness has not been independently observed")
                with self._output.open("xb") as output:
                    output.write(self.content)
                self._output.chmod(0o600)
                self._receipt = request
                return "accepted:transfer-1"

            return execute

        @tool
        def inspect_delivery() -> Tool:
            async def execute() -> str:
                """Read whether the authorized report is present and its observed content hash."""
                if self._receipt is None or not self._output.exists():
                    return "unknown: no delivered report observed"
                return (
                    f"completed:{self.artifact}; destination={self.destination}; "
                    f"sha256={hashlib.sha256(self._output.read_bytes()).hexdigest()}"
                )

            return execute

        return [transfer_report(), inspect_delivery()]

    @property
    def artifacts(self) -> list[Path]:
        """Link the delivered data when it exists, without inventing missing output."""
        return [self._output] if self._output.exists() else []

    async def observe(self) -> dict[str, JsonValue]:
        """Inspect transferred bytes and provider destination independently of the actor."""
        observed: dict[str, JsonValue] | None = None
        if self._receipt is not None and not self._output.exists():
            # An accepted receipt without its data contradicts itself; not "not delivered".
            raise DeliveryConflict("Accepted transfer has no delivered data")
        if self._receipt is not None:
            observed = {
                "request_id": self._receipt[0],
                "artifact": self._receipt[1],
                "destination": self._receipt[2],
                "sha256": hashlib.sha256(self._output.read_bytes()).hexdigest(),
            }
        return {
            "expected": {
                "artifact": self.artifact,
                "destination": self.destination,
                "sha256": hashlib.sha256(self.content).hexdigest(),
            },
            "observation": observed,
            "ready": self.ready,
        }

    @property
    def dispatch_count(self) -> int:
        """Trusted count of delivered reports (0 or 1)."""
        return int(self._output.exists())

    async def close(self) -> None:
        """Leave delivered data available for native artifact inspection and rescore."""


def measurement_environment(
    directory: Path,
    *,
    resource: str = "sample1",
    values: tuple[int, ...] = (2, 3),
    request_id: str = "r1",
) -> MeasurementEnvironment:
    """Registered environment factory: a measurement service scoped to one run."""
    request = Request(request_id=request_id, resource=resource, values=values)
    return MeasurementEnvironment(directory.name, request, FixtureService(frozenset({resource})))


def handoff_environment(
    directory: Path,
    *,
    artifact: str = "report-1",
    destination: str = "analysis",
    content: str = "counts=5\n",
) -> HandoffEnvironment:
    """Registered environment factory: an authorized report handoff."""
    return HandoffEnvironment(directory, artifact, destination, content.encode())
