# v3: why the last two rounds failed, and what to do instead

Target: **40% mean method coverage**, from 28.64%. That is +11.36 points of mean, or
568 app-points across the 50-app dataset.

This document exists because three rounds of work have now produced under a point
each, and the reason is measurable rather than mysterious.

---

## 1. The measurement that settles the strategy

For each app's best v2 run, every method in the fixed static universe was assigned
to its declaring class, and each class was asked whether *any* of its methods were
ever executed.

| | methods | share of the gap |
|---|---:|---:|
| universe | 456,567 | |
| covered | 111,237 | |
| **unreached** | **345,330** | 100% |
| — in classes **never touched at all** | **277,920** | **80.5%** |
| — in classes partially covered | 67,410 | 19.5% |

**Every "explore more intelligently inside the screens we reach" idea competes for
19.5% of the gap.** Both previous rounds were spent there:

- v1 → v2 cut Back presses 20.84% → 7.60% and raised in-app actions 66% → 91%.
  Paid +0.71 on the mean.
- v2 → v3 fixed typed input, deep links and model auth. Paid **0.00**.

That is not bad execution. It is a ceiling: the work was aimed at a fifth of the
problem, and only part of that fifth is convertible.

Where the mass sits, by kind of code:

| kind | universe | unreached | cov% | share of gap |
|---|---:|---:|---:|---:|
| other | 145,531 | 113,001 | 22.4% | 32.7% |
| fragment | 66,745 | 50,746 | 24.0% | 14.7% |
| activity | 46,332 | 34,711 | 25.1% | 10.1% |
| util/helper | 49,025 | 31,583 | 35.6% | 9.1% |
| net/api | 25,527 | 22,136 | 13.3% | 6.4% |
| adapter/holder | 20,954 | 17,372 | 17.1% | 5.0% |
| model/data | 20,100 | 16,920 | 15.8% | 4.9% |
| viewmodel/presenter | 20,338 | 14,357 | 29.4% | 4.2% |
| dialog | 15,715 | 12,786 | 18.6% | 3.7% |
| view/widget | 17,776 | 12,446 | 30.0% | 3.6% |
| db/storage | 12,497 | 7,832 | 37.3% | 2.3% |
| service | 7,512 | 5,790 | 22.9% | 1.7% |

Activity classes are only 10.1% of the gap directly, but activities are the *entry
points*: fragment, adapter, viewmodel, dialog and view together are another 31.2%,
and they are downstream of entering a screen. `net/api` at 6.4% with 13.3% coverage
is the lowest-covered kind in the table and is downstream of a working server.

**The gap is an entry problem, not a tap-quality problem.**

---

## 2. Why the mean is concentrated in fifteen apps

| app | cov% | universe | untouched | wall |
|---|---:|---:|---:|---|
| Twire | 2.80% | 3,713 | 92.5% | Twitch OAuth — out of scope |
| Kore | 3.30% | 5,941 | 93.5% | needs a Kodi server |
| Fedilab | 3.51% | 16,665 | 90.3% | needs a Mastodon-compatible server |
| Jellyfin-Android | 4.58% | 27,921 | 91.4% | Jellyfin server + WebView connect screen |
| WordPress | 6.44% | 19,002 | 84.5% | account + WebView |
| Frost-Debug | 9.21% | 5,223 | 78.6% | Facebook — out of scope |
| ODK-Collect | 9.55% | 5,896 | 81.2% | needs ODK Central |
| Geohash-Droid | 11.18% | 1,011 | 72.9% | exploration-limited |
| A-Photo-Manager | 13.08% | 1,690 | 61.1% | works inside the system Gallery |
| Infinity-For-Reddit | 13.30% | 19,899 | 76.9% | Reddit OAuth — out of scope |
| Commons | 14.14% | 2,971 | 76.0% | Wikimedia account |
| Trackbook | 17.12% | 1,040 | 55.2% | exploration-limited |
| NewPipe-Debug | 17.58% | 18,079 | 70.2% | exploration-limited |
| Eventyay-Attendee | 17.72% | 9,896 | 71.6% | needs an Eventyay backend |
| Breezy-Weather | 18.07% | 24,916 | 71.9% | exploration-limited |

