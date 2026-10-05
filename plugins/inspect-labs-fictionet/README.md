# inspect-labs-fictionet

Connects [Fictionet](https://github.com/amlalabs/fictionet-sdk) worlds, closed
simulated internets, to Inspect Labs. An existing Fictionet task keeps its dataset,
solver and sandbox. `connect_world` adds:

- a shell tool whose every command passes the Inspect Labs gateway;
- a lab log made from the world's own log, copied out of the world's sandbox
  before teardown, hashed and checked for completeness;
- per-command byte ranges, so each world event can be traced to the command that
  caused it;
- monitors that read the world's labels, for example a page that carried a password.

Because the world log is saved with the run, a run can be scored again later
without the world, and without sending anything.

```python
from inspect_labs_fictionet import WorldLogSpec, connect_world

task = connect_world(
    my_unscored_fictionet_task,  # without its own bash tool
    scorer=my_world_log_scorer,  # (report, lab_log) -> {"known": ..., "correct": ...}
    lab_log_dir=Path(".research/fictionet-lab-logs"),
    spec=WorldLogSpec(service="fictionet"),  # Border-style compose files
)
```

## Limits

- Tested with fake sandboxes that follow the same contract. Real worlds need Linux
  with Docker and `/dev/net/tun`, and have not been run with this plugin yet.
- Kubernetes is not supported: Inspect can't reach the world's sandbox there.
- Commands are allowed inside the closed world by default. Holding a specific
  request, such as a password form, for approval before it reaches a site needs a
  hook inside the world; none exists yet.
- With no end marker, the log counts as complete once it has stopped growing for a
  quiet window (2 seconds by default, `WorldLogSpec.quiet_seconds`). An event later
  than that would be missed.
- Fictionet worlds use the wall clock and fresh randomness, so runs are not
  reproducible; the saved world log is the record.
