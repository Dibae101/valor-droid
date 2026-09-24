# Final results: three models across 450 Android GUI tests

## Study setup

The design is 50 apps × 3 tools × 3 model campaigns, with one intended
60-minute run per cell. Results are descriptive; one run per cell does not estimate repeat-run
variance. The tools are grouped by their directory names, not by tool aliases
inside JSON records.

| Setting | Value |
|---|---|
| Host | AWS `m7g.4xlarge`, 16 Arm Neoverse-V1 CPUs, 61 GB memory |
| Android | redroid Android 14 (API 34), `arm64-v8a`, 720 × 1280 |
| Driving budget | 3,600 seconds per cell; install and final collection outside the budget |
| Completion rule | `duration_seconds_actual >= 3300` |
| Coverage | external JaCoCo probe dump every 30 seconds and at the end |
| Parallel workers | 10 for Gemma, 10 for GPT-4o-mini, 14 for Gemini |

| Model | Observed raw model ID(s) | Workers | Status counts | Finished | Coverage | Duration mean | Duration median | Duration total | Duration range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemma 3 4B | `google.gemma-3-4b-it` | 10 | `completed_forced_stop` 100, `completed_no_actions` 1, `succeeded` 49 | 150/150 | 149/150 | 60.02 min | 60.01 min | 150.06 h | 60.00–60.11 min |
| GPT-4o-mini | `gpt-4o-mini` | 10 | `completed_forced_stop` 99, `completed_no_actions` 1, `succeeded` 50 | 150/150 | 149/150 | 60.03 min | 60.01 min | 150.06 h | 60.00–60.12 min |
| Gemini 2.5 Flash | `gemini-2.5-flash`, `google/gemini-2.5-flash` | 14 | `completed_forced_stop` 99, `completed_no_actions` 1, `succeeded` 50 | 150/150 | 149/150 | 60.02 min | 60.01 min | 150.04 h | 58.73–60.16 min |

Gemini raw IDs `gemini-2.5-flash` and `google/gemini-2.5-flash` are displayed as
one campaign label only; the raw values remain in the artifacts. The Gemini
campaign used 14 workers, compared with 10 in each older campaign.

## Exact completion and coverage availability

All **450 of 450** canonical tests ran for at least 3,300 seconds. Exactly **447 of
450** have valid coverage: Gemma 3 4B 149 of 150, GPT-4o-mini 149 of 150, Gemini 2.5 Flash 149 of 150. Completion is not coverage
availability, so these totals are reported separately.

One completed run was shorter than the requested 3,600 seconds:
`AntennaPod.apk/llmdroid` under Gemini 2.5 Flash ended normally
at 3523.7 seconds with status `succeeded`
and stop reason `tool_exited`. It passes the stated
3,300-second completion rule but is not described as a literal full hour.

### The three unavailable coverage results

The same app + tool cell lacks coverage in every model campaign. These tests are
finished tests, not 0% results, and are excluded from coverage summaries.

| Model | App directory | Tool | Duration | Recorded reason | Evidence |
|---|---:|---:|---:|---:|---:|
| Gemma 3 4B | APhotoManager-0.6.4.180314-debug-_116.apk | GPTDroid | 3600.7 s | JaCoCo socket closed unexpectedly | `reproduction/results/run-60m/APhotoManager-0.6.4.180314-debug-_116.apk/gptdroid/r1/code_coverage.json` |
| GPT-4o-mini | APhotoManager-0.6.4.180314-debug-_116.apk | GPTDroid | 3604.2 s | JaCoCo socket closed unexpectedly | `reproduction/results/openai-60m/APhotoManager-0.6.4.180314-debug-_116.apk/gptdroid/r1/code_coverage.json` |
| Gemini 2.5 Flash | APhotoManager-0.6.4.180314-debug-_116.apk | GPTDroid | 3603.7 s | JaCoCo socket closed unexpectedly | `reproduction/results/gemini-2-5-flash-60m/APhotoManager-0.6.4.180314-debug-_116.apk/gptdroid/r1/code_coverage.json` |