Bringing the in-scope members of that list to 35% is worth **+5.67 mean points on
its own** — half the distance to the target, from eleven apps, with no change to the
exploration algorithm at all.

Four apps (Twitch, Facebook ×2, Reddit) cannot be fixtured without breaching those
services' terms. They are a permanent cap of roughly 2 mean points and should be
reported as excluded rather than quietly carried.

---

## 3. Honest projection

| stack | mean | basis |
|---|---:|---|
| v2 measured | 28.64% | published |
| + root/forced activation | 31.64% | 65.8% of activities were unlaunchable; reach correlates +0.65 with coverage. **Unmeasured.** |
| + self-hosted backends, 8 apps | 37.64% | ownCloud went 16.92% → 43.89% once its fixture worked. Mechanism proven twice. |
| + WebView via CDP, 4 apps | 40.14% | ~59,000 methods; sockets verified present on device. **Unbuilt.** |
| + `all_route` reported beside `gui ∪ deep_link` | 42.14% | matches what CovAgent and VLM-Fuzz measure. Metric change, must be labelled. |

**40% is reachable. It is an infrastructure and reach problem, not an algorithm
problem.** Nothing in that table is a smarter tap policy.

### Three things that would reach 40% dishonestly, and are excluded

1. **Denominator switch to "classes observed during the run."** The three baseline
   tools in this repository already do this, which is most of why they report
   ~33.8% where we report 28.8%. It adds no covered method.
2. **Quietly promoting `all_route` to the headline.** Invariant I4 keeps forced
   activation out of `gui ∪ deep_link` so a coverage figure still means
   user-reachable. Reporting both, labelled, is fine. Replacing one with the other
   is not.
3. **Dropping hard apps.** Coverage runs 37.50% under 2,000 methods against 21.60%
   over 12,000, so the mean moves on app selection alone.

---

## 4. The work, ranked by measured yield

### 4.1 Root-backed forced activation — built, unvalidated

`am start` from adb runs as uid 2000 and the framework refuses any non-exported
component:

    SecurityException: Permission Denial ... not exported from uid 10214

1,298 of 1,974 declared activities (65.8%) are not exported, matching Akinotcho et
al. at 16.8% exported. Every container here ships `/system/xbin/su`, and the same
launch as uid 0 is permitted — verified on device for AnkiDroid's `CardBrowser`,
`Statistics`, `Reviewer` and `StudyOptionsActivity`.

Three defects had to be cleared and all three now are: the target list was empty
unless `allow_forced_routes` was set; the launch was permission-denied; and the
first root fallback was dead code because `am` exits non-zero, so the refusal
arrives as a raised `AdbError` rather than as inspectable output.

Remaining: rung 7 is reached only after the 60%-of-clock gate or a
`screen_exhausted` trigger, and `screen_exhausted` is 4% of triggers. In a 3600s
run the treatment window is the last ~24 minutes. **The gate is now the binding
constraint on this lever, not the launch.**

### 4.2 Self-hosted backends — highest yield, mechanism proven

Per-app, all self-hostable, all with a published server image:

| app | server | unreached methods |
|---|---|---:|
| Jellyfin-Android | Jellyfin (container already exists) | 26,642 |
| Fedilab | GoToSocial (single binary) | 15,054 |
| WordPress | self-hosted WordPress | 16,049 |
| Eventyay-Attendee | Eventyay backend | 7,086 |
| Kore | Kodi | 5,556 |
| ODK-Collect | ODK Central | 4,787 |
| Ultrasonic | Navidrome | 4,065 |
| Wallabag | Wallabag | 1,684 |

