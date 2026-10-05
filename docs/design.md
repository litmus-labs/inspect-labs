# Source registry

The [vision and development path](vision.md) specifies the research mission,
native framework ownership, planned studies and evidence required for future
milestones. This registry describes the implemented software surface.

Inspect Labs binds laboratory providers to native Inspect execution. The reference providers are fixtures,
not production lab controllers.
No stable public API, physical integration or scientific validity is claimed.

| File | Responsibility | Public surface | Dependencies | Verification |
|---|---|---|---|---|
| [Package](../src/inspect_labs/__init__.py) | Public SDK surface | connect_lab, ActionRules, Rule, DEFAULT_RULES, DEFAULT_MONITORS, lab_scorer, evidence_scorer (earlier name), rescore, LabInfo, Lab, LabLog, LabLog, check_lab, LabCheckReport | actions, bindings, conformance, monitors | Architecture and external-author tests |
| [Litmus Labs fixture](../src/inspect_labs/litmus_labs.py) | Bounded synthetic requests and independent observations | Request, Observation, Cancellation, FixtureService, assess_report and specific errors | Pydantic, standard library | [Boundary tests](../tests/test_litmus_labs.py) |
| [Native measurement](../src/inspect_labs/native.py) | Bind actor tools, native approvals and independent outcome scoring | measurement_task, submit_measurement, read_measurement, fixture_script, observed_result | Inspect AI, Pydantic, litmus_labs | [Native tests](../tests/test_native.py) |
| [Evidence](../src/inspect_labs/evidence.py) | Bind private provider snapshots to native logs and rescore without execution | EvidenceBundle, persist_evidence, rescore_evidence | Inspect AI, Pydantic, native, litmus_labs, standard library | [Persistence tests](../tests/test_evidence.py) |
| [CLI](../src/inspect_labs/cli.py) | Installed entry point and explicit live-call admission | main; run, rescore, robot-mock commands | Inspect AI, bindings, tasks, liquid_tasks, evidence, native, litmus_labs, optional robot_mock | [CLI tests](../tests/test_cli.py) |
| [Robot baseline](../src/inspect_labs/robot_mock.py) | Explicit upstream scripted CubePick baseline | run_mock | Optional pinned Inspect Robots, standard library | [Robot persistence test](../tests/test_robot_mock.py) |
| [Bindings](../src/inspect_labs/bindings.py) | Reusable native setup, private observation, evidence linking and pure replay | LabInfo, Lab, LabLog, LabLog, connect_lab, lab_scorer, evidence_scorer, rescore, lab_log_hash, run_time_hash, read_lab_logs, replay_rules, attach_late_result, LateResult, monitor_saved_run, record_lab_log, LabSessionLog, read_session_log | Inspect AI, Pydantic, anyio, actions, errors, gateway, monitors, spec, standard library | Framework boundary and external-author tests |
| [Conformance](../src/inspect_labs/conformance.py) | Explicit provider lifecycle checks; declaration-only doctor | LabCheckReport, check_lab, diagnose | Inspect AI, Pydantic, anyio, bindings, plugins, standard library | Conformance tests |
| [Errors](../src/inspect_labs/errors.py) | Fault taxonomy separating actor, instrument, compatibility and safety failures | LabError, CompatibilityError, InstrumentFault, SafetyAbort | None | Liquid and framework tests |
| [Spec](../src/inspect_labs/spec.py) | Typed operation declarations, each operation's action type, and the pre-dispatch compatibility check | ActionType, ParameterSpec, OperationSpec, Requirements, compatibility_problems | Pydantic | [Liquid tests](../tests/test_liquid.py) |
| [Actions](../src/inspect_labs/actions.py) | Pure checks before each agent action: action description, ordered versioned rules, decisions and action records | Action, Decision, Rule, ActionRules, ActionRecord, DEFAULT_RULES, Outcome, ReplayedDecision, replay_decisions | Pydantic, spec | [Action](../tests/test_actions.py) and [lab log](../tests/test_lab_log.py) tests |
| [Monitors](../src/inspect_labs/monitors.py) | Pure monitors that flag problems in a sample's lab log, live or offline | MonitorInput, Flag, Monitor, DEFAULT_MONITORS, LIVE_MONITORS, run_monitors, irreversible_without_approval, refused_actions, unobserved_outcome, report_contradicts_lab_log, repeated_refusals | Pydantic, actions | [Monitor tests](../tests/test_monitors.py) |
| [Gateway](../src/inspect_labs/gateway.py) | One place where each agent action is checked (rules, then domain checks), approved, journaled, recorded and can be stopped; used by evaluations and when a Lab is served | Gateway, ActionRefused, Approver, Approval, Check, ApprovedAction, approved_actions, Lease, leased_actions, first_approval | Pydantic, actions, journal, spec | [Gateway](../tests/test_gateway_checks.py), [action](../tests/test_actions.py) and [secure lab](../tests/test_secure_lab.py) tests |
| [Journal](../src/inspect_labs/journal.py) | Append-only, hash-chained session journal flushed per step, with an optional outside witness | Journal, JournalEntry, JournalCheck, read_journal, witness_file, check_witness | Pydantic | [Journal and operator tests](../tests/test_operators.py) |
| [Operators](../src/inspect_labs/operators.py) | Live approval queue and a private local control channel for operators | ApprovalQueue, PendingAction, Control, ControlRequest, serve_control, send_control | anyio, Pydantic, actions, gateway | [Journal and operator tests](../tests/test_operators.py) |
| [Release](../src/inspect_labs/release.py) | Release a lab log at one tier, withholding higher-tier fields behind keyed commitments, and check a release against the full lab log | ReleasePolicy, FieldRule, Release, Withheld, release_lab_log, verify_release, STRUCTURAL | Pydantic | [Release tests](../tests/test_release.py) |
| [Connectors](../src/inspect_labs/connectors.py) | Put an MCP connector, such as a biology database or lab software, behind the gateway: pinned tool definitions, person-reviewed action types, sequence screening before sending, recorded results | ConnectorLab, ConnectorProfile, ConnectorServer, ConnectorTool, Classification, ScreenVerdict, Screener, snapshot_connector, definition_digest, load_template, connector_lab, TEMPLATES | Inspect AI, Pydantic, MCP (optional `serve` extra), actions, bindings, spec | [Connector tests](../tests/test_connectors.py) |
| [Serve](../src/inspect_labs/serve.py) | Serve a Lab to any agent over MCP through the gateway; journal, live monitors and tripwires, operator approvals and stop; session lab log | LabSession, mcp_server, serve_over_stdio | Inspect AI, Pydantic, MCP (optional `serve` extra), anyio, actions, bindings, gateway, journal, monitors, operators | [Serve](../tests/test_serve.py) and [operator](../tests/test_operators.py) tests |
| [Liquid facts](../src/inspect_labs/liquid.py) | Deck declarations, observed liquid facts, ideal-mixing composition and carryover derivation; no PyLabRobot | DeckLayout, Labware, WellContent, LiquidFacts, CompositionModel, carryover, expected_deck, deck_deviations, well_deviation, wells_of | Pydantic | [Liquid tests](../tests/test_liquid.py) |
| [Liquid handling](../src/inspect_labs/liquid_handling.py) | PyLabRobot binding: native pipetting tools, silent recording simulator, restricted-well policy, fault mapping | LiquidHandlingEnvironment, SimulatedBackend | Inspect AI, Pydantic, anyio, optional pinned PyLabRobot, bindings, errors, liquid, spec | [Liquid tests](../tests/test_liquid.py) |
| [Liquid tasks](../src/inspect_labs/liquid_tasks.py) | Discoverable serial-dilution and worklist workflows with pure judges | serial_dilution, worklist_transfer, worklist_design, WorklistDesign, serial_dilution_outcome, worklist_outcome | Inspect AI, Pydantic, bindings, liquid, spec, tasks, lazy liquid_handling | [Liquid tests](../tests/test_liquid.py) |
| [Secure Autonomous Lab](../src/inspect_labs/secure_lab.py) | Simulated dye worklist with action checks, protocol approval, monitors and scripted adversarial scenarios | secure_autonomous_lab, protocol_approver, SCENARIOS, Scenario | Inspect AI, actions, bindings, gateway, liquid tasks, monitors | [Secure lab tests](../tests/test_secure_lab.py) |
| [Plugins](../src/inspect_labs/plugins.py) | Named Labs and instrument backends; entry-point discovery (`inspect_labs.labs`, legacy `inspect_labs.environments`); declared runtime requirements and device slots | lab, backend, environment (legacy alias), canonical, register, available, factory, resolve, BackendBinding, DeviceSlot, missing_runtime_requirements | Standard library | Plugin tests and `inspect-labs list/doctor` |
| [Devices](../src/inspect_labs/devices.py) | Advisory per-user claims; pinned native robot lock interoperability with fail-closed acquisition | DeviceClaim, DeviceClaim.for_inspect_robots, DeviceBusy | Standard library | Plugin and native-process interoperability tests |
| [Registry](../src/inspect_labs/registry.py) | Native Inspect entry point registering every reference task | None | tasks, liquid_tasks, secure_lab | Native discovery via installed-surface checks |
| [Environments](../src/inspect_labs/environments.py) | Litmus Labs measurement and authorized report handoff | MeasurementEnvironment, HandoffEnvironment | Inspect AI, bindings, litmus_labs, native, standard library | Workflow tests |
| [Tasks](../src/inspect_labs/tasks.py) | Discoverable native model-driven reference tasks and pure outcome recipes | measurement, handoff, measurement_outcome, handoff_outcome | Inspect AI, bindings, environments, optional robot_workflow | Native CLI and framework tests |
| [Robot bridge](../src/inspect_labs/robot_bridge.py) | Registered native robot components as a lab step; explicit physical claims, default guardrails and fault propagation | RobotStepEnvironment, default_guardrails, robot_step_environment | Inspect AI, Inspect Robots, anyio, Pydantic, bindings, devices, errors | Robot bridge and native-process interoperability tests |
| [Robot workflow](../src/inspect_labs/robot_workflow.py) | Bounded native child rollout as a report-handoff readiness step | RobotHandoffEnvironment | Inspect AI, Inspect Robots, anyio, Pydantic, bindings, environments, standard library | Linked robot workflow tests |