Coverage below is JaCoCo probe coverage read only from `code_coverage.json`.
Every included percentage equals `round(100 * covered_probes / total_probes, 2)`.

### Model-overall coverage

| Model | n | Mean | Median | Min | Max |
|---|---:|---:|---:|---:|---:|
| Gemma 3 4B | 149 | 33.818792% | 33.36% | 8.41% | 70.25% |
| GPT-4o-mini | 149 | 33.492886% | 33.32% | 6.03% | 69.78% |
| Gemini 2.5 Flash | 149 | 33.683960% | 33.54% | 8.19% | 69.84% |

### Coverage by model and tool

| Model | Tool | n | Mean | Median | Min | Max |
|---|---:|---:|---:|---:|---:|---:|
| Gemma 3 4B | GPTDroid | 49 | 31.367143% | 30.010% | 8.41% | 58.42% |
| Gemma 3 4B | LLMDroid | 50 | 31.504600% | 32.285% | 13.56% | 60.44% |
| Gemma 3 4B | HybridDroid | 50 | 38.535600% | 38.495% | 10.70% | 70.25% |
| GPT-4o-mini | GPTDroid | 49 | 30.999388% | 30.190% | 9.82% | 59.55% |
| GPT-4o-mini | LLMDroid | 50 | 30.678800% | 30.555% | 9.52% | 50.98% |
| GPT-4o-mini | HybridDroid | 50 | 38.750600% | 39.550% | 6.03% | 69.78% |
| Gemini 2.5 Flash | GPTDroid | 49 | 30.488367% | 30.240% | 8.19% | 50.35% |
| Gemini 2.5 Flash | LLMDroid | 50 | 31.524400% | 32.860% | 14.24% | 59.77% |
| Gemini 2.5 Flash | HybridDroid | 50 | 38.975200% | 37.670% | 10.78% | 69.84% |

No model is designated the winner. The span between the three overall medians is
only 0.22 percentage points, and the observed-class denominator is run-dependent.

## Same-app + same-tool comparisons

Each difference below is the named left model minus the right model for the same
app and directory tool. “Wins” and “losses” count cells with higher and lower
reported percentages; they are not claims about a generally superior model.

| Same app + tool pair | n | Mean difference | Median difference | Wins | Ties | Losses |
|---|---:|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 149 | -0.325906 pp | +0.02 pp | 76 | 10 | 63 |
| Gemini 2.5 Flash − Gemma 3 4B | 149 | -0.134832 pp | +0.00 pp | 72 | 8 | 69 |
| Gemini 2.5 Flash − GPT-4o-mini | 149 | +0.191074 pp | +0.00 pp | 68 | 12 | 69 |

| Pair | All paired | Same total probes | Same strict class signature | Same total, different signature | Different total probes |
|---|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 149 | 28 | 28 | 0 | 121 |
| Gemini 2.5 Flash − Gemma 3 4B | 149 | 32 | 32 | 0 | 117 |
| Gemini 2.5 Flash − GPT-4o-mini | 149 | 32 | 32 | 0 | 117 |

Most pair denominators differ. In these data, every equal total-probe count also
matched the strict class signature; the strict signature, rather than the total
alone, is the comparison control. Full results are in
[model-comparison.md](model-comparison.md).

## Controls and fidelity

| Pair | Cells | Source APK hash matched | Installed hash matched | Installed hash differed | Installed hash unavailable |
|---|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 150 | 150 | 0 | 150 | 0 |
| Gemini 2.5 Flash − Gemma 3 4B | 150 | 150 | 0 | 150 | 0 |
| Gemini 2.5 Flash − GPT-4o-mini | 150 | 150 | 0 | 150 | 0 |

The source APK hashes match for every paired cell, but installed APK hashes differ
across campaigns. Thus source selection is controlled while installed binaries are
not byte-identical. GPTDroid is also not a model-only comparison: Gemma and OpenAI
records warn that its public reconstruction is partial, while Gemini records state
that functionality-aware memory and all five operation primitives are implemented.

