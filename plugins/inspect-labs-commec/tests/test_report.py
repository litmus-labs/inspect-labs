"""Untrusted JSON and native coverage boundaries, with inert hand-authored records."""

import json

import pytest
from inspect_labs_commec.report import interpret_report

REVISIONS = {"biorisk": "1.2", "best_match": "1.0", "low_concern": "1.1", "control_lists": "1.1"}
LOG = "Commec Screen completed at 2026-09-28\n"


def native_report():
    return {
        "commec_info": {
            "commec_version": "2.1.0",
            "json_output_version": "0.6",
            "time_taken": "00:00:01",
            "date_run": "2026-09-28",
        },
        "query_info": {
            "file": "/work/input.fasta",
            "number_of_queries": 1,
            "total_query_length": 60,
        },
        "queries": {
            "item0": {
                "query": "item0",
                "description": "",
                "length": 60,
                "hits": [],
                "status": {
                    "screen_status": "Pass",
                    "biorisk": "Pass",
                    "protein_taxonomy": "Pass",
                    "nucleotide_taxonomy": "Pass",
                    "low_concern": "Pass",
                    "rationale": "No flag in this fixture",
                },
            }
        },
        "database_info": {
            "revisions": REVISIONS,
            "control_list_info": [],
            "search_tool_info": {
                key: {"tool_info": "tool 1.0", "database_info": "db 1.0"}
                for key in (
                    "biorisk_search_info",
                    "protein_search_info",
                    "nucleotide_search_info",
                    "low_concern_protein_search_info",
                    "low_concern_rna_search_info",
                    "low_concern_dna_search_info",
                )
            },
        },
    }


def interpret(value, **kwargs):
    options = dict(
        expected_queries={"item0": ("item0", 60)},
        input_path="/work/input.fasta",
        revisions=REVISIONS,
        exit_code=0,
        process_log=LOG,
    )
    options.update(kwargs)
    return interpret_report(json.dumps(value), **options)


def test_complete_and_original_identity_mapping():
    data = native_report()
    assert interpret(data).known
    data["queries"]["item0"]["query"] = "item0|original"
    assert not interpret(data).known
    assert interpret(data, expected_queries={"item0": ("item0|original", 60)}).known


@pytest.mark.parametrize("status", ["Warning", "Flag", "Warning (Cleared)", "Flag (Cleared)"])
def test_review_is_distinct_from_pass(status):
    data = native_report()
    data["queries"]["item0"]["status"]["screen_status"] = status
    result = interpret(data)
    assert result.known and result.review_required


@pytest.mark.parametrize(
    "status",
    [
        "-",
        "Skip",
        "Skip (too short)",
        "Skip (too long)",
        "Pass (Skipped Taxonomy)",
        "Incomplete",
        "Error",
    ],
)
def test_noncomplete_statuses_do_not_clear(status):
    data = native_report()
    data["queries"]["item0"]["status"]["screen_status"] = status
    assert not interpret(data).known


@pytest.mark.parametrize(
    "stage,explanation",
    [
        ("nucleotide_taxonomy", "skipping nucleotide search since no noncoding regions fetched"),
        ("low_concern", "no regulated regions to clear"),
    ],
)
def test_applicability_requires_evidence(stage, explanation):
    data = native_report()
    data["queries"]["item0"]["status"][stage] = "Skip"
    assert not interpret(data).known
    assert interpret(data, process_log=LOG + explanation).known


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_query",
        "extra_query",
        "missing_stage",
        "version",
        "wrong_count",
        "boolean_count",
        "revision",
        "placeholder",
        "contradiction",
        "missing_hits",
    ],
)
def test_incomplete_or_inconsistent_input_is_unknown(mutation):
    data = native_report()
    match mutation:
        case "missing_query":
            data["queries"] = {}
        case "extra_query":
            data["queries"]["unexpected"] = data["queries"]["item0"]
        case "missing_stage":
            del data["queries"]["item0"]["status"]["biorisk"]
        case "version":
            data["commec_info"]["json_output_version"] = "future"
        case "wrong_count":
            data["query_info"]["number_of_queries"] = 2
        case "boolean_count":
            data["query_info"]["number_of_queries"] = True
        case "revision":
            data["database_info"]["revisions"] = {}
        case "placeholder":
            data["database_info"]["search_tool_info"]["biorisk_search_info"]["tool_info"] = "x.x.x"
        case "contradiction":
            data["queries"]["item0"]["status"]["biorisk"] = "Flag"
        case "missing_hits":
            del data["queries"]["item0"]["hits"]
    assert not interpret(data).known


@pytest.mark.parametrize(
    "options",
    [
        {"exit_code": 1},
        {"exit_code": None},
        {"interrupted": True},
        {"process_log": LOG + "Commec Screen Terminated."},
        {"process_log": ""},
    ],
)
def test_process_completion_is_required(options):
    assert not interpret(native_report(), **options).known


def test_duplicate_keys_are_not_silently_replaced():
    raw = json.dumps(native_report()).replace(
        '"query": "item0"', '"query": "wrong", "query": "item0"'
    )
    result = interpret_report(
        raw,
        expected_queries={"item0": ("item0", 60)},
        input_path="/work/input.fasta",
        revisions=REVISIONS,
        exit_code=0,
        process_log=LOG,
    )
    assert not result.known and "Duplicate JSON key" in result.issues[-1]


def test_mixed_batch_does_not_hide_incomplete_query():
    data = native_report()
    second = json.loads(json.dumps(data["queries"]["item0"]))
    second["query"] = "item1"
    second["status"]["screen_status"] = "Error"
    data["queries"]["item1"] = second
    data["query_info"].update(number_of_queries=2, total_query_length=120)
    result = interpret(data, expected_queries={"item0": ("item0", 60), "item1": ("item1", 60)})
    assert not result.known and result.recommendations == {"item0": "Pass", "item1": "Error"}


@pytest.mark.parametrize("status", ["Flag", "Warning", "Flag (Cleared)", "Error"])
def test_pass_cannot_hide_hit_recommendations(status):
    data = native_report()
    data["queries"]["item0"]["hits"] = [
        {"recommendation": {"status": status, "from_step": "Biorisk Search"}}
    ]
    assert not interpret(data).known


def test_cleared_aggregate_requires_cleared_hits():
    data = native_report()
    data["queries"]["item0"]["status"]["screen_status"] = "Flag (Cleared)"
    data["queries"]["item0"]["hits"] = [
        {"recommendation": {"status": "Flag", "from_step": "Biorisk Search"}}
    ]
    assert not interpret(data).known
    data["queries"]["item0"]["hits"][0]["recommendation"]["status"] = "Flag (Cleared)"
    assert interpret(data).known and interpret(data).review_required
