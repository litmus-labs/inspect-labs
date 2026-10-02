"""An explicit native Inspect Robots mock baseline, with no hardware selection."""

from pathlib import Path

from inspect_robots import EvalLog, Scene, Task, eval, success_at_end
from inspect_robots.mock import CubePickEmbodiment, ScriptedPolicy


def run_mock(log_dir: Path) -> EvalLog:
    """Run the upstream cube-reaching mock and retain its native JSON/actions.

    This is a robotics mechanics baseline, not laboratory task equivalence.

    Args:
        log_dir: Private directory for native robot records.

    Returns:
        Native Inspect Robots evaluation record.
    """
    task = Task(
        name="litmus-robot-mock-baseline",
        scenes=[Scene(id="mock-0", instruction="reach the cube", init_seed=0)],
        scorer=success_at_end(),
        max_steps=80,
    )
    embodiment = CubePickEmbodiment()
    try:
        return eval(task, ScriptedPolicy(), embodiment, log_dir=str(log_dir), seed=0)[0]
    finally:
        embodiment.close()