Coverage is external and reporting-only. It does not enter a prompt or action
selector. LLMDroid ran in `time` mode, which bypasses its paper's coverage-growth
trigger. HybridDroid explores without a model and calls one only after its detector
fires. These mechanisms make similar model-level coverage unsurprising.

## Steps and model calls

| Model | Tool | Steps mean | Steps median | Steps total | Calls mean | Calls median | Calls total |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gemma 3 4B | GPTDroid | 603.4 | 669.5 | 30,168 | 604.4 | 670.5 | 30,218 |
| Gemma 3 4B | LLMDroid | 489.2 | 588.5 | 24,460 | 136.1 | 73.5 | 6,805 |
| Gemma 3 4B | HybridDroid | 1062.8 | 1088.0 | 53,142 | 5.7 | 3.0 | 283 |
| Gemma 3 4B | All tools | 718.5 | 738.5 | 107,770 | 248.7 | 69.0 | 37,306 |
| GPT-4o-mini | GPTDroid | 523.7 | 678.5 | 26,183 | 524.7 | 679.5 | 26,233 |
| GPT-4o-mini | LLMDroid | 726.6 | 762.0 | 36,332 | 77.7 | 77.0 | 3,886 |
| GPT-4o-mini | HybridDroid | 1083.8 | 1095.5 | 54,190 | 7.1 | 2.5 | 356 |
| GPT-4o-mini | All tools | 778.0 | 761.5 | 116,705 | 203.2 | 60.5 | 30,475 |
| Gemini 2.5 Flash | GPTDroid | 362.5 | 354.0 | 18,127 | 363.5 | 355.0 | 18,177 |
| Gemini 2.5 Flash | LLMDroid | 643.8 | 665.5 | 32,188 | 72.2 | 74.0 | 3,612 |
| Gemini 2.5 Flash | HybridDroid | 1041.6 | 1061.0 | 52,079 | 4.3 | 2.0 | 216 |
| Gemini 2.5 Flash | All tools | 682.6 | 661.5 | 102,394 | 146.7 | 73.0 | 22,005 |

Steps and calls are computed from the 450 `result.json` files. A HybridDroid call is
conditional on detector activation, so its call volume is not directly comparable
to GPTDroid's per-step use.

## Activity coverage

| Model | Tool | n | Mean | Median | Covered sum | Declared sum |
|---|---:|---:|---:|---:|---:|---:|
| Gemma 3 4B | GPTDroid | 50 | 17.31% | 11.65% | 94 | 987 |
| Gemma 3 4B | LLMDroid | 50 | 30.30% | 25.66% | 220 | 987 |
| Gemma 3 4B | HybridDroid | 50 | 33.92% | 32.45% | 255 | 987 |
| Gemma 3 4B | All tools | 150 | 27.18% | 20.00% | 569 | 2,961 |
| GPT-4o-mini | GPTDroid | 50 | 19.46% | 14.29% | 107 | 987 |
| GPT-4o-mini | LLMDroid | 50 | 29.75% | 26.67% | 211 | 987 |
| GPT-4o-mini | HybridDroid | 50 | 35.17% | 33.33% | 258 | 987 |
| GPT-4o-mini | All tools | 150 | 28.13% | 21.43% | 576 | 2,961 |
| Gemini 2.5 Flash | GPTDroid | 50 | 16.73% | 10.00% | 83 | 987 |
| Gemini 2.5 Flash | LLMDroid | 50 | 32.09% | 28.18% | 240 | 987 |
| Gemini 2.5 Flash | HybridDroid | 50 | 33.70% | 33.33% | 258 | 987 |
| Gemini 2.5 Flash | All tools | 150 | 27.50% | 21.82% | 581 | 2,961 |

Summing cells repeats each app's declaration once per tool, as intended for the
per-run table. For a reachability universe, declaration sets preserved by the old
campaign artifacts were combined with covered sets from all three campaigns:
**360 of 987** declared activities were reached and
**627** were not observed.

## Operational gap summary

