# inspect-labs-ot

A Lab for the [OT AI Assurance Lab](https://github.com/xienanzheng/ot-ai-assurance-lab)'s
simulated water plant, so agents supervising critical infrastructure can be
evaluated with Inspect AI.

The agent reads the plant, proposes setpoint changes and waits while the plant's
own PLC runs it. It never commands actuators. Each proposal passes two independent
layers before it reaches the PLC:

- the Inspect Labs gateway: declared ranges, action rules and approvals;
- the plant's own deterministic safety gate, run as a domain check. A proposal the
  gate rejects is refused and recorded; one it clips runs with the clipped values.

The lab log keeps ground truth the agent never sees: the simulator's true chlorine
and storage, the safety state for every minute, and every gate decision.

## Use

The OT AI Assurance Lab is not a Python package. Clone it and point the plugin at it:

```bash
git clone https://github.com/xienanzheng/ot-ai-assurance-lab
export OT_ASSURANCE_LAB=$PWD/ot-ai-assurance-lab
uv pip install -e plugins/inspect-labs-ot
inspect eval inspect_labs_ot/water_plant_supervision -T scripted=careful --model mockllm/model
```

Tested against upstream commit `c7361ce`. Scenarios are the upstream water
scenarios, such as `zone_leak`, `pump_failure` and `unsafe_ai`.

## Limits

- A simulated plant with illustrative values. Not a physical plant, a digital twin
  of one, or regulatory limits.
- When WNTR is not installed, or a WNTR step fails, the upstream simulator falls
  back to analytical hydraulics. The lab log records this.
- The task's unattended operator approves every proposal the plant's gate allows.
  A real deployment needs a person, or leases, in that role.
- Only the water plant is connected. The upstream PWR and grid simulators are not.

The OT AI Assurance Lab is MIT-licensed, by Nanzheng Xie. This plugin imports it
from a local checkout and copies none of its code; the supervisory lease follows
its PLC service's behavior.
