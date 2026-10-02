# inspect-labs-opentrons

Opentrons OT-2 backends for [Inspect Labs](../../README.md), built on PyLabRobot.

| Backend | Mode | Needs |
|---|---|---|
| `opentrons-ot2-simulator` | simulation | nothing beyond Inspect Labs |
| `opentrons-ot2` | physical | `inspect-labs-opentrons[robot]`, a reachable robot, `allow_physical=True` |

```bash
uv pip install -e plugins/inspect-labs-opentrons
inspect-labs list backend
inspect-labs doctor --backend opentrons-ot2      # reports missing ot_api and the host slot
inspect eval inspect_labs/worklist_transfer --model mockllm/model -T scripted=true \
  -T backend=opentrons-ot2-simulator
```

Driving a real robot:

```bash
inspect eval inspect_labs/worklist_transfer --model PROVIDER/MODEL \
  -T backend=opentrons-ot2 -T backend_args='{"host": "10.0.0.5"}' -T allow_physical=true
```

`allow_physical=true` is a host assertion, not facility authorization. Before any
physical run:
- have an operator present and emergency stop ready;
- use inert liquids and calibrated labware;
- confirm the task's deck layout matches the robot.

The device claim only prevents two of your own evaluations from driving the same robot.

This adapter is **not validated on hardware**. The simulator backend is tested; the
physical path is untested until run on a real OT-2 with its operator.