Setup profiles already support native login forms, secrets from the environment,
and a restart-survival check. The rule that decides feasibility: **profiles work
for native forms, not for WebView or OAuth.** Jellyfin and WordPress therefore
depend on 4.3 as well.

### 4.3 WebView content via CDP — largest single unreached block

Jellyfin 26,642, WordPress 16,049, Firefox-Lite 13,711 and Nextcloud 1,698 methods
sit behind a node UIAutomator reports as empty. All four ship debuggable APKs and
publish a `webview_devtools_remote_<pid>` socket, both verified.

    adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
    GET /json/list                        -> page targets
    DOM.getDocument, DOM.getBoxModel      -> elements and rectangles

Element rectangles are WebView-relative; add the WebView node's screen origin from
the UIAutomator dump. Give these actions their own provenance. This also unblocks
Nextcloud's login, which is a WebView form.

### 4.4 Release the component-launch gate on evidence, not the clock

38 of 98 v2 runs (39%) recorded their last coverage gain before the gate opened, a
mean 32% of budget with nothing left to gain and activation still withheld.
`launch_on_frontier_exhaustion` extended the rung-5 escape to deep links and forced
activation, but it only fires on `screen_exhausted`, which is 4% of triggers.
`coverage_plateau` is 93%. The gate should open on the condition that actually
holds.

### 4.5 Tarpit escape, from HybridMonkey (ASE 2026)

Their preliminary study measures random testing spending ~50% of its time in UI
tarpits, defined as more than eight consecutive screenshots of high visual
similarity. Escape is a dedicated LLM query, and successful escapes are **cached
and reused**. Escape success 72.9%.

Ours is the same phenomenon — 91.8% zero-gain, 48.1% no-effect — and our four
reverted mechanisms all failed. Two concrete differences: their detection is visual
similarity between consecutive screens, which a changing state hash cannot fool;
and cheap detection is separated from expensive escape. This addresses the 19.5%
band, so it is ranked below everything above it.

### 4.6 Do not bother with

- **More waste reduction.** 19.5% ceiling, two rounds spent, both under a point.
- **Steering on discovery.** Measured: AnkiDroid 13.51% → 5.4%.
- **Retiring inert actions.** Measured: Chess 46.58% → 23.29%.
- **Frida-based guard bypass**, until 4.1–4.4 are done. It addresses the 47.6% of
  activities with *mandatory* guards; root activation addresses the 22.7%
  straightforward band first, which is the staging the 30% Curse paper recommends.

---

## 5. Validation, because the last four waves could not have detected the target

Within-app spread across four waves of the same six apps, code held constant:

    AnkiDroid    18.89 -> 31.93   (13.0 points)
    Binary-Eye   34.78 -> 49.44   (14.7 points)
    mean within-app spread                8.69 points

The effects being chased are 1–3 points. A six-app single-run wave cannot see them,
which is why every round has reported "under a point" — that is the resolution of
the instrument, not necessarily the size of the effect.

Required from here:

1. **Repeated runs.** At least 3 per app per arm, reporting median and spread.
2. **Paired per-app comparison**, never a difference of means across waves.
3. **The full 50 apps** for any headline claim.
4. **Report both** `gui ∪ deep_link` and `all_route`, always labelled.

---

## 6. Order of work

1. Open the launch gate on `coverage_plateau` as well as `screen_exhausted` (4.4),
   then validate 4.1 with repeated runs. Cheapest, already built, unblocks the
   measurement of everything else.
2. Stand up GoToSocial, Kodi, Navidrome, Wallabag, ODK Central and write their
   setup profiles (4.2). Six native-login apps, ~31,000 unreached methods.
3. Build the CDP observation and dispatch path (4.3). Unblocks four apps and
   ~58,000 methods, and Jellyfin and WordPress logins with them.
4. Only then consider 4.5.

Steps 1–3 are the 40%. Step 1 is measurement, step 2 is containers and selectors,
step 3 is the only real engineering.
