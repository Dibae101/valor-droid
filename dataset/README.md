# The data: 50 apps and 150 one-hour tests per model

This folder holds the app files that were tested and the tables of every number the
report quotes: 1.17 GB of app files, 50 apps,
150 tests per model pass.

Each test's own artifacts sit under [`../results/`](../results), one folder per
model pass, 3,388 files in all. The `evidence` column of
[`tests.tsv`](tests.tsv) gives the path for any row.

*Rebuilt from the raw runs by `python3 reproduction/runner/collect_release.py`
(apps and tables) and `python3 run_second_pass.py --phase organize` (the per-test
artifacts under `results/`).*

## Layout

```
dataset/                      the apps, and the tables of numbers about them
├── apks/                     one file per app
│   └── Money-Manager-Ex.apk
├── apps.tsv / apps.json      one row per app: version, install name, SHA-256, origin
└── tests.tsv / tests.json    one row per test: the reported numbers

results/                      each test's own artifacts, one folder per model pass
├── gemma-local-result/       the local pass; the `evidence` column points here
│   └── Money_Manager_Ex/
│       ├── gptdroid/
│       ├── llmdroid/
│       └── hybriddroid/
│           ├── result.json             the test: length, actions, status, checksums
│           ├── code_coverage.json      coverage points reached, and the total
│           ├── coverage_timeline.json  coverage every 30 seconds through the hour
│           ├── coverage.exec           JaCoCo's own execution data
│           ├── activity_coverage.json  screens reached, and which ones
│           ├── logcat.log.gz           the device log for the whole hour
│           ├── tool.log.gz             what the tool itself wrote
│           ├── steps.jsonl.gz          the actions it took, in order
│           └── crash.log.gz            crash lines, when there were any
└── openai-commercial-result/ the commercial pass, same layout
```

The raw runs are untouched under `reproduction/results/run-60m/` and
`reproduction/results/openai-60m/`, screenshots, per-screen dumps and every
superseded attempt included. `results/` is the readable copy of their finished runs.

## Every app, and what each tool reached

Code coverage, each figure linking to the test that produced it.

