"""Provider binding contract checks: reference bindings pass, broken bindings are named."""

from pathlib import Path

import anyio
import pytest

from inspect_labs import check_environment
from inspect_labs.environments import HandoffEnvironment, MeasurementEnvironment
from inspect_labs.litmus_labs import FixtureService, Request

REQUEST = Request(request_id="r1", resource="sample1", values=(2, 3))


def test_reference_measurement_binding_conforms() -> None:
    service = FixtureService(frozenset({"sample1"}))
    report = anyio.run(
        check_environment,
        lambda: MeasurementEnvironment("run", REQUEST, service),
        lambda: service.submissions,
    )
    assert report.passed, report.violations
    assert service.submissions == 0


def test_reference_handoff_binding_conforms(tmp_path: Path) -> None:
    def delivered() -> int:
        return int((tmp_path / "delivered-report.bin").exists())

    report = anyio.run(
        check_environment,
        lambda: HandoffEnvironment(tmp_path, "report-1", "analysis", b"x"),
        delivered,
    )
    assert report.passed, report.violations


class SubmitsOnObserve(MeasurementEnvironment):
    async def observe(self):
        self.service.submit(self.run_id, REQUEST)
        return await super().observe()


class DuplicateTools(MeasurementEnvironment):
    @property
    def tools(self):
        return [*super().tools, super().tools[0]]


class UnstableObservation(MeasurementEnvironment):
    count = 0

    async def observe(self):
        UnstableObservation.count += 1
        return {"reading": UnstableObservation.count}


class MissingArtifact(MeasurementEnvironment):
    @property
    def artifacts(self):
        return [Path("/nonexistent/child.json")]


class ObserveFails(MeasurementEnvironment):
    async def observe(self):
        raise ConnectionError("down")


@pytest.mark.parametrize(
    ("binding", "violation"),
    [
        (SubmitsOnObserve, "observe dispatched provider work"),
        (DuplicateTools, "tool names are not unique"),
        (UnstableObservation, "repeated observation changed without actor action"),
        (MissingArtifact, "artifact is not an existing file"),
        (ObserveFails, "observe raised ConnectionError"),
    ],
)
def test_contract_violations_are_named(binding, violation: str) -> None:
    service = FixtureService(frozenset({"sample1"}))
    report = anyio.run(
        check_environment, lambda: binding("run", REQUEST, service), lambda: service.submissions
    )
    assert not report.passed
    assert any(item.startswith(violation) for item in report.violations), report.violations


def test_construction_dispatch_is_named() -> None:
    service = FixtureService(frozenset({"sample1"}))

    def eager():
        service.submit("run", REQUEST)
        return MeasurementEnvironment("run", REQUEST, service)

    report = anyio.run(check_environment, eager, lambda: service.submissions)
    assert "construction dispatched provider work" in report.violations


def test_reference_robot_binding_conforms(tmp_path: Path) -> None:
    pytest.importorskip("inspect_robots")
    from inspect_labs.robot_workflow import RobotHandoffEnvironment

    def rollouts() -> int:
        return len(list((tmp_path / "robot").glob("*.json")))

    report = anyio.run(
        check_environment,
        lambda: RobotHandoffEnvironment(tmp_path, "report-1", "analysis", b"x"),
        rollouts,
    )
    assert report.passed, report.violations


class InfoRaises(MeasurementEnvironment):
    @property
    def info(self):
        raise RuntimeError("no declaration")


class ArtifactsRaise(MeasurementEnvironment):
    closed = False

    @property
    def artifacts(self):
        raise OSError("unreadable")

    async def close(self) -> None:
        ArtifactsRaise.closed = True


class CloseRaises(MeasurementEnvironment):
    async def close(self) -> None:
        raise ConnectionError("hung up")


class NoToolsBadInfo(MeasurementEnvironment):
    info = None

    @property
    def tools(self):
        return []


class Undocumented(MeasurementEnvironment):
    @property
    def tools(self):
        from inspect_ai.tool import tool

        @tool
        def bare():
            async def execute(x: int) -> str:
                return str(x)

            return execute

        return [bare()]


class Timestamped(MeasurementEnvironment):
    count = 0

    async def observe(self):
        Timestamped.count += 1
        return {**(await super().observe()), "read_at": Timestamped.count}


@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        (InfoRaises, ["info raised RuntimeError"]),
        (ArtifactsRaise, ["artifacts raised OSError"]),
        (CloseRaises, ["close raised ConnectionError"]),
        (NoToolsBadInfo, ["info is not an EnvironmentInfo", "no actor tools are declared"]),
        (Undocumented, ["tool 0 is not a documented native Inspect tool"]),
    ],
)
def test_provider_exceptions_are_reported_not_raised(binding, expected: list[str]) -> None:
    service = FixtureService(frozenset({"sample1"}))
    report = anyio.run(
        check_environment, lambda: binding("run", REQUEST, service), lambda: service.submissions
    )
    for clause in expected:
        assert any(item.startswith(clause) for item in report.violations), report.violations
    if binding is ArtifactsRaise:
        assert ArtifactsRaise.closed


def test_factory_failure_and_volatile_keys() -> None:
    def broken():
        raise ValueError("bad config")

    report = anyio.run(check_environment, broken, lambda: 0)
    assert report.violations == ("construction raised ValueError",)
    service = FixtureService(frozenset({"sample1"}))

    def check(volatile):
        return anyio.run(
            lambda: check_environment(
                lambda: Timestamped("run", REQUEST, service),
                lambda: service.submissions,
                volatile=volatile,
            )
        )

    assert "repeated observation changed without actor action" in check(frozenset()).violations
    assert check(frozenset({"read_at"})).passed


@pytest.mark.parametrize("kind", ["lab", "environment", "backend"])
def test_doctor_checks_declarations_without_constructing_physical_components(kind: str) -> None:
    from inspect_labs import plugins
    from inspect_labs.conformance import diagnose

    @plugins.register(kind, f"physical-doctor-{kind}")
    def physical(*args):
        raise AssertionError("doctor must not construct a physical component or call its lifecycle")

    report = anyio.run(diagnose, kind, f"physical-doctor-{kind}")
    assert report["ok"]
    assert report["conformance"] == "not_run"


def test_lab_is_the_canonical_kind_and_environment_a_legacy_alias() -> None:
    from inspect_labs import plugins

    @plugins.lab("alias-check-lab")
    def make_lab(*args):
        raise AssertionError("registration must not construct")

    assert "alias-check-lab" in plugins.available("lab")
    assert "alias-check-lab" in plugins.available("environment")
    assert plugins.factory("environment", "alias-check-lab") is make_lab
    with pytest.raises(ValueError, match="already registered"):
        plugins.environment("alias-check-lab")(make_lab)
    with pytest.raises(ValueError, match="Unknown component kind"):
        plugins.canonical("sandbox")


def test_new_entry_point_group_wins_over_the_legacy_group(monkeypatch) -> None:
    from importlib.metadata import EntryPoint

    from inspect_labs import plugins

    groups = {
        "inspect_labs.labs": [EntryPoint("shared", "new_pkg:make", "inspect_labs.labs")],
        "inspect_labs.environments": [
            EntryPoint("shared", "old_pkg:make", "inspect_labs.environments"),
            EntryPoint("legacy-only", "old_pkg:other", "inspect_labs.environments"),
        ],
    }
    monkeypatch.setattr(plugins, "entry_points", lambda group: groups.get(group, []))

    points = plugins._entry_points("lab")
    assert points["shared"].value == "new_pkg:make"
    assert points["legacy-only"].value == "old_pkg:other"
