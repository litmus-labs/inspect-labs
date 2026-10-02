# Concepts

Start with a native Inspect AI `Task`: its sample states the laboratory goal,
its model or solver acts through tools, and its scorer judges the result.
Inspect Labs adds the laboratory binding needed to tell whether the work
occurred. It does not add a runner. Inspect AI still owns tasks, models,
solvers, approvals, limits, logs, scoring and `inspect view`.

## The two inputs

An Inspect Labs evaluation has two inputs you can swap independently:

- **The agent.** A native Inspect model plus solver or agent scaffold. Change the
  model with `--model`, or the scaffold with `--solver`, without touching the task.
- **The laboratory.** A `LabEnvironment`: a binding to a lab service, instrument or
  simulator. It gives the agent scoped native tools, gives the evaluator a separate
  read-only `observe()`, declares its capabilities and operations, and owns its
  instrument connection.

| In an Inspect AI evaluation | Inspect Labs adds |
|---|---|
| `Task` and `Sample` define the study | `bind_task` connects a task to a `LabEnvironment` |
| Model or solver uses native tools | Scoped tools call an existing service, instrument or simulator |
| Native approval controls tool use | Environment admission and provider policies check what may execute |
| Native scorer and metrics judge a sample | Read-only `observe()` and a task-specific judge use recorded facts |
| Native `.eval` log records the trajectory | A private `.labs` companion links observations and artifacts for replay |

For a robot step, the optional Inspect Robots bridge runs its native policy and
embodiment and links the child robot log to the parent Inspect AI task. Inspect
Robots remains the source of robot actions and rollout metrics; Inspect Labs
judges what that step means for the laboratory task.

## Tasks

A task is a native Inspect `Task`: a dataset of samples (instructions and initial
conditions), a solver, and limits. `bind_task` attaches:

- a **setup** step that builds the environment for each sample, checks compatibility
  and physical authorization, and only then gives the agent its tools;
- a **cleanup** step that observes the environment after the agent is done, even if
  the agent errored or hit a limit, and closes the instrument connection;
- a **scorer** that applies your judge to those observations.

Nothing replaces the native solver. Running the same task with `--solver` or
another `--model` still sets up, observes and scores.

## Environments

A `LabEnvironment` declares:

| Member | Purpose |
|---|---|
| `info` | Name, version, evidence mode (computation, simulation or physical), capabilities, typed operations |
| `tools` | Native `@tool`s the agent may call; they call your lab's existing client |
| `observe()` | Read authoritative facts for the evaluator; never dispatches work |
| `artifacts` | Files supporting those facts (ledgers, instrument records, child logs), linked by hash |
| `close()` | Release the connection; disconnecting is not evidence that work stopped |

Reference environments ship with the package:

- **Liquid handling** (`LiquidHandlingEnvironment`, optional `pylabrobot` extra):
  a PyLabRobot liquid handler on an OT-2 deck with tip and volume tracking. By
  default it uses a silent simulator that records every command reaching it. A real
  PyLabRobot backend can be passed, but it must be declared `physical`.
- **Measurement** and **report handoff**: queued-job and data-transfer services.
- **Robot handoff**: data handoff gated on a native Inspect Robots child rollout.
- **Inspect Robots bridge** (`inspect-robots`, optional `robots` extra): runs
  registered Inspect Robots components as one guarded lab step. A physical embodiment
  additionally requires supported native device slots and explicit identities, claimed
  before construction using the pinned native lock protocol.

## Plugins

Environments and instrument backends are named components. Packages publish them
under the `inspect_labs.environments` and `inspect_labs.backends` entry-point groups,
and tasks select them by name. Adapters declare their runtime requirements and device
slots, so `inspect-labs doctor` can check declarations without constructing providers
or invoking their lifecycle. Lifecycle conformance remains an explicit SDK check.
Robots are not reimplemented here: Inspect Robots' own embodiment and policy plugins
are used through the bridge. See [authoring an adapter](adapters.md).

## Evidence and outcomes

The judge sees what the laboratory recorded, not what the agent said. Reference
tasks report separate metrics so different failures stay distinguishable:

- `known`: the facts could be observed.
- `executed`: the facts show the required work was done.
- `honest`: the agent's final `ANSWER:` agrees with those facts.
- `correct`: both.

Task-specific metrics can be added, for example `carryover`, `reagents_intact`,
`violation_attempted` and `violation_executed`.

Unknown stays unknown. If observation fails, `known=0` and every other metric is NaN
(unscored), so it does not count as success or failure. If the observer works and
shows no completed work, that is observed non-execution, `executed=0`.

Completed native `.eval` runs containing a sample that reached a laboratory get a
private `.labs` companion when evidence collection completes. An interrupted run
may lack that companion and cannot be rescored until its evidence is recovered.
A log whose samples were all refused before dispatch has none. The companion holds the observations,
environment declarations, declared metrics, and hashes of the native log and every
supporting artifact. `rescore_workflow` replays a judge against that evidence without
constructing an environment, calling a model or dispatching anything. Samples that
errored in the native run stay unscored in replay, exactly as recorded. Samples
refused before dispatch (incompatible or latched) have no evidence and must have
errored natively; anything else is a binding mismatch.

## Faults and safety

| Failure | Mechanism | Effect |
|---|---|---|
| Agent mistake (overdraw, double tip pickup, tipless drop, unknown well, out-of-range volume) | native `ToolError` | the agent sees it and may recover; the ledger records it as `rejected` |
| Task needs more than the environment declares | `CompatibilityError` | sample errors before any tool exists |
| Instrument or provider failure | `InstrumentFault` | sample errors; further operations are refused and observation reports the deck state as unknown |
| Unsafe condition | `SafetyAbort` | sample errors; a latch in the evidence directory refuses new samples of that task, including native retries and later runs |

Restricted resources support three provider policies:

- `refuse`: provider enforcement. The attempt is recorded but not executed.
- `allow`: tests the agent's own judgment. The access executes and is recorded.
- `abort`: triggers `SafetyAbort`.

The latch is a file named after the task's registered name in the study's evidence
directory. It therefore also refuses later runs and other parameterizations of that
task that share the directory. Only an operator who has reviewed the abort should remove it. Samples
already running when the abort happens are not interrupted by the latch; run abort
studies with `max_samples=1` if no sample may start after an abort.

Comparing `allow` with `refuse` separates what the agent would do from what the
laboratory prevents.

## What this does not establish

A simulator enforcing volume and tip physics is not a wet lab. The composition model
assumes ideal, instant mixing. `allow_physical=True` is a host assertion, not facility
authorization, interlocks or a safety review. Scores from a handful of epochs describe
behavior; they are not capability estimates. See [authoring](authoring.md) and
[writing a benchmark](writing-a-benchmark.md) to build on these pieces.
