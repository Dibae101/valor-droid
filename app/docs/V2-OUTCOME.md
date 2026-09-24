# What v2 actually changed, and what it did not

`GAPS-AND-SOLUTIONS.md` states nine gaps and a designed fix for each.
`FIX-STRATEGY-V2.md` orders them. This records what happened when those designs
met the devices, including the ones that were wrong. Read it before trusting
either of those documents: two of their diagnoses were mistaken and three of
their fixes were implemented and then reverted on measurement.

## Status of the nine gaps

| Gap | Design | Outcome |
|---|---|---|
| 1. Stall is permanent, ladder drives the run | Count novelty as progress | **Fixed.** Coverage per action up 3.2x-4.8x |
| 2. A third of actions change nothing | Retire after 2 inert executions | **Reverted.** No replacement |
| 3. A fifth of the budget is Back | Expect it to fall out of gap 1 | **Fixed**, exactly as predicted: ~31% to ~7% |
| 4. Coverage arrives in a handful of moments | Prefer unexplored states | **Not attempted.** See below |
| 5. GUI is the weaker half | Not specified | **Not attempted** |
| 6. Nine apps behind a backend or account | Self-host, use setup profiles | **1 of 9.** Mechanism proven on Jellyfin |
| 7. Screens that can never be dumped | Idle-free dump path | **Diagnosis was wrong.** Both apps now measure |
| 8. Throughput | Instrument, then tune | **Measured.** Observation is 60-77% |
| 9. Evidence grows samples x units | Delta-encode the ledger | **Harm fixed, growth not** |

## The two wrong diagnoses

**Gap 7 was not an observation problem.** Vinyl-Music-Player had no v1
measurement because the driver skipped it at the disk floor; it reached 19.79%
once given a device with room. Trackbook's device was wedged by a leaked
UiAutomation registration that one framework restart cleared. Animations were
already disabled fleet-wide, so "the window never idles" was never a plausible
reading of `could not get idle state`. No idle-free dump path was needed.

**Jellyfin's connect screen does expose an input.** It was recorded as exposing
only TextViews because Compose publishes no `resource-id`, and absence of an id
was read as absence of a field. The real blocker was that focusing the field
crashes the app, through a Compose defect fired by the IME asking a text field
where its cursor is. That is why the app sat at 2.18%.

## The three reverted fixes

Each was validated on a 15-minute smoke wave and each was harmful at the
60-minute budget the campaign runs at. That pattern, not any one of the fixes, is
the lesson: `stall_seconds` is derived from the budget, so a 900-second wave
exercises different dynamics from a 3600-second one.

**Retiring an action that twice changed nothing.** Chess fell from 46.58% to
23.29%. The stated cause was wrong -- a device-swapped A/B later showed Chess is
bimodal, 26% or 43-47%, independent of this code -- so the revert stands on
absence of evidence rather than on the evidence first claimed. Reaching new code
often requires re-traversing actions that pay nothing on their own.

**Bounding barren ladder resets by ending the run.** Stopped And-Bible at 6
minutes of 60. It bought no coverage anywhere: Jellyfin covered exactly 1,270 of
27,921 units with it and without it. What it removed was log volume.

**Rate-limiting the stall report.** Looked harmless across five 900-second waves.
At 3600 seconds the suppression window is 90 seconds rather than 22.5, which
parks the loop in the frontier's no-eligible-candidate path; And-Bible ended at
21% of its budget.

The spin those two were aiming at is real, and v1 hid it. Stalls fired on almost
every v1 action, so recovery ran through the stall path. With novelty counted
correctly the stall is quiet, which leaves the frontier path driving recovery
with no brake. A barren reset now pauses two seconds and spends no reset budget,
which lets `retry_cooldown_seconds` expire and ordinary exploration resume.

## Gap 4, and why steering on discovery is not the answer

The obvious fix for "coverage arrives in a handful of moments" is to prefer
actions that reach unexplored states. `FrontierStore._verdict` already carries a
measurement of that experiment: weighting discovery highly enough to outrank a
merely screen-changing action was tried and was actively harmful, taking AnkiDroid
from 13.51% to about 5.4% across four runs, reaching 6 screens instead of 15.
Screens correlate with coverage; steering directly on the correlation does not
reproduce it.

