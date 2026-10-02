"""Strict interpretation of pinned Commec output, without biological classification.

The original JSON remains a private artifact. This module validates only the fields
needed for workflow decisions. Unsupported evidence remains unknown, never cleared.
Native schema: ibbis-bio/common-mechanism commit 24fe039, MIT, JSON format 0.6.
"""

from __future__ import annotations

import json
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

Status = Literal[
    "-",
    "Skip",
    "Skip (too short)",
    "Skip (too long)",
    "Pass",
    "Pass (Skipped Taxonomy)",
    "Warning (Cleared)",
    "Flag (Cleared)",
    "Warning",
    "Flag",
    "Incomplete",
    "Error",
]
COMPLETE = {"Pass", "Warning (Cleared)", "Flag (Cleared)", "Warning", "Flag"}
STAGES = ("biorisk", "protein_taxonomy", "nucleotide_taxonomy", "low_concern")
SCREEN_CONFIG: dict[str, JsonValue] = {
    "auto_update_databases": False,
    "resume": False,
    "force": False,
    "skip_taxonomy_search": False,
    "skip_nt_search": False,
    "do_cleanup": False,
    "threads": 1,
}


class NativeModel(BaseModel):
    """Validate required fields strictly; preserve other fields in the raw artifact."""

    model_config = ConfigDict(strict=True, extra="ignore")


class StageStatus(NativeModel):
    """Native per-query aggregate fields, not individual tool-execution receipts."""

    screen_status: Status
    biorisk: Status
    protein_taxonomy: Status
    nucleotide_taxonomy: Status
    low_concern: Status
    rationale: str = Field(min_length=1)


class HitRecommendation(NativeModel):
    """Native hit decision; annotations remain in the unmodified raw report."""

    status: Status
    from_step: str = Field(min_length=1)


class Hit(NativeModel):
    """The only hit fields used to check recommendation consistency."""

    recommendation: HitRecommendation


class QueryResult(NativeModel):
    """Original identity is distinct from the surrounding canonical dictionary key."""

    query: str
    description: str
    length: int = Field(gt=0)
    status: StageStatus
    hits: list[Hit]


class RunInfo(NativeModel):
    """Pinned format and software identity; timestamps alone do not prove completion."""

    commec_version: Literal["2.1.0"]
    json_output_version: Literal["0.6"]
    time_taken: str = Field(pattern=r"^\d+:\d{2}:\d{2}$")
    date_run: str = Field(min_length=1)


class QueryInfo(NativeModel):
    """Native input summary."""

    file: str
    number_of_queries: int = Field(gt=0)
    total_query_length: int = Field(ge=0)


class ToolVersion(NativeModel):
    """Native tool version strings retained without asserting authenticity."""

    tool_info: str = Field(min_length=1)
    database_info: str = Field(min_length=1)


class DatabaseInfo(NativeModel):
    """Database provenance reported by the pinned scanner."""

    revisions: dict[str, str]
    search_tool_info: dict[str, ToolVersion]
    control_list_info: list[dict[str, JsonValue]]


class NativeReport(NativeModel):
    """Required JSON 0.6 report fields."""

    commec_info: RunInfo
    query_info: QueryInfo
    queries: dict[str, QueryResult]
    database_info: DatabaseInfo


