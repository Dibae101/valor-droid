# Gaps across all 49 measured apps, including the best ones

`GAPS.md` explains why the low apps are low. This is a different sweep: for every
app that produced a measurement, where did its budget actually go, and what stopped
it going further. It covers the apps at 40-68% as well, because those turn out to
share most of the same waste. Numbers come from `analysis/full-gap-sweep.json`,
derived from retained run evidence only.

## The headline

    mean coverage                      28.1%
    mean actions that gained nothing   91.4%
    mean actions with no UI effect     36.0%
    mean actions that were Back        20.8%
    mean actions inside the app        66.2%
    mean throughput                    10.8 actions per minute
    mean GUI+deep-link coverage        16.5%
    mean coverage from other routes    11.6 points

44 of 49 apps spend more than 80% of their actions gaining nothing. This is not a
property of the blocked apps: **Sunflower, the best result in the campaign at
68.29%, gains nothing on 98.8% of its actions.** Coverage arrives in a handful of
moments and the rest of the hour is overhead.

## 1. Almost every action gains nothing

| App | Coverage | Zero-gain actions | Gaining actions |
| --- | ---: | ---: | ---: |
| Sunflower | 68.29% | 98.8% | 11 of 908 |
| Commons | 15.08% | 99.2% | 7 of 930 |
| OwnTracks | 28.85% | 97.8% | 18 of 800 |
| Muzei | 44.16% | 97.4% | 17 of 651 |
| Chess | 53.58% | 92.4% | 57 of 754 |

A run of 900 actions can be carried by fewer than 20 of them. That is the single
biggest efficiency gap in the tool and it affects every app regardless of score.
Worth knowing before optimising: method coverage is a coarse unit, so once a method
has been entered, re-entering it correctly gains nothing. Some of this is inherent.
The question for v2 is what fraction is inherent and what fraction is the explorer
repeating itself.

## 2. A third of all actions do not even change the screen

`no_effect` means the action dispatched and the UI did not change.

| App | Coverage | No-effect actions |
| --- | ---: | ---: |
| AnkiDroid | 25.30% | 81.8% |
| Vespucci | 25.87% | 79.0% |
| FeederD | 43.07% | 69.6% |
| Fedilab | 5.55% | 66.9% |
| Scarlet-Notes-FD | 39.51% | 60.2% |
| Money-Manager-Ex | 34.27% | 54.2% |
| Binary-Eye | 33.96% | 52.0% |

Mean 36.0%. These are dispatched actions that achieved nothing observable, so they
are pure loss, unlike zero-gain actions which at least move the app. Candidate
ranking already tracks effectfulness; something is still selecting inert controls
repeatedly.

## 3. A fifth of the budget is Back

| App | Coverage | Back share |
| --- | ---: | ---: |
| Twire | 2.80% | 50.0% |
| Geohash-Droid | 10.39% | 42.3% |
| AnkiDroid | 25.30% | 40.5% |
| Vespucci | 25.87% | 38.0% |
| Kore | 4.33% | 37.8% |
| Muzei | 44.16% | 36.3% |
| Transistor | 31.21% | 34.8% |
| OwnTracks | 28.85% | 33.9% |

26 of 49 apps spend more than 20% of their actions pressing Back. Back is rung 2 of
the recovery ladder, so this is the ladder hammering rather than the explorer
choosing. Sunflower spends 30% of its actions on Back and still scores highest,
which suggests Back is not harmful so much as it is the default when nothing better
is offered.

## 4. The stall signal is effectively always on

This is the finding that explains the three above.

    apps where stall events exceed the number of actions taken:  23 of 49

    Sunflower           908 actions, 1100 stall events   (1.21 per action)
    Muzei               651 actions, 1092 stall events   (1.68)
    OpenLauncher        883 actions,  999 stall events   (1.13)
    Simple-Alarm-Clock  694 actions,  700 stall events   (1.01)

The detector treats only a fresh associated unit as progress. Since 91.4% of
actions gain nothing, the run is "stalled" almost continuously, so `_explore` hands
control to the recovery ladder on most iterations and ordinary candidate selection
rarely runs. And-Bible showed the extreme: 848 of 851 attempts came from recovery.

