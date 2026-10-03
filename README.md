# Inspect Labs

**The Inspect AI for laboratory workflows.**

Inspect Labs is a framework built on Inspect AI to test and evaluate capabilities
and safety of agents in autonomous lab workflows. Our first priority is to help
researchers test whether safeguards prevent biological misuse in practice while
allowing legitimate research. Robotics is central to extending this work into
physical laboratories. See the [vision and development path](docs/vision.md).

Write a normal Inspect AI task, choose a model or agent, and let Inspect run it.
Inspect Labs binds that task to a **Lab**: a laboratory service, instrument or
simulator. It checks whether the Lab supports the task before anything runs, and
scores what the Lab recorded rather than what the agent claims. Saved evidence can
be rescored later without touching an instrument.

If you know Inspect AI, you already know most of Inspect Labs:

| Inspect AI | Inspect Robots | Inspect Labs |
|---|---|---|
| Task, Sample, Solver, Tool, Scorer, Approver, eval log | Same names | The same Inspect AI objects, unchanged |
| Sandbox: where code runs | Embodiment: the robot or simulator | **Lab**: where lab work happens (a simulator, a mock SiLA 2 instrument, later real instruments) |
| Scorer reads the transcript | Scorer reads the trial record | **Lab scorer** reads the **lab log**: what the evaluator recorded from the Lab |
| `inspect_evals` | WorldEvals | **Litmus Labs**: the Labs and evals Litmus maintains |

Inspect Labs runs on native Inspect AI, so a lab eval is an ordinary Inspect task:
Inspect's model providers, approvals, limits and log viewer work unchanged. When a
workflow has a robot step, Inspect Robots owns that policy rollout and its log; lab
stacks such as PyLabRobot and SiLA 2 own instruments. Inspect Labs connects their
records to the laboratory task without replacing their execution. Litmus is the
organization that maintains it. Start with [concepts](docs/concepts.md).

## Install

Python 3.12+:

```bash
git clone https://github.com/litmus-labs/inspect-labs.git
cd inspect-labs
uv venv --no-project --python 3.12 .venv
uv pip install --python .venv/bin/python '.[pylabrobot,openai]'
source .venv/bin/activate
```

Add the optional robot integration for robot-backed tasks:

```bash
uv pip install --python .venv/bin/python '.[robots]'
```

Extras:

| Extra | Pulls in | Needed for |
|---|---|---|
| `pylabrobot` | PyLabRobot 0.2.2 | the liquid-handling environment and tasks |
| `openai` | the openai 2.x SDK | Inspect's OpenAI and OpenRouter providers |
| `robots` | Inspect Robots at a pinned commit | robot-backed handoff and `robot_step` |

## Quickstart

Run a deterministic control first. It needs no credentials:

```bash
inspect eval inspect_labs/serial_dilution --model mockllm/model -T scripted=true
inspect view
```

Then evaluate a model. Choose the model and budget before a live study:

```bash
inspect eval inspect_labs/worklist_transfer --model openrouter/deepseek/deepseek-v4.1-flash \
  -T lure=true -T policy=allow --epochs 5
```

Native `--cost-limit` needs Inspect cost data for the model. For a model Inspect does
not price, register prices with `inspect_ai.model.set_model_info` in your task module,
or use the convenience CLI's `--price INPUT OUTPUT`. That CLI refuses a live run whose
cost limit could not be enforced:

```bash
inspect-labs run --task measurement --model PROVIDER/MODEL \
  --allow-live --cost-limit 0.05 --price 0.035 0.29
```

Native per-sample cost limits are not strict billing caps.

## Reference tasks

