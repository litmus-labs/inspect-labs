# Author a laboratory evaluation

Inspect Labs provides bindings, reference environments and outcome recipes on top
of Inspect AI. A laboratory evaluation is still an `inspect_ai.Task`. Its dataset,
solver or agent scaffold, model configuration, approval policy, limits, metrics,
logs and viewer remain native Inspect objects. Litmus Labs environments supply
scoped native tools and a separate read-only observation channel.

Start with the Inspect AI task and its question; add a lab binding only for
actions and observations that the task needs. Robot-specific components enter
through Inspect Robots when the task includes a robot step.

## Add a task without changing the framework

[custom_assay.py](../examples/custom_assay.py) changes the resource, input values,
request identity and native approval policy. Copy it into your own project and run:

```bash
inspect eval custom_assay.py@custom_assay --model PROVIDER/MODEL \
  --log-dir .research/native -T evidence_dir=.research/provider-evidence
```

Use a model and budget approved for your study. To test the plumbing first, explicitly
select its scripted control with `--model mockllm/model -T scripted=true`. Add
`-T reject=true` for the native approval control. A scripted control is not a model
capability measurement.

`bind_task` attaches native setup, an outcome scorer and cleanup to an unscored Task.
It leaves the native solver intact. Replacing the solver with native `eval(...,
solver=...)` still runs tool preparation and observation collection. References live
in the task's trusted closure and private evidence, not in actor-facing sample targets.

## Bind an existing laboratory provider

Implement the structural `LabEnvironment` protocol; inheritance is unnecessary:

| Surface | Responsibility |
|---|---|
| `info: EnvironmentInfo` | Name/version, computation/simulation/physical mode, explicit capabilities |
| `tools: list[Tool]` | Native `@tool` functions calling your existing scoped client |
| `observe()` | Read authoritative provider/sensor facts into a task-owned JSON payload, or return `None` |
| `artifacts: list[Path]` | Existing provider records or native child artifacts supporting those facts |
| `close()` | Release the client; do not equate disconnection with stopping work |

Pass a trusted factory to `bind_task`. It receives native `TaskState`, including the
sample UUID. Use that identity to scope provider requests and observations. Provider
construction must not dispatch work. Capability preflight and physical authorization
are checked before actor tools are installed. `allow_physical=True` is an explicit
host assertion; it does not supply facility authorization, interlocks or validation.

Your existing service or device stack continues to own jobs, durable state,
idempotency, access control, exclusive resources and operational cancellation.
Native Inspect approvals are candidate controls, not substitutes for provider
enforcement. Constructors and observers must not secretly submit jobs. Credentials
belong to the trusted client, never registered task/scorer arguments or tool results.

The framework does not normalize every provider into a universal job schema.
Task-owned Pydantic records can retain native fields. The reference measurement
environment demonstrates request/job linkage and unit/input validation; the handoff
environment demonstrates independent content and destination verification.

## Check a provider binding

Run the conformance check in your provider's test suite before evaluating with it:

```python
import anyio
from inspect_labs import check_environment

report = anyio.run(check_environment, make_binding, count_accepted_jobs)
assert report.passed, report.violations
```

`make_binding` builds one fresh binding. `count_accepted_jobs` must read the provider's
own record of accepted work, not the binding. The check never calls actor tools. It
reports:

- construction, observation or close that dispatches work
- provider calls that raise, reported as violations instead of crashing the check
- no tools, duplicate names, or tools without a model-facing docstring
- observations that fail or time out, are not strict JSON, or change between two
  reads with no actor action. Pass `volatile={"read_at"}` for keys that legitimately
  change, such as timestamps or live sensor values
- linked artifacts that do not exist

Passing is evidence about these clauses only. It does not show that declared
capabilities are truthful, that the provider enforces access control, or anything
about physical behavior.

## Define an outcome

A pure judge accepts `(report: str, evidence: LabEvidence)` and returns numeric
metrics such as `{"known": 1, "correct": 0}`. `known` reports observation coverage;
`correct` reports task correctness among observed outcomes. Additional metrics remain
task-specific.

Distinguish three states. If the observer fails, times out or returns no payload,
the outcome is unknown: the scorer records `{"known": 0, "correct": NaN}`. Native
Inspect treats NaN as unscored; persisted logs show it as `null`. If the observer
reads the provider successfully but finds no qualifying completed work (the actor
never acted, a call was rejected, work is still queued, or it used the wrong
identity), that is an observed non-completion, `{"known": 1, "correct": 0}`. Do not
report it as unknown, or idle and fabricating actors drop out of `correct`. This
depends on the provider's records for the sample being complete; a real adapter must
state that guarantee or return unknown.

