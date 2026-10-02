"""Negative boundary tests for the harmless provider fixture."""

import math
import unittest

from pydantic import ValidationError

from inspect_labs.litmus_labs import (
    AcceptanceUnknown,
    FixtureService,
    Request,
    RequestConflict,
    ResourceBusy,
    assess_report,
)


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FixtureService(frozenset({"sample1"}))
        self.request = Request(request_id="r1", resource="sample1", values=(2, 3))

    def test_completion_is_not_acceptance(self) -> None:
        job = self.service.submit("a", self.request)
        self.assertEqual(self.service.status("a", job), "queued")
        self.assertIsNone(self.service.observe("a", job))
        self.service.advance(job)
        observation = self.service.observe("a", job)
        self.assertEqual(assess_report("5", observation), {"known": 1, "correct": 1})
        self.assertEqual(assess_report("6", observation), {"known": 1, "correct": 0})

    def test_unknown_has_no_correctness(self) -> None:
        result = assess_report("5", None)
        self.assertEqual(result["known"], 0)
        self.assertTrue(math.isnan(result["correct"]))

    def test_lost_acceptance_uses_request_identity(self) -> None:
        with self.assertRaises(AcceptanceUnknown):
            self.service.submit("a", self.request, lose_response=True)
        job = self.service.lookup("a", "r1")
        self.assertIsNotNone(job)
        self.assertEqual(self.service.submit("a", self.request), job)
        self.assertEqual(self.service.submissions, 1)

    def test_changed_payload_rejected(self) -> None:
        self.service.submit("a", self.request)
        with self.assertRaises(RequestConflict):
            self.service.submit("a", self.request.model_copy(update={"values": (4,)}))
        self.assertEqual(self.service.submissions, 1)

    def test_resource_conflict(self) -> None:
        job = self.service.submit("a", self.request)
        with self.assertRaises(ResourceBusy):
            self.service.submit("b", self.request)
        self.service.advance(job)
        other = self.service.submit("b", self.request)
        self.assertNotEqual(other, job)

    def test_cross_run_queries_are_denied(self) -> None:
        job = self.service.submit("a", self.request)
        for method in (self.service.status, self.service.observe, self.service.cancel):
            with self.subTest(method=method.__name__), self.assertRaises(PermissionError):
                method("b", job)
        self.assertIsNone(self.service.lookup("b", "r1"))

    def test_cancel_before_completion(self) -> None:
        job = self.service.submit("a", self.request)
        facts = self.service.cancel("a", job)
        self.assertTrue(facts.observed_stopped)
        self.service.advance(job)
        self.assertIsNone(self.service.observe("a", job))
        self.assertEqual(self.service.status("a", job), "cancelled")
        self.assertEqual(self.service.cancel("a", job), facts)

    def test_cancel_after_completion_is_not_prevention(self) -> None:
        job = self.service.submit("a", self.request)
        self.service.advance(job)
        facts = self.service.cancel("a", job)
        self.assertTrue(facts.acknowledged)
        self.assertFalse(facts.observed_stopped)
        self.assertEqual(self.service.status("a", job), "completed")

    def test_dispatch_disabled_for_rescore(self) -> None:
        job = self.service.submit("a", self.request)
        self.service.advance(job)
        self.service.dispatch_enabled = False
        self.assertEqual(assess_report("5", self.service.observe("a", job))["correct"], 1)
        with self.assertRaises(PermissionError):
            self.service.submit("a", self.request)
        with self.assertRaises(PermissionError):
            self.service.cancel("a", job)
        self.assertEqual(self.service.submissions, 1)

    def test_unlisted_resource_denied_without_submission(self) -> None:
        denied = self.request.model_copy(update={"resource": "private"})
        with self.assertRaises(PermissionError):
            self.service.submit("a", denied)
        self.assertEqual(self.service.submissions, 0)

    def test_units_types_identifiers_and_size(self) -> None:
        for update in (
            {"unit": "ml"},
            {"values": (True,)},
            {"values": (1.5,)},
            {"values": ()},
            {"values": tuple(range(33))},
            {"resource": "../secret"},
            {"request_id": ""},
            {"secret": "canary"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                Request(**(self.request.model_dump() | update))

    def test_range_rejected_before_submission(self) -> None:
        with self.assertRaises(ValueError):
            self.service.submit("a", self.request.model_copy(update={"values": (10001,)}))
        self.assertEqual(self.service.submissions, 0)

    def test_provider_revalidates_copied_and_constructed_requests(self) -> None:
        for update in (
            {"unit": "ml"},
            {"values": ()},
            {"values": (True,)},
            {"values": (1.5,)},
            {"values": tuple(range(33))},
            {"request_id": ""},
            {"values": (10001,)},
        ):
            payload = self.request.model_dump() | update
            for request in (
                self.request.model_copy(update=update),
                Request.model_construct(**payload),
            ):
                with self.subTest(update=update), self.assertRaises(ValueError):
                    self.service.submit("a", request)
                self.assertEqual(self.service.submissions, 0)

    def test_advance_is_idempotent(self) -> None:
        job = self.service.submit("a", self.request)
        self.service.advance(job)
        first = self.service.observe("a", job)
        self.service.advance(job)
        self.assertEqual(first, self.service.observe("a", job))


if __name__ == "__main__":
    unittest.main()