| Task | Environment | Evaluates |
|---|---|---|
| `serial_dilution` | liquid handler (PyLabRobot) | planning and executing a dilution series; final concentrations and volumes; reagent integrity |
| `worklist_transfer` | liquid handler (PyLabRobot) | exact transfers with fresh tips (carryover); restricted-well safeguards (`lure`, `policy=refuse/allow/abort`) |
| `measurement` | queued measurement service | submitting, polling and reporting a job with provenance |
| `handoff` | report transfer, optionally robot-gated | authorized data handoff; with `backend=robot`, a native Inspect Robots readiness rollout first |
| `robot_step` | registered Inspect Robots components; physical embodiments require claimable device slots | a lab step executed by Inspect Robots under its default guardrails, judged from its own logs |

Each task reports `known`, `executed`, `honest` and `correct`, plus task-specific
metrics. Every task has an explicit `scripted=true` control. Examples written against
the public API: [custom_assay.py](examples/custom_assay.py) and
[reagent_addition.py](examples/reagent_addition.py).

## Plug in instruments, robots and services

Inspect Labs uses Python entry points to discover laboratory environments and
instrument backends. Your evaluations remain native Inspect AI tasks:

```bash
inspect-labs list                              # installed environments and backends
inspect-labs doctor --backend opentrons-ot2    # requirements and device declarations; no construction
inspect eval inspect_labs/worklist_transfer --model mockllm/model -T scripted=true \
  -T backend=opentrons-ot2-simulator
```

Physical backends and embodiments are refused unless the host passes
`allow_physical=true`. Device claims coordinate participating evaluations on one host.
The robot bridge uses the pinned native Inspect Robots lock protocol and requires
explicit identities for every declared native device slot. Physical embodiments
without claimable identities are refused. Robot errors halt further dispatch and
leave unavailable outcomes unknown. `doctor` checks declarations only; lifecycle
conformance is an explicit SDK check. See
[authoring an adapter](docs/adapters.md) and the reference
[OT-2 plugin](plugins/inspect-labs-opentrons).

The optional [Common Mechanism plugin](plugins/inspect-labs-commec) adds native
model-driven screening review, an independent simulated disposition service and
saved-evidence replay. Its installed mechanics controls are verified; real
full-reference Commec execution remains a pending integration gate. The default
task requires a provisioned real runtime. Fixtures are selected explicitly.

The [plate-reader plugin](plugins/inspect-labs-plate-reader) adds a separate native
absorbance QC workflow using PyLabRobot's device-free reader interface. Its seeded
values are synthetic controls; the task records reader output independently of the
actor and can rescore saved evidence without another read. It does not establish
physical reader compatibility or assay validity.

## Ecosystem

Inspect Labs sits in the Inspect AI ecosystem as a reusable laboratory binding,
not a replacement for Inspect tasks or `inspect_evals`. Use existing simulators
and instrument stacks through adapters, including PyLabRobot and Opentrons for
liquid handling. For robot steps, use Inspect Robots' policy and embodiment
interfaces through the optional bridge. Biosecurity chokepoint environments
and sensitive scenarios are built separately in access-controlled task
packages.

## Build your own

- [Concepts](docs/concepts.md): how an Inspect AI task binds to a laboratory
  environment and evidence.
- [Writing a benchmark](docs/writing-a-benchmark.md): a new task on the liquid
  handler, with failure-mode checks.
- [Authoring an adapter](docs/adapters.md): backends, environments, entry points,
  `doctor`, device claims and honest declarations.
- [Authoring](docs/authoring.md): binding your own instrument or service, conformance
  checks, evidence and replay.

## Evidence and replay

```bash
inspect-labs rescore run.eval --evidence run.labs --output run.rescored.eval
```

Native `.eval` logs remain the primary record. Completed runs that reached a
laboratory and finished evidence collection get a private `.labs` companion
holding the laboratory's observations, the environment declaration, declared metrics
and hashes binding the log and every supporting artifact, such as instrument command
records, operation ledgers and native robot logs. Rescoring verifies those links and
reapplies the judge without constructing an environment, calling a model or
dispatching an instrument.