| App | Version | GPTDroid | LLMDroid | HybridDroid |
|---|---|---:|---:|---:|
| [A Photo Manager](apks/A-Photo-Manager.apk) | 0.6.4.180314 | [no coverage](../results/gemma-local-result/A_Photo_Manager/gptdroid/result.json) | [17.16%](../results/gemma-local-result/A_Photo_Manager/llmdroid/result.json) | [17.10%](../results/gemma-local-result/A_Photo_Manager/hybriddroid/result.json) |
| [Amaze](apks/Amaze.apk) | 3.2.1 | [26.19%](../results/gemma-local-result/Amaze/gptdroid/result.json) | [32.01%](../results/gemma-local-result/Amaze/llmdroid/result.json) | [35.35%](../results/gemma-local-result/Amaze/hybriddroid/result.json) |
| [And Bible](apks/And-Bible.apk) | 3.2.327 | [27.60%](../results/gemma-local-result/And_Bible/gptdroid/result.json) | [34.96%](../results/gemma-local-result/And_Bible/llmdroid/result.json) | [50.67%](../results/gemma-local-result/And_Bible/hybriddroid/result.json) |
| [AnkiDroid](apks/AnkiDroid.apk) | 2.6beta6 | [18.65%](../results/gemma-local-result/AnkiDroid/gptdroid/result.json) | [31.47%](../results/gemma-local-result/AnkiDroid/llmdroid/result.json) | [38.59%](../results/gemma-local-result/AnkiDroid/hybriddroid/result.json) |
| [AntennaPod Debug](apks/AntennaPod-Debug.apk) | 3.5.0 | [23.97%](../results/gemma-local-result/AntennaPod_Debug/gptdroid/result.json) | [26.61%](../results/gemma-local-result/AntennaPod_Debug/llmdroid/result.json) | [52.74%](../results/gemma-local-result/AntennaPod_Debug/hybriddroid/result.json) |
| [Binary Eye](apks/Binary-Eye.apk) | 1.63.12 | [31.26%](../results/gemma-local-result/Binary_Eye/gptdroid/result.json) | [34.70%](../results/gemma-local-result/Binary_Eye/llmdroid/result.json) | [46.66%](../results/gemma-local-result/Binary_Eye/hybriddroid/result.json) |
| [Breezy Weather](apks/Breezy-Weather.apk) | 5.2.8-r1 | [26.82%](../results/gemma-local-result/Breezy_Weather/gptdroid/result.json) | [36.24%](../results/gemma-local-result/Breezy_Weather/llmdroid/result.json) | [49.56%](../results/gemma-local-result/Breezy_Weather/hybriddroid/result.json) |
| [Chess](apks/Chess.apk) | 9.5.0 | [58.42%](../results/gemma-local-result/Chess/gptdroid/result.json) | [23.05%](../results/gemma-local-result/Chess/llmdroid/result.json) | [33.36%](../results/gemma-local-result/Chess/hybriddroid/result.json) |
| [Commons](apks/Commons.apk) | 2.7.1-debug-HEAD~c6b74e72f | [36.81%](../results/gemma-local-result/Commons/gptdroid/result.json) | [35.73%](../results/gemma-local-result/Commons/llmdroid/result.json) | [39.31%](../results/gemma-local-result/Commons/hybriddroid/result.json) |
| [Eventyay Attendee](apks/Eventyay-Attendee.apk) | 0.5.0 | [34.25%](../results/gemma-local-result/Eventyay_Attendee/gptdroid/result.json) | [37.72%](../results/gemma-local-result/Eventyay_Attendee/llmdroid/result.json) | [37.97%](../results/gemma-local-result/Eventyay_Attendee/hybriddroid/result.json) |
| [Fedilab](apks/Fedilab.apk) | 3.28.0 | [8.41%](../results/gemma-local-result/Fedilab/gptdroid/result.json) | [14.67%](../results/gemma-local-result/Fedilab/llmdroid/result.json) | [14.37%](../results/gemma-local-result/Fedilab/hybriddroid/result.json) |
| [FeederD](apks/FeederD.apk) | 2.6.33 | [46.84%](../results/gemma-local-result/FeederD/gptdroid/result.json) | [46.26%](../results/gemma-local-result/FeederD/llmdroid/result.json) | [47.29%](../results/gemma-local-result/FeederD/hybriddroid/result.json) |
| [Firefox Lite Dev](apks/Firefox-Lite-Dev.apk) | 2.1.20.debug.ting | [44.00%](../results/gemma-local-result/Firefox_Lite_Dev/gptdroid/result.json) | [21.23%](../results/gemma-local-result/Firefox_Lite_Dev/llmdroid/result.json) | [28.65%](../results/gemma-local-result/Firefox_Lite_Dev/hybriddroid/result.json) |
| [Frost Debug](apks/Frost-Debug.apk) | 2.2.1-debug | [33.82%](../results/gemma-local-result/Frost_Debug/gptdroid/result.json) | [34.03%](../results/gemma-local-result/Frost_Debug/llmdroid/result.json) | [34.68%](../results/gemma-local-result/Frost_Debug/hybriddroid/result.json) |
| [Geohash Droid](apks/Geohash-Droid.apk) | 0.9.4 | [17.25%](../results/gemma-local-result/Geohash_Droid/gptdroid/result.json) | [13.56%](../results/gemma-local-result/Geohash_Droid/llmdroid/result.json) | [12.42%](../results/gemma-local-result/Geohash_Droid/hybriddroid/result.json) |
| [GPSLogger](apks/GPSLogger.apk) | 131-rc2 | [23.16%](../results/gemma-local-result/GPSLogger/gptdroid/result.json) | [30.35%](../results/gemma-local-result/GPSLogger/llmdroid/result.json) | [40.30%](../results/gemma-local-result/GPSLogger/hybriddroid/result.json) |
| [Infinity For Reddit](apks/Infinity-For-Reddit.apk) | 7.3.4 (DEBUG) | [16.11%](../results/gemma-local-result/Infinity_For_Reddit/gptdroid/result.json) | [20.23%](../results/gemma-local-result/Infinity_For_Reddit/llmdroid/result.json) | [25.06%](../results/gemma-local-result/Infinity_For_Reddit/hybriddroid/result.json) |
| [Jellyfin Android](apks/Jellyfin-Android.apk) | 0.0.0-dev.1 | [33.84%](../results/gemma-local-result/Jellyfin_Android/gptdroid/result.json) | [39.13%](../results/gemma-local-result/Jellyfin_Android/llmdroid/result.json) | [32.37%](../results/gemma-local-result/Jellyfin_Android/hybriddroid/result.json) |
| [Kiwix](apks/Kiwix.apk) | 3.11.1 | [32.28%](../results/gemma-local-result/Kiwix/gptdroid/result.json) | [32.56%](../results/gemma-local-result/Kiwix/llmdroid/result.json) | [45.43%](../results/gemma-local-result/Kiwix/hybriddroid/result.json) |
| [Kore](apks/Kore.apk) | v3.1.0 | [15.30%](../results/gemma-local-result/Kore/gptdroid/result.json) | [17.06%](../results/gemma-local-result/Kore/llmdroid/result.json) | [10.70%](../results/gemma-local-result/Kore/hybriddroid/result.json) |
| [LibreTorrent](apks/LibreTorrent.apk) | 3.5.2-DEBUG | [27.42%](../results/gemma-local-result/LibreTorrent/gptdroid/result.json) | [34.53%](../results/gemma-local-result/LibreTorrent/llmdroid/result.json) | [32.61%](../results/gemma-local-result/LibreTorrent/hybriddroid/result.json) |
| [MaterialFBook](apks/MaterialFBook.apk) | 4.0.2 | [40.52%](../results/gemma-local-result/MaterialFBook/gptdroid/result.json) | [40.41%](../results/gemma-local-result/MaterialFBook/llmdroid/result.json) | [51.41%](../results/gemma-local-result/MaterialFBook/hybriddroid/result.json) |
| [Money Manager Ex](apks/Money-Manager-Ex.apk) | 2024.09.21 | [26.21%](../results/gemma-local-result/Money_Manager_Ex/gptdroid/result.json) | [28.33%](../results/gemma-local-result/Money_Manager_Ex/llmdroid/result.json) | [43.73%](../results/gemma-local-result/Money_Manager_Ex/hybriddroid/result.json) |
| [Muzei](apks/Muzei.apk) | 3.6.2 Debug | [45.84%](../results/gemma-local-result/Muzei/gptdroid/result.json) | [38.00%](../results/gemma-local-result/Muzei/llmdroid/result.json) | [45.59%](../results/gemma-local-result/Muzei/hybriddroid/result.json) |
| [My Expenses Debug](apks/My-Expenses-Debug.apk) | 3.9.3 | [30.57%](../results/gemma-local-result/My_Expenses_Debug/gptdroid/result.json) | [25.91%](../results/gemma-local-result/My_Expenses_Debug/llmdroid/result.json) | [42.59%](../results/gemma-local-result/My_Expenses_Debug/hybriddroid/result.json) |
| [NewPipe Debug](apks/NewPipe-Debug.apk) | 0.27.2 | [24.41%](../results/gemma-local-result/NewPipe_Debug/gptdroid/result.json) | [30.77%](../results/gemma-local-result/NewPipe_Debug/llmdroid/result.json) | [15.46%](../results/gemma-local-result/NewPipe_Debug/hybriddroid/result.json) |
| [Nextcloud](apks/Nextcloud.apk) | 3.9.0 RC1 | [48.77%](../results/gemma-local-result/Nextcloud/gptdroid/result.json) | [49.07%](../results/gemma-local-result/Nextcloud/llmdroid/result.json) | [50.43%](../results/gemma-local-result/Nextcloud/hybriddroid/result.json) |
| [ODK Collect](apks/ODK-Collect.apk) | v1.23.0-beta.2-dirty | [23.30%](../results/gemma-local-result/ODK_Collect/gptdroid/result.json) | [26.91%](../results/gemma-local-result/ODK_Collect/llmdroid/result.json) | [32.68%](../results/gemma-local-result/ODK_Collect/hybriddroid/result.json) |
| [Omni Notes Alpha](apks/Omni-Notes-Alpha.apk) | 6.4.0 | [26.60%](../results/gemma-local-result/Omni_Notes_Alpha/gptdroid/result.json) | [31.08%](../results/gemma-local-result/Omni_Notes_Alpha/llmdroid/result.json) | [47.96%](../results/gemma-local-result/Omni_Notes_Alpha/hybriddroid/result.json) |
| [Open Food Facts](apks/Open-Food-Facts.apk) | 3.9.0 | [21.10%](../results/gemma-local-result/Open_Food_Facts/gptdroid/result.json) | [24.38%](../results/gemma-local-result/Open_Food_Facts/llmdroid/result.json) | [30.46%](../results/gemma-local-result/Open_Food_Facts/hybriddroid/result.json) |
| [OpenLauncher](apks/OpenLauncher.apk) | 0.3.1 | [48.94%](../results/gemma-local-result/OpenLauncher/gptdroid/result.json) | [33.14%](../results/gemma-local-result/OpenLauncher/llmdroid/result.json) | [58.56%](../results/gemma-local-result/OpenLauncher/hybriddroid/result.json) |
| [Orgzly Revived](apks/Orgzly-Revived.apk) | 1.8.27-beta.2 | [25.65%](../results/gemma-local-result/Orgzly_Revived/gptdroid/result.json) | [24.45%](../results/gemma-local-result/Orgzly_Revived/llmdroid/result.json) | [43.60%](../results/gemma-local-result/Orgzly_Revived/hybriddroid/result.json) |
| [ownCloud](apks/ownCloud.apk) | 4.4.0 | [24.84%](../results/gemma-local-result/ownCloud/gptdroid/result.json) | [24.47%](../results/gemma-local-result/ownCloud/llmdroid/result.json) | [24.74%](../results/gemma-local-result/ownCloud/hybriddroid/result.json) |
| [OwnTracks](apks/OwnTracks.apk) | 2.5.3 | [35.26%](../results/gemma-local-result/OwnTracks/gptdroid/result.json) | [33.80%](../results/gemma-local-result/OwnTracks/llmdroid/result.json) | [35.86%](../results/gemma-local-result/OwnTracks/hybriddroid/result.json) |
| [Phonograph DEBUG](apks/Phonograph-DEBUG.apk) | 0.15.0 BETA 1 DEBUG | [34.15%](../results/gemma-local-result/Phonograph_DEBUG/gptdroid/result.json) | [35.37%](../results/gemma-local-result/Phonograph_DEBUG/llmdroid/result.json) | [47.56%](../results/gemma-local-result/Phonograph_DEBUG/hybriddroid/result.json) |
| [RedReader](apks/RedReader.apk) | 1.24.1 | [23.29%](../results/gemma-local-result/RedReader/gptdroid/result.json) | [39.53%](../results/gemma-local-result/RedReader/llmdroid/result.json) | [49.29%](../results/gemma-local-result/RedReader/hybriddroid/result.json) |
| [Scarlet Notes FD](apks/Scarlet-Notes-FD.apk) | 6.9.5-pro | [35.20%](../results/gemma-local-result/Scarlet_Notes_FD/gptdroid/result.json) | [36.34%](../results/gemma-local-result/Scarlet_Notes_FD/llmdroid/result.json) | [48.40%](../results/gemma-local-result/Scarlet_Notes_FD/hybriddroid/result.json) |
| [Simple Alarm Clock](apks/Simple-Alarm-Clock.apk) | 3.16.01 | [56.43%](../results/gemma-local-result/Simple_Alarm_Clock/gptdroid/result.json) | [57.71%](../results/gemma-local-result/Simple_Alarm_Clock/llmdroid/result.json) | [70.25%](../results/gemma-local-result/Simple_Alarm_Clock/hybriddroid/result.json) |
| [SkyTube](apks/SkyTube.apk) | 2.999 | [25.39%](../results/gemma-local-result/SkyTube/gptdroid/result.json) | [21.26%](../results/gemma-local-result/SkyTube/llmdroid/result.json) | [31.09%](../results/gemma-local-result/SkyTube/hybriddroid/result.json) |
| [StreetComplete](apks/StreetComplete.apk) | 57.3 | [34.42%](../results/gemma-local-result/StreetComplete/gptdroid/result.json) | [33.52%](../results/gemma-local-result/StreetComplete/llmdroid/result.json) | [45.22%](../results/gemma-local-result/StreetComplete/hybriddroid/result.json) |
| [Sunflower](apks/Sunflower.apk) | 0.1.6 | [53.13%](../results/gemma-local-result/Sunflower/gptdroid/result.json) | [60.44%](../results/gemma-local-result/Sunflower/llmdroid/result.json) | [66.91%](../results/gemma-local-result/Sunflower/hybriddroid/result.json) |
| [Trackbook](apks/Trackbook.apk) | 2.1.2 | [30.01%](../results/gemma-local-result/Trackbook/gptdroid/result.json) | [33.39%](../results/gemma-local-result/Trackbook/llmdroid/result.json) | [38.40%](../results/gemma-local-result/Trackbook/hybriddroid/result.json) |
| [Transistor](apks/Transistor.apk) | 4.1.1 | [27.33%](../results/gemma-local-result/Transistor/gptdroid/result.json) | [23.42%](../results/gemma-local-result/Transistor/llmdroid/result.json) | [30.25%](../results/gemma-local-result/Transistor/hybriddroid/result.json) |
| [Twire](apks/Twire.apk) | 2.11.0-DEBUG | [38.23%](../results/gemma-local-result/Twire/gptdroid/result.json) | [27.02%](../results/gemma-local-result/Twire/llmdroid/result.json) | [49.41%](../results/gemma-local-result/Twire/hybriddroid/result.json) |
| [Ultrasonic](apks/Ultrasonic.apk) | 3.2.0 | [36.28%](../results/gemma-local-result/Ultrasonic/gptdroid/result.json) | [35.52%](../results/gemma-local-result/Ultrasonic/llmdroid/result.json) | [33.04%](../results/gemma-local-result/Ultrasonic/hybriddroid/result.json) |
| [Vespucci](apks/Vespucci.apk) | 11.0.0.8 | [23.42%](../results/gemma-local-result/Vespucci/gptdroid/result.json) | [27.05%](../results/gemma-local-result/Vespucci/llmdroid/result.json) | [31.16%](../results/gemma-local-result/Vespucci/hybriddroid/result.json) |
| [Vinyl Music Player](apks/Vinyl-Music-Player.apk) | 1.11.0-HEAD_d8aeb8c_260816-1554 CI DEBUG | [31.10%](../results/gemma-local-result/Vinyl_Music_Player/gptdroid/result.json) | [26.65%](../results/gemma-local-result/Vinyl_Music_Player/llmdroid/result.json) | [30.63%](../results/gemma-local-result/Vinyl_Music_Player/hybriddroid/result.json) |
| [Wallabag](apks/Wallabag.apk) | 2.5.3-DEBUG | [19.95%](../results/gemma-local-result/Wallabag/gptdroid/result.json) | [20.81%](../results/gemma-local-result/Wallabag/llmdroid/result.json) | [28.25%](../results/gemma-local-result/Wallabag/hybriddroid/result.json) |
| [Wikipedia Alpha](apks/Wikipedia-Alpha.apk) | 2.7.50507-alpha-2024-10-25 | [29.92%](../results/gemma-local-result/Wikipedia_Alpha/gptdroid/result.json) | [39.08%](../results/gemma-local-result/Wikipedia_Alpha/llmdroid/result.json) | [41.39%](../results/gemma-local-result/Wikipedia_Alpha/hybriddroid/result.json) |
| [WordPress](apks/WordPress.apk) | 9.2 | [34.33%](../results/gemma-local-result/WordPress/gptdroid/result.json) | [34.14%](../results/gemma-local-result/WordPress/llmdroid/result.json) | [37.27%](../results/gemma-local-result/WordPress/hybriddroid/result.json) |

## Checking a copy is what was tested

```bash
cd dataset/apks
sha256sum -c <(awk -F'\t' 'NR==1 {for (i=1; i<=NF; i++) col[$i]=i; next}
                {print $col["sha256"] "  " $col["apk_file"]}' ../apps.tsv)
```

## Reading the tables instead of the folders

```bash
# the ten highest coverage results
sort -t$'\t' -k8 -gr dataset/tests.tsv | head -10 | cut -f1,4,8

# one tool only
awk -F'\t' '$4=="hybriddroid"' dataset/tests.tsv | cut -f1,8,11
```

Column 8 is code coverage, column 11 is screens reached. The report is in
[result.md](../result.md); how these apps were chosen and built is in
[dataset.md](../dataset.md).
