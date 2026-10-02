"""Test persisted identities, failures and the non-executing replay boundary."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from inspect_ai import eval
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.model import Model, ModelConfig
from inspect_ai.solver import solver

from inspect_labs.evidence import EvidenceBundle, persist_evidence, rescore_evidence
from inspect_labs.litmus_labs import FixtureService
from inspect_labs.native import fixture_script, measurement_task


@pytest.mark.parametrize("case", ["correct", "wrong", "missing", "rejected", "error", "queued"])
def test_persisted_rescore_without_dispatch(tmp_path: Path, case: str) -> None:
    @solver
    def failed_actor():
        async def solve(state, generate):
            if case == "queued":
                from inspect_labs.litmus_labs import Request

                service.submit(
                    state.uuid, Request(request_id="r1", resource="sample1", values=(2, 3))
                )
            else:
                await fixture_script()(state, generate)
            raise RuntimeError("injected actor error")

        return solve

    service = FixtureService(frozenset({"sample1"}))
    observations = {}
    task = measurement_task(
        service,
        observations,
        report="9" if case == "wrong" else "5",
        observation_available=case != "missing",
        reject=case == "rejected",
    )
    if case in {"error", "queued"}:
        with patch("inspect_labs.native.fixture_script", return_value=failed_actor()):
            task = measurement_task(service, observations)
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path), display="none")[0]
    original = Path(log.location)
    before = original.read_bytes()
    evidence = persist_evidence(original, observations, service.records())
    bundle = EvidenceBundle.model_validate_json(evidence.read_text())
    assert bundle.native_sha256 == hashlib.sha256(before).hexdigest()
    assert evidence.stat().st_mode & 0o777 == 0o600
    if case == "queued":
        assert bundle.jobs[0].status == "queued"
        assert bundle.observations == {}
    service.dispatch_enabled = False
    output = tmp_path / "rescored.eval"
    with (
        patch.object(FixtureService, "submit", side_effect=AssertionError("dispatch")),
        patch.object(FixtureService, "advance", side_effect=AssertionError("advance")),
        patch.object(FixtureService, "cancel", side_effect=AssertionError("cancel")),
        patch.object(Model, "generate", side_effect=AssertionError("model call")),
    ):
        rescore_evidence(original, evidence, output)
    assert original.read_bytes() == before
    reopened = read_eval_log(str(output))
    assert "litmus-fixture/v1" not in reopened.model_dump_json()
    assert reopened.status == log.status
    assert reopened.error == log.error
    if case in {"error", "queued"}:
        assert reopened.samples[0].error == log.samples[0].error
    else:
        assert reopened.samples[0].scores == log.samples[0].scores
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        rescore_evidence(original, evidence, output)


def test_artifact_and_provenance_mismatches_rejected(tmp_path: Path) -> None:
    service = FixtureService(frozenset({"sample1"}))
    observations = {}
    log = eval(
        measurement_task(service, observations),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )[0]
    original = Path(log.location)
    evidence = persist_evidence(original, observations, service.records())
    payload = json.loads(evidence.read_text())
    altered = tmp_path / "altered.json"
    for changed in (
        payload | {"native_sha256": "0" * 64},
        payload | {"sample_uuids": ["unrelated"]},
        payload | {"framework_version": ""},
        payload | {"observations": {"unrelated": next(iter(payload["observations"].values()))}},
        payload | {"jobs": []},
    ):
        altered.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            rescore_evidence(original, altered, tmp_path / "invalid.eval")
        assert not (tmp_path / "invalid.eval").exists()


def test_rescore_never_reconstructs_saved_live_roles(tmp_path: Path) -> None:
    service = FixtureService(frozenset({"sample1"}))
    observations = {}
    log = eval(
        measurement_task(service, observations),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )[0]
    log = read_eval_log(log.location)
    log.eval.model_roles = {"judge": ModelConfig(model="openrouter/unapproved")}
    original = tmp_path / "saved-role.eval"
    write_eval_log(log, str(original))
    evidence = persist_evidence(original, observations, service.records())
    with patch(
        "inspect_ai.model._model_config.get_model",
        side_effect=AssertionError("saved live role initialized"),
    ):
        rescored = rescore_evidence(original, evidence, tmp_path / "replay.eval")
    assert rescored.eval.model_roles == log.eval.model_roles
