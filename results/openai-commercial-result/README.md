# GPT-4o mini (commercial OpenAI API)

Each of 50 Android apps driven for 60 minutes by each of three LLM GUI-testing tools, with code coverage read live from the running app.

- **Model** `gpt-4o-mini`
- **Served by** OpenAI API
- **Tests** 149 finished of 150 planned
- **Written** 2026-08-25 17:15 UTC
- **Raw harness tree** `reproduction/results/openai-60m/`

## Results by tool

| Measure | GPTDroid | LLMDroid | HybridDroid |
|---|---|---|---|
| Tests finished | 49 | 50 | 50 |
| Code coverage, median | 30.19% | 30.55% | 39.55% |
| Code coverage, mean | 31.00% | 30.68% | 38.75% |
| Code coverage, lowest | 9.82% | 9.52% | 6.03% |
| Code coverage, highest | 59.55% | 50.98% | 69.78% |
| Screens reached, median | 14.29% | 26.67% | 33.33% |
| Actions, total | 26,183 | 36,332 | 54,190 |
| Model calls, total | 26,233 | 3,886 | 356 |
| Input tokens, total | 38,807,389 | not recorded by the tool | 130,820 |
| Output tokens, total | 631,940 | not recorded by the tool | 418 |
| Model cost, all tests | $6.2003 | not recorded | $0.0199 |
| Apps where it led | 6 | 4 | 40 |

## Every test, per app

Each cell reads **code coverage · screens reached · actions**.