Native metrics and epoch reduction need the same keys on every sample. Declare every
key your judge can return with `bind_task(..., metrics=("known", "correct", ...))`,
and pass the same tuple to `rescore_workflow`. The scorer fills unknown outcomes and
omitted keys with NaN. A judge that returns an undeclared key fails that sample
clearly, instead of breaking reduction for the whole run.

Contradictory provider records are unknown too. In the reference environments, a
transfer receipt without delivered data raises `DeliveryConflict`, and a robot
readiness rollout that fails or lacks native records is recorded as
`robot_rollout: "failed"` and scored unknown. Neither is counted as actor failure.
An actor that never prepares the station is `not_attempted`, which is a known
non-completion.

Validate the payload schema and its provenance before judging correctness. Empty
objects are not evidence. Required child artifacts must be present in the link set;
validating only links that happen to be listed permits omission errors. A score
compares separately collected facts with the task reference, not an actor report,
approval verdict or a provider's acceptance receipt. The reference judges also
require the actor's report to agree with those facts. A delivered handoff that the
actor reports as failed or unknown is not correct.

Observation collection runs at native sample scoring, including early exits, with
an explicit timeout (30 seconds by default, configurable via `observation_timeout`).
Observer failures remain unknown with a recorded error category. Cleanup preserves
observations on actor failures and closes provider clients. Failed native runs retain
their native error status and raw records.

## Evidence and replay

Native `.eval` files remain the primary agent records. A `.labs` companion contains
JSON with private per-sample observations, environment versions and hashes binding
the parent and child artifacts. The non-`.json` extension prevents native Inspect
log discovery from mistaking it for an Inspect JSON log. Per-sample `.lab-sample`
files preserve collected evidence before the run is sealed.

Use private directories outside public datasets and source control. CLI-created
artifacts are owner-readable; native CLI/SDK callers own their log directory's
permissions. Hashes detect mismatched artifacts; they do not authenticate a hostile
evaluator or make native logs safe for monitors/publication. Keep child files with
the evidence. Current links use absolute paths; arbitrary relocation requires
explicit rebinding and is not automatically supported.

For a custom task, supply its trusted lab scorer explicitly:

```python
from pathlib import Path
from inspect_labs import rescore_workflow
from inspect_labs.tasks import measurement_outcome

rescore_workflow(Path("run.eval"), Path("run.labs"),
                 Path("run.rescored.eval"), measurement_outcome)
```

Replay constructs no environment and dispatches no provider, robot or model actions.
It verifies parent and listed child hashes, task/sample identity, and task-owned
required provenance. The original files remain unchanged. Evidence never names an
arbitrary Python module for automatic import; the caller selects the scorer.

Native automatic retries can repeat side effects. A local admission marker keyed
by Inspect's stable task/sample/epoch identity rejects another attempt before
provider construction, including `eval_set` retries. It is not provider recovery or
a scheduler. Reconcile previous evidence and outstanding jobs before authorizing a
new native evaluation. Do not delete these private markers to force retries.

Native Inspect emits no task-end event for an errored task execution, and an
`eval_set` run end lists only each task's final retry. The binding therefore seals
companions at run end by locating each attempt's own native log by `eval_id`, so
evidence is sealed with the native log that contains the dispatching sample. That
is usually the attempt that dispatched. When a retry reuses an already-completed
sample, it can be the retry's log. `eval_set`'s default
`retry_cleanup=True` deletes older failed native logs, including that attempt's
log; its `.labs` and `.lab-sample` records survive, but replay then fails because
the parent log is gone. Pass `retry_cleanup=False` to keep replayable evidence.

Two cases are not sealed automatically; their evidence remains only in `.lab-sample`
records and needs manual reconciliation. The first is a run-level exception, where
native run end carries no logs (for example with `debug_errors=True`). The second is
a non-local log directory (`s3://`, `memory://`): companions are written only beside
local native logs. Hook failures appear as native warnings, not errors.

The admission marker is written before provider construction and capability
preflight. A failed factory or incompatible provider therefore also blocks automatic
retry of that sample, even though nothing was dispatched. Task arguments are
recorded in native logs (`eval.task_args`). Do not pass private references, such as
the reference handoff's `content`, as task arguments in studies where logs reach
monitors.

## Add a robot step to an Inspect AI workflow

The registered `inspect_labs/handoff` task with `backend=robot` gives the model a
`prepare_station` tool. That tool invokes one bounded native Inspect Robots rollout
and links its native JSON/action records. A privileged observation reads final
effector and target coordinates; readiness is computed from those coordinates.
The model then performs an authorized report transfer and can inspect its delivery.

The shared digital/robot predicate is **data handoff with preserved identity and
authorized destination**. The robot variant adds a readiness precondition. CubePick
does not model grasping, transport or custody of a physical container. Integrating a
real lab controller means implementing a provider binding for that controller's
supported interface, not replacing it with this simulator.
