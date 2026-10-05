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
    read_lab_logs,
    read_session_log,
    replay_rules,
    rescore,
)
from inspect_labs.conformance import diagnose
from inspect_labs.connectors import (
    ConnectorServer,
    connector_lab,
    load_template,
    snapshot_connector,
)
from inspect_labs.evidence import rescore_evidence
from inspect_labs.gateway import (
    ApprovedAction,
    Approver,
    Lease,
    approved_actions,
    first_approval,
    leased_actions,
)
from inspect_labs.journal import Journal, check_witness, read_journal, witness_file
from inspect_labs.liquid_tasks import serial_dilution_outcome, worklist_outcome
from inspect_labs.monitors import DEFAULT_MONITORS, LIVE_MONITORS, repeated_refusals
from inspect_labs.operators import ApprovalQueue, ControlRequest, send_control
from inspect_labs.plugins import Kind, available, canonical, resolve
from inspect_labs.release import ReleasePolicy, release_lab_log, verify_release
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
    served = serve.add_mutually_exclusive_group(required=True)
    served.add_argument("--lab", help="Registered Lab name (see inspect-labs list)")
    served.add_argument(
        "--connector", type=Path, help="A reviewed connector profile (see inspect-labs connector)"
    )
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
        "--leases",
        type=Path,
        help="Time-limited permissions, as a JSON list of "
        "{name, tool, match, expires_at, uses, granted_by, reason}",
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
    serve.add_argument(
        "--journal",
        type=Path,
        help="Where to write each step as it happens (default: the lab log path "
        "with .journal.jsonl)",
    )
    serve.add_argument(
        "--witness-file",
        type=Path,
        help="Also append each journal digest here; keep it where the lab can't rewrite it",
    )
    serve.add_argument(
        "--control-socket",
        type=Path,
        help="Private Unix socket for operators to watch, approve and stop the session",
    )
    serve.add_argument(
        "--approval-timeout",
        type=float,
        default=300,
        help="Seconds a held action waits for an operator before it is refused",
    )
    serve.add_argument(
        "--refusal-limit",
        type=int,
        help="Stop the session after this many refused actions",
    )
    serve.add_argument(
        "--stop-on",
        action="append",
        default=[],
        metavar="MONITOR",
        help="Stop the session when this live monitor flags (repeatable)",
    )
    operator = commands.add_parser(
        "operator", help="Watch, approve or stop a served session over its control socket"
    )
    operator.add_argument("socket", type=Path, help="The session's control socket")
    operator.add_argument(
        "command_name",
        metavar="action",
        choices=["status", "pending", "approve", "refuse", "stop"],
        help="What to do",
    )
    operator.add_argument("id", nargs="?", help="The pending action's id (approve, refuse)")
    operator.add_argument("--by", help="Your name, recorded with the answer")
    operator.add_argument("--reason", help="Why the session is stopped")
    journal = commands.add_parser(
        "journal", help="Check a session journal's digests, and optionally its witness file"
    )
    journal.add_argument("path", type=Path, help="The session's .journal.jsonl")
    journal.add_argument("--witness", type=Path, help="A witness file to compare with")
    connector = commands.add_parser(
        "connector", help="Pin and classify an MCP connector's tools for review"
    )
    connector_commands = connector.add_subparsers(dest="connector_command", required=True)
    snapshot = connector_commands.add_parser(
        "snapshot", help="List a connector's tools and write a profile to review"
    )
    snapshot.add_argument("name", help="A short name, such as pubmed")
    where = snapshot.add_mutually_exclusive_group(required=True)
    where.add_argument("--command", dest="server_command", help="Command that starts the server")
    where.add_argument("--url", help="The server's https URL")
    snapshot.add_argument(
        "--arg", action="append", default=[], dest="server_args", help="Server argument (repeat)"
    )
    snapshot.add_argument(
        "--env", action="append", default=[], help="Environment variable to pass (repeat)"
    )
    snapshot.add_argument(
        "--template",
        help="Classifications to apply: a built-in template name or a JSON file",
    )
    snapshot.add_argument("--output", type=Path, required=True, help="Profile to write")
    release = commands.add_parser(
        "release", help="Write a lab log released at one tier, with its key in a private file"
    )
    release.add_argument("lab_log", type=Path, help="A .labs file or a session lab log")
    release.add_argument("--policy", type=Path, required=True, help="Release policy as JSON")
    release.add_argument("--tier", required=True, help="The tier to release at")
    release.add_argument("--output", type=Path, required=True, help="The release to write")
    release.add_argument("--key", type=Path, required=True, help="Where to write the key")
    verify = commands.add_parser(
        "verify-release", help="Check a release against the full lab log it came from"
    )
    verify.add_argument("release_file", type=Path, help="The release")
    verify.add_argument("--lab-log", type=Path, required=True, help="The full lab log")
    verify.add_argument("--key", type=Path, required=True, help="The release's key")
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
            if args.command == "operator":
                try:
                    reply = send_control(
                        args.socket,
                        ControlRequest(
                            command=args.command_name, id=args.id, by=args.by, reason=args.reason
                        ),
                    )
                except (ValueError, OSError) as exc:
                    parser.error(f"Cannot reach the session: {type(exc).__name__}: {exc}")
                print(json.dumps(reply, indent=2), file=stdout)
                if not reply.get("ok"):
                    sys.exit(1)
                return
            if args.command == "journal":
                try:
                    checked = read_journal(args.path)
                    problems = check_witness(checked, args.witness) if args.witness else []
                except (ValueError, OSError) as exc:
                    parser.error(f"Journal check failed: {type(exc).__name__}: {exc}")
                print(
                    json.dumps(
                        {
                            "entries": len(checked.entries),
                            "head": checked.head,
                            "ended": checked.ended,
                            "torn_tail": checked.torn_tail,
                            "witness_problems": problems,
                        },
                        indent=2,
                    ),
                    file=stdout,
                )
                if problems:
                    sys.exit(1)
                return
            if args.command == "connector":
                try:
                    template = load_template(args.template) if args.template else None
                    server = ConnectorServer(
                        command=args.server_command,
                        args=tuple(args.server_args),
                        url=args.url,
                        env=tuple(args.env),
                    )
                    profile = anyio.run(
                        lambda: snapshot_connector(args.name, server, template=template)
                    )
                    _write_text_new(args.output, profile.model_dump_json(indent=2) + "\n")
                except (LookupError, ValueError, OSError) as exc:
                    parser.error(f"Cannot snapshot: {type(exc).__name__}: {exc}")
                unclassified = sorted(n for n, t in profile.tools.items() if t.action is None)
                disagreements = sorted(
                    n
                    for n, t in profile.tools.items()
                    if t.server_hints and t.server_hints.disagrees_with(t.action)
                )
                print(
                    json.dumps(
                        {
                            "profile": str(args.output),
                            "tools": len(profile.tools),
                            "unclassified": unclassified,
                            "server_hints_disagree": disagreements,
                        },
                        indent=2,
                    ),
                    file=stdout,
                )
                return
            if args.command == "release":
                try:
                    policy = ReleasePolicy.model_validate_json(args.policy.read_text())
                    released = release_lab_log(
                        args.lab_log, policy, args.tier, args.output, args.key
                    )
                except (ValueError, OSError) as exc:
                    parser.error(f"Cannot release: {type(exc).__name__}: {exc}")
                print(
                    json.dumps(
                        {
                            "release": str(args.output),
                            "tier": released.tier,
                            "key": str(args.key),
                            "source_sha256": released.source_sha256,
                        }
                    ),
                    file=stdout,
                )
                return
            if args.command == "verify-release":
                try:
                    problems = verify_release(args.release_file, args.lab_log, args.key)
                    # The full lab log must also match its own hash chains.
                    if json.loads(args.lab_log.read_text()).get("kind") == "session":
                        read_session_log(args.lab_log)
                    else:
                        read_lab_logs(args.lab_log)
                except (ValueError, OSError) as exc:
                    parser.error(f"Cannot verify: {type(exc).__name__}: {exc}")
                print(json.dumps({"faithful": not problems, "problems": problems}), file=stdout)
                if problems:
                    sys.exit(1)
                return
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