| Gap | Final measured evidence | Interpretation boundary |
|---:|---|---|
| 1 | 43,046/46,100 targeted selections repeated a prior `(state_hash, target)` | A repeat can still execute stateful code. |
| 2 | median 3.0 distinct state hashes; 24/147 ledgers had one | Hashes are observed UI states, not guaranteed screens. |
| 3 | 27,880/74,478 GPTDroid steps used `back_fallback` | The fallback does not by itself prove hallucination. |
| 4 | 104/695 typed values were literal `<value>` or empty; Gemini recorded zero typed fields | Gemini typed-input quality is unmeasurable here. |
| 5 | 224/439 eligible timelines had a raw covered-probe drop | A drop is observed; restart causation is not proved by the timeline alone. |
| 6 | 326/439 eligible timelines grew their denominator | Reported percentages need denominator controls. |
| 7 | 232.23 aggregate hours after the final high-water mark; median 35.24 min | This is time, not a run count or proof that no other work occurred. |
| 8 | 627/987 declared activities were never observed | Reach is pooled across tools and models. |
| 9 | 69/101 source builds failed | This coverage channel depends on an instrumented build. |
| 10 | 6 apps were excluded for missing `INTERNET` | This is a static harness constraint. |
| 11 | 88/159 nonempty crash logs had a mismatching first process; 70 had no app-owned process | First-process mismatch is not whole-log attribution. |
| 12 | LLMDroid has 0/150 positive-call runs with usable token counts | GPTDroid and positive-call HybridDroid runs retain token totals; all-tool cost remains incomplete. |

Full definitions, model breakdowns, examples, proof limits, and proposed fixes are
in [carbide/GAPS-AND-FIXES.md](carbide/GAPS-AND-FIXES.md).

## Token evidence

| Model | Tool | Positive-call runs with usable token counts | Input tokens | Output tokens |
|---|---:|---:|---:|---:|
| Gemma 3 4B | GPTDroid | 50/50 | 38,372,013 | 804,472 |
| Gemma 3 4B | LLMDroid | 0/50 | unavailable | unavailable |
| Gemma 3 4B | HybridDroid | 34/50 | 101,337 | 951 |
| GPT-4o-mini | GPTDroid | 50/50 | 38,807,389 | 631,940 |
| GPT-4o-mini | LLMDroid | 0/50 | unavailable | unavailable |
| GPT-4o-mini | HybridDroid | 30/50 | 130,820 | 418 |
| Gemini 2.5 Flash | GPTDroid | 50/50 | 21,043,632 | 9,228,029 |
| Gemini 2.5 Flash | LLMDroid | 0/50 | unavailable | unavailable |
| Gemini 2.5 Flash | HybridDroid | 32/50 | 78,698 | 557 |

There is deliberately no all-tool token total. LLMDroid's
`LLM-Interaction.txt` stores latency and response character length, and its zero
fields are placeholders. Consequently a complete all-tool dollar cost cannot be
derived; provider pricing would also need an explicit, dated price source.

## Evidence locations

- Canonical raw cells: `reproduction/results/run-60m/<app>/<tool>/r1`,
  `reproduction/results/openai-60m/<app>/<tool>/r1`, and
  `reproduction/results/gemini-2-5-flash-60m/<app>/<tool>/r1`.
- Historical large-log mirrors: `results/gemma-local-result/` and
  `results/openai-commercial-result/`. No Gemini mirror is claimed.
- Static build and exclusion evidence: `reproduction/build/build-state.json` and
  `reproduction/datasets/excluded-apps.tsv`.
- Generator: `carbide/analysis/render_final_reports.py`.

## Interpretation

Near-equal model medians are compatible with this design: startup executes much of
the observed code; coverage is not an optimization signal; LLMDroid's tested mode
bypasses coverage guidance; and HybridDroid consults a model only on detector
activation. Small differences also mix run-dependent observed-class denominators,
GPTDroid reconstruction changes, installed-binary differences, and different
worker counts. The evidence supports reporting the measurements and limitations,
not selecting a winning model.
