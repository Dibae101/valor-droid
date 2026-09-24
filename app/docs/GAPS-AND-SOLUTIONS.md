# Gaps, the research on them, and the solutions we should build

> **Read [V2-OUTCOME.md](V2-OUTCOME.md) first.** These designs have since met the
> devices. Two diagnoses below are wrong -- gap 7 entirely, and Jellyfin's connect
> screen within gap 6 -- three of the fixes were implemented and then reverted on
> measurement, and four campaign-level faults that cost more measured apps than
> several of these gaps were not in this list at all. This document is kept as
> written so the reasoning can be compared against what happened.


Each gap below is a measurement from the v1 campaign, followed by what the
literature does about it, followed by a design. Every design is checked against one
hard requirement, because it is the requirement most easily violated by accident:

> **It must work on an APK nobody has looked at.** No per-app selectors, no
> hardcoded activity names, no manually recorded scripts. If a solution needs a
> human to study an app first, it does not scale to a dataset that grows.

Where a design fails that test, it is marked and confined to per-app fixture
configuration rather than the engine.

Sources are linked. Their reported numbers are their own; we have not reproduced
them.

---

## Gap 1 — "Stalled" is the permanent state, so the ladder drives the run

**Measured.** 23 of 49 apps record more stall events than actions taken. Sunflower:
1,100 stalls across 908 actions, while scoring the campaign's best 68.29%.
And-Bible ran 848 of 851 attempts through the recovery ladder. Downstream: 20.8% of
all actions are Back, 36.0% change nothing.

**Why.** Progress is defined as "a method was covered that wasn't before". Method
coverage saturates: re-entering a covered method gains nothing, so within minutes
the honest answer to "did we gain?" is permanently no. A progress signal that can
only fire on novelty, against a metric that saturates, degenerates into always-on.