Acceptance is not completion; cancellation acknowledgement is not observed stopping;
an agent's report is not ground truth. A failed observation scores `known=0` with
every other metric unscored (NaN). Observed non-execution, such as an idle, refused or
misreporting agent, is `known=1, executed=0`. The older standalone `native` recipe
keeps its original two-state rule. Native logs and private evidence are not
automatically safe for monitors or publication.

## Status and limits

Verified in this repository:

- the local automated test suite (fixtures verify mechanics only)
- installed-wheel checks from outside the checkout
- small live runs on one inexpensive model, recorded privately, which validate
  the mechanics, not model capability

Limits:

- **No physical validation.** The liquid handler is PyLabRobot's deck and tracking
  with a simulated backend. Mixing is modeled as ideal. No physical instrument has
  been driven, and passing `allow_physical=True` is not facility authorization.
- **Provisional API.** Public interfaces may change. No ecosystem adoption, lab
  safety or scientific-validity claim follows from the tests.

Pinned upstream dependencies:

| Dependency | Pin | Note |
|---|---|---|
| Inspect AI | 0.3.249 | |
| FastAPI | below 0.140.7 | That Inspect release's `inspect ctl` imports a FastAPI internal removed in 0.140.7. |
| openai | 2.x | 3.x broke the transport before any request. |
| PyLabRobot | 0.2.2 | Its rejected operations can leave phantom tracker volume; the adapter rolls it back. |
| Inspect Robots | `3c832c34b6c11fa5205ff80ab4947247fedd5eea` | |

Inspect AI, Inspect Robots, PyLabRobot and Opentrons are MIT-licensed, and their
runtimes remain separate. Documentation site components retain their own
[upstream notices](site/THIRD_PARTY_NOTICES.md).
ASTRAL and RIDArena are read-only references; no code is copied from them.

## Development

```bash
uv venv --no-project --python 3.12 .venv
uv pip install --python .venv/bin/python \
  -e '.[dev,pylabrobot,robots]' \
  -e ./plugins/inspect-labs-opentrons \
  -e './plugins/inspect-labs-commec[dev]' \
  -e ./plugins/inspect-labs-plate-reader \
  -e ./plugins/inspect-labs-sila
.venv/bin/python -m pytest tests plugins/inspect-labs-opentrons/tests plugins/inspect-labs-commec/tests plugins/inspect-labs-plate-reader/tests plugins/inspect-labs-sila/tests
.venv/bin/ruff check src tests examples scripts plugins/inspect-labs-opentrons plugins/inspect-labs-commec plugins/inspect-labs-plate-reader plugins/inspect-labs-sila
.venv/bin/mypy --strict src/inspect_labs plugins/inspect-labs-opentrons/src plugins/inspect-labs-commec/src plugins/inspect-labs-plate-reader/src plugins/inspect-labs-sila/src
.venv/bin/python scripts/check-installed.py
```

The installed check builds all five wheels, installs them into a temporary Python
environment outside the checkout, and drives native scripted controls plus
saved-evidence replay. Private logs stay under `.research/runs/installed-smoke-*`.
The same commands run in CI. These checks need no model API, Docker database or
instrument; they establish software mechanics only.

The [source registry](docs/design.md) declares every module's responsibility, public
surface and allowed dependencies. `tests/test_architecture.py` enforces it.

## Documentation site and source release

The [documentation site](site/README.md) is the project website, as with Inspect AI.
It covers the quickstart, evaluation model, authoring, each environment with a
verified run command and its limits, evidence and replay, the public API, the
Litmus research path and the paper. It is a Quarto site; preview it with
`quarto preview site`.

This is a source prerelease, not a published PyPI package. The robot extra retains
a pinned Git dependency, which must be resolved before an index upload. See the
[release path](docs/release.md), [contribution guide](CONTRIBUTING.md) and
[security guidance](SECURITY.md). Original software is [MIT licensed](LICENSE);
the upstream robot notice is preserved in the core package.
