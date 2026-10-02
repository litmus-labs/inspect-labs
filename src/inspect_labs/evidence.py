"""Private evidence linked to native logs; replay has no provider dependency."""

from __future__ import annotations

import hashlib
import os
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from inspect_ai import score
from inspect_ai.log import EvalLog, read_eval_log, write_eval_log
from pydantic import BaseModel, ConfigDict, Field

from inspect_labs.litmus_labs import JobRecord, Observation
from inspect_labs.native import observed_result


class EvidenceBundle(BaseModel):
    """Versioned private evidence bound to one exact native artifact.

    The hash detects mismatched artifacts, not a malicious trusted evaluator.
    Native transcripts remain in the native log, never copied into this file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1]
    task: Literal["litmus-count-measurement"]
    framework_version: str = Field(min_length=1)
    inspect_version: str = Field(min_length=1)
    native_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sample_uuids: list[str]
    observations: dict[str, Observation]
    jobs: list[JobRecord]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _validate_binding(log: EvalLog, bundle: EvidenceBundle) -> None:
    samples = {sample.uuid for sample in log.samples or []}
    if None in samples or len(bundle.sample_uuids) != len(samples):
        raise ValueError("Native sample identities are absent or duplicated")
    if log.eval.task != bundle.task or samples != set(bundle.sample_uuids):
        raise ValueError("Evidence task or sample identities do not match the native log")
    if not set(bundle.observations).issubset(samples):
        raise ValueError("Evidence contains an unknown sample")
    jobs = {(job.run_id, job.job_id): job for job in bundle.jobs}
    if len(jobs) != len(bundle.jobs):
        raise ValueError("Evidence contains duplicate provider jobs")
    for job in bundle.jobs:
        if job.run_id not in samples:
            raise ValueError("Provider job belongs to another native sample")
        observation = job.observation
        if (job.status == "completed") != (observation is not None):
            raise ValueError("Provider completion and observation disagree")
        if observation is not None and (
            observation.run_id != job.run_id
            or observation.job_id != job.job_id
            or observation.request_id != job.request.request_id
            or observation.resource != job.request.resource
            or observation.values != job.request.values
            or observation.value != sum(job.request.values)
        ):
            raise ValueError("Provider observation does not match its executed request")
    for sample_uuid, observation in bundle.observations.items():
        source_job = jobs.get((sample_uuid, observation.job_id))
        if (
            observation.run_id != sample_uuid
            or source_job is None
            or source_job.observation != observation
        ):
            raise ValueError("Scoring evidence is not linked to its provider job")


def persist_evidence(
    native_log: Path, observations: dict[str, Observation], jobs: list[JobRecord]
) -> Path:
    """Save private provider evidence even when the native evaluation failed.

    Args:
        native_log: Existing native .eval artifact.
        observations: Collected scoring observations keyed by native sample UUID.
        jobs: Provider snapshots, including incomplete jobs.

    Returns:
        New private evidence path next to the native log.

    Raises:
        ValueError: Evidence identities or provider facts are inconsistent.
        OSError: The evidence cannot be written or already exists.
    """
    log = read_eval_log(str(native_log))
    sample_uuids = []
    for sample in log.samples or []:
        if sample.uuid is None:
            raise ValueError("Native sample has no UUID")
        sample_uuids.append(sample.uuid)
    bundle = EvidenceBundle(
        schema_version=1,
        task="litmus-count-measurement",
        framework_version=version("inspect-labs"),
        inspect_version=version("inspect-ai"),
        native_sha256=_digest(native_log),
        sample_uuids=sample_uuids,
        observations=observations,
        jobs=jobs,
    )
    _validate_binding(log, bundle)
    destination = native_log.with_suffix(".evidence")
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(bundle.model_dump_json(indent=2) + "\n")
    return destination


def rescore_evidence(native_log: Path, evidence: Path, output: Path) -> EvalLog:
    """Rescore recorded facts through native Inspect without dispatch or model calls.

    Args:
        native_log: Original native artifact, left unchanged.
        evidence: Private evidence saved with that artifact.
        output: New native .eval destination; must not already exist.

    Returns:
        Native rescored log, preserving original errors and unknown evidence.

    Raises:
        ValueError: Evidence or its artifact binding is invalid.
        OSError: An input cannot be read or output already exists.
    """
    bundle = EvidenceBundle.model_validate_json(evidence.read_text())
    if _digest(native_log) != bundle.native_sha256:
        raise ValueError("Native log hash does not match the evidence bundle")
    log = read_eval_log(str(native_log))
    _validate_binding(log, bundle)
    saved_roles = log.eval.model_roles
    log.eval.model_roles = None
    # The saved live model is replaced with an offline native model. This
    # deterministic scorer uses neither generation nor a provider object.
    rescored = score(
        log,
        observed_result(bundle.observations),
        model="mockllm/model",
        model_roles={},
        action="overwrite",
        display="none",
    )
    rescored.eval.model_roles = saved_roles
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"):
        pass
    write_eval_log(rescored, str(output))
    return rescored