**What the research does.** Nobody uses raw coverage delta as the exploration signal.
[APE (ICSE 2019)](https://www.cbafloridainc.com/yuanyao/static/icse2019.pdf) drives
exploration from a *model* of GUI states and refines the abstraction when the model
stops discriminating usefully; it reports beating the then state of the art on both
coverage and unique crashes across 15 Play Store apps.
[Fastbot2](https://github.com/chongchong01/Fastbot_Android), ByteDance's production
tool, states its "GUI state abstraction is achieved with reference to project APE",
so the same idea survived contact with industry.

An [empirical study across exploration strategies and state
abstractions](https://arxiv.org/abs/2606.16650) adds the part that matters for us:
fine-grained abstractions suit model-based strategies, compact ones suit
reinforcement-learning strategies. Our abstraction is fine-grained — activity plus
in-package resource and class skeleton — and our strategy is model-based, so the
abstraction is already the right shape. **The defect is the progress signal, not the
state identity.** That is worth stating clearly because the tempting fix is to
change the abstraction, and the research says ours is appropriate.

**Solution.** Replace one global coverage-derived signal with two model-derived ones.

*Local exhaustion, per screen.* A screen is done when every candidate on it has been
executed and none is still productive. That is a question about the frontier, which
already records per-candidate executions, effects and yield. Local exhaustion means
"leave this screen" and should trigger rungs 1 and 3, which navigate.

*Global plateau.* Every reachable screen is locally exhausted and no new screen has
appeared for some interval. Only then reach for public routes and reset, rungs 4, 5
and 8. Currently one signal triggers both groups, which is why a local problem gets
a global remedy.

*Progress becomes novelty, not coverage.* A new state identity, or a candidate
executed for the first time, counts as progress. Both are already recorded.

**Generalises?** Yes. Nothing app-specific: it reads the frontier and the state
graph the tool already builds for every app.

**Risk.** Recovery currently rescues runs by accident, and Back is a cheap escape.
Suppressing it may cost coverage on some apps before better navigation exists.

**Validation.** The ladder's share of attempts falls sharply; Back's share falls with
it. If the ladder's share stays high, this diagnosis is wrong and gaps 2 and 3 need
separate treatment.

---

## Gap 2 — A third of actions change nothing

**Measured.** Mean 36.0% of actions record `no_effect`. AnkiDroid 81.8%, Vespucci
79.0%, FeederD 69.6%, Fedilab 66.9%.

**Why.** Partly gap 1: the ladder selects with less information than ordinary
ranking. The rest is inert controls chosen repeatedly, even though the frontier
already counts effects per candidate.

**What the research does.** This is what state abstraction refinement is for. In
APE's terms, an action that produces no state change is evidence the model's
abstraction is not distinguishing anything useful there, and the response is to stop
treating it as a frontier edge.

**Solution.** Suppress a candidate after two executions that produced no effect,
keyed by screen identity so the same widget is reconsidered on a different screen.
This mirrors the existing zero-gain rule, which already establishes that a bounded
suppression cap is safe and auditable. Also filter at construction on `enabled`,
which is free.

Deliberately *not* a scoring penalty: reordering an inert control still leaves it in
the pool, and an app whose screen is mostly inert will still spend its budget there.

**Generalises?** Yes. Effect is observed per action on every app.

**Risk.** A control inert until state changes elsewhere. Screen-keyed suppression
handles the common case; a control that becomes live on the *same* screen identity
would be wrongly suppressed. Bound the suppression to the current screen visit
rather than the whole run if that shows up.

---

## Gap 3 — A fifth of the budget is Back

**Measured.** Mean 20.8%. Twire 50.0%, Geohash 42.3%, AnkiDroid 40.5%. 26 of 49 apps
above 20%.

**Why.** Back is rung 2, and the ladder runs constantly because of gap 1.

**Solution.** None directly. Predicted to fall out of gap 1, then re-measured. A cap
on Back's share is held in reserve, because capping a symptom whose cause is present
moves waste elsewhere. Note Sunflower spends 30% of actions on Back and scores best
in the campaign, so Back is not intrinsically wasteful — it is the default when
nothing better is offered.

---

## Gap 4 — Coverage arrives in a handful of moments

**Measured.** 91.4% of actions gain nothing. 44 of 49 apps exceed 80%. Sunflower is
carried by 11 gaining actions out of 908; Commons by 7 out of 930.

**Why.** Partly inherent: with method coverage, the second visit to a screen gains
almost nothing by construction. Partly real: the explorer re-treads shallow screens
instead of reaching new functionality.

**What the research does.** This is the most active area, and two recent results
address exactly the "we are exploring but not reaching anything new" problem.

[EpiDroid](https://arxiv.org/abs/2604.01522) recomposes dependencies between
observed interactions to reach *deep* states, and reports 10–28% higher average code
coverage across baselines and **3–4× more coverage gain than continuing the same
baseline from the same starting point**. That last comparison is the interesting one:
the extra coverage came from recomposition, not from more time.

[FuncDroid](https://arxiv.org/abs/2602.12834) models inter-functional flows rather
than general exploration and reports +28% coverage and +107% bugs found, on the
argument that general exploration misses critical functionality precisely because it
has no notion of a task spanning screens.

[SceneDroid](https://arxiv.org/html/2308.10228v1) models GUI *scenes* and transitions
rather than flat states, reporting large gains in transition coverage.

**Solution, staged.** The full EpiDroid or FuncDroid design is a research project.
What is adaptable now, and cheap, is the underlying insight: prefer actions that
lead somewhere not yet reached over actions on the screen in front of us.

*Stage one.* Rank candidates by whether their state has unexplored outgoing edges,
using the existing state graph, and prefer navigating toward screens with untried
candidates over re-tapping the current screen. The tool already computes
`worthwhile_states` and navigation plans; this raises their priority relative to
local candidates.

*Stage two, later.* Replay a prefix that reached a productive state, then diverge —
which is the recomposition idea, and needs replayable action sequences that the
route store already retains.

**Generalises?** Yes, both stages read only the recorded graph.

**Caution.** Do not chase this metric for its own sake. Some of the 91.4% is the
metric saturating, and no design removes that. The honest target is the *ratio* of
new-state visits to actions, not the zero-gain percentage.

---

## Gap 5 — GUI exploration is the weaker half

**Measured.** Mean GUI+deep-link coverage 16.5%; mean 11.6 points from launching
public components. For Phonograph, 19 of its 22 points come from launching
activities rather than from using the app. Simple-Alarm-Clock: 31 of 58.62.

**What the research says.** This is a known and legitimate strategy, not a
workaround. [Delm](https://arxiv.org/abs/2404.19307) integrates deep links into
Monkey and mocks activity contexts to reach activities with hidden entrances,
reporting +27.2% activity and +21.13% method coverage. We already do both — the
activity-context mock-up behind `--no-mock-activity-context` is the same idea.

**Solution.** Keep it, and let gap 1 fix the timing. Component launching is currently
gated until 60% of the budget has elapsed, which was tuned because launching early
displaced productive GUI work. With a correct global-plateau signal the gate becomes
unnecessary: routes fire when the GUI is genuinely exhausted, whenever that is.
Replace the time gate with the plateau condition and keep the time gate as a
fallback.

**Generalises?** Yes. Targets come from the manifest of whatever APK is supplied.

---

## Gap 6 — Nine apps stopped by a backend or an account

**Measured.** About 70,000 unmeasured methods. Jellyfin 2.18% with 94.7% of its
actions *inside* the app: stopped, not lost. WordPress 8.04%, Fedilab 5.55%,
Twire 2.80%, Kore 4.33%.

**What the research does.** Less than one might hope. The classic surveys, including
[Automated Test Input Generation for Android: Are We There
Yet?](https://arxiv.org/abs/1503.07217) and the [industrial
follow-up](https://sos-vo.org/system/files/sos_files/Automated_Test_Input_Generation_for_Android_Are_we_Really_There_Yet_in_an_Industrial_Case.pdf),
treat login as an environmental precondition rather than something the tool solves.
The active work is on *text input*, not credentials: [Large Language Models for
Mobile GUI Text Input Generation](https://arxiv.org/html/2404.08948) studies how UI
context, execution feedback and human intervention affect LLM-generated input, which
helps a form be filled plausibly but cannot invent a valid account.

So the literature does not have a trick here, which matches what we found.

**Solution, in two parts with different scaling properties.**

*Engine side, generalises.* Detect a credential or server-configuration screen and
apply a bounded, recorded budget, then leave it and continue elsewhere. This already
exists and works: the seven apps that hit an *optional* login abandoned it after six
actions and went on to average 34.03%, the highest of any category. Nothing to build.

*Fixture side, does not generalise, and that is acceptable.* A real backend we own,
plus a setup profile. `setup_profile.py` already supports `input_secret`, an
`oauth-login-and-restart` profile, secrets from the environment,
`allowed_foreign_packages`, and a check that seeded state survives a real process
restart. It has never been used in a campaign. Jellyfin, WordPress, Nextcloud,
ownCloud, ODK Central and Kodi all publish server images; a Jellyfin server was
already stood up in v1, provisioned through its own Startup API, authenticating, and
reachable from the devices.

**This part is per-app by nature.** A new APK needing an account will need a fixture.
The engine must degrade gracefully — measure what is reachable and report the wall —
rather than depend on a fixture existing. That is what it does today.

**Rejected: patching the auth check.** Technically easy. The result would describe a
modified program, which defeats the fixed-universe guarantee. If ever used, it
belongs in a separate column, as rung 7 already is.

**Out of scope.** Twitch and Facebook cannot be self-hosted, and automating account
creation would breach their terms. Twire, Frost and MaterialFBook stay blocked unless
someone supplies credentials they own.

---

## Gap 7 — Screens that can never be dumped

> **Corrected in v2. The diagnosis in this section is wrong; it is kept because
> the reasoning error is worth seeing.** Both named apps dump perfectly well when
> asked. Vinyl-Music-Player had no v1 measurement because the driver *skipped* it
> at the disk floor ("only 0 GiB free"), not because of any observation problem,
> and it reached 19.79% the first time it was given a device with room.
> Trackbook's device was wedged by a leaked UiAutomation registration,
> `IllegalStateException: UiAutomationService ... already registered!`, left by an
> earlier dump that died without unregistering. One framework restart cleared it
> -- 0 of 3 dumps before, 3 of 3 after -- and Trackbook then measured 18.37%
> against 16.35% in v1.
>
> The error was reading `could not get idle state` as proof of a continuously
> redrawing view without testing the alternative. Animations were already
> disabled fleet-wide, which should have been the clue. No idle-free dump path
> was needed, and the `dumpsys activity top` fallback designed below was never
> built: its bounds are parent-relative and would have needed offset accumulation
> and its own dispatch provenance to be trustworthy.
>
> One attempt here was withdrawn. The wedging exception usually reaches only
> logcat, because `uiautomator dump` crashes in its own process, so a probe read
> logcat to recognise it. The coverage collector holds a long-lived streaming
> `adb logcat`, and a second reader on the same transport stalls it until the ring
> buffer overwrites unread `METHOD=` lines. Chess measured 42.72% without the
> probe and 23.70% with it on identical exploration -- coverage executed but no
> longer recorded. Nothing in the tool may compete with the collector for logcat.
> What survives is the useful half: when the shell itself reports the leak, the
> framework is restarted on the first failure instead of the fourth.


**Measured.** Trackbook: 4 actions in 61 minutes, 0.0% of actions in-app,
`gui+deeplink` 0.00%. Vinyl-Music-Player: no measurement at all. Both return
`could not get idle state` on every attempt, compressed and uncompressed,
independent of location permission, while the launcher on the same device dumps
fine.

**Why.** `adb shell uiautomator dump` waits for the window to become idle. A
continuously redrawing view never does. Confirmed by
[practitioner reports](https://stackoverflow.com/questions/25201743/error-in-using-uiautomatorviewer-for-testing-android-app-in-appium/44418874):
any painting action prevents the dump.

**What the research and tooling do.** Two established routes.
[AndroidDumpUI](https://github.com/sccjava/AndroidDumpUI) exists specifically to
dump animating UIs by calling the UiAutomator library directly instead of the shell
command, and [CulebraTester2 avoids uiautomator
entirely](https://stackoverflow.com/questions/59386490/how-to-dump-the-xml-layout-of-the-foreground-android-activity-without-uiautomat)
for the same reason. Separately, [Efficiency Matters (ICSE
2023)](https://dl.acm.org/doi/10.1109/ICSE48619.2023.00084) frames the general
problem: a long wait slows testing while a short wait acts on a partly rendered GUI,
and it infers rendering completion instead of guessing — which is the principled
answer to both this gap and gap 8.

**Solution.** Add a second observation source and pick between them automatically.

*Preferred source stays UIAutomator*, because selectors are richer and dispatch is
exact.

*Fallback is `dumpsys activity top`*, already verified on Trackbook's screen: 50
nodes and 8 clickable controls with resource ids and bounds, on the same frame where
`uiautomator dump` failed. No APK to build or install.

*The real cost is dispatch, not observation.* Actions are dispatched by exact
selector, which also needs a working dump, so a dumpsys-derived candidate must be
tapped at coordinates from its bounds. Give such actions their own provenance so a
reader can tell which were selector-verified, and never replay a coordinate action
as if it were selector-matched.

*If fidelity proves insufficient*, build the instrumentation APK with
`Configurator.setWaitForIdleTimeout(0)` and `UiDevice.dumpWindowHierarchy()`. Higher
fidelity, keeps exact selectors, costs a signed APK per device.

**Generalises?** Yes, and this is the point: the fallback triggers on a condition
(`could not get idle state`), not on an app name. Any future animating app is covered
without anyone noticing it is special.

**Already tried and rejected.** Freezing the process with SIGSTOP: a stopped app
returns `null root node returned by UiTestAutomationBridge`.

---

## Gap 8 — Throughput

**Measured.** Mean 10.8 actions per minute, about 5.5 seconds per action. Slowest at
a full hour: Amaze 5.5, Ultrasonic 7.8, My-Expenses 7.9. The baseline tools report
medians of 670, 588 and 1,088 actions per hour, so HybridDroid does roughly twice our
volume.

**What the research says.** [Efficiency Matters (ICSE
2023)](https://dl.acm.org/doi/10.1109/ICSE48619.2023.00084) is directly on point: a
fixed wait is the wrong instrument, because it is either too long and wastes time or
too short and acts on a half-rendered screen. Inferring when rendering has completed
dominates both.

**Solution.** Measure before changing, then replace the fixed wait rather than tuning
it.

*First, attribute the 5.5 seconds.* Record time in dump, parse, dispatch, delay and
adb round trips. We do not currently know which dominates, and guessing here would be
guessing.

*Then, in increasing risk:* reuse one action's after-observation as the next action's
before-observation if that is not already done, halving observations; replace the
fixed `post_action_delay_seconds` of 0.6 with a readiness check in the spirit of the
ICSE paper; batch adb round trips.

**Explicitly not a goal on its own.** More actions at 91.4% zero-gain buys
proportionally little. Throughput matters after gap 1, not before.

---

## Gap 9 — Evidence grows with samples times units

**Measured.** StreetComplete's coverage ledger reached 4.3 GB, OwnTracks 1.6 GB. The
host disk filled during v1 and nineteen runs were lost.

**Current state.** Runs are compacted after their summary is captured, reclaiming 93%
on StreetComplete. Its limit is known: validating the uncompressed 4.3 GB still cost
twenty minutes and 3.2 GB of memory.

**Solution.** Record observed units as a delta against the previous sample. Correct
and invasive — codec, validator and replay — so it should land on its own, not mixed
with the exploration changes. Compaction holds the disk meanwhile.

**Generalises?** Yes, and it matters more as apps get bigger: the cost is samples
times universe size, so the next 30,000-method app is worse than StreetComplete.

---

## What the research says not to do

- **Do not replace the state abstraction.** The [abstraction
  study](https://arxiv.org/abs/2606.16650) finds fine-grained abstractions suit
  model-based strategies, which is what we are. Our abstraction is not the defect.
- **Do not make the LLM drive every step.** Our own v3 data showed it: 120 calls in
  861 s, 12.3 actions per minute against a deterministic 15.3, and lower coverage.
  The [LLM text-input study](https://arxiv.org/html/2404.08948) puts LLMs where they
  are strong — generating plausible field content — not in the inner loop.
- **Do not chase raw action count.** Monkey [beat research tools in an industrial
  study](https://sos-vo.org/system/files/sos_files/Automated_Test_Input_Generation_for_Android_Are_we_Really_There_Yet_in_an_Industrial_Case.pdf)
  partly on volume, but our constraint is yield per action, not volume.
- **Do not expect to pass ~30% by exploration alone.** The [ICSE 2025
  study](https://people.ece.ubc.ca/mjulia/publications/Mobile_Application_Coverage_ICSE2025.pdf)
  reports GUI exploration reaching about 30% of a real app, and our healthy runs
  average 30.38%. Beyond that comes from routes, fixtures and deep-state work, not
  from tuning the tapper.

## Order, and why

1. **Gap 1**, the progress signal. Everything else is measured through a run that
   the ladder currently dominates.
2. **Gap 2**, no-effect suppression. Small, and its effect is only visible after 1.
3. **Gap 7**, the second observation source. Independent of 1, unblocks two apps, and
   may unblock Jellyfin's input field.
4. **Gap 6**, fixtures and self-hosted backends. Largest single prize at ~70,000
   methods, but worth most once the engine behind the door is fixed.
5. **Gap 4 stage one**, prefer navigation toward unexplored states.
6. **Gap 8**, throughput, measured first.
7. **Gap 9**, delta ledger, on its own.

Gaps 3 and 5 have no separate work: both should improve as consequences of 1.
