# Strategy for v2

> **Outcome recorded in [V2-OUTCOME.md](V2-OUTCOME.md).** Of the eight items
> below, two landed as designed (1 and 3), three were implemented and reverted
> on measurement (2, and two attempts at the recovery spin), one had a wrong
> diagnosis (4), and three were not attempted (5, and gaps 4 and 5 of
> GAPS-AND-SOLUTIONS). Item 8's requirement, that v2 be comparable to v1, is
> weaker than assumed: Chess is bimodal at 26% or 43-47% with code held
> constant, so single-run per-app comparison cannot resolve differences of that
> size.

This is a plan, not a result. Every item states what the evidence is, what the
options were, which one is chosen and why, and how we will know whether it worked.
Where a hypothesis could be wrong, that is written down rather than smoothed over.

Evidence: `reproduction/results/valordroid-50app-60m-v1/GAPS.md` for the apps that
score low, and `GAPS-ALL-APPS.md` for the waste that every app shares.

## Should this document exist yet?

Partly. Items 1 to 4 are diagnoses with a clear causal chain, so committing to a
direction now is what stops v2 becoming another round of guessing. Items 5 and 6
are hypotheses about cost and yield that need a measurement first, and they say so.
The document to write *after* iterating is the results comparison, not the plan.

The risk in planning this early is anchoring: item 1 says the stall signal is
mis-calibrated, and if that turns out to be wrong then items 2 and 3 need
rethinking too, because they are predicted to follow from it. Item 1 therefore has
an explicit falsification test.

## Ordering principle

Two axes compete. `GAPS.md` ranks by unmeasured methods behind a wall, which is
about 70,000 and the single largest prize. `GAPS-ALL-APPS.md` ranks by waste inside
runs that already work, which affects all 49 apps.

The waste comes first, for one reason: the wall work multiplies whatever the
explorer does once it is past the wall. Unlocking Jellyfin's 27,312 methods with an
explorer that gains nothing on 98.8% of its actions banks a fraction of what it
could. Fix the engine, then open the doors.

The exception is item 4, which is cheap enough to do alongside anything.

---

## 1. The stall signal, which is the root cause

**Evidence.** 23 of 49 apps record more stall events than actions taken. Sunflower
reaches 1,100 stalls across 908 actions while scoring the campaign's best 68.29%.
And-Bible ran 848 of its 851 attempts through the recovery ladder.

**Diagnosis.** The detector counts only a fresh associated unit as progress. With
method coverage, gain is monotone and saturating: once a method has been entered,
entering it again gains nothing, so "no new method" becomes the permanent state
within minutes. Using it as the progress signal guarantees a permanent stall, which
hands control to the recovery ladder on most iterations and means ordinary candidate
selection rarely runs. Back being a fifth of the budget and the reset counter being
exhausted are both downstream of this.

**Options considered.**

*Raise `stall_actions`.* One line, and wrong. It delays the same failure and makes
the threshold arbitrary; with 91.4% zero-gain actions any fixed threshold is
eventually crossed and never uncrossed.

*Treat structural change as progress.* A stall becomes "the UI stopped responding to
us", not "coverage stopped moving". Cheap and closer to what recovery is for, but it
would call an app that cycles between two screens forever productive.

*Per-screen productivity, with a separate global plateau.* Track whether *this
screen* still has untried or productive candidates. Local exhaustion means "leave
this screen", which is what rungs 1 and 3 do. A global plateau, meaning no screen
anywhere has work, means "reach for a public route or reset", which is what rungs 4,
5 and 8 do. The two triggers are currently collapsed into one signal, which is why
the ladder answers a local problem with a global remedy.

*Coverage-derived novelty.* Count progress as reaching a new state or executing a
candidate never executed before, rather than as new methods. Composes with the
option above and is what the frontier already records.

**Chosen: per-screen productivity plus a global plateau, with novelty as the
progress unit.** It matches the structure the recovery ladder already has, it uses
data the frontier already keeps, and it makes the two rung groups fire for the
reasons they were designed for.

**Falsification test.** If this is right, the ladder's share of attempts falls
sharply, Back's share falls with it, and no-effect actions fall because ordinary
ranking is doing the selecting. If the ladder's share stays high after the change,
the diagnosis is wrong and items 2 and 3 need revisiting independently.

**Risk.** Recovery currently rescues runs by accident: Back happens to be a cheap
way out of a dead end. Suppressing it may lower coverage on some apps before the
better path is in place. Compare per app, not on the mean, and keep Chess,
Sunflower and Simple-Alarm-Clock as controls since they are healthy today.

