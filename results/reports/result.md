# Gemma baseline: 150 one-hour tests of three tools on 50 apps

This is the detailed report for the `google.gemma-3-4b-it` baseline only. The
complete three-model study is in **[results.md](results.md)**, and its paired
comparison is in **[model-comparison.md](model-comparison.md)**.

Every baseline test was given **one hour** with one app and one tool, on the
same machine, with code coverage read from the running app. All
150 planned tests have a result and all
150 of them ran the full hour. One of them, GPTDroid on A Photo Manager, ran the hour without reporting any coverage, counted as unavailable rather than as 0%; [why is below](#tests-that-reported-no-coverage).

*Snapshot 2026-08-30 20:20 UTC, repository at `43365b83a`.*

## Summary by tool

| Measure | GPTDroid | LLMDroid | HybridDroid |
|---|---:|---:|---:|
| Tests | 50 | 50 | 50 |
| Ran the full hour | 50 | 50 | 50 |
| Reported code coverage | 49 | 50 | 50 |
| **Code coverage, median** | **30.01%** | **32.28%** | **38.50%** |
| Code coverage, mean | 31.37% | 31.50% | 38.54% |
| Code coverage, range | 8.41–58.42% | 13.56–60.44% | 10.70–70.25% |
| Screens reached, median | 11.65% | 25.66% | 32.45% |
| Screens reached, mean | 17.31% | 30.30% | 33.92% |
| Actions, median | 670 | 588 | 1,088 |
| Actions, total | 30,168 | 24,460 | 53,142 |
| Model calls, total | 30,218 | 6,805 | 283 |
| Model cost, all tests | $1.60 | not recorded by the tool | $0.0039 |
| Apps where it led the three | 7 | 6 | 37 |
| Tests where the app crashed | 1 | 17 | 31 |
| Tests where the app stopped responding | 0 | 2 | 3 |

37 of the 50 apps were led by HybridDroid,
6 by LLMDroid and 7 by GPTDroid, counting the
tool that reached the most code coverage on that app.

### How wide the spread is

One median hides a lot: the same tool reaches under 15% on one app and over 60% on
another, because what a tool can reach depends heavily on the app.

| Tool | Lowest | Lower quarter | Median | Upper quarter | Highest |
|---|---:|---:|---:|---:|---:|
| GPTDroid | 8.41% | 24.19% | 30.01% | 35.77% | 58.42% |
| LLMDroid | 13.56% | 24.46% | 32.28% | 35.86% | 60.44% |
| HybridDroid | 10.70% | 30.97% | 38.50% | 47.66% | 70.25% |

Highest three for each tool:

- **HybridDroid** — Simple Alarm Clock 70.25%, Sunflower 66.91%, OpenLauncher 58.56%
- **LLMDroid** — Sunflower 60.44%, Simple Alarm Clock 57.71%, Nextcloud 49.07%
- **GPTDroid** — Chess 58.42%, Simple Alarm Clock 56.43%, Sunflower 53.13%

## What the two numbers mean

**Code coverage** is the share of the JaCoCo coverage points built into the app's
own code that were reached while the tool drove the app. It is read live from the
running app every 30 seconds and again at the end. It is **not** line, branch,
method, class, instruction or statement coverage, and it should not be compared
with the percentages published in the three papers.

**Screens reached** is activity coverage: the app's own screens that were actually
shown, out of the screens the app declares.

Both are stored per test in `results/<model>-result/<app>/<tool>/code_coverage.json` and
`activity_coverage.json`, with the numbers gathered in
[`dataset/tests.tsv`](dataset/tests.tsv).

## Where the tests ran

Android 14 (API 34, arm64-v8a) in containers on one AWS `m7g.4xlarge` machine
(16 processors, 61 GB): sixteen Android instances, ten to twelve tests at a time.
No emulator is involved; the Android system shares the host kernel, which is why
sixteen instances fit. Each test gets its own instance, its own coverage port and
its own result folder, and the harness refuses to start a test on an instance
another test is using.

Concurrency matters for a fair reading: a tool gets fewer actions per hour on a
busy machine than on an idle one. All three tools were run under the same
conditions, interleaved rather than in separate batches.

## Every test, per app

Each cell reads **code coverage · screens reached · actions**.

| App | GPTDroid | LLMDroid | HybridDroid | Led by |
|---|---:|---:|---:|---|
| A Photo Manager | no coverage (11% screens · 0 actions) | 17.16% · 11% · 46 | 17.10% · 11% · 573 | LLMDroid |
| Amaze | 26.19% · 17% · 763 | 32.01% · 33% · 163 | 35.35% · 33% · 1,101 | HybridDroid |
| And Bible | 27.60% · 6% · 754 | 34.96% · 32% · 186 | 50.67% · 58% · 1,133 | HybridDroid |
| AnkiDroid | 18.65% · 10% · 752 | 31.47% · 71% · 564 | 38.59% · 57% · 1,089 | HybridDroid |
| AntennaPod Debug | 23.97% · 17% · 602 | 26.61% · 8% · 651 | 52.74% · 25% · 1,094 | HybridDroid |
| Binary Eye | 31.26% · 50% · 663 | 34.70% · 75% · 613 | 46.66% · 50% · 1,125 | HybridDroid |
| Breezy Weather | 26.82% · 7% · 627 | 36.24% · 25% · 610 | 49.56% · 36% · 1,191 | HybridDroid |
| Chess | 58.42% · 19% · 673 | 23.05% · 81% · 690 | 33.36% · 62% · 1,144 | GPTDroid |
| Commons | 36.81% · 14% · 687 | 35.73% · 21% · 534 | 39.31% · 14% · 1,202 | HybridDroid |
| Eventyay Attendee | 34.25% · 8% · 720 | 37.72% · 8% · 275 | 37.97% · 8% · 1,153 | HybridDroid |
| Fedilab | 8.41% · 3% · 722 | 14.67% · 1% · 674 | 14.37% · 1% · 1,154 | LLMDroid |
| FeederD | 46.84% · 10% · 747 | 46.26% · 20% · 784 | 47.29% · 30% · 1,100 | HybridDroid |
| Firefox Lite Dev | 44.00% · 10% · 755 | 21.23% · 3% · 166 | 28.65% · 3% · 1,040 | GPTDroid |
| Frost Debug | 33.82% · 12% · 740 | 34.03% · 12% · 730 | 34.68% · 12% · 1,156 | HybridDroid |
| Geohash Droid | 17.25% · 14% · 30 | 13.56% · 29% · 338 | 12.42% · 43% · 1,087 | GPTDroid |
| GPSLogger | 23.16% · 9% · 642 | 30.35% · 27% · 157 | 40.30% · 36% · 1,097 | HybridDroid |
| Infinity For Reddit | 16.11% · 3% · 662 | 20.23% · 10% · 157 | 25.06% · 11% · 1,149 | HybridDroid |
| Jellyfin Android | 33.84% · 20% · 781 | 39.13% · 20% · 715 | 32.37% · 20% · 1,072 | LLMDroid |
| Kiwix | 32.28% · 17% · 756 | 32.56% · 17% · 110 | 45.43% · 33% · 1,184 | HybridDroid |
| Kore | 15.30% · 12% · 445 | 17.06% · 6% · 745 | 10.70% · 6% · 1,055 | LLMDroid |
| LibreTorrent | 27.42% · 11% · 493 | 34.53% · 39% · 297 | 32.61% · 67% · 1,009 | LLMDroid |
| MaterialFBook | 40.52% · 25% · 536 | 40.41% · 25% · 735 | 51.41% · 25% · 1,157 | HybridDroid |
| Money Manager Ex | 26.21% · 12% · 3 | 28.33% · 41% · 729 | 43.73% · 46% · 1,178 | HybridDroid |
| Muzei | 45.84% · 7% · 446 | 38.00% · 7% · 397 | 45.59% · 7% · 794 | GPTDroid |
| My Expenses Debug | 30.57% · 4% · 631 | 25.91% · 21% · 795 | 42.59% · 23% · 1,118 | HybridDroid |
| NewPipe Debug | 24.41% · 8% · 476 | 30.77% · 25% · 108 | 15.46% · 50% · 996 | LLMDroid |
| Nextcloud | 48.77% · 9% · 1 | 49.07% · 6% · 237 | 50.43% · 6% · 1,091 | HybridDroid |
| ODK Collect | 23.30% · 8% · 746 | 26.91% · 28% · 444 | 32.68% · 19% · 1,129 | HybridDroid |
| Omni Notes Alpha | 26.60% · 8% · 751 | 31.08% · 38% · 794 | 47.96% · 46% · 1,071 | HybridDroid |
| Open Food Facts | 21.10% · 8% · 665 | 24.38% · 46% · 328 | 30.46% · 50% · 1,050 | HybridDroid |
| OpenLauncher | 48.94% · 12% · 521 | 33.14% · 25% · 721 | 58.56% · 62% · 947 | HybridDroid |
| Orgzly Revived | 25.65% · 10% · 757 | 24.45% · 10% · 149 | 43.60% · 10% · 1,051 | HybridDroid |
| ownCloud | 24.84% · 12% · 736 | 24.47% · 4% · 51 | 24.74% · 4% · 972 | GPTDroid |
| OwnTracks | 35.26% · 17% · 773 | 33.80% · 8% · 693 | 35.86% · 8% · 1,139 | HybridDroid |
| Phonograph DEBUG | 34.15% · 13% · 741 | 35.37% · 40% · 400 | 47.56% · 40% · 1,084 | HybridDroid |
| RedReader | 23.29% · 9% · 464 | 39.53% · 30% · 747 | 49.29% · 43% · 563 | HybridDroid |
| Scarlet Notes FD | 35.20% · 13% · 767 | 36.34% · 27% · 790 | 48.40% · 27% · 1,138 | HybridDroid |
| Simple Alarm Clock | 56.43% · 20% · 492 | 57.71% · 40% · 692 | 70.25% · 40% · 1,127 | HybridDroid |
| SkyTube | 25.39% · 15% · 777 | 21.26% · 23% · 679 | 31.09% · 23% · 1,068 | HybridDroid |
| StreetComplete | 34.42% · 14% · 666 | 33.52% · 57% · 687 | 45.22% · 57% · 1,012 | HybridDroid |
| Sunflower | 53.13% · 100% · 499 | 60.44% · 100% · 737 | 66.91% · 100% · 1,010 | HybridDroid |
| Trackbook | 30.01% · 100% · 254 | 33.39% · 100% · 676 | 38.40% · 100% · 1,043 | HybridDroid |
| Transistor | 27.33% · 50% · 744 | 23.42% · 50% · 50 | 30.25% · 50% · 1,070 | HybridDroid |
| Twire | 38.23% · 15% · 487 | 27.02% · 30% · 618 | 49.41% · 65% · 988 | HybridDroid |
| Ultrasonic | 36.28% · 33% · 744 | 35.52% · 33% · 730 | 33.04% · 33% · 1,038 | GPTDroid |
| Vespucci | 23.42% · 11% · 771 | 27.05% · 26% · 105 | 31.16% · 53% · 1,074 | HybridDroid |
| Vinyl Music Player | 31.10% · 11% · 535 | 26.65% · 37% · 567 | 30.63% · 32% · 997 | GPTDroid |
| Wallabag | 19.95% · 30% · 784 | 20.81% · 60% · 486 | 28.25% · 30% · 1,135 | HybridDroid |
| Wikipedia Alpha | 29.92% · 7% · 634 | 39.08% · 19% · 344 | 41.39% · 21% · 1,086 | HybridDroid |
| WordPress | 34.33% · 4% · 753 | 34.14% · 6% · 766 | 37.27% · 6% · 1,108 | HybridDroid |

Each app's evidence is in `results/<model>-result/<app>/<tool>/`, where `<app>` is
the app's name with underscores: `results/gemma-local-result/Money_Manager_Ex/hybriddroid/`
holds that test's coverage, screen count, device log, tool log and action record. The
`evidence` column of [`dataset/tests.tsv`](dataset/tests.tsv) gives the path for any
row, and the commercial pass sits beside it in `results/openai-commercial-result/`.

## Tests that reported no coverage

These ran their full hour and produced no coverage number. Coverage is recorded as unavailable rather than 0%, so these tests are left out of the medians above instead of dragging them down.

**GPTDroid on A Photo Manager.** The app targets Android 5.0 (API 21), old enough that Android shows its own permission-review screen at every launch even once the permission is granted — the fault described under *Corrections made along the way*. The fix for it, clearing the `review-required` flag off each permission, lives in the shared install path in `run_tool.py` that LLMDroid and HybridDroid use, which is why both of them drive this app normally. GPTDroid installs the app itself inside `gptdroid.run()` and never clears that flag, so the review screen is never dismissed: its log shows 184 launch attempts, the app's own process never starting once, and the review screen in front for the whole hour. It made one model call, the opening activity-priority prompt, and took no action. Retried nine times with the same result, so it is a defect in that install path rather than a flaky run.

## How the hour was completed

Two normal endings appear in the per-test files. GPTDroid stops itself when its
hour is up and is recorded as `succeeded`. LLMDroid and HybridDroid keep going
until told to stop, so the harness stops them on the hour, recorded as
`completed_forced_stop`. Neither is a failure; both used their hour.

| App | Tool | Times started again | Code coverage |
|---|---|---:|---:|
| AntennaPod Debug | llmdroid | 13 | 26.61% |
| Breezy Weather | llmdroid | 8 | 36.24% |
| Twire | llmdroid | 1 | 27.02% |

In those the tool ended its own run early, the harness started it again and kept
going until the hour was spent. Coverage was dumped before each restart and the
dumps merged with JaCoCo's own `merge`, so no coverage point is counted twice.
They are not one uninterrupted hour of one tool, which is why they are named here.

## What the model calls cost

The model was `google.gemma-3-4b-it` on a pay-per-token endpoint, priced at **$0.04 per million input tokens and $0.08 per million output tokens**. Each test records its own token counts where the tool reports them, and the cost below is those counts at that price.

| | GPTDroid | LLMDroid | HybridDroid |
|---|---:|---:|---:|
| Model calls | 30,218 | 6,805 | 283 |
| Input tokens | 38,372,013 | not recorded by the tool | 101,337 |
| Output tokens | 804,472 | not recorded by the tool | 951 |
| **Cost, all 50 tests** | **$1.5992** | **not known, see below** | **$0.0039** |
| Cost per test, median | $0.0301 | not known | $0.0000 |
| Cost per test, highest | $0.1116 | not known | $0.0007 |
| Cost per hour of testing | $0.0320 | not known | $0.0001 |
| Cost per 1,000 actions | $0.0530 | not known | $0.0001 |

**GPTDroid: $1.60 for all 50 hours.** It asks the model at every step, which came to 30,218 calls and 38,372,013 input tokens against only 804,472 output tokens: the spend is almost entirely the screen descriptions it sends, not the answers it gets back.

**HybridDroid: $0.0039 across its 50 hours.** It made 283 calls in 34 of 50 tests, using 101,337 input tokens and 951 output tokens. HybridDroid explores on its own and calls the model only after its tarpit detector fires, so its low call count is expected and is not evidence that the model path was unused.

**LLMDroid: not known, and it cannot be recovered from these runs — because the tool does not write it down.** It made 6,805 calls across its 50 tests. Its only record of them is the file it keeps itself, `native/LLM-Interaction.txt`, and one line of that file is written per call by `droidbot/policy/llm_agent.py`: the seconds the call took and the length of the answer, nothing else — for example `0.89219, 264`. The token usage the endpoint returns with every response is never stored, and no run of the 50 carries a token count in any field. Adding that accounting would have meant editing the tool, which this study does not do, so the honest answer is that the number is missing rather than zero. The 1,745,634 characters of output it received are about 0.44 million tokens at four characters each, so the output side alone is roughly **$0.0349**. That is a floor, not an estimate of the total: on GPTDroid, where both sides were measured, input tokens were 48 times the output tokens. Capturing prompt tokens for the tools that run from their own source is the first thing to fix before repeating this study.

Two things to keep in mind before reading these as the cost of the approach. This is a small 4-billion-parameter model; the same study on a large model would cost one to two orders of magnitude more, and GPTDroid's per-step design is what scales with that price. And a cost per hour is only comparable because every test here got exactly one hour.

### Cost per hour, model and machine side by side

| Per hour of testing | GPTDroid | LLMDroid | HybridDroid |
|---|---:|---:|---:|
| Model spend | $0.0320 | not known | $0.0001 |
| Machine share, at 11 tests on one host | $0.0258 | $0.0258 | $0.0258 |
| **Together** | **$0.0578** | **≥ $0.0258** | **$0.0259** |

For the Gemma baseline as a whole: 150 hours of testing, **$1.60
of recorded model spend** for GPTDroid and HybridDroid, and roughly
$3.87 of machine time at the spot price paid.
LLMDroid's spend is excluded because its native logs do not retain token counts.

## What the apps did under test

| | GPTDroid | LLMDroid | HybridDroid |
|---|---:|---:|---:|
| Tests where the app crashed | 1 | 17 | 31 |
| Tests where the app stopped responding | 0 | 2 | 3 |

A crash did not end a test: the run carried on and finished its hour. The crash
lines are kept per test in `crash.log.gz` and in the device log
`logcat.log.gz` beside each test.

## Corrections made along the way

These are faults in this harness, not in any tool, and each one changed numbers
that had already been recorded. They are listed because a reader who cannot see
them cannot judge the rest.

**HybridDroid's early runs were cut short by my own guard.** The harness stops a
run when the same screen and action repeat, and that guard was set to eight for
HybridDroid on the reading that eight is the tool's own number. Eight is its
number, but it means something else: in the UI-tarpit paper, eight similar screens
declare a tarpit and start an LLM-guided escape, and testing continues to the end
of the time budget. My guard ended the test instead. The guard is now off for all
three tools, HybridDroid's own detector is untouched, and every affected run was
done again for the full hour.

**Screen counts were read too late.** The earliest runs read the device log only
at the end, and Android's log is a small rolling buffer, so an hour-long test had
already lost its early screens and often reported none. The log is now written
continuously from the start, the affected runs were counted again from the screens
each tool recorded for itself, and every recounted file says so under
`recounted`. Code coverage was never affected, since it is read from the running
app.

**A screen was only recognised in one of the three ways Android names it.** A full
hour of real testing was being reported as reaching no screen at all. All three
forms are read now.

**Android's permission-review screen blocked two apps for an hour at a time.**
Granting permissions at install was not enough: for an app that targets an old
Android version the permissions come out granted but still flagged
`REVIEW_REQUIRED`, so Android puts its own screen in front of the app at every
launch. Closing that screen only makes Android show it again — it appeared 358
times in one run. Taking the flag off each permission is what works, and it has to
be done while the run is going, because each tool installs the app itself and a
fresh install brings the flag back. A Photo Manager and ODK Collect had each used
eight attempts against this before the cause was found.

**One debug build named a diagnostics screen as its entry point.** ODK Collect's
debug build ships LeakCanary, which also declares itself launchable, so the app
file names LeakCanary's leak viewer as the way in. Android refuses to start it
from here, so the app never appeared. The harness now asks the device for the real
entry point in that case. That test went from 1 screen in an hour to 41 in three
minutes.

**A one-word fault in the harness ended runs early.** When a tool exited before its
hour the harness restarted it, and the restart path called a constant that did not
exist, so the run aborted after a minute or two. Five runs died that way before it
was found. A failed coverage reading can no longer end a test either.

**Two campaigns once drove the same result folders.** A test with no result file is
not always a test that never ran; it may be running. The campaign now skips any
test a process is already working on, and the coverage port follows the Android
instance rather than the worker number, so two tests cannot claim one port.

## Retries

117 earlier attempt folders sit beside these 150 tests
as `r1.attempt-N` in `reproduction/results/run-60m/`. 77
of them got far enough to write a result file; the rest were stopped before that,
which is why an attempt folder can be nearly empty. Nearly all of them are the
faults above rather than tool behaviour. They are kept so the failures can be read
too, and they are counted separately: the tables in this report read one test per
app and tool, never a retry.

The same 150 tests appear in two places, and it is worth saying which
is which.

`reproduction/results/run-60m/` is the **raw tree**, exactly as the harness wrote
it: one folder per app **file**, then per tool, then `r1` and every superseded
attempt, with screenshots and per-screen dumps alongside. It is the source every
number is derived from, and `reproduction/results/openai-60m/` is its counterpart
for the commercial pass.

`results/gemma-local-result/` is the **readable copy** of the 150
tests that count: one folder per **app**, then per tool, no attempts and no
screenshots, with `results/openai-commercial-result/` beside it for the other pass.
Every file in the copy was checked byte-for-byte against the raw tree.

There used to be a third copy under `dataset/results/`, holding this pass only. It
was byte-identical to `results/gemma-local-result/` and could not hold the
commercial pass, so it was removed; `dataset/` now holds the apps and the tables,
and the `evidence` column of `dataset/tests.tsv` points into `results/`.

## What was kept, what was left out, and why

Nothing was dropped for scoring badly. Every app that could report coverage and
that all three tools could drive was kept, and the ones left out are named with a
reason that can be checked from the app file itself.

| Stage | Apps | Note |
|---|---:|---|
| Public benchmark app files on hand | 28 | 28 kept |
| Apps put through a build here | 101 | 125 build logs, since some were retried after a fix |
| — built with coverage inside | 32 |  |
| — of those, kept | 22 | passed the three-tool check |
| — ruled out | 0 | cannot report coverage; named below |
| — built but never needed | 0 | the set had reached 50 before their turn |
| **Apps tested** | 50 | **150 tests** |

**Public benchmark apps: all 28 were kept.** Themis, Themis+/DDroid and
HybridDroid publish app files already built with coverage switched on, one file per
app, and each one passed the three-tool check first time.

**Apps built here: 32 built, 22 kept.** 0 were
ruled out because they cannot report coverage at all, which is a property of the app
and not of any tool: the coverage agent hands its counts over a local socket, and
Android denies that socket to an app which does not declare
`android.permission.INTERNET`. Three of them were found the slow way, by checking
them with every tool first; after that the manifest is read up front, which takes a
moment instead of 45 minutes.

**0 apps were built and never used**: none. They finished
building after the set had reached 50 apps, so their turn never came. They are kept
in `reproduction/datasets/built-from-source/` for anyone extending this work, and
they are not counted anywhere in this report.

**No app was dropped after its hour-long tests.** The set was fixed before the tests
that count, by the three-tool check, so no result was thrown away for being
inconvenient.


### The apps left out, one line each

Dropping an app quietly would flatter whichever tool struggled with it, so each one
is named with its reason, and its app file and check evidence are kept.

| App | Install name | Why it was left out |
|---|---|---|
| Aegis | `com.beemdevelopment.aegis.debug` | Cannot report coverage at all: the app does not declare android.permission.INTERNET, and the coverage agent hands its counts over a local socket, which Android denies without that permission. Checked with every tool for 15 minutes each: the tools drove the app and the dump still failed. Nothing in the tools or the app was changed to work around it. |
| Catima | `me.hackerchick.catima.debug` | Cannot report coverage at all: the app does not declare android.permission.INTERNET, and the coverage agent hands its counts over a local socket, which Android denies without that permission. Checked with every tool for 15 minutes each: the tools drove the app and the dump still failed. Nothing in the tools or the app was changed to work around it. |
| WiFiAnalyzer | `com.vrem.wifianalyzer.debug` | Cannot report coverage at all: the app does not declare android.permission.INTERNET, and the coverage agent hands its counts over a local socket, which Android denies without that permission. Checked with every tool for 15 minutes each: the tools drove the app and the dump still failed. Nothing in the tools or the app was changed to work around it. |
| Unlauncher | `com.jkuester.unlauncher` | Cannot report coverage at all: the app does not declare android.permission.INTERNET, which the coverage agent's socket needs, so no tool can read coverage from it. Read from the app's manifest, so no test time was spent on it. |
| KISS Launcher | `fr.neamar.kiss.debug` | Cannot report coverage at all: the app does not declare android.permission.INTERNET, which the coverage agent's socket needs, so no tool can read coverage from it. Read from the app's manifest, so no test time was spent on it. |
| Photok | `dev.leonlatsch.photok.debug` | Cannot report coverage at all: the app does not declare android.permission.INTERNET, which the coverage agent's socket needs. Read from the app's manifest, so no test time was spent on it. |

## What to be careful about when citing this

- The coverage metric is JaCoCo **probe** coverage, described above. It is not
  comparable with the papers' published percentages.
- **GPTDroid is a reconstruction**: no official source is published. The paper's
  functionality-aware memory (§2.3) and its five operation primitives (§2.2) are
  implemented here, so these are not the floor they were while that memory was
  missing; they still cannot be diffed against an official implementation.
- **LLMDroid ran in time mode**, which does not use the paper's own coverage-growth
  trigger to end a session.
- One run each, per app per tool. There is no repeat-run variance here, so small
  differences between tools on a single app should not be read as meaningful; the
  50-app medians are the comparison this study supports.
- Ten to twelve tests shared one machine, so all three tools were slowed alike.
- The set is apps that **can** report coverage, which is a specific and not random
  slice of Android software: open-source apps buildable with coverage switched on,
  or shipped that way by a benchmark.