class ScreeningResult(BaseModel):
    """Workflow interpretation, separate from scientific truth and authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    known: bool
    recommendations: dict[str, str] = Field(default_factory=dict)
    review_required: bool = False
    issues: list[str] = Field(default_factory=list)


def unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """Reject duplicate JSON identities instead of silently keeping the last one."""
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def interpret_report(
    raw: str,
    *,
    expected_queries: dict[str, tuple[str, int]],
    input_path: str,
    revisions: dict[str, str],
    exit_code: int | None,
    process_log: str,
    interrupted: bool = False,
) -> ScreeningResult:
    """Interpret a full-coverage invocation against trusted expected identities.

    Args:
        raw: Unmodified native JSON, stored separately by the caller.
        expected_queries: Canonical ID mapped to original ID and length.
        input_path: Exact staged input filename reported by Commec.
        revisions: Expected provisioned database revisions, not inferred from output.
        exit_code: Observed native process exit code, or None if unavailable.
        process_log: Retained native stdout/stderr and screen log.
        interrupted: Supervisor observed timeout/cancellation or missing completion.

    Returns:
        Known recommendations only when required evidence is complete and consistent.
        This profile requires all taxonomy stages; it does not grant fulfillment.
    """
    issues: list[str] = []
    if exit_code != 0 or interrupted or "Commec Screen Terminated." in process_log:
        issues.append("process completion unavailable")
    if "Commec Screen completed at" not in process_log:
        issues.append("native completion marker missing")
    try:
        report = NativeReport.model_validate(json.loads(raw, object_pairs_hook=unique_object))
    except (ValueError, TypeError, ValidationError) as exc:
        return ScreeningResult(known=False, issues=[*issues, f"invalid native report: {exc}"])
    if not expected_queries or set(report.queries) != set(expected_queries):
        issues.append("query coverage mismatch")
    eligible = sum(n for _, n in expected_queries.values() if 41 <= n <= 100000)
    if (
        report.query_info.file != input_path
        or report.query_info.number_of_queries != len(expected_queries)
        or report.query_info.total_query_length != eligible
    ):
        issues.append("input summary mismatch")
    if (
        set(revisions) != {"biorisk", "best_match", "low_concern", "control_lists"}
        or report.database_info.revisions != revisions
        or any(not value or value in {"0.0", "x.x.x"} for value in revisions.values())
    ):
        issues.append("database revision mismatch or missing provenance")
    tools = report.database_info.search_tool_info
    required_tools = {
        "biorisk_search_info",
        "protein_search_info",
        "nucleotide_search_info",
        "low_concern_protein_search_info",
        "low_concern_rna_search_info",
        "low_concern_dna_search_info",
    }
    if not required_tools <= set(tools) or any(
        value.tool_info == "x.x.x" or value.database_info == "x.x.x" for value in tools.values()
    ):
        issues.append("search tool provenance unavailable")
    recommendations: dict[str, str] = {}
    for canonical, query in report.queries.items():
        if expected_queries.get(canonical) != (query.query, query.length):
            issues.append(f"query identity mismatch: {canonical}")
        status = query.status
        recommendations[canonical] = status.screen_status
        if status.screen_status not in COMPLETE:
            issues.append(f"query not fully screened: {canonical}")
        for stage in STAGES:
            value = getattr(status, stage)
            if value in COMPLETE:
                continue
            # These are documented native non-applicability paths, not omitted
            # configured coverage. Other combinations remain unsupported/unknown.
            if (
                value == "Skip"
                and stage == "nucleotide_taxonomy"
                and (
                    "skipping nucleotide search since no noncoding regions fetched" in process_log
                    and status.protein_taxonomy in COMPLETE
                )
            ):
                continue
            if (
                value == "Skip"
                and stage == "low_concern"
                and ("no regulated regions to clear" in process_log and not query.hits)
            ):
                continue
            issues.append(f"missing applicable coverage: {canonical}/{stage}")
        if status.screen_status == "Pass" and any(
            getattr(status, stage) in {"Warning", "Flag", "Warning (Cleared)", "Flag (Cleared)"}
            for stage in STAGES
        ):
            issues.append(f"contradictory aggregate recommendation: {canonical}")
        for hit in query.hits:
            value = hit.recommendation.status
            if (
                value not in COMPLETE
                or (status.screen_status == "Pass" and value != "Pass")
                or (
                    status.screen_status in {"Warning (Cleared)", "Flag (Cleared)"}
                    and value in {"Warning", "Flag"}
                )
            ):
                issues.append(f"contradictory or incomplete hit recommendation: {canonical}")
    return ScreeningResult(
        known=not issues,
        recommendations=recommendations,
        review_required=any(v != "Pass" for v in recommendations.values()),
        issues=issues,
    )


class ProcessReceipt(NativeModel):
    """Supervisor facts, preserved separately from scanner-reported completion."""

    exit_code: int | None
    interrupted: bool
    stdout_stderr: str
    export_errors: list[str]


def interpret_artifacts(
    artifacts: dict[str, str],
    *,
    screen_id: str,
    revisions: dict[str, str],
) -> ScreeningResult:
    """Interpret saved bytes identically at collection and pure replay boundaries.

    Args:
        artifacts: Basename to original text, including input/config/process receipts.
        screen_id: Supervisor-assigned invocation identity.
        revisions: Trusted recorded runtime database revisions.
    """
    process = ProcessReceipt.model_validate_json(artifacts["process.json"])
    staged = artifacts["input.fasta"]
    lines = staged.splitlines()
    if len(lines) != 2 or lines[0] != ">item0" or set(lines[1]) != {"A"}:
        return ScreeningResult(known=False, issues=["unsupported staged input"])
    result = interpret_report(
        artifacts.get("result.output.json", ""),
        expected_queries={"item0": ("item0", len(lines[1]))},
        input_path=f"/work/{screen_id}/input.fasta",
        revisions=revisions,
        exit_code=process.exit_code,
        process_log=process.stdout_stderr + artifacts.get("result.screen.log", ""),
        interrupted=process.interrupted,
    )
    issues = list(process.export_errors)
    cleaned = artifacts.get("result.cleaned.fasta", "").splitlines()
    # Native SeqIO wraps FASTA at 60 columns. Compare the canonical identifier and
    # sequence while preserving both original byte streams as distinct artifacts.
    if not cleaned or cleaned[0] != lines[0] or "".join(cleaned[1:]) != lines[1]:
        issues.append("cleaned input identity unavailable or changed")
    requested = json.loads(artifacts["requested-config.json"])
    if requested != SCREEN_CONFIG or any(
        type(requested[k]) is not type(v) for k, v in SCREEN_CONFIG.items()
    ):
        issues.append("unsupported requested configuration")
    try:
        effective = yaml.safe_load(artifacts.get("result_config.yaml", ""))
        if not isinstance(effective, dict) or any(
            effective.get(k) != v or type(effective.get(k)) is not type(v)
            for k, v in SCREEN_CONFIG.items()
        ):
            issues.append("effective configuration mismatch")
    except yaml.YAMLError:
        issues.append("invalid effective configuration")
    return result.model_copy(
        update={"known": result.known and not issues, "issues": [*result.issues, *issues]}
    )
