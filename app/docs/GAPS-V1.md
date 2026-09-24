# Gaps in v1, and what to do about them

Everything here is drawn from this campaign's own evidence: `analysis/blocker-diagnosis.json`,
the run manifests, and the driver logs. Each gap says what was measured, what it
costs, and what a fix would involve. This is the work list for v2.

## Result this campaign is measured against

49 of 50 apps measured. Mean 28.09%, median 27.12%, 108,464 of 450,868 methods.
Runs that stayed inside the app under test averaged 30.38%, which is the ceiling
an [ICSE 2025 study](https://people.ece.ubc.ca/mjulia/publications/Mobile_Application_Coverage_ICSE2025.pdf)
reports for GUI exploration, so the remaining gap is mostly apps that cannot be
entered rather than an explorer that gives up early.

## 1. Apps behind a backend or an account

The largest gap by a wide margin.

| App | Coverage | Unmeasured methods | Wall |
| --- | ---: | ---: | --- |
| Jellyfin | 2.18% | 27,312 | needs a Jellyfin server |
| WordPress | 8.04% | 17,474 | wordpress.com account |
| Fedilab | 5.55% | 15,740 | Mastodon instance |
| Twire | 2.80% | 3,609 | Twitch OAuth |
| Kore | 4.33% | 5,684 | needs a Kodi server |
| ODK-Collect | 10.50% | 5,277 | needs an ODK server |
| Commons | 15.08% | 2,523 | Wikimedia account |
| Frost, MaterialFBook | 9.02%, 24.28% | ~5,100 | Facebook account |

Note that Jellyfin, WordPress and Twire spend 85-95% of their actions *inside*
the app. They are not lost; they are stopped. No exploration change moves them.

What to do. Self-host what can be self-hosted: Jellyfin, WordPress, Nextcloud,
ownCloud, ODK Central and Kodi all publish server images. A real Jellyfin server
was already stood up during v1, provisioned through its own Startup API, with a
user authenticating and TCP reachable from the devices; what stopped it was the
app side, below. Twitch and Facebook cannot be self-hosted, and creating accounts
on them to drive a test would breach their terms, so those three apps stay out of
scope until someone supplies credentials they own.

## 2. Screens UIAutomator can never dump

Two apps, and one of them has no measurement at all.

    Trackbook           uiautomator dump -> ERROR: could not get idle state
    Vinyl-Music-Player  aborted/screen_never_idle_unrecoverable

Trackbook's map redraws continuously, so the dump never succeeds, with or without
location permission, while the launcher on the same device dumps fine. Its 16.35%
is startup only: `gui+deeplink` is 0.00% and 0% of its actions ran inside the app.
It also has no deep links and no exported components, so routes cannot substitute.

A working source already exists and was verified during v1 on the same never-idle
screen:

    dumpsys activity top -> 50 nodes, 8 clickable, with resource ids and bounds
                            (FloatingActionButton, BottomNavigationItemView, ...)

What to do. Add `dumpsys activity top` as a fallback observation source. The
obstacle is dispatch, not observation: actions are currently dispatched by exact
UIAutomator selector, which also needs a working dump, so a dumpsys-derived
candidate has to be dispatched by coordinates from its bounds. That splits the
evidence model into selector-matched and coordinate-matched actions and touches
the validator and replay, so it is a design change rather than a patch. Freezing
the process was tried and does not work: a stopped app returns `null root node
returned by UiTestAutomationBridge`.

## 3. Jellyfin's connect screen exposes no input

Its own gap, because the server for it already exists.

    text='Connect to Server'  class=TextView  clickable=False
    text='Host'               class=TextView  clickable=False
    text='Connect'            class=TextView  clickable=False

It is a WebView wrapper and no `EditText` is exposed, so a setup profile has
nothing to type into. Two routes: seed the server address into app storage before
launch, or see whether the idle-free dump from gap 2 exposes the field.

## 4. Setup profiles exist and have never been used

`setup_profile.py` already supports `input_secret`, an `oauth-login-and-restart`
profile, secrets read from the environment, `allowed_foreign_packages`, and a check
that seeded state survives a real process restart. No campaign has ever run with
one. This is the cheapest item on the list and it addresses gap 1 for every app
whose backend is self-hosted.

## 5. Throughput

    mean 10.5 actions per minute, about 6 seconds per action

For comparison, the baseline tools report medians of 670, 588 and 1,088 actions
per hour, so HybridDroid does roughly twice our volume. Each action costs two
observations plus a post-action delay plus adb round trips. Worth measuring where
the six seconds actually go before changing anything.

## 6. A quarter of each run produces nothing

Over runs that reached at least 25%, 24.5% of the run happens after its last
coverage gain, and 30 of 35 exhausted their frontier. The tails are long:
OpenLauncher spent 711 of 883 actions with no new coverage, AnkiDroid 586,
Simple-Alarm-Clock 473. The explorer has nowhere better to go and keeps paying for
it. This is where the healthy 30% apps have room, and it is a policy question:
what should a run do once its frontier is genuinely exhausted.

## 7. Evidence grows quadratically

Every coverage sample records the whole observed unit set, not only what was newly
covered, so a ledger grows with samples times units.

    StreetComplete coverage_events.jsonl   4.3 GB
    OwnTracks                              1.6 GB
    Muzei                                  1.5 GB

This filled the host disk during v1 and cost nineteen runs. Runs are now compacted
once their summary is captured, which reclaimed 93% on StreetComplete, but that is
after the fact: validating the uncompressed 4.3 GB still took twenty minutes and
3.2 GB of memory. Recording observed units as a delta would fix it at the source
and needs changes to the codec, validator and replay.

## 8. The dataset is not internally uniform

Fixes landed while v1 was running, and the driver spawns a fresh process per run,
so early and late runs came from different code. Only the nine runs in
`final50-missing` were produced entirely by the final code; they averaged 36.44%
against the campaign's 28.09%, on a different and smaller app mix. v2 should be
one campaign on frozen code so the comparison means something.

## 9. Smaller items

- **My-Expenses crashed 18 times** and still reached 29.56%. Whether those crashes
  are ours is unknown; the uninstrumented-APK check that exonerated five apps in v1
  would settle it.
- **The smali backend has no unblock mechanism.** Any app needing
  `VD_UNBLOCK_METHODS` must use Soot, which is why Amaze silently broke when
  backend selection gave it a smali bundle. Backend selection should know this.
- **The smali backend under-detects some universes** (And-Bible resolved 173
  methods where Soot finds 6,721). Selection now rejects that by comparing against
  Soot, but the detection itself is still wrong.
- **Three analysis scripts still reference the old result folder names**
  (`baseline_code_coverage.py`, `baseline_summary.py`, `compare_tools.py`) and will
  fail until updated.
- **A-Photo-Manager got worse under containment** (13.02% to 8.17% in a 15-minute
  check), because its own work happens inside the system Gallery and picker. It
  needs the `allowed_foreign_packages` list that setup profiles already support.

## Suggested order for v2

1. Fix the ledger at the source, because it constrains every campaign.
2. Turn on setup profiles and self-host Jellyfin, WordPress, Nextcloud, ownCloud
   and ODK. This is where roughly 70,000 unmeasured methods are.
3. Add the idle-free observation source, which unblocks two apps and may unblock
   Jellyfin's input field.
4. Investigate throughput and the exhausted-frontier tail, which is where the
   healthy apps gain.
5. Re-run the full 50 on frozen code, into a new results folder, and compare
   against v1.