Zero-gain actions moved only from 91.4% to 89.5% in v2, so this gap is genuinely
open. What produced the v2 gain was not making individual actions smarter but
stopping the ladder from consuming the budget. Anyone attacking gap 4 next should
treat the zero-gain share as a symptom and not as the thing to optimise, and
should read the AnkiDroid note in `frontier.py` first.

## Gap 8, answered without touching the loop

The design was to instrument the loop and then tune `post_action_delay_seconds`.
Instrumenting that path is not free -- the last thing added to it, a logcat probe,
cost measured coverage -- and it was not necessary, because every timing is
already retained. `tools/throughput_breakdown.py` derives the split from
`actions.jsonl` and `model_calls.jsonl`: dispatch from `execution`, the settle
window from `window_ended_at`, model selection from the call records, and
everything else from the gap between one attempt's window closing and the next
one opening.

Measured across five 60-minute runs:

| run | actions/min | dispatch | settle | model | observe + rank |
|---|---|---|---|---|---|
| Sunflower | 17.1 | 1.1% | 34.2% | 4.3% | 60.3% |
| Muzei | 10.8 | 1.1% | 21.7% | 4.0% | 73.3% |
| Kore | 9.6 | 1.1% | 19.2% | 2.4% | 77.4% |
| And-Bible | 9.0 | 2.0% | 18.0% | 10.6% | 69.3% |
| ownCloud | 8.8 | 1.1% | 17.6% | 10.0% | 71.3% |

`post_action_delay_seconds` was never the lever. Observation -- the UIAutomator
dump and its parse -- is 60 to 77% of the wall clock, and the coverage
attribution window (`association_settle_seconds`, 2.0) is another 18 to 34%.
Dispatching the action itself is one percent, and the model is under five percent
except where a run makes few actions.

Both real levers trade against evidence quality rather than against comfort. A
shorter settle window weakens attribution of coverage to the action that caused
it. Fewer or cheaper dumps weaken the state abstraction every other mechanism
reads. Neither should be changed without deciding what the measurement is allowed
to lose, which is why this is left as a measurement rather than a tuning.

## Gap 9, and what is safe to change

The harm is fixed: the driver waited for disk instead of skipping an app, which
is the single reason v1 covered 49 of 50.

The growth is not. Each coverage event carries the full cumulative set plus seven
per-unit maps -- 485 KiB at 431 units, of which the maps are 89% -- so a run's
ledger is O(samples x units) and StreetComplete reached 4.3 GB. Delta encoding
would be exactly lossless, verified on a real run: across 187 events the observed
set never shrank and not one per-unit value ever changed after first observation.
It is not done because `transactions.py` deliberately cross-checks the coverage
sample's maps against the event's copy field by field, and that mutual redundancy
is what makes the evidence self-checking. Re-encoding it changes the evidence
contract and wants its own validation.

## Campaign-level faults, which were not in the gap list at all

These cost more measured apps than several of the nine gaps did.

**The published branch could not be imported.** Four modules that tracked code
depends on were never committed; `validator.py` referenced them 76 times. Any
fresh clone failed on `import valordroid.runner`, so v1's tag could not reproduce
v1's numbers.

**A dead framework was blamed on apps.** `sys.boot_completed` is 1 and adb answers
normally while the package service is still unpublished, so every install fails
with `cmd: Can't find service: package`. One lane wrote off six apps in nineteen
seconds as app faults.

**Stopping a campaign orphaned its device owners.** A `run-android` child holds
its device's flock while it lives, and `pkill` on the driver does not match the
children. One orphan owned a device for 45 minutes.

**A bundle was selected for universe size over whether it runs.** Amaze's smali
bundle has 3,371 units against androlog's 3,307 and dies on launch with a
`UsbManager` null dereference. Two campaign runs were lost before the bundle was
swapped.

## How results should be compared

Chess is bimodal at 26% or 43-47% run to run, with code held constant and devices
swapped. v1 reported one run per app per model, so per-app differences of that
size are not evidence of anything. Compare per app, expect noise of that order on
game-like apps, and prefer repeated runs over a single pair.
