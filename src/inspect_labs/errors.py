"""Laboratory fault taxonomy: who failed, and whether evaluation may continue.

Actor mistakes that the agent can see and recover from (overdrawing a well, an
unauthorized destination) are native Inspect ``ToolError`` results, not faults.

- ``CompatibilityError``: raised before provider tools are installed. The sample
  errors and nothing is dispatched.
- ``InstrumentFault``: the provider or instrument failed, not the actor. The sample
  errors and its outcome is unknown.
- ``SafetyAbort``: an unsafe condition was detected. The sample errors and the task
  admits no new samples.
"""

from __future__ import annotations


class LabError(Exception):
    """Base class for laboratory evaluation faults."""


class CompatibilityError(LabError, ValueError):
    """The task requires operations or ranges the environment does not declare."""


class InstrumentFault(LabError):
    """The provider or instrument failed independently of the actor's decisions."""


class SafetyAbort(LabError):
    """An unsafe condition occurred; stop admitting new work for this task."""
