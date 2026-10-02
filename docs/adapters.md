# Authoring an adapter

Author an ordinary Inspect AI task first. Then connect the external system at
the layer that owns it: a lab service or instrument through Inspect Labs, or a
robot policy and embodiment through the optional Inspect Robots bridge.
Adapters can be separately installed packages.

[inspect-labs-opentrons](../plugins/inspect-labs-opentrons) is the
liquid-handler reference; [inspect-labs-plate-reader](../plugins/inspect-labs-plate-reader)
is a device-free reader workflow reference. Decide first which layer your hardware
belongs to:

| You are integrating | Use | Why |
|---|---|---|
| A model, agent or approval policy | a native **Inspect AI** extension | Inspect AI owns agents; approvers register through `inspect_ai` entry points |
| A lab service with its own API (scheduler, LIMS, synthesis ordering, cloud lab) | an Inspect Labs **environment** | the service owns jobs; you expose scoped tools and a separate observation |
| A liquid handler PyLabRobot can drive on an OT-2 deck | an Inspect Labs **backend** | the deck, tracking and ledger already exist |
| A robot arm, mobile manipulator or robot simulator | an **Inspect Robots embodiment** plugin | Inspect Robots owns robot control; physical embodiments additionally require explicit claimable native device slots |

## Register through entry points

```toml
[project.entry-points."inspect_labs.backends"]
my-handler = "my_package:my_handler"

[project.entry-points."inspect_labs.environments"]
my-lims = "my_package:my_lims_environment"
```

After installation, `inspect-labs list` shows the component without importing it, and
tasks select it by name, for example `-T backend=my-handler`.

## Backends

Backends currently serve the liquid-handling environment, which drives an OT-2-style
deck. A reader or incubator should get a scoped environment with its own operations
and observations. The plate-reader plugin demonstrates that shape through a
synthetic PyLabRobot control; it does not authorize or validate physical hardware.

A backend factory returns a `BackendBinding`:

```python
from inspect_labs.plugins import BackendBinding, DeviceSlot

RUNTIME_REQUIREMENTS = {"vendor_sdk": 'uv pip install "my-package[hardware]"'}


def my_handler(host: str) -> BackendBinding:
    # Import lazily: listing and doctor must work without the vendor SDK.
    from vendor_sdk import PyLabRobotBackend

    return BackendBinding(
        backend=PyLabRobotBackend(host),  # no connection here: connect on first operation
        mode="physical",
        deck="ot2",
        devices=(("http", host),),  # claimed so two evaluations cannot drive it
        notes="Honest operating notes: mounts, units, limits, calibration.",
    )


my_handler.DEVICE_SLOTS = (DeviceSlot(arg="host", kind="http", label="handler address"),)
```

The contract:

- **Constructors are hardware-free.** Connect on first use. Native task setup and
  explicit conformance checks construct components before admission. `doctor` only
  loads factory declarations; it does not construct components or call their lifecycle.
- **Declarations are honest.** `mode="simulation"` is accepted only for backends known
  to perform no I/O. Anything else must be `physical`, which `bind_task` refuses
  without the host's `allow_physical=True`. Do not subclass the built-in simulators to
  reach hardware.
- **Notes are for agents and operators.** They are shown in the agent's deck
  description. Wrong notes silently degrade both agents and safety reviews.
- **Report instrument failures as failures.** Exceptions from your hardware become
  `InstrumentFault`: the sample's outcome is unknown, never an agent failure.

## Environments

An environment factory takes a private ``directory`` plus keyword options and returns a
`LabEnvironment`. For a dispatch conformance check, supply a trusted
``dispatch_count`` read from your service, not from the agent. The check compares
this count across construction, observation and close. A passing check supports
those tested clauses only. `doctor` does not execute them.

## Check it

```python
import anyio
from inspect_labs.conformance import diagnose

def test_adapter_declarations_load() -> None:
    assert anyio.run(diagnose, "backend", "my-handler")["ok"]
```

```bash
inspect-labs doctor --backend my-handler
inspect-labs doctor --lab my-lims
```

`doctor` reports:

- the component failing to load;
- missing runtime requirements, with the install commands you declared;
- declared device slots;
- declaration errors, with `conformance="not_run"` distinguishing this diagnostic
  from a lifecycle test.

It does not construct components or invoke observations, tools or shutdown. Plugin
imports are trusted code and must themselves be free of hardware I/O. For lifecycle
testing use `check_environment` with an explicitly chosen test instance and a trusted
dispatch counter; that check invokes provider methods and is not automatically safe
for an arbitrary physical adapter.

## Native robot device claims and failures

The bridge acquires advisory claims before constructing an embodiment, holds them
through all child rollouts, and releases them on construction failure or close.
Physical embodiments must declare native `DEVICE_SLOTS` of kind `serial`, `v4l2`, or
`can`, with every identity explicitly supplied in `embodiment_args`. Missing or
malformed declarations are refused; device defaults are not guessed.

The protocol matches Inspect Robots commit
`3c832c34b6c11fa5205ff80ab4947247fedd5eea`. There is no public native claim API at
that pin, so Labs implements its lock-file convention without importing the private
helper. Unlike that helper, Labs refuses admission on filesystem or locking errors.
The two-direction interoperability tests are a gate for changing the dependency pin.
The packaged `THIRD_PARTY_NOTICES.md` retains upstream attribution and license.

Coordination requires the same user, host and runtime directory, and identities
that normalize to the same native value. It cannot fix a native process that omits
device identities or proceeds after its own lock failure. Claims are not interlocks.

Native child errors, cancellations and missing/nonfinite outcome measurements
halt additional rollouts and mark the outcome unavailable. Their logs remain linked
to private evidence; native SafetyAbort becomes a Labs SafetyAbort, and other
unavailable outcomes become InstrumentFault. The existing parent latch refuses later
samples/runs of that task until reviewed. This prevents new dispatch, not a claim
that an already-started physical action was stopped.

## What the checks cannot prove

Conformance proves an adapter is wired correctly. It cannot prove:

- that declarations are honest, or that the instrument does what the notes say;
- that recorded volumes match reality;
- that provider-side access control holds.

Validate those with the instrument's operator, inert materials and the provider's own
records before any evaluation relies on them. Device claims coordinate your own
evaluations on one host. They are not interlocks, emergency stops or a lab scheduler.
