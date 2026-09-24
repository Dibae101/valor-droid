# Gemma 3 4B (local model)

Each of 50 Android apps driven for 60 minutes by each of three LLM GUI-testing tools, with code coverage read live from the running app.

- **Model** `google.gemma-3-4b-it`
- **Served by** local, OpenAI-compatible
- **Tests** 149 finished of 150 planned
- **Written** 2026-08-25 17:15 UTC
- **Raw harness tree** `reproduction/results/run-60m/`

## Results by tool

| Measure | GPTDroid | LLMDroid | HybridDroid |
|---|---|---|---|
| Tests finished | 49 | 50 | 50 |
| Code coverage, median | 30.01% | 32.28% | 38.50% |
| Code coverage, mean | 31.37% | 31.50% | 38.54% |
| Code coverage, lowest | 8.41% | 13.56% | 10.70% |
| Code coverage, highest | 58.42% | 60.44% | 70.25% |
| Screens reached, median | 11.65% | 25.66% | 32.45% |
| Actions, total | 30,168 | 24,460 | 53,142 |
| Model calls, total | 30,218 | 6,805 | 283 |
| Input tokens, total | 38,372,013 | not recorded by the tool | 101,337 |
| Output tokens, total | 804,472 | not recorded by the tool | 951 |
| Model cost, all tests | $1.5992 | not recorded | $0.0041 |
| Apps where it led | 7 | 6 | 37 |

## Every test, per app

Each cell reads **code coverage · screens reached · actions**.

| App | GPTDroid | LLMDroid | HybridDroid | Led by |
|---|---|---|---|---|
| A Photo Manager | not available | 17.16% · 11% · 46 | 17.10% · 11% · 573 | LLMDroid |
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

## Layout

```
results/gemma-local-result/
├── <App name>/
│   ├── gptdroid/      that tool's evidence for this app
│   ├── llmdroid/
│   └── hybriddroid/
├── ... one folder per app
├── summary-gptdroid.tsv    one row per app, for that tool
├── summary-llmdroid.tsv
├── summary-hybriddroid.tsv
├── tests.tsv       one row per test, every number in this document
└── README.md       this file
```

The three tools sit inside the app they tested, so one app's three results are side by side, which is the comparison this study is about. It is also how the rest of the repository stores a result.

Each tool folder holds that test's evidence: `result.json`, `native_result.json`, `code_coverage.json`, `coverage_timeline.json`, `activity_coverage.json`, `coverage.exec`, `execinfo.txt`, `loop_detection.json`, `runner.log`, and the gzipped device log, tool log and step record.

`crash.log.gz` is there only for a test where the app actually crashed. `result.json` says `crash_detected` either way, so no file means no crash rather than no evidence.

The raw harness tree these were read from is `reproduction/results/run-60m/`, which additionally holds the screenshots and the per-screen dumps. Those stay on the test machine: they run to tens of gigabytes and no number in any report comes from them.

## What the numbers mean

**Code coverage** is JaCoCo *probe* coverage: the share of the coverage points compiled into the app's own code that were reached. It is read from the running app every 30 seconds and again at the end. It is not line, branch, method, class or statement coverage, and it should not be compared with the percentages published in the tools' papers.

**Screens reached** is activity coverage: the app's own screens that were shown, out of the screens the app declares.

A test counts as finished only when it ran at least 55 of its 60 minutes, reported code coverage, took at least one action, and reached at least one screen.
