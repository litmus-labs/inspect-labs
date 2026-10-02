"""Convenience commands for native Inspect laboratory tasks and evidence replay."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
from pathlib import Path

import anyio
from inspect_ai import eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelCost, ModelInfo, get_model_info, set_model_info

from inspect_labs.bindings import WorkflowEvidence, rescore_workflow
from inspect_labs.conformance import diagnose
from inspect_labs.evidence import rescore_evidence
from inspect_labs.liquid_tasks import serial_dilution_outcome, worklist_outcome
from inspect_labs.plugins import Kind, available
from inspect_labs.tasks import (
    handoff,
    handoff_outcome,
    measurement,
    measurement_outcome,
    robot_step_outcome,
)


def main() -> None:
    """Execute a native model-driven task or an explicitly selected scripted control.

    Raises:
        SystemExit: Arguments, artifacts or the native evaluation are invalid.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run a native laboratory evaluation task")
    run.add_argument("--task", choices=["measurement", "handoff"], default="measurement")
    run.add_argument("--backend", choices=["digital", "robot"], default="digital")
    run.add_argument("--scripted", action="store_true", help="Select the deterministic control")
    run.add_argument("--log-dir", type=Path, default=Path(".research/runs/measurement"))
    run.add_argument("--model", help="Native Inspect model identifier")
    run.add_argument("--allow-live", action="store_true", help="Explicitly permit a model API call")
    run.add_argument(
        "--cost-limit", type=float, help="Native per-sample USD limit, not a billing cap"
    )
    run.add_argument(
        "--price",
        type=float,
        nargs=2,
        metavar=("INPUT", "OUTPUT"),
        help="USD per million input/output tokens, for models without native cost data",
    )
    run.add_argument("--reject", action="store_true", help="Reject tools using native approval")
    replay = commands.add_parser("rescore", help="Score existing evidence with zero dispatch")
    replay.add_argument("native_log", type=Path)
    replay.add_argument("--evidence", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    listing = commands.add_parser("list", help="List installed environments and backends")
    listing.add_argument("kind", nargs="?", choices=["environment", "backend"])
    doctor = commands.add_parser(
        "doctor", help="Check an installed environment or backend without touching hardware"
    )
    target = doctor.add_mutually_exclusive_group(required=True)
    target.add_argument("--environment", help="Registered environment name")
    target.add_argument("--backend", help="Registered backend name")
    robot = commands.add_parser("robot-mock", help="Run the optional native robot mock baseline")
    robot.add_argument("--log-dir", type=Path, default=Path(".research/runs/robot-mock"))
    args = parser.parse_args()
    old_umask = os.umask(0o077)
    # Native Inspect prints console banners to stdout; reserve it for this command's JSON.
    stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if args.command == "list":
                kinds: list[Kind] = [args.kind] if args.kind else ["environment", "backend"]
                print(json.dumps({kind: available(kind) for kind in kinds}), file=stdout)
                return
            if args.command == "doctor":
                kind = "environment" if args.environment else "backend"
                report = anyio.run(diagnose, kind, args.environment or args.backend)
                print(json.dumps(report, indent=2), file=stdout)
                if not report["ok"]:
                    raise SystemExit(1)
                return
            if args.command == "robot-mock":
                try:
                    from inspect_labs.robot_mock import run_mock
                except ModuleNotFoundError as exc:
                    if exc.name != "inspect_robots":
                        raise
                    parser.error("Install inspect-labs[robots] to run the pinned native mock")
                robot_log = run_mock(args.log_dir)
                print(
                    json.dumps(
                        {
                            "log_dir": str(args.log_dir),
                            "status": robot_log.status,
                            "trials": robot_log.results.total_trials,
                            "metrics": robot_log.results.metrics,
                        }
                    ),
                    file=stdout,
                )
                if robot_log.status != "success":
                    raise SystemExit("Native robot mock failed; inspect the private native records")
                return
            if args.command == "rescore":
                try:
                    document = json.loads(args.evidence.read_text())
                    if not isinstance(document, dict):
                        raise ValueError("Evidence must be a JSON object")
                    schema = document.get("schema_version")
                    if schema == 2:
                        bundle = WorkflowEvidence.model_validate_json(args.evidence.read_text())
                        judges = {
                            "measurement": measurement_outcome,
                            "handoff": handoff_outcome,
                            "serial_dilution": serial_dilution_outcome,
                            "worklist_transfer": worklist_outcome,
                            "robot_step": robot_step_outcome,
                        }
                        task_name = bundle.task.rsplit("/", 1)[-1]
                        if task_name not in judges:
                            parser.error(
                                "Custom tasks require rescore_workflow with their trusted judge"
                            )
                        rescore_workflow(
                            args.native_log, args.evidence, args.output, judges[task_name]
                        )
                        log = read_eval_log(str(args.output))
                    else:
                        log = rescore_evidence(args.native_log, args.evidence, args.output)
                except (ValueError, OSError):
                    parser.error(
                        "Cannot rescore: invalid/mismatched evidence or unavailable file path"
                    )
                print(
                    json.dumps(
                        {"native_log": str(args.output), "status": log.status, "new_submissions": 0}
                    ),
                    file=stdout,
                )
                return
            live = bool(args.model and not args.model.startswith("mockllm/"))
            if not args.model and not args.scripted:
                parser.error("Select --model PROVIDER/MODEL, or --scripted for a control run")
            if args.model and args.scripted:
                parser.error("Choose a model evaluation or a scripted control, not both")
            if live and (not args.allow_live or args.cost_limit is None):
                parser.error("Live runs require --allow-live and an approved --cost-limit")
            if args.cost_limit is not None and (
                not math.isfinite(args.cost_limit) or args.cost_limit <= 0
            ):
                parser.error("--cost-limit must be positive and finite")
            if args.price is not None and not all(
                math.isfinite(value) and value > 0 for value in args.price
            ):
                parser.error("--price values must be positive and finite")
            if not live and (args.cost_limit is not None or args.price is not None):
                parser.error("--cost-limit and --price apply only to live model runs")
            if live:
                info = get_model_info(args.model)
                has_cost = info is not None and info.cost is not None
                if args.price is not None and has_cost:
                    parser.error("Native Inspect already prices this model; omit --price")
                if args.price is not None:
                    # Cache prices are unknown here. Writes are assumed to cost 1.25x input
                    # (common provider pricing) and reads 1x input; both can differ.
                    price_in, price_out = args.price
                    cost = ModelCost(
                        input=price_in,
                        output=price_out,
                        input_cache_write=price_in * 1.25,
                        input_cache_read=price_in,
                    )
                    set_model_info(
                        args.model, (info or ModelInfo()).model_copy(update={"cost": cost})
                    )
                elif not has_cost:
                    parser.error(
                        "Native Inspect has no cost data for this model, so --cost-limit "
                        "cannot be enforced; pass --price INPUT OUTPUT (USD per million tokens)"
                    )
            evidence_dir = str(args.log_dir.parent / (args.log_dir.name + "-evidence"))
            if args.task == "handoff":
                task = handoff(
                    backend=args.backend,
                    scripted=args.scripted,
                    reject=args.reject,
                    evidence_dir=evidence_dir,
                )
            else:
                if args.backend != "digital":
                    parser.error("The measurement task requires the digital backend")
                task = measurement(
                    scripted=args.scripted, reject=args.reject, evidence_dir=evidence_dir
                )
            log = eval(
                task,
                model=args.model or "mockllm/model",
                log_dir=str(args.log_dir),
                display="none",
                max_samples=1,
                max_connections=1,
                max_retries=0,
                max_tokens=256,
                token_limit=4096,
                time_limit=120,
                cost_limit=args.cost_limit,
                log_model_api=False,
            )[0]
            evidence = Path(log.location).with_suffix(".labs")
            if not evidence.exists():
                raise SystemExit("Native run has no sealed lab evidence; inspect native errors")
            bundle = WorkflowEvidence.model_validate_json(evidence.read_text())
            # Provider-side count; None when any sample's provider facts are unobserved.
            submissions: int | None = 0
            for record in bundle.samples.values():
                if record.payload is None or submissions is None:
                    submissions = None
                    continue
                jobs = record.payload.get("jobs")
                submissions += (
                    len(jobs)
                    if isinstance(jobs, list)
                    else int(record.payload.get("observation") is not None)
                )
            print(
                json.dumps(
                    {
                        "native_log": log.location,
                        "private_evidence": str(evidence),
                        "submissions": submissions,
                        "live": live,
                        "status": log.status,
                    }
                ),
                file=stdout,
            )
            if log.status != "success":
                raise SystemExit(
                    "Native evaluation failed; private logs and provider evidence retained"
                )
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