That is why the ladder dominates, why Back is a fifth of the budget, and why the
ladder-reset counter was being exhausted. The threshold is mis-calibrated against
how often coverage actually moves. A stall should mean "this screen has stopped
paying", not "the last action did not add a method".

More stall events than actions also means loop iterations that produce no action at
all, which is budget spent on deciding rather than acting.

## 5. GUI exploration is the weaker half

    mean GUI+deep-link coverage   16.5%
    mean from other routes        11.6 points

For some apps the routes carry most of it:

| App | Total | GUI+deep-link | From exported components |
| --- | ---: | ---: | ---: |
| Simple-Alarm-Clock | 58.62% | 27.6% | 31.0 pts |
| FeederD | 43.07% | 15.5% | 27.6 pts |
| Transistor | 31.21% | 7.9% | 23.3 pts |
| Phonograph-DEBUG | 22.01% | 2.9% | 19.1 pts |
| MaterialFBook | 24.28% | 5.6% | 18.7 pts |

Launching public components is doing work the GUI never reaches, which is a
strength of the design. It also says the GUI half is underperforming: for
Phonograph, 19 of its 22 points come from launching activities rather than from
using the app.

## 6. Nineteen apps still spend most actions outside the app

| App | Coverage | Actions inside the app |
| --- | ---: | ---: |
| Trackbook | 16.35% | 0.0% |
| A-Photo-Manager | 13.02% | 1.9% |
| Phonograph-DEBUG | 22.01% | 2.6% |
| And-Bible | 12.66% | 3.5% |
| MaterialFBook | 24.28% | 6.4% |
| Frost-Debug | 9.02% | 9.6% |
| Muzei | 44.16% | 11.5% |
| Geohash-Droid | 10.39% | 12.5% |
| Kore | 4.33% | 13.4% |
| AnkiDroid | 25.30% | 15.5% |
| Transistor | 31.21% | 15.9% |
| Nextcloud | 27.12% | 16.2% |

Most of these ran before the containment fix, which took And-Bible from 3.5% to
81.6%, so this list should shrink on its own in v2. It has to be re-measured rather
than assumed. Note Muzei at 44.16% with 11.5% in-app: it scores well *because* its
coverage comes from routes, not from the app being explored.

## 7. Throughput

    mean 10.8 actions per minute, roughly 5.5 seconds per action

Slowest among apps that ran a full hour: Amaze 5.5, My-Expenses 7.9, Ultrasonic
7.8, FeederD 8.4, Wikipedia 8.4, StreetComplete 8.5. Fastest: Frost 15.6,
Commons 15.5, Sunflower 15.1. The baseline tools report medians of 670, 588 and
1,088 actions per hour, so HybridDroid does roughly twice our volume. Each action
costs two observations, a post-action delay and several adb round trips; the split
between those has not been measured.

## 8. Six apps did not use their budget

    Twire        6% of budget, 34 actions
    Jellyfin     6% of budget, 38 actions
    WordPress   15% of budget, 63 actions
    Nextcloud   66% of budget
    Fedilab     85% of budget
    Kore        96% of budget

Twire, Jellyfin and WordPress were ended by the crash-relaunch ceiling and Fedilab,
Kore and Twire by the ladder-reset cap. Both were fixed after this campaign, so v2
should show these using their full hour. Jellyfin's 11 crashes and My-Expenses'
18 crashes remain unexplained: whether they are ours is unknown, and the
uninstrumented-APK check that exonerated five apps would settle it.

## 9. Trackbook is a category of its own

    4 actions in 61 minutes, 0.1 actions per minute, 0.0% inside the app

Its screen can never be dumped, so nothing else could happen. Covered in `GAPS.md`.

## What this changes about the v2 plan

`GAPS.md` ranks the work by unmeasured methods behind a wall, which is still the
largest single prize. This sweep adds a second axis that applies to every app,
including the healthy ones:

1. **Recalibrate the stall signal.** It is the root cause of items 3, 4 and much of
   2, and it is why the recovery ladder rather than the explorer drives most runs.
   Nothing else on this list is worth tuning until this is right.
2. **Stop selecting inert controls.** A third of the budget produces no UI change.
3. **Measure where the 5.5 seconds per action goes** before trying to make it
   faster.
4. Re-measure the in-app share now that containment is in, rather than assuming the
   fix carried across all nineteen apps.