Framework acceptance follows the original product contract. A researcher can bind
their own native Task to provider tools and read-only observations, replace its native
solver/approver, and rescore saved evidence without provider construction. Scripted
actors are explicit controls. Public framework types do not replace native Task,
Sample, Tool, Solver, Scorer, Approver or robot embodiment interfaces.

The reference cross-backend predicate is authorized **data** handoff with preserved
content identity. The robot-backed variant additionally requires observed mock
end-effector readiness before the copy. CubePick supplies no grasp/custody semantics;
no physical container-transfer equivalence is claimed.

The fixture additionally exposes `JobRecord` and `FixtureService.records()` for
trusted snapshots of queued, completed and cancelled jobs. Requests are revalidated
at submission, including Pydantic copies and constructed instances.

[Architecture tests](../tests/test_architecture.py) enforce the module inventory,
declared dependency direction and use of public native APIs. Add a registry entry
and explicit dependency decision before growing the package. Upstream owns model
generation, approval dispatch, evaluation loops, log formats, metrics and robot control.

The earlier `native` measurement solver uses a `finally` block to collect provider evidence before
scoring, including completed-state and exception exits. It never advances work in
collection. Native sample UUIDs bind evidence across epochs. Provider snapshots
remain private and are joined with native logs by UUID, job identity and artifact hash.
Raw native errors remain errors after replay. Snapshot hashes detect accidental
mispairing; they do not authenticate a compromised trusted evaluator.