def _write_text_new(path: Path, text: str) -> None:
    """Write a new private file; never overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(text)


def _serve(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Serve one Lab session over MCP stdio and report the lab log on stderr."""
    old_umask = os.umask(0o077)
    try:
        try:
            lab = (
                connector_lab(args.lab_dir, profile=args.connector)
                if args.connector
                else resolve("lab", args.lab, directory=args.lab_dir)
            )
            rules = (
                ActionRules.model_validate_json(args.rules.read_text())
                if args.rules
                else DEFAULT_RULES
            )
            approvers: list[Approver] = []
            if args.approvals:
                approved = TypeAdapter(list[ApprovedAction]).validate_json(
                    args.approvals.read_text()
                )
                approvers.append(approved_actions(approved))
            if args.leases:
                leases = TypeAdapter(list[Lease]).validate_json(args.leases.read_text())
                approvers.append(leased_actions(leases))
            approver = first_approval(*approvers) if approvers else None
            live = list(LIVE_MONITORS)
            stop_on = set(args.stop_on)
            if args.refusal_limit is not None:
                live.append(repeated_refusals(args.refusal_limit))
                stop_on.add("repeated-refusals")
            # Without a control channel nobody could answer, so held actions just refuse.
            queue = ApprovalQueue(args.approval_timeout) if args.control_socket else None
            if args.lab_log.exists():
                raise FileExistsError(f"{args.lab_log} already exists")
            journal_path = args.journal or args.lab_log.with_suffix(".journal.jsonl")
            journal = Journal(
                journal_path,
                witness=witness_file(args.witness_file) if args.witness_file else None,
            )
        except (LookupError, ValueError, OSError) as exc:
            parser.error(f"Cannot serve: {type(exc).__name__}: {exc}")
        session = LabSession(
            lab,
            rules,
            approver=approver,
            stop_file=args.stop_file,
            queue=queue,
            journal=journal,
            live_monitors=live,
            stop_on=frozenset(stop_on),
        )
        log = anyio.run(serve_over_stdio, session, args.lab_log, args.control_socket)
        (record,) = log.samples.values()
        print(
            json.dumps(
                {
                    "lab_log": str(args.lab_log),
                    "actions": len(record.actions),
                    "refused": sum(1 for a in record.actions if a.status == "refused"),
                    "flags": len(log.flags),
                    "stopped": log.stopped,
                    "journal": str(journal_path),
                }
            ),
            file=sys.stderr,
        )
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
