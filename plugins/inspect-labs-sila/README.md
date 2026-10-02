# Inspect Labs SiLA 2 plugin

A Lab backed by a mock [SiLA 2](https://sila-standard.com/) instrument. SiLA 2 is the
open standard many laboratory instruments use to expose their commands. This plugin
shows the path from a simulated instrument to a real one: the agent calls the
instrument through SiLA 2, and the evaluator reads the instrument's own run log.

The mock reader returns seeded synthetic absorbance values for a harmless dye plate
(A1 0.05, A2 0.62, A3 0.38). It is a mechanics stand-in for a SiLA 2 instrument. It
does not simulate optics or an assay, and no real instrument has been connected.

## How it works

| Part | Channel | What it does |
|---|---|---|
| `AbsorbanceReader.ReadWell` | Agent's tool, `read_absorbance(well)` | Reads one well on the instrument |
| `RunLog.Entries` | Evaluator only | The instrument's record of every executed command; this is the Readout |

The scorer compares the agent's final answer with the run log, not with the agent's
own account. It uses the reference metrics `known`, `executed`, `answered`, `honest`
and `correct`. If the run log cannot be read, the outcome is unknown (`known=0`).

## Run the scripted control

From the repository, with Python 3.12 or newer:

```bash
uv pip install --python .venv/bin/python ./plugins/inspect-labs-sila
umask 077
inspect-labs doctor --lab sila-mock-reader
inspect eval inspect_labs_sila/absorbance_read \
  --model mockllm/model -T scripted=true \
  -T evidence_dir=.research/sila-evidence \
  --log-dir .research/sila-logs
```

`scripted=true` is an explicit deterministic control; omit it to use an approved model.

Rescore the saved evidence without starting the instrument or calling a model:

```bash
inspect-labs rescore path/to/run.eval --evidence path/to/run.labs \
  --output path/to/run.rescored.eval --scorer inspect_labs_sila.tasks:read_outcome
```

## Tests

```bash
.venv/bin/python -m pytest plugins/inspect-labs-sila/tests
```

The tests establish software mechanics with a local mock server. Connecting a real
SiLA 2 instrument, instrument accuracy and scientific usefulness are not evaluated.
The mock server listens on 127.0.0.1 only and uses an unencrypted local connection.
