# Writing a benchmark

A benchmark is a native Inspect `Task`, bound to a laboratory environment with a pure
judge. You never edit Inspect Labs to add one. [reagent_addition.py](../examples/reagent_addition.py)
is a complete example written only against the public API. Read
[concepts](concepts.md) first.

## 1. Choose or write the environment

To use the liquid handler, install `inspect-labs[pylabrobot]` and describe the deck:

```python
layout = DeckLayout(
    labware=(
        Labware(name="tips", kind="tiprack_300ul", slot=1),
        Labware(name="plate", kind="plate_96_360ul", slot=2),
        Labware(name="reservoir", kind="plate_24_10ml", slot=3),
    ),
    contents={"reservoir": {"A1": WellContent(volume_ul=5000, solutes={"reagent": 5000})}},
    restricted=frozenset(),  # e.g. {"reservoir:D6"} for wells the agent must not touch
)
```

Solute amounts use any consistent unit; concentration is amount divided by volume.
For another instrument or service, implement `LabEnvironment` over its existing
client ([authoring guide](authoring.md)) and add `check_environment` to your tests.

## 2. Put the reference in the environment, not the prompt

Pass the private target as `reference=`. It travels into the evidence for the judge
and replay, and the agent never sees it. The prompt states the goal the way an
operator would.

## 3. Write a pure judge and declare its metrics

The judge receives the agent's final text and the `LabEvidence`. It reads facts
(`LiquidFacts` for the liquid handler) and returns numbers. Keep `executed` (what the
facts show) separate from `honest` (whether the final `ANSWER:` line agrees). Declare
every key with `bind_task(..., metrics=...)`; unknown outcomes are then NaN for all
of them. Validate that required facts exist, and raise rather than score if the
evidence is inconsistent.

Use `carryover(facts)` for tip-reuse contamination of filled containers. When the
intended transfers are fixed, compare the whole deck: `expected_deck(layout,
transfers)` gives the ideal end state, and `deck_deviations(facts, expected)` lists
every well that differs. Contaminated sources, dumped excess and stray wells then
fail, not just wrong destinations. `honest` should mean the literal truth of the
claim your prompt defines; keep protocol violations in `executed`.

## 4. Declare requirements

List the operations and ranges your protocol needs in `Requirements`. A deck or
instrument that cannot meet them fails with `CompatibilityError` before the agent gets
any tools.

## 5. Add a scripted control

`scripted_liquid_protocol(calls, answer)` issues fixed tool calls through the same
tools a model uses. Select it with a `scripted` task argument. It must score
`correct=1` for every design the task constructor accepts. Reject infeasible
parameters in the constructor instead of letting the control fail. If the control
does not score `correct=1`, the task or judge is wrong. It is a control, not a
capability measurement.

## 6. Test failure modes, not just success

Before trusting a benchmark, check that each of these scores as intended. Use the
native `mockllm/model` with `custom_outputs` tool calls, as the package tests do:

- the correct protocol, with a false `ANSWER: incomplete`: `executed=1, honest=0`
- a plausible but wrong protocol claiming completion: `executed=0`. `honest` is 1 only
  if the completion claim itself is literally true, for example every line transferred
  but with a reused tip
- a physics violation, such as overdrawing a well: recorded as `rejected`, not executed
- for restricted resources: `allow` versus `refuse` gives the expected attempted/executed split
- replay with `rescore_workflow` while provider, model and instrument calls are patched
  to fail: scores unchanged

## 7. Run it

```bash
inspect eval reagent_addition.py@reagent_addition --model mockllm/model -T scripted=true
inspect eval reagent_addition.py@reagent_addition --model PROVIDER/MODEL --epochs 5 \
  --cost-limit APPROVED_USD
```

Pre-register conditions and budgets before a live study. Report the metrics
separately, with their denominators. A few epochs on one model describe behavior;
they do not establish capability or safety.
