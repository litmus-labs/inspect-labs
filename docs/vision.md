# Vision and development path

Inspect Labs aims to become shared evaluation infrastructure for AI-operated
laboratories. Our first priority is testing and strengthening safeguards against
biological misuse in practice. Researchers should be able to measure what
scientific agents can accomplish, check laboratory outcomes, and evaluate
defenses across software, laboratory services and robots.

Litmus maintains the framework and aims to conduct recurring evaluations with
researchers and infrastructure providers. Litmus Labs names the environments
developed for these studies. Community adoption and a common evaluation standard
are goals, not properties established by the current prototype.

## Why this matters

Scientific AI is moving into biological research and physical laboratory work.
AI assistance can benefit legitimate research while also supporting concerning
dual-use activity. Anthropic's September 2026 report describes circumvention of
access controls and limits to content classifiers alone. Its cases motivate
stronger defenses without establishing imminent biological threats.
[Biological misuse report](https://www.anthropic.com/threat-intelligence-report-september-2026#biological-misuse-sep-26).

Reuters reports that Anthropic has established a wet lab and is pursuing
automation involving AI-directed robotic experiments. This is early work,
but it makes evaluation of physical laboratory operations a concrete need.
[Reuters reporting](https://www.reuters.com/world/anthropic-quietly-sets-up-biology-lab-it-ramps-ai-drug-program-2026-09-18/).

Our response is to develop reusable laboratory environments for capability
measurement and adversarial testing of safeguards. Studies need to establish
what agents accomplished and whether defenses prevented disallowed work while
allowing legitimate research. A refusal, an accepted request or an agent's
report cannot establish the outcome of the wider workflow.

## What the framework contributes

Inspect AI owns native task execution, models, agents, tools, approvals, limits,
scoring and logs. Inspect Robots owns robot policies, embodiments, execution
and robot evaluation. Existing laboratory stacks operate services and instruments.
Inspect Labs connects their tools and outcome observations within a laboratory
evaluation. It must not become another runner, scheduler, controller or scanner.

Robotics is central to the physical laboratory mission. Robot actions affect
samples and equipment and must be evaluated alongside software and service
operations. Individual studies can still focus on a digital task. Researchers
need to check each component and then investigate how safeguards hold across
the connected workflow.

An environment should provide meaningful tasks, scoped tools and observations
that answer a research question. A device adapter or an isolated tool operation
is a building block. Domain experts must establish whether the resulting
environment represents the laboratory work and risks being studied.

## Studies we aim to support

| Study | Purpose | Inspect Labs contribution |
|---|---|---|
| Hybrid evaluations with wet-lab components | Assess performance and safeguards across digital and physical work | Link software records, robot trials and laboratory measurements |
| Experimental verification | Check whether model-generated outputs work experimentally | Connect generated outputs to independently measured results |
| Pilot uplift studies | Inform the design of larger randomized studies | Record completion, intermediate progress and observation failures on matched tasks |
| Rapid uplift studies | Track performance with and without AI on bounded research tasks | Reuse environments and compare outcomes under defined conditions |
| Demonstrations for decision makers | Establish concrete capabilities and failures relevant to safeguards | Present checked outcomes with supporting records and explicit limits |

These are proposed uses. The framework supplies connections and evidence.
Researchers define comparison groups, assignments, outcome measures and
analysis. Software tests cannot establish scientific validity or the causal
effect of AI assistance.

## Development sequence and completion evidence

Our next research milestone is one independently useful environment, developed
with laboratory researchers and a service provider. A candidate workflow spans
research planning, provider review, simulated robotic execution and result
analysis using harmless materials. Choose the exact task with prospective users
rather than allowing available adapters to determine the scientific question.

The study should separately test what agents can accomplish, whether safeguards
prevent unauthorized work while allowing legitimate research, and whether an
intervention stops downstream execution. Use matched legitimate cases and
predeclared prohibited actions. A service hold or cancellation receipt alone
does not establish prevention; verify downstream state. Simulation supports
bounded studies but does not substitute for experimental or physical validation.

An independent group using the environment for its own question, then returning
for a second study, is stronger adoption evidence than adding more adapters.
Provider partnerships should yield a concrete failure, a changed defense and a
repeat evaluation establishing what improved. Environment-building challenges
can expand this foundation after entrants have a reproducible starter and judges
have a tested rubric. Incident exercises can reuse the same environments through
specialist partnerships, without turning Inspect Labs into an incident controller.

| Stage | Deliverable | Evidence required before advancing |
|---|---|---|
| Software foundation | Installed authoring surface, native execution and saved-evidence replay | A user can author and run a task outside the checkout; replay submits no new actions; failures and missing observations remain distinguishable |
| Reusable research environment | A domain-reviewed task set with legitimate and disallowed cases | Independent review of tasks and observations; difficult legitimate controls; a second author uses the environment for another study |
| Robotic laboratory simulation | A real robot policy evaluated on harmless laboratory tasks | Native robot records, independently checked outcomes, repeated trials and tests separating refusal, execution failure and missing evidence |
| Hybrid laboratory study | Selected digital outputs or robot-operated steps checked experimentally | An approved protocol, validated measurements, comparison conditions and a documented account of simulation and experimental limits |
| Recurring provider evaluation | Bounded adversarial testing with an infrastructure partner | Authorized access, provider-specific policies, observed downstream outcomes and verification of fixes under the same protocol |

The prototype implements software bindings and evidence mechanics, including
simulated liquid handling, service fixtures and native robot integration.
It has not established scientific validity, evaluated a robot foundation model,
validated physical laboratory execution or demonstrated deployed safeguard
effectiveness. See [concepts](concepts.md), [authoring](authoring.md) and the
[source registry](design.md) for the implemented surface.

Provider studies should begin at meaningful AIxBio infrastructure chokepoints,
including gene synthesis and biotech services. Work with providers and domain
experts to turn findings into safeguards and active defenses, then repeat the
evaluation as capabilities and deployments change. A provider fixing a failure
and passing a repeat test is more useful evidence than a growing adapter count.

## Conditions for credible claims

- Measure capabilities, safeguard effectiveness and workflow validity separately.
  Failure to complete a task is not by itself evidence of prevention.
- Preserve unknown outcomes. Record how often observations are available as
  well as how often tasks succeed.
- Keep evaluator records outside the agent's control. Hashes identify changes
  but do not authenticate a compromised source or prove tamper resistance.
- Check legitimate research alongside disallowed cases. Define prohibited work
  and provider policy before scoring an adversarial study.
- Revalidate tasks and scoring when changing services, simulators or hardware.
  Simulation results do not establish physical safety.
- Protect private logs, sensitive scenarios and provider data. Share reviewed
  methods and sanitized artifacts under appropriate access conditions.

Loss of control and rogue deployment are further questions for this
infrastructure. Biological misuse prevention remains the immediate priority.
Our longer-term goal is to help strengthen defenses against catastrophic
biological risks and prevent engineered pandemics.
