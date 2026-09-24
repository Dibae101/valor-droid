# VALOR-Droid campaign runs (GitHub Actions, emulator, JaCoCo probe coverage)

All runs use the `valordroid-jacoco-smoke` workflow on an Android emulator with
JaCoCo-instrumented APKs. Coverage = JaCoCo probe coverage unless noted.
Budgets are 3600s unless noted.

## Batch 1 — 2026-09-21, pollinations config, round-2 fixes (forced routes on, 48 model calls)

| App | Probe | Activities entered |
|---|---|---|
| OpenLauncher | 66.1% | — |
| Nextcloud | 51.6% | 4/35 |
| MaterialFBook | 48.1% | — |
| Amaze | 45.5% | — |
| PhonographDEBUG | 44.2% | — |
| Vespucci | 35.5% | — |
| WordPress | 32.7% | 5/68 |
| AnkiDroid | 25.1% | — |
| AndBible | 23.7% | 2/31 |
| GeohashDroid | 21.2% | — |

AnkiDroid re-run (round-1 config), 2026-09-21: 35.0% probe, full budget, exit 0.

## Batch 2 — 2026-09-21 ~19:32 EDT, round-2 config

| App | Probe | Activities | Notes |
|---|---|---|---|
| AntennaPodDebug | 42.0% | 9/12 | full budget, exit 0 |
| Commons | 40.0% | 8/14 | full budget, exit 0 |
| FrostDebug | 49.3% | 10/16 | ~82% budget, recovery_ladder_exhausted |
| FeederD | 52.8% | 2/10 | full budget, exit 0 |

First dispatch failed for all 4 (validator: core max_model_calls 24 vs model 48;
FeederD bundle missing original.apk); fixed and re-dispatched clean.

## Jev verification — 2026-09-21

- GeohashDroid 10-min smoke, Jev config: exit 0, 23.3% probe (baseline 21.2%), 5/7
  screens; Jev never fired (no stalls in the window).
- GeohashDroid 3600s, Jev config, run 35671893086: exit 0, 24.3% probe (756/3115),
  5/7 activities, 947 attempts, 15 crashes (one signature). Jev consulted 4× on
  recovery choices, abstained 4× by design; no tarpit verdicts. Wiring proven safe;
  no exploration gain attributable to Jev.

## Round-3 verification — 2026-09-22

Six harness fixes active (spin loop-breaker, exported-component sweep fallback,
LLM gate counts zero-gain rungs, dead-route retirement, crash blacklist,
long-press size filter).

- FrostDebug, run 35684044850: 47.78% probe (3951/8269), 9/16 activities, exit 0,
  90.7% budget, stop reason recovery_ladder_exhausted (unchanged from batch 2).
  Fix 1 fired 19×; model_calls 1/48.
- FeederD, run 35684043466: 47.32% probe (16676/35240), 5/10 activities, exit 10 —
  aborted ~36% into budget, `screen_never_idle_unrecoverable` (webview shell stuck).
  Infra failure; not a valid round-3 comparison.

Verdict: no coverage gain from round-3 fixes on these two apps.

## Sunflower — 2026-09-23, techbros-andy fork (first fork run)

- Run 35802588627: failed fast (exit 2) — wrong `model_config` input value.
- Run 35812577704: 66.32% probe (1010/1523), 100% class coverage (88/88),
  1/1 activities, exit 0, 75.3% budget, recovery_ladder_exhausted.

## Averages

15 of 50 dataset apps tested. Mean of each app's best probe coverage: 43.8%.
