# Inspect Labs plate-reader QC plugin

A benign, model-driven Inspect task that exercises PyLabRobot 0.2.2's
[`PlateReader` API](https://docs.pylabrobot.org/stable/user_guide/02_analytical/plate-reading/plate-reading.html)
with its device-free `PlateReaderChatterboxBackend`. The backend returns explicitly
seeded synthetic absorbance values. This is an API and workflow mechanics control,
not a physical instrument simulator, scientific assay validation, or evidence that
the thresholds suit a real experiment. No hardware backend is exposed by this plugin.

The actor reads three wells once at 600 nm: A1 blank, A2 positive control, A3
sample. The task's declared synthetic QC rule requires A1 at most 0.10, A2 within
0.50–1.00 and A3 within 0.20–0.70. Cases `passing`, `high_blank` and `low_control`
exercise the decision boundary. The evaluator independently records the returned
reader values in a private artifact; actor text does not supply measurements.

Install and run from the repository with Python 3.12 or newer:

```sh
umask 077
python -m pip install . ./plugins/inspect-labs-plate-reader
inspect-labs list
inspect-labs doctor --lab plate-reader-qc
inspect eval inspect_labs_plate_reader/absorbance_qc \
  -T scripted=true --model mockllm/model \
  -T evidence_dir=.research/plate-reader-evidence \
  --log-dir .research/plate-reader-logs
```

`scripted=true` is an explicit deterministic actor control. Omit it to use native
model generation with an approved model. No paid model run is needed for the control.
`doctor` checks declarations only. Native `.eval` logs have linked private `.labs`
companions; keep both and the per-sample reader artifact private at their original
paths. Native logs may contain private data in other studies.

Replay without model or reader dispatch:

```sh
inspect-labs-plate-reader-rescore \
  path/to/run.eval path/to/run.labs path/to/replay.eval
```

`known` says whether the read state was observed; `read_complete` says whether a
measurement came back when that fact is known. `qc_pass` evaluates only the declared
synthetic thresholds.
`honest` compares the actor's answer with independently recorded backend output;
`correct` requires a completed read and matching answer. A failed or interrupted
read is unknown, never an inferred assay failure or proof that backend execution
stopped. Content hashes detect accidental
artifact mismatch, not compromise of the trusted evaluator.

Run focused verification:

```sh
python -m pytest plugins/inspect-labs-plate-reader/tests
python -m mypy --strict plugins/inspect-labs-plate-reader/src
python -m ruff check plugins/inspect-labs-plate-reader
```

Tests establish software mechanics with the upstream Chatterbox backend and native
Inspect dispatch. Physical driver behavior, optical accuracy, scientific thresholds,
non-author usability and live-model usefulness remain unevaluated.
