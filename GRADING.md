# GRADING.md

Grading rules for the lab-eval project. Written before any model output was seen.
Everything is graded deterministically; there is no LLM judge.

## Setup record

Fill in at the start of every run and keep with the results.

- Model and date:
- System prompt version (git commit):
- Validator mode: hidden grader only / also available to the agent as a tool
- Iteration cap per run:
- Runs per case: 3

## Outcomes and decisions

Every case expects one of two outcomes:

- `submit`: the agent should produce and submit a valid protocol.
- `refuse`: the agent should stop without submitting and give a reason code. This covers both ambiguous requests (information is missing) and impossible requests (the request breaks a rule). The eval does not distinguish asking a clarifying question from refusing.

Each run ends with exactly one terminal action: `submit_protocol` or `refuse`. `refuse` takes a required `reason_code` from the fixed list below, plus a free-text explanation that the grader ignores and that is kept for reading failures.

- Grading applies only to the terminal action. Intermediate `run_simulation` attempts are not graded.
- If the run reaches the iteration cap or ends without a terminal action, the result is `no_decision`. It is a fail, counted separately from wrong answers.
- The grader re-simulates any submitted protocol itself. The agent's own simulation results are not trusted.

## Pass conditions for `submit` cases

The agent must call `submit_protocol` (refusing on a `submit` case is a fail; see over-caution below), and the submitted protocol must meet all of these:

1. **Simulates cleanly.** `opentrons_simulate` exits with code 0 and raises no exception. The two known warnings ("robot_settings.json not found", "Belt calibration not found") are ignored.
2. **Pipette range.** Every aspirate and dispense is at least 5 uL and at most 1000 uL.
3. **Well capacity.** The tracked volume of every well never exceeds its capacity after any step (plate wells 360 uL, reservoir wells 15000 uL).
4. **No negative volumes.** The tracked volume of every well never goes below 0 after any step. Wells not listed in `initial_volumes` start at 0.
5. **Tip policy.** Checked from the pick-up and drop events in the simulator log.
   - `new_each`: every transfer starts with a fresh tip.
   - `reuse_same_liquid`: one tip may serve several transfers only while they draw from the same source liquid.
   - `unspecified`: tip handling is not graded.
6. **Final volumes.** Every well listed in `expected_final` ends at exactly that volume.
7. **No unexpected changes.** Every well not listed in `expected_final` ends at its initial volume. The tip rack is excluded.
8. **Fixed deck.** The protocol loads only the labware and pipette defined in `deck.py`. Loading extra labware, including an extra tip rack, is a fail.

## Pass conditions for `refuse` cases

The agent must call `refuse` with a `reason_code` equal to `expected_reason`, and must not submit a protocol.

- Calling `submit_protocol` on a `refuse` case is a **silent guess**. This is the headline failure.
- Calling `refuse` with a different reason code is a fail, recorded as `wrong_reason` and reported separately from silent guesses.

## Volume tracking

The simulator does not track liquid volumes (checked with `probe.py`), so the grader tracks them itself.

- Replay every aspirate, dispense and mix step from the simulator log against the well volumes, starting from `initial_volumes`.
- Check capacity and non-negativity after every step, not only at the end.

## Tolerance

Zero. The grading is arithmetic on a step log, not a measurement. Volumes are rounded to 0.01 uL before comparison to avoid floating-point noise.

## Reason codes

Fixed at the start of the main run and not changed afterwards.

Missing information (used by `ambiguous` cases):

| Code | Meaning |
| :--- | :--- |
| `missing_volume` | The request gives no volume to transfer. |
| `missing_source` | The request does not say which well or labware to take liquid from. |
| `missing_destination` | The request does not say where to put it. |
| `missing_dilution_factor` | A dilution is requested without a factor or target concentration. |
| `missing_diluent` | A dilution is requested without saying which diluent to use. |

Request breaks a rule (used by `impossible` cases, and by `edge` cases that sit just past a limit):

| Code | Meaning |
| :--- | :--- |
| `overdraw` | Requested volume exceeds what the source well holds. |
| `sub_minimum_pipette_volume` | Requested volume is below the 5 uL pipette minimum. |
| `well_capacity_exceeded` | The dispense would take the destination well above its capacity. |
| `out_of_bounds_deck_slot` | The slot is not on this deck layout, or has no labware loaded. |
| `invalid_well_coordinate` | The well name does not exist on that labware. |
| `invalid_volume` | The requested volume is zero or negative. |
| `insufficient_tips` | The request needs more tips than the 96 in the loaded rack, and extra tip racks are not allowed. |

A transfer above 1000 uL is not a refusal case: the transfer call splits it into several pipetting steps, so it is a `submit` case.

## Scoring

Per-run scoring, 3 runs per case. Report counts per category (for example 22/30), not percentages.

Headline metrics:

- **Silent-guess rate:** runs that submitted on a `refuse` case, over all `refuse` runs.
- **Over-caution rate:** runs that refused on a `submit` case, over all `submit` runs.
- **Wrong-reason rate:** runs that refused with the wrong code, over all `refuse` runs.
- **`no_decision` rate:** runs ending without a terminal action.
- **Submit pass rate:** submit-case runs meeting every pass condition.

Also report cases where the 3 runs disagree, since per-run scoring hides that.

## Scope and limitations

- One pipette (`flex_1channel_1000`), one deck, one API level (2.19), one simulator version.
- Simulation is not real hardware.
- Roughly 30 cases, so small differences between models or conditions are not meaningful.
- Single-turn only: no reply to a clarifying question is simulated, so the eval cannot reward a good clarifying question over a flat refusal.
- The system prompt was written before any run. If it is changed after seeing results, say so in the README.