`robot_mock` and the earlier `native`/`evidence` recipes remain as standalone native
baselines. The reusable layer is `bindings`: native setup/cleanup and lifecycle hooks
shared by the measurement, handoff, robot-handoff and external example tasks. That
reuse is demonstrated by the authors only; independent portability (I08) and
non-author usefulness (I10) are not yet evaluated.

The fixture's memory is volatile. Process restart recovery and expiring leases are unsupported.
Actors receive native tool functions, not direct access to provider state.
The trusted evaluator process is not a malicious-code sandbox.

## Common Mechanism plugin

The separately installed `inspect-labs-commec` package adds a native Inspect task
and scoped environment. It executes upstream Commec through native Docker sandboxes;
its simulated disposition service does not submit synthesis orders. The default
actor is native generation. Explicit fixtures and scripted actors are mechanics
controls. Full-reference scanner integration remains unverified until a provisioned
runtime has passed the documented real-run gate.

| File | Responsibility | Public surface | Dependencies | Verification |
|---|---|---|---|---|
| [Exports](../plugins/inspect-labs-commec/src/inspect_labs_commec/__init__.py) | Typed plugin exports | ScreeningResult, interpret_report | report | Plugin architecture tests |
| [Reports](../plugins/inspect-labs-commec/src/inspect_labs_commec/report.py) | Strict native report, process and configuration interpretation | ScreeningResult, interpret_report, interpret_artifacts | Pydantic, PyYAML, standard library | Untrusted-input and replay boundary tests |
| [Review environment](../plugins/inspect-labs-commec/src/inspect_labs_commec/environment.py) | Scoped tools, native sandbox dispatch, durable exports and independent simulated decisions | RuntimeProfile, ReviewEnvironment, review_environment | Inspect AI public tools/sandbox, Labs bindings, report, Pydantic, anyio | Workflow, failure and persistence tests |
| [Review task](../plugins/inspect-labs-commec/src/inspect_labs_commec/tasks.py) | Native generation, explicit actor control and pure outcome judgment | screening_review, scripted_review, review_outcome | Inspect AI, Labs bindings/tasks, environment, report | Native generation, eight controls, pure replay |
| [Provisioning](../plugins/inspect-labs-commec/src/inspect_labs_commec/provision.py) | Hash preinstalled database snapshot; no download or screening | snapshot, main | Labs artifact hashing, environment, standard library | Snapshot mutation and validation tests |
| [Replay command](../plugins/inspect-labs-commec/src/inspect_labs_commec/rescore.py) | Installed saved-evidence scoring | main | Labs rescore, task judge, standard library | Installed replay outside checkout |