| App | GPTDroid | LLMDroid | HybridDroid | Led by |
|---|---|---|---|---|
| A Photo Manager | not available | 17.10% · 11% · 420 | 17.00% · 11% · 595 | LLMDroid |
| Amaze | 25.64% · 17% · 703 | 27.70% · 33% · 729 | 40.29% · 50% · 1,077 | HybridDroid |
| And Bible | 20.89% · 6% · 29 | 29.58% · 55% · 774 | 46.88% · 55% · 1,123 | HybridDroid |
| AnkiDroid | 18.70% · 14% · 7 | 24.31% · 57% · 744 | 37.79% · 48% · 1,105 | HybridDroid |
| AntennaPod Debug | 23.63% · 25% · 671 | 24.49% · 8% · 708 | 55.39% · 25% · 1,112 | HybridDroid |
| Binary Eye | 32.57% · 75% · 4 | 36.91% · 50% · 780 | 47.09% · 75% · 1,141 | HybridDroid |
| Breezy Weather | 27.05% · 7% · 705 | 36.91% · 21% · 680 | 50.83% · 36% · 1,183 | HybridDroid |
| Chess | 30.19% · 12% · 688 | 44.61% · 69% · 777 | 6.03% · 75% · 1,180 | LLMDroid |
| Commons | 37.59% · 14% · 659 | 35.73% · 21% · 755 | 40.34% · 21% · 1,218 | HybridDroid |
| Eventyay Attendee | 37.81% · 8% · 711 | 35.01% · 8% · 774 | 42.34% · 8% · 1,169 | HybridDroid |
| Fedilab | 9.82% · 3% · 679 | 9.52% · 1% · 793 | 14.45% · 1% · 1,142 | HybridDroid |
| FeederD | 46.77% · 10% · 685 | 49.88% · 20% · 770 | 47.71% · 30% · 1,079 | LLMDroid |
| Firefox Lite Dev | 46.44% · 10% · 482 | 21.35% · 3% · 754 | 28.65% · 3% · 1,020 | GPTDroid |
| Frost Debug | 33.96% · 12% · 699 | 34.14% · 12% · 757 | 34.90% · 12% · 1,148 | HybridDroid |
| Geohash Droid | 14.62% · 14% · 4 | 12.95% · 43% · 282 | 12.42% · 29% · 1,093 | GPTDroid |
| GPSLogger | 22.93% · 9% · 703 | 30.49% · 27% · 762 | 40.44% · 36% · 1,078 | HybridDroid |
| Infinity For Reddit | 18.32% · 5% · 652 | 18.67% · 11% · 763 | 26.12% · 15% · 1,140 | HybridDroid |
| Jellyfin Android | 39.07% · 20% · 643 | 32.24% · 40% · 779 | 32.38% · 20% · 1,085 | GPTDroid |
| Kiwix | 37.07% · 33% · 19 | 36.14% · 33% · 766 | 44.69% · 33% · 1,186 | HybridDroid |
| Kore | 19.01% · 12% · 698 | 18.56% · 6% · 694 | 10.81% · 6% · 1,035 | GPTDroid |
| LibreTorrent | 28.96% · 11% · 668 | 34.53% · 33% · 274 | 45.55% · 61% · 1,139 | HybridDroid |
| MaterialFBook | 38.67% · 25% · 611 | 40.41% · 25% · 763 | 49.78% · 50% · 1,142 | HybridDroid |
| Money Manager Ex | 26.38% · 12% · 227 | 15.63% · 10% · 771 | 41.25% · 37% · 1,163 | HybridDroid |
| Muzei | 45.84% · 7% · 417 | 38.00% · 7% · 717 | 46.85% · 7% · 818 | HybridDroid |
| My Expenses Debug | 34.72% · 7% · 685 | 29.35% · 23% · 776 | 41.18% · 19% · 1,112 | HybridDroid |
| NewPipe Debug | 26.92% · 25% · 699 | 30.25% · 50% · 756 | 43.26% · 42% · 1,103 | HybridDroid |
| Nextcloud | 48.77% · 6% · 1 | 47.94% · 9% · 745 | 51.12% · 3% · 1,096 | HybridDroid |
| ODK Collect | 23.29% · 8% · 4 | 22.99% · 31% · 778 | 33.58% · 28% · 1,134 | HybridDroid |
| Omni Notes Alpha | 29.09% · 8% · 688 | 31.10% · 15% · 764 | 33.80% · 31% · 1,094 | HybridDroid |
| Open Food Facts | 23.94% · 17% · 688 | 22.14% · 38% · 451 | 29.04% · 42% · 1,070 | HybridDroid |
| OpenLauncher | 31.16% · 25% · 727 | 36.12% · 50% · 703 | 57.04% · 62% · 989 | HybridDroid |
| Orgzly Revived | 25.59% · 10% · 4 | 31.65% · 10% · 739 | 45.25% · 20% · 1,072 | HybridDroid |
| ownCloud | 26.25% · 19% · 658 | 24.49% · 4% · 822 | 24.74% · 4% · 973 | GPTDroid |
| OwnTracks | 34.47% · 17% · 703 | 33.38% · 8% · 776 | 35.84% · 8% · 1,101 | HybridDroid |
| Phonograph DEBUG | 32.59% · 20% · 577 | 26.24% · 27% · 761 | 48.51% · 40% · 1,070 | HybridDroid |
| RedReader | 33.41% · 13% · 678 | 22.50% · 17% · 818 | 27.65% · 39% · 1,191 | GPTDroid |
| Scarlet Notes FD | 32.99% · 7% · 697 | 41.48% · 27% · 769 | 49.24% · 27% · 1,144 | HybridDroid |
| Simple Alarm Clock | 52.46% · 20% · 658 | 50.98% · 40% · 774 | 69.78% · 40% · 1,133 | HybridDroid |
| SkyTube | 25.24% · 15% · 690 | 20.27% · 23% · 753 | 38.81% · 31% · 1,026 | HybridDroid |
| StreetComplete | 33.15% · 14% · 683 | 34.63% · 57% · 779 | 38.08% · 57% · 1,020 | HybridDroid |
| Sunflower | 59.55% · 100% · 679 | 50.22% · 100% · 745 | 66.91% · 100% · 1,024 | HybridDroid |
| Trackbook | 31.82% · 100% · 16 | 30.62% · 100% · 743 | 36.97% · 100% · 1,065 | HybridDroid |
| Transistor | 24.10% · 50% · 697 | 25.63% · 50% · 783 | 33.35% · 50% · 1,071 | HybridDroid |
| Twire | 31.95% · 15% · 430 | 28.49% · 20% · 724 | 54.70% · 65% · 1,007 | HybridDroid |
| Ultrasonic | 33.32% · 33% · 694 | 43.38% · 33% · 762 | 37.04% · 33% · 1,047 | LLMDroid |
| Vespucci | 24.00% · 16% · 703 | 25.44% · 32% · 754 | 37.86% · 53% · 1,079 | HybridDroid |
| Vinyl Music Player | 29.71% · 16% · 676 | 24.75% · 32% · 774 | 32.67% · 37% · 1,052 | HybridDroid |
| Wallabag | 25.61% · 30% · 690 | 22.37% · 50% · 771 | 29.04% · 50% · 1,143 | HybridDroid |
| Wikipedia Alpha | 28.30% · 3% · 687 | 38.00% · 29% · 749 | 44.75% · 28% · 1,095 | HybridDroid |
| WordPress | 34.64% · 4% · 707 | 34.67% · 6% · 777 | 37.34% · 6% · 1,108 | HybridDroid |

## Layout

```
results/openai-commercial-result/
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

The raw harness tree these were read from is `reproduction/results/openai-60m/`, which additionally holds the screenshots and the per-screen dumps. Those stay on the test machine: they run to tens of gigabytes and no number in any report comes from them.

## What the numbers mean

**Code coverage** is JaCoCo *probe* coverage: the share of the coverage points compiled into the app's own code that were reached. It is read from the running app every 30 seconds and again at the end. It is not line, branch, method, class or statement coverage, and it should not be compared with the percentages published in the tools' papers.

**Screens reached** is activity coverage: the app's own screens that were shown, out of the screens the app declares.

A test counts as finished only when it ran at least 55 of its 60 minutes, reported code coverage, took at least one action, and reached at least one screen.
