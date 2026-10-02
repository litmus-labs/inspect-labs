"""Exercise actual native dispatch, approvals, persistence and rescoring."""

import math
import tempfile
import unittest
from unittest.mock import patch

from inspect_ai import eval, score
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.solver import solver
from inspect_ai.tool import ToolCall

from inspect_labs.litmus_labs import FixtureService, Observation
from inspect_labs.native import fixture_script, measurement_task, observed_result


class NativeTests(unittest.TestCase):
    def run_case(self, **kwargs: object) -> tuple[object, FixtureService, dict[str, Observation]]:
        service = FixtureService(frozenset({"sample1"}))
        observations: dict[str, Observation] = {}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "CREDENTIAL-CANARY-NOT-A-REAL-KEY"}),
        ):
            task = measurement_task(service, observations, **kwargs)
            log = eval(
                task, model="mockllm/model", log_dir=directory, display="none", max_samples=1
            )[0]
            self.assertEqual(log.status, "success", str(log.error))
            reopened = read_eval_log(log.location)
            self.assertNotIn("CREDENTIAL-CANARY-NOT-A-REAL-KEY", reopened.model_dump_json())
            self.assertTrue(all(not s.target and not s.metadata for s in reopened.samples))
            service.dispatch_enabled = False
            before = service.submissions
            rescored = score(reopened, observed_result(observations), display="none")
            self.assertEqual(service.submissions, before)
            original = next(iter(reopened.samples[0].scores.values())).value
            result = next(iter(rescored.samples[0].scores.values())).value
            self.assertEqual(original, result)
            return result, service, observations

    def test_native_generation_collects_evidence(self) -> None:
        service = FixtureService(frozenset({"sample1"}))
        observations: dict[str, Observation] = {}
        calls = [
            ToolCall(
                id="submit",
                function="submit_measurement",
                arguments={"request_id": "r1", "resource": "sample1", "values": [2, 3]},
            ),
            ToolCall(id="poll", function="read_measurement", arguments={"job_id": "job-1"}),
        ]
        outputs = [
            ModelOutput.from_message(ChatMessageAssistant(content="", tool_calls=[call]))
            for call in calls
        ]
        outputs.append(ModelOutput.from_content("mockllm/model", "5"))
        model = get_model("mockllm/model", custom_outputs=outputs)
        with tempfile.TemporaryDirectory() as directory:
            log = eval(
                measurement_task(service, observations, scripted=False),
                model=model,
                log_dir=directory,
                display="none",
                max_samples=1,
            )[0]
            self.assertEqual(log.status, "success", str(log.error))
            self.assertEqual(service.submissions, 1)
            self.assertEqual(
                next(iter(log.samples[0].scores.values())).value, {"known": 1, "correct": 1}
            )

    def test_native_completed(self) -> None:
        result, service, observations = self.run_case()
        self.assertEqual(result, {"known": 1, "correct": 1})
        self.assertEqual(service.submissions, 1)
        self.assertEqual(len(observations), 1)

    def test_completed_state_still_collects_evidence(self) -> None:
        @solver
        def completed_script():
            async def solve(state, generate):
                state = await fixture_script()(state, generate)
                state.completed = True
                return state

            return solve

        with patch("inspect_labs.native.fixture_script", return_value=completed_script()):
            result, _, observations = self.run_case()
        self.assertEqual(result, {"known": 1, "correct": 1})
        self.assertEqual(len(observations), 1)

    def test_error_still_collects_evidence(self) -> None:
        @solver
        def failing_script():
            async def solve(state, generate):
                await fixture_script()(state, generate)
                raise RuntimeError("injected failure after measurement")

            return solve

        service = FixtureService(frozenset({"sample1"}))
        observations: dict[str, Observation] = {}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("inspect_labs.native.fixture_script", return_value=failing_script()),
        ):
            log = eval(
                measurement_task(service, observations),
                model="mockllm/model",
                log_dir=directory,
                display="none",
            )[0]
        self.assertEqual(log.status, "error")
        self.assertEqual(len(observations), 1)

    def test_wrong_request_is_not_task_success(self) -> None:
        outputs = [
            ModelOutput.from_message(ChatMessageAssistant(content="", tool_calls=[call]))
            for call in (
                ToolCall(
                    id="submit",
                    function="submit_measurement",
                    arguments={"request_id": "r1", "resource": "sample1", "values": [99]},
                ),
                ToolCall(id="poll", function="read_measurement", arguments={"job_id": "job-1"}),
            )
        ]
        outputs.append(ModelOutput.from_content("mockllm/model", "99"))
        service = FixtureService(frozenset({"sample1"}))
        with tempfile.TemporaryDirectory() as directory:
            log = eval(
                measurement_task(service, {}, scripted=False),
                model=get_model("mockllm/model", custom_outputs=outputs),
                log_dir=directory,
                display="none",
            )[0]
            self.assertEqual(log.status, "success")
            self.assertEqual(
                next(iter(log.samples[0].scores.values())).value, {"known": 1, "correct": 0}
            )

    def test_epochs_do_not_reuse_jobs_or_evidence(self) -> None:
        service = FixtureService(frozenset({"sample1"}))
        observations: dict[str, Observation] = {}
        with tempfile.TemporaryDirectory() as directory:
            log = eval(
                measurement_task(service, observations),
                model="mockllm/model",
                epochs=2,
                max_samples=1,
                log_dir=directory,
                display="none",
            )[0]
            self.assertEqual(log.status, "success")
            self.assertEqual(service.submissions, 2)
            self.assertEqual(len(observations), 2)
            for sample in log.samples:
                self.assertEqual(observations[sample.uuid].run_id, sample.uuid)
                self.assertEqual(
                    next(iter(sample.scores.values())).value, {"known": 1, "correct": 1}
                )

    def assertUnknown(self, result: dict) -> None:
        """Unknown: no coverage, and correctness unscored (NaN; null once persisted)."""
        self.assertEqual(result["known"], 0)
        correct = result["correct"]
        self.assertTrue(correct is None or math.isnan(correct))

    def test_actor_claim_is_not_truth(self) -> None:
        result, _, _ = self.run_case(report="9000")
        self.assertEqual(result, {"known": 1, "correct": 0})

    def test_observer_failure_stays_unknown(self) -> None:
        result, service, observations = self.run_case(observation_available=False)
        self.assertUnknown(result)
        self.assertEqual(service.submissions, 1)
        self.assertEqual(observations, {})

    def test_native_approval_prevents_submission(self) -> None:
        result, service, observations = self.run_case(reject=True)
        self.assertUnknown(result)
        self.assertEqual(service.submissions, 0)
        self.assertEqual(observations, {})


if __name__ == "__main__":
    unittest.main()
