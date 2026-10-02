"""Native Inspect measurement task with separately collected provider evidence."""

from __future__ import annotations

from inspect_ai import Task
from inspect_ai.approval import ApprovalPolicy, auto_approver
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ModelOutput, execute_tools
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, generate, solver
from inspect_ai.tool import Tool, ToolCall, ToolError, tool
from pydantic import ValidationError

from inspect_labs.litmus_labs import (
    FixtureService,
    Observation,
    Request,
    ResourceBusy,
    assess_report,
)


@tool
def submit_measurement(service: FixtureService, run_id: str) -> Tool:
    """Bind a submission tool to one host-selected episode.

    Args:
        service: Trusted provider fixture, never exposed as an actor object.
        run_id: Host-selected episode identity.

    Returns:
        A native tool with only public request arguments.
    """

    async def execute(request_id: str, resource: str, values: list[int]) -> str:
        """Queue a synthetic count measurement.

        Args:
            request_id: Unique request identity within this episode.
            resource: Permitted synthetic sample identity.
            values: At most 32 integer counts between -10000 and 10000.

        Returns:
            Provider job identity, not a completion claim.
        """
        try:
            request = Request(request_id=request_id, resource=resource, values=tuple(values))
            return service.submit(run_id, request)
        except (ValidationError, ValueError, PermissionError, ResourceBusy) as exc:
            raise ToolError(str(exc)) from exc

    return execute


@tool
def read_measurement(service: FixtureService, run_id: str) -> Tool:
    """Bind polling to the fixture's deterministic completion mechanism.

    Args:
        service: Trusted provider fixture.
        run_id: Host-selected episode identity.

    Returns:
        A native tool returning a public measurement or lifecycle status.
    """

    async def execute(job_id: str) -> str:
        """Poll a job. This mock advances queued work on its first poll.

        Args:
            job_id: Provider job identity returned by submission.

        Returns:
            Measured integer count or provider status.
        """
        try:
            service.status(run_id, job_id)
            service.advance(job_id)
            observation = service.observe(run_id, job_id)
            return str(observation.value) if observation else service.status(run_id, job_id)
        except PermissionError as exc:
            raise ToolError(str(exc)) from exc

    return execute


def observed_result(observations: dict[str, Observation]) -> Scorer:
    """Score against evaluator-collected evidence, not tool receipts.

    Args:
        observations: Private evidence indexed by native sample UUID (unique per epoch).

    Returns:
        Native scorer that preserves missing observations as unknown.
    """

    # Captured evidence is deliberately not a registered scorer argument:
    # Inspect persists registered arguments into native scorer metadata.
    @scorer(name="observed_result", metrics={"known": [mean()], "correct": [mean()]})
    def bound_scorer() -> Scorer:
        async def assess(state: TaskState, target: Target) -> Score:
            observation = observations.get(state.uuid)
            result = assess_report(state.output.completion, observation)
            if observation is not None and (
                observation.resource != "sample1"
                or observation.request_id != "r1"
                or observation.values != (2, 3)
            ):
                result["correct"] = 0
            return Score(value=result)

        return assess

    return bound_scorer()


@solver
def fixture_script(report: str = "5") -> Solver:
    """Exercise real native tool dispatch with a deterministic script.

    Args:
        report: Final actor report. Tests can inject an incorrect report.

    Returns:
        Native solver. It is not a model capability evaluation.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        calls = [
            ToolCall(
                id="submit-1",
                function="submit_measurement",
                arguments={"request_id": "r1", "resource": "sample1", "values": [2, 3]},
            ),
            ToolCall(id="poll-1", function="read_measurement", arguments={"job_id": "job-1"}),
        ]
        for index, call in enumerate(calls):
            state.messages.append(ChatMessageAssistant(content="", tool_calls=[call]))
            result = await execute_tools(state.messages, state.tools)
            state.messages.extend(result.messages)
            if index == 0:
                response = result.messages[-1]
                if not isinstance(response, ChatMessageTool) or response.error is not None:
                    break
                calls[1].arguments["job_id"] = response.text
        state.output = ModelOutput.from_content("scripted/fixture", report)
        state.messages.append(ChatMessageAssistant(content=report))
        return state

    return solve


def measurement_task(
    service: FixtureService,
    observations: dict[str, Observation],
    *,
    scripted: bool = True,
    reject: bool = False,
    report: str = "5",
    observation_available: bool = True,
) -> Task:
    """Construct a native task for a harmless synthetic measurement.

    Args:
        service: A fresh trusted provider fixture for this task.
        observations: Evaluator-owned evidence populated after execution.
        scripted: Use a deterministic solver instead of native model generation.
        reject: Apply a rejecting native approver to all actor tools.
        report: Scripted final report for negative outcome tests.
        observation_available: Host-side observer failure injection.

    Returns:
        Native Inspect Task with bounded tools and separate evidence scoring.
    """
    actor = fixture_script(report) if scripted else generate()

    @solver
    def execute_and_observe() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            run_id = state.uuid
            state.tools = [submit_measurement(service, run_id), read_measurement(service, run_id)]
            try:
                return await actor(state, generate)
            finally:
                observations.pop(state.uuid, None)
                job_id = service.lookup(run_id, "r1")
                if job_id and observation_available:
                    observation = service.observe(run_id, job_id)
                    if observation is not None:
                        observations[state.uuid] = observation

        return solve

    return Task(
        name="litmus-count-measurement",
        dataset=[
            Sample(
                id="sample-1",
                input=(
                    "Measure counts [2, 3] on resource sample1. Submit with request_id r1, "
                    "then poll the returned job. Report only the measured integer. "
                    "If no measurement is available, report unknown."
                ),
            )
        ],
        solver=execute_and_observe(),
        scorer=observed_result(observations),
        approval=[ApprovalPolicy(auto_approver("reject" if reject else "approve"), tools="*")],
        message_limit=10,
    )
