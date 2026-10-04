"""An author-owned native task: no framework source changes or custom runner.

Run with the installed Inspect CLI and a model selected by the researcher:
inspect eval examples/custom_assay.py@custom_assay --model PROVIDER/MODEL
"""

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.approval import ApprovalPolicy, auto_approver
from inspect_ai.dataset import Sample
from inspect_ai.solver import generate

from inspect_labs import connect_lab
from inspect_labs.environments import MeasurementEnvironment
from inspect_labs.litmus_labs import FixtureService, Request
from inspect_labs.tasks import OUTCOME_METRICS, measurement_outcome, scripted_measurement


@task
def custom_assay(
    scripted: bool = False, reject: bool = False, evidence_dir: str = ".research/custom-evidence"
) -> Task:
    """Change data, resources, control and scaffold through native task authoring."""
    request = Request(request_id="custom-readout", resource="sensor-b", values=(31, 7, -2))
    result = Task(
        dataset=[
            Sample(
                id="replicate-b",
                input=(
                    "Measure counts [31, 7, -2] on sensor-b with request_id custom-readout. "
                    "Poll the accepted job. End with a final line `ANSWER: <integer>`, "
                    "or `ANSWER: unknown` if no result was observed."
                ),
            )
        ],
        solver=scripted_measurement(request) if scripted else generate(),
        approval=[ApprovalPolicy(auto_approver("reject" if reject else "approve"), tools="*")],
        message_limit=20,
    )
    return connect_lab(
        result,
        lab=lambda state: MeasurementEnvironment(
            state.uuid, request, FixtureService(frozenset({"sensor-b"}))
        ),
        scorer=measurement_outcome,
        requires=frozenset({"measurement", "reconcile"}),
        lab_log_dir=Path(evidence_dir),
        metrics=OUTCOME_METRICS,
    )