---

## 2. Actions that change nothing

**Evidence.** Mean 36.0% of actions record `no_effect`. AnkiDroid 81.8%, Vespucci
79.0%, FeederD 69.6%.

**Diagnosis.** Partly downstream of item 1: the ladder selects with less information
than ordinary ranking. The rest is inert controls being chosen repeatedly. The
frontier already counts effects per candidate, so the information exists and is not
being used decisively.

**Options.** Penalising inert candidates in scoring is the smallest change but only
reorders them, so an app whose screen is mostly inert still spends its budget there.
Suppressing a candidate after two executions with no effect is nearly as cheap and
actually removes it; the existing zero-gain rule already establishes that a
suppression cap works. Filtering at construction on `enabled` and `clickable` is
free but only catches the honest cases, since a control can be enabled and still
inert.

**Chosen: suppress after two no-effect executions, plus the free construction
filter.** Deliberately not a scoring tweak, because reordering does not stop a
budget being spent.

**Watch for.** A control that is inert until some state changes elsewhere. The
suppression must be per screen identity so the same control on a later screen is
reconsidered.

---

## 3. Back presses

**Evidence.** Mean 20.8%, 26 of 49 apps above 20%, Twire at 50%.

**Diagnosis.** Back is rung 2 and fires because the ladder is running constantly.
Predicted to fall out of item 1 without separate work.

**Chosen: fix item 1, then re-measure.** A direct cap on Back's share is held in
reserve, because capping a symptom whose cause is still present usually moves the
waste somewhere else. Sunflower spends 30% of its actions on Back and scores best in
the campaign, so Back is not intrinsically harmful.

---

## 4. Apps that cannot be observed at all

**Evidence.** Trackbook: 4 actions in 61 minutes, 0.0% of actions inside the app,
`gui+deeplink` 0.00%. Vinyl-Music-Player: no measurement. Both return
`could not get idle state` for every dump attempt.

**Diagnosis.** `adb shell uiautomator dump` waits for the window to go idle. A
continuously redrawing view never does. Confirmed to be independent of location
permission, and the launcher on the same device dumps fine.

**Options.**

*Custom instrumentation APK.* `Configurator.setWaitForIdleTimeout(0)` with
`UiDevice.dumpWindowHierarchy()` gives a real UIAutomator hierarchy with no idle
wait. Highest fidelity, and it keeps selector-based dispatch intact. Costs an APK to
build, sign, install and version per device.

*`dumpsys activity top`.* Verified on Trackbook's screen: 50 nodes, 8 clickable,
with resource ids and bounds, while `uiautomator dump` failed on the same frame. No
build, no install, available today. Different format, and no content-description or
tree path, so selectors are weaker.

*Freeze the process and dump.* Tried and it does not work: a stopped app returns
`null root node returned by UiTestAutomationBridge`.

*Give up and drive routes.* Works only where public routes exist. Trackbook has no
deep links and no exported components, so it would stay unmeasured.

**Chosen: `dumpsys activity top` as a fallback source, with the instrumentation APK
as the fallback's fallback.** It is available now and it is enough to unblock two
apps.

**The real cost is dispatch, not observation.** Actions are dispatched by exact
UIAutomator selector, which also needs a working dump, so a dumpsys-derived
candidate has to be tapped at coordinates from its bounds. That splits the evidence
model into selector-matched and coordinate-matched actions and touches the validator
and replay. Do it deliberately: mark such actions with their own provenance so a
reader can tell which actions were selector-verified, and never let a
coordinate-tapped action be replayed as if it were selector-matched.

---

## 5. Throughput

**Evidence.** Mean 10.8 actions per minute, about 5.5 seconds per action. The
baseline tools report medians of 670, 588 and 1,088 actions per hour, so HybridDroid
does roughly twice our volume.

**Hypothesis, not a diagnosis.** Each action costs two observations, a
post-action delay and several adb round trips. Which of those dominates has not been
measured, and the honest answer is that we do not know yet.

**Chosen: measure first.** Instrument the loop to record time in dump, in parse, in
dispatch, in delay, and in adb round trips, then decide. Candidate levers, in
increasing risk: reuse the after-observation of one action as the before-observation
of the next if that is not already done; reduce `post_action_delay_seconds` from 0.6
with a per-app check that effects are still detected; batch adb calls.

