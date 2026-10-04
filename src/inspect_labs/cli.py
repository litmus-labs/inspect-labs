"""Convenience commands for native Inspect laboratory tasks and evidence replay."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import cast

import anyio
from inspect_ai import eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelCost, ModelInfo, get_model_info, set_model_info
from pydantic import TypeAdapter

from inspect_labs.actions import DEFAULT_RULES, ActionRules
from inspect_labs.bindings import (
    EvidenceJudge,
    LabLogFile,
    attach_late_result,
    monitor_saved_run,
    replay_rules,
    rescore,
)
from inspect_labs.conformance import diagnose
from inspect_labs.evidence import rescore_evidence
from inspect_labs.gateway import ApprovedAction, approved_actions
from inspect_labs.liquid_tasks import serial_dilution_outcome, worklist_outcome
from inspect_labs.monitors import DEFAULT_MONITORS
from inspect_labs.plugins import Kind, available, canonical, resolve
from inspect_labs.serve import LabSession, serve_over_stdio
from inspect_labs.tasks import (
    handoff,
    handoff_outcome,
    measurement,
    measurement_outcome,
    robot_step_outcome,
)


class JudgeError(ValueError):
    """A ``--judge`` specification could not be loaded."""


def load_judge(spec: str) -> EvidenceJudge:
    """Load a judge named explicitly by the user as ``FILE.py:function`` or ``module:function``.

    Raises:
        JudgeError: The specification is malformed, missing or not callable.
    """
    source, _, name = spec.rpartition(":")
    if not source or not name:
        raise JudgeError("--judge must look like FILE.py:function or module:function")
    try:
        if source.endswith(".py"):
            path = Path(source).resolve()
            module_spec = importlib.util.spec_from_file_location(f"_judge_{path.stem}", path)
            if module_spec is None or module_spec.loader is None:
                raise JudgeError(f"Cannot load judge file {source}")
            sys.path.insert(0, str(path.parent))
            try:
                module = importlib.util.module_from_spec(module_spec)
                module_spec.loader.exec_module(module)
            finally:
                sys.path.remove(str(path.parent))
        else:
            module = importlib.import_module(source)
    except FileNotFoundError as exc:
        raise JudgeError(f"Judge file not found: {source}") from exc
    except ImportError as exc:
        raise JudgeError(f"Cannot import judge module {source}: {exc}") from exc
    judge = getattr(module, name, None)
    if not callable(judge):
        raise JudgeError(f"{spec} is not a callable judge")
    return cast(EvidenceJudge, judge)


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
    replay.add_argument(
        "--scorer",
        "--judge",
        dest="scorer",
        help="Lab scorer for a custom task as FILE.py:function or module:function. This "
        "runs the named code; it is chosen by you, never read from the evidence file. "
        "--judge is an earlier spelling.",
    )
    serve = commands.add_parser(
        "serve", help="Serve a Lab to an MCP agent over stdio, every action through the gateway"
    )
    serve.add_argument("--lab", required=True, help="Registered Lab name (see inspect-labs list)")
    serve.add_argument(
        "--lab-dir",
        type=Path,
        default=Path(".research/serve-lab"),
        help="The Lab's working directory",
    )
    serve.add_argument("--rules", type=Path, help="Action rules as JSON (default: DEFAULT_RULES)")
    serve.add_argument(
        "--approvals",
        type=Path,
        help="Actions approved in advance, as a JSON list of {tool, arguments}",
    )
    serve.add_argument(
        "--stop-file",
        type=Path,
        help="Stop the session when this file appears (its text is the reason)",
    )
    serve.add_argument(
        "--lab-log",
        type=Path,
        required=True,
        help="Where to write the session's lab log (never overwritten)",
    )
    monitor = commands.add_parser(
        "monitor", help="Run the default monitors on a saved run and list their flags"
    )
    monitor.add_argument("native_log", type=Path, help="The run's .eval log")
    monitor.add_argument("--evidence", type=Path, required=True, help="The run's .labs file")
    attach = commands.add_parser(
        "attach", help="Attach a result that arrived after the run, as a new lab log file"
    )
    attach.add_argument("evidence", type=Path, help="The run's .labs file")
    attach.add_argument("--sample", required=True, help="Sample UUID the result belongs to")
    attach.add_argument(
        "--observation", type=Path, required=True, help="The late observation as JSON"
    )
    attach.add_argument("--output", type=Path, required=True, help="New .labs file to write")
    attach.add_argument("--note", default="", help="Where the result came from")
    replay_rules_command = commands.add_parser(
        "replay-rules",
        help="Re-decide a run's saved actions under other action rules, running nothing",
    )
    replay_rules_command.add_argument("evidence", type=Path, help="The run's .labs file")
    replay_rules_command.add_argument(
        "--rules", type=Path, required=True, help="Action rules as a JSON file"
    )
    listing = commands.add_parser("list", help="List installed Labs and backends")
    listing.add_argument("kind", nargs="?", choices=["lab", "backend", "environment"])
    doctor = commands.add_parser(
        "doctor", help="Check an installed Lab or backend without touching hardware"
    )
    target = doctor.add_mutually_exclusive_group(required=True)
    target.add_argument("--lab", help="Registered Lab name")
    target.add_argument("--environment", dest="lab", help=argparse.SUPPRESS)
    target.add_argument("--backend", help="Registered backend name")
    robot = commands.add_parser("robot-mock", help="Run the optional native robot mock baseline")
    robot.add_argument("--log-dir", type=Path, default=Path(".research/runs/robot-mock"))
    args = parser.parse_args()
    if args.command == "serve":
        # MCP over stdio owns stdin and stdout, so serving runs before the stdout redirect.
        _serve(parser, args)
        return
    old_umask = os.umask(0o077)
    # Native Inspect prints console banners to stdout; reserve it for this command's JSON.
    stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if args.command == "list":
                kinds: list[Kind] = [canonical(args.kind)] if args.kind else ["lab", "backend"]
                print(json.dumps({kind: available(kind) for kind in kinds}), file=stdout)
                return
            if args.command == "monitor":
                try:
                    flagged = monitor_saved_run(args.native_log, args.evidence, DEFAULT_MONITORS)
                except (ValueError, OSError) as exc:
                    parser.error(f"Cannot monitor: {type(exc).__name__}: {exc}")
                flags = [
                    flag.model_dump(mode="json") for items in flagged.values() for flag in items
                ]
                print(
                    json.dumps(
                        {"samples": len(flagged), "flags": flags, "replay_only": True}, indent=2
                    ),
                    file=stdout,
                )
                return
            if args.command == "attach":
                try:
                    payload = json.loads(args.observation.read_text())
                    if not isinstance(payload, dict):
                        raise ValueError("The observation must be a JSON object")
                    attach_late_result(
                        args.evidence, args.sample, payload, args.output, note=args.note
                    )
                except (ValueError, OSError) as exc:
                    parser.error(f"Cannot attach: {type(exc).__name__}: {exc}")
                print(
                    json.dumps({"lab_log": str(args.output), "sample": args.sample}),
                    file=stdout,
                )
                return
            if args.command == "replay-rules":
                try:
                    rules = ActionRules.model_validate_json(args.rules.read_text())
                    replayed = replay_rules(args.evidence, rules)
                except (ValueError, OSError) as exc:
                    parser.error(f"Cannot replay rules: {type(exc).__name__}: {exc}")
                decisions = [
                    {"sample": uuid, **decision.model_dump(mode="json")}
                    for uuid, items in replayed.items()
                    for decision in items
                ]
                print(
                    json.dumps(
                        {
                            "rules_version": rules.version,
                            "actions": len(decisions),
                            "changed": sum(1 for d in decisions if d["changed"]),
                            "decisions": decisions,
                            "replay_only": True,
                        },
                        indent=2,
                    ),
                    file=stdout,
                )
                return
            if args.command == "doctor":
                kind = "lab" if args.lab else "backend"
                report = anyio.run(diagnose, kind, args.lab or args.backend)
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
                    if schema in (2, 3):
                        bundle = LabLogFile.model_validate_json(args.evidence.read_text())
                        judges = {
                            "measurement": measurement_outcome,
                            "handoff": handoff_outcome,
                            "serial_dilution": serial_dilution_outcome,
                            "worklist_transfer": worklist_outcome,
                            "robot_step": robot_step_outcome,
                        }
                        task_name = bundle.task.rsplit("/", 1)[-1]
                        if args.scorer:
                            judge = load_judge(args.scorer)
                        elif task_name in judges:
                            judge = judges[task_name]
                        else:
                            parser.error(
                                f"Task {bundle.task!r} is not built in: pass its scorer with "
                                "--scorer FILE.py:function (for example my_task.py:my_outcome)"
                            )
                        rescore(args.native_log, args.evidence, args.output, judge)
                        log = read_eval_log(str(args.output))
                    else:
                        log = rescore_evidence(args.native_log, args.evidence, args.output)
                except JudgeError as exc:
                    parser.error(str(exc))
                except (ValueError, OSError):
                    parser.error(
                        "Cannot rescore: invalid/mismatched evidence or unavailable file path"
                    )
                print(
                    json.dumps(
                        # Replay constructs no environment and calls no model by design;
                        # tests verify this. It is a property of the path, not a count.
                        {"native_log": str(args.output), "status": log.status, "replay_only": True}
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
            bundle = LabLogFile.model_validate_json(evidence.read_text())
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


def _serve(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Serve one Lab session over MCP stdio and report the lab log on stderr."""
    old_umask = os.umask(0o077)
    try:
        try:
            lab = resolve("lab", args.lab, directory=args.lab_dir)
            rules = (
                ActionRules.model_validate_json(args.rules.read_text())
                if args.rules
                else DEFAULT_RULES
            )
            approver = None
            if args.approvals:
                approved = TypeAdapter(list[ApprovedAction]).validate_json(
                    args.approvals.read_text()
                )
                approver = approved_actions(approved)
        except (LookupError, ValueError, OSError) as exc:
            parser.error(f"Cannot serve: {type(exc).__name__}: {exc}")
        session = LabSession(lab, rules, approver=approver, stop_file=args.stop_file)
        log = anyio.run(serve_over_stdio, session, args.lab_log)
        (record,) = log.samples.values()
        print(
            json.dumps(
                {
                    "lab_log": str(args.lab_log),
                    "actions": len(record.actions),
                    "refused": sum(1 for a in record.actions if a.status == "refused"),
                    "flags": len(log.flags),
                    "stopped": log.stopped,
                }
            ),
            file=sys.stderr,
        )
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