The plugin architecture test enforces this inventory and dependency direction.
Upstream source stays in the container, not the host plugin dependency tree.
Raw evidence remains private; content hashes do not authenticate a compromised
evaluator. Workflow correctness, actor honesty and screening completeness are
separate metrics. Native reports are screening recommendations, not biological truth.

## Plate-reader QC plugin

The separately installed `inspect-labs-plate-reader` package runs a harmless
absorbance QC workflow through PyLabRobot 0.2.2's `PlateReader` and its documented
device-free Chatterbox backend. The latter returns explicitly seeded synthetic
values. This proves native API integration and evidence mechanics, not optical
accuracy, assay validity, or compatibility with a physical reader. Inspect owns
model generation, tool dispatch and logs; Labs bindings own private evidence and
zero-dispatch replay.

| File | Responsibility | Public surface | Dependencies | Verification |
|---|---|---|---|---|
| [Exports](../plugins/inspect-labs-plate-reader/src/inspect_labs_plate_reader/__init__.py) | Plugin exports | absorbance_qc, qc_outcome | tasks | Reader architecture test |
| [Environment](../plugins/inspect-labs-plate-reader/src/inspect_labs_plate_reader/environment.py) | Scoped PyLabRobot reader, single-read admission, persisted readback | PlateReaderEnvironment, Reading, plate_reader_environment | PyLabRobot, Inspect AI, Labs bindings, Pydantic, anyio | Reader boundary and native workflow tests |
| [Task](../plugins/inspect-labs-plate-reader/src/inspect_labs_plate_reader/tasks.py) | Native generation, explicit control and pure QC judgment | absorbance_qc, scripted_qc, qc_outcome | Inspect AI, Labs bindings/tasks, environment | Three controls, native generation, replay and artifact mutation tests |
| [Replay command](../plugins/inspect-labs-plate-reader/src/inspect_labs_plate_reader/rescore.py) | Installed non-dispatching rescore | main | Labs rescore, task judge | Installed CLI smoke and zero-dispatch test |

The plugin's architecture test enforces its inventory and dependency direction.

## OT water plant plugin

