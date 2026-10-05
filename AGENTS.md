# Inspect Labs agent instructions

## Purpose and ownership

Litmus maintains Inspect Labs, an evaluation framework built on Inspect. Litmus Labs names the environments. Use native Inspect AI and Inspect Robots execution and artifacts. Do not add another runner, laboratory controller, scanner or universal task hierarchy.

In public docs and paper prose, lead with Inspect AI as the familiar task and evaluation substrate. Explain Inspect Labs as its laboratory environment and evidence layer. Introduce Inspect Robots when a workflow uses robot policies or rollouts; keep its native ownership explicit without making prior knowledge of it a prerequisite for understanding the framework.

This repository is isolated from the original ASTRAL and RIDArena projects. Treat those projects as read-only references. Preserve upstream licenses and attribution. Do not copy archived module trees.

## Work discipline

- Read current code, tests and the private research contract before changing behavior.
- Use one owner per file. Keep patches small and evidence-linked.
- Prefer direct native recipes until repeated code justifies extraction.
- Use Python 3.12+, strict public types, Google-style docstrings and clear errors.
- Test at user, persistence and untrusted-input boundaries.
- Keep private plans, references and run artifacts under gitignored `.research/`.
- Never put credentials or private evaluation answers in public files.
- Native logs are not automatically safe for monitors or publication.
- Unknown execution or observation must remain unknown.
- Rescoring must not submit new actions.

## Verification and claims

Run targeted tests and the documented installed user surface. A successful build is not sufficient. Add a source registry and enforced architecture checks before public package growth. Declare module purpose, public surface, dependencies and tests.

Deterministic fixtures establish mechanics only. Domain relevance, scientific validity, physical guarantees and researcher usefulness need independent evidence. Preserve failures and separate capability, safeguards, controls and workflow validity.

## Authorization

Current approval covers local offline implementation and bounded reviews with configured models. No model-selection changes. Ask before paid evaluation batches, new cloud services, hardware, production operations, outreach or publication. Do not force-push or alter unrelated work.

The parent is the sole source writer unless it assigns a disjoint implementation scope explicitly. Read-only reviewers must not be required to edit files. Preserve review artifacts even if the harness misclassifies the role.

## Context and progress

pi-goal-x owns milestones. Todos track only immediate session work. ACP remains the sole context manager. Save durable evidence before folding consumed context. Do not rerun timed-out or unchanged failing calls without a new hypothesis.

## Review guidelines

Review against `.github/REVIEW_GUIDE.md`. Report only real problems, each with file and line, severity and a concrete failure scenario:

- an unknown outcome scored as known, or a missing result treated as pass, fail or zero;
- rescoring, replay, monitor, attach, release or verify dispatching an action or calling a Lab;
- a path where a refused, unapproved or stopped action still reaches the Lab, or a safety check that fails open;
- lab log, journal or release digests that change for old files, differ across processes or can verify a changed record;
- evaluation and serving deciding differently for the same action;
- credentials, private evaluation answers or `.research/` content in public files;
- missing tests at a user, persistence or untrusted-input boundary;
- docs claiming real-instrument, physical-safety or scientific validity beyond the tests.

Skip style that ruff enforces.