**Explicitly not chosen: more actions as an end in itself.** More actions at 91.4%
zero-gain buys proportionally little. Throughput matters after item 1, not before.

---

## 6. Apps behind a backend or an account

**Evidence.** Nine apps, about 70,000 unmeasured methods. Jellyfin at 2.18% with
94.7% of its actions inside the app: stopped, not lost.

**Options.**

*Self-host the backend.* Real servers we own, no third-party terms, deterministic
and repeatable. Jellyfin, WordPress, Nextcloud, ownCloud, ODK Central and Kodi all
publish images. A Jellyfin server was already stood up during v1, provisioned
through its own Startup API, authenticating, and reachable from the devices.

*Public demo servers.* Less work, but they change without notice and make a run
depend on someone else's uptime.

*Third-party accounts.* Twitch and Facebook cannot be self-hosted. Automating
account creation would breach their terms, so those three apps are out of scope
unless someone supplies credentials they own.

*Patch the auth check in the app.* Would work and is rejected. The result would
describe a modified program, and the whole point of the fixed universe is that the
number describes the app as shipped. If it is ever done it belongs in a separate
column, as rung 7 already is.

**Chosen: self-host, driven by the setup profiles that already exist.**
`setup_profile.py` already supports `input_secret`, an `oauth-login-and-restart`
profile, secrets from the environment, `allowed_foreign_packages`, and a check that
seeded state survives a real process restart. It has never been used in a campaign.
This is the cheapest item on the list relative to what it unlocks.

**Known obstacle.** Jellyfin's connect screen exposes only `TextView` nodes with no
`EditText`, so a profile has nothing to type into. Either seed the server address
into app storage before launch, or see whether item 4's idle-free dump exposes the
field. Try item 4 first, since it is needed anyway.

---

## 7. Evidence that grows with samples times units

**Evidence.** StreetComplete's coverage ledger reached 4.3 GB, OwnTracks 1.6 GB.
The host disk filled during v1 and nineteen runs were lost.

**Current state.** Runs are compacted once their summary is captured, which
reclaimed 93% on StreetComplete. That is a mitigation and its limit is known:
validating the uncompressed 4.3 GB still cost twenty minutes and 3.2 GB of memory,
because compaction runs after the summary by design.

**Options.** Recording observed units as a delta against the previous sample fixes
it at the source and changes the codec, the validator and replay. Compressing on
write avoids the schema change but complicates append-only writes with fsync.
Sampling less often loses the timeline that makes attribution possible.

**Chosen: delta encoding, scheduled but not first.** It is the correct fix and it is
invasive, so it should not be entangled with the exploration changes above.
Compaction holds the disk in the meantime. If a v2 campaign is planned before this
lands, budget the disk for it: v1 needed more than 130 GB.

---

## 8. Making v2 comparable to v1

v1 is not internally uniform. Fixes landed while it ran and the driver spawns a
fresh process per run, so early and late runs came from different code. Only the
nine runs in `final50-missing` used the final code, and they averaged 36.44% on a
smaller and different app mix, which is suggestive and not a result.

**For v2:** freeze the code, tag the commit, record the tag in every run manifest,
and run all 50 apps on both models in one pass. Compare per app against v1 rather
than on the mean, because the app mix dominates the mean. Keep Chess, Sunflower and
Simple-Alarm-Clock as controls: they are healthy today, and if they drop then a
change meant to help elsewhere has cost something here.

## Order of work

1. Stall signal, with its falsification test. Nothing below is worth tuning while
   the ladder rather than the explorer drives most runs.
2. No-effect suppression, which is predicted to shrink once 1 lands and should be
   measured before and after.
3. Idle-free observation, which unblocks two apps and may unblock Jellyfin's input.
4. Setup profiles plus self-hosted backends, which is where the 70,000 methods are.
5. Throughput, measured before changed.
6. Delta-encoded ledger, on its own, not mixed with the above.
7. One frozen campaign into a fresh results folder, compared per app against v1.

## What would make this plan wrong

- If the ladder's share of attempts stays high after item 1, the causal chain from
  item 1 to items 2 and 3 is broken and each needs its own diagnosis.
- If no-effect actions stay near 36% after items 1 and 2, then inert controls are
  being generated rather than merely chosen, and candidate construction is the
  problem.
- If self-hosted backends do not move the nine blocked apps, the wall is not the
  binding constraint there and something else is stopping them after login.
- If the controls drop, a change has traded healthy apps for blocked ones, which is
  not a trade worth making at this stage.