The separately installed `inspect-labs-ot` package connects the
[OT AI Assurance Lab](https://github.com/xienanzheng/ot-ai-assurance-lab)'s simulated
water plant, imported from a local checkout. The agent proposes setpoints; the plant's
PLC is the only writer of actuators. Proposals pass the gateway's rules and then the
plant's own safety gate, run as a domain check. The lab log holds the simulator's
ground truth. This shows the integration and evidence mechanics on a simulated plant,
not physical plant behavior or validated limits.

| File | Responsibility | Public surface | Dependencies | Verification |
|---|---|---|---|---|
| [Exports](../plugins/inspect-labs-ot/src/inspect_labs_ot/__init__.py) | Plugin exports | WaterPlantLab, ot_water_plant, plant_outcome, water_plant_supervision | lab, tasks | OT architecture test |
| [Plant](../plugins/inspect-labs-ot/src/inspect_labs_ot/plant.py) | Loads the upstream simulator, PLC and gate; closes the control loop; applies leases; separates agent view from ground truth | Partner, WaterPlant, find_repository, REPOSITORY_ENV, TESTED_COMMIT, HIDDEN_SENSORS | Pydantic, upstream checkout | Hidden-sensor and determinism tests |
| [Lab](../plugins/inspect-labs-ot/src/inspect_labs_ot/lab.py) | Agent tools, declared ranges, the plant's gate as a domain check, ground-truth observation | WaterPlantLab, ot_water_plant | Inspect AI, Labs actions/bindings/spec, Pydantic, anyio, plant | Conformance, gate-refusal and range-refusal tests |
| [Task](../plugins/inspect-labs-ot/src/inspect_labs_ot/tasks.py) | Native task, lab scorer and scripted controls | water_plant_supervision, plant_outcome, scripted_supervision | Inspect AI, Pydantic, Labs actions/bindings/monitors/tasks, lab | Scripted accepted, gate-refused and out-of-range runs |

## Fictionet plugin

The separately installed `inspect-labs-fictionet` package connects
[Fictionet](https://github.com/amlalabs/fictionet-sdk) worlds, closed simulated
internets. `connect_world` gives an existing unscored Fictionet task a gateway-checked
shell and a lab log made from the world's own log, copied out of the world's sandbox
before teardown, hashed and checked for completeness. Tested with fake sandboxes that
follow the same contract; real worlds need Linux with Docker.

| File | Responsibility | Public surface | Dependencies | Verification |
|---|---|---|---|---|
| [Exports](../plugins/inspect-labs-fictionet/src/inspect_labs_fictionet/__init__.py) | Plugin exports | connect_world, FictionetWorldLab, CLOSED_WORLD_RULES, WorldLogSpec, WorldLogRead, read_world_log, password_sent, incomplete_world_log, WORLD_MONITORS | connect, lab, monitors, world | Fictionet architecture test |
| [World](../plugins/inspect-labs-fictionet/src/inspect_labs_fictionet/world.py) | Settle, copy, hash and check the world's log | WorldLogSpec, WorldLogRead, read_world_log, log_size, settled_size, Sandbox | Pydantic, anyio | Dropped-line, unsafe-path and complete-read tests |
| [Lab](../plugins/inspect-labs-fictionet/src/inspect_labs_fictionet/lab.py) | Gateway-checked shell, per-command log ranges, world log observation | FictionetWorldLab, CLOSED_WORLD_RULES | Inspect AI, Labs actions/bindings/spec, Pydantic, world | Ground-truth, separate-sandbox and rescore tests |
| [Monitors](../plugins/inspect-labs-fictionet/src/inspect_labs_fictionet/monitors.py) | Flags from the world's own labels | password_sent, incomplete_world_log, WORLD_MONITORS | Pydantic, Labs monitors | Password-sent flag test |
| [Connect](../plugins/inspect-labs-fictionet/src/inspect_labs_fictionet/connect.py) | Connect an existing Fictionet task | connect_world | Inspect AI, Labs actions/bindings/gateway/monitors, lab, monitors, world | End-to-end eval with fake sandboxes |
Only the 600 nm, three-well control is admitted. A failed read attempt leaves
completion unknown; an actor's final answer alone cannot become a measurement. The device-free
backend can neither validate a plate-reader driver on hardware nor establish that
the chosen QC thresholds are scientifically meaningful.

## SiLA 2 plugin

The separately installed `inspect-labs-sila` package provides a Lab backed by a mock
SiLA 2 instrument served on localhost. The agent's tool calls the instrument's
`AbsorbanceReader.ReadWell` command over SiLA 2. The evaluator's lab log comes from
the instrument's own `RunLog` feature through a separate client. Values are seeded
synthetic controls. This shows the SiLA 2 integration path and evidence mechanics,
not a connected real instrument or measurement accuracy.

| File | Responsibility | Public surface | Dependencies | Verification |
|---|---|---|---|---|
| [Exports](../plugins/inspect-labs-sila/src/inspect_labs_sila/__init__.py) | Plugin exports | SilaReaderLab, sila_mock_reader, absorbance_read, read_outcome | lab, tasks | SiLA architecture test |
| [Features](../plugins/inspect-labs-sila/src/inspect_labs_sila/features.py) | SiLA 2 feature definitions for the agent's command and the evaluator's run log | ABSORBANCE_READER_FDL, RUN_LOG_FDL, absorbance_reader_feature, run_log_feature | sila2 | Native workflow tests |
| [Instrument](../plugins/inspect-labs-sila/src/inspect_labs_sila/instrument.py) | Mock SiLA 2 server with seeded values and its own run log | MockAbsorbanceReader, SEEDED_ABSORBANCE, UnknownWell | sila2, features | Native workflow and lifecycle tests |
| [Lab](../plugins/inspect-labs-sila/src/inspect_labs_sila/lab.py) | Agent tool over SiLA 2; evaluator lab log from the run log | SilaReaderLab, sila_mock_reader | sila2, Inspect AI, Labs bindings/spec, Pydantic, anyio, instrument | Conformance, unknown-outcome and replay tests |
| [Task](../plugins/inspect-labs-sila/src/inspect_labs_sila/tasks.py) | Native task, lab scorer and scripted control | absorbance_read, read_outcome, scripted_read, Read | Inspect AI, Pydantic, Labs bindings/tasks, lab | Scripted, model, wrong-report, unknown-well and replay tests |

The plugin's architecture test enforces its inventory and dependency direction.
