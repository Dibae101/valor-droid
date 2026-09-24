# Three-model comparison

The same 50 source APKs and three directory tools were used in three campaigns.
There is one run per app + tool + model cell. **450 tests completed**, while **447
valid coverage results** are available. Coverage differences below always pair the
same app and the same tool.

## Campaigns and completion

| Model | Observed raw model ID(s) | Workers | Status counts | Finished | Coverage | Duration mean | Duration median | Duration total | Duration range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemma 3 4B | `google.gemma-3-4b-it` | 10 | `completed_forced_stop` 100, `completed_no_actions` 1, `succeeded` 49 | 150/150 | 149/150 | 60.02 min | 60.01 min | 150.06 h | 60.00–60.11 min |
| GPT-4o-mini | `gpt-4o-mini` | 10 | `completed_forced_stop` 99, `completed_no_actions` 1, `succeeded` 50 | 150/150 | 149/150 | 60.03 min | 60.01 min | 150.06 h | 60.00–60.12 min |
| Gemini 2.5 Flash | `gemini-2.5-flash`, `google/gemini-2.5-flash` | 14 | `completed_forced_stop` 99, `completed_no_actions` 1, `succeeded` 50 | 150/150 | 149/150 | 60.02 min | 60.01 min | 150.04 h | 58.73–60.16 min |

Raw Gemini aliases are normalized only for display. No current timestamp is used,
so unchanged evidence produces unchanged text.

## Final coverage tables

### Model overall

| Model | n | Mean | Median | Min | Max |
|---|---:|---:|---:|---:|---:|
| Gemma 3 4B | 149 | 33.818792% | 33.36% | 8.41% | 70.25% |
| GPT-4o-mini | 149 | 33.492886% | 33.32% | 6.03% | 69.78% |
| Gemini 2.5 Flash | 149 | 33.683960% | 33.54% | 8.19% | 69.84% |

### Model and tool

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

The close overall medians do not identify a winning model. This is one run per
cell, with no repeat-run uncertainty estimate.

## Pairwise same-app + same-tool differences

The difference is left minus right in percentage points. Wins, ties, and losses are
cell counts, not significance claims.

| Same app + tool pair | n | Mean difference | Median difference | Wins | Ties | Losses |
|---|---:|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 149 | -0.325906 pp | +0.02 pp | 76 | 10 | 63 |
| Gemini 2.5 Flash − Gemma 3 4B | 149 | -0.134832 pp | +0.00 pp | 72 | 8 | 69 |
| Gemini 2.5 Flash − GPT-4o-mini | 149 | +0.191074 pp | +0.00 pp | 68 | 12 | 69 |

### Pairwise differences by tool

| Pair | Tool | n | Mean difference | Median difference | Wins | Ties | Losses |
|---|---:|---:|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | GPTDroid | 49 | -0.368 pp | +0.05 pp | 25 | 2 | 22 |
| GPT-4o-mini − Gemma 3 4B | LLMDroid | 50 | -0.826 pp | -0.03 pp | 21 | 4 | 25 |
| GPT-4o-mini − Gemma 3 4B | HybridDroid | 50 | +0.215 pp | +0.18 pp | 30 | 4 | 16 |
| Gemini 2.5 Flash − Gemma 3 4B | GPTDroid | 49 | -0.879 pp | -0.14 pp | 20 | 2 | 27 |
| Gemini 2.5 Flash − Gemma 3 4B | LLMDroid | 50 | +0.020 pp | +0.04 pp | 26 | 3 | 21 |
| Gemini 2.5 Flash − Gemma 3 4B | HybridDroid | 50 | +0.440 pp | +0.03 pp | 26 | 3 | 21 |
| Gemini 2.5 Flash − GPT-4o-mini | GPTDroid | 49 | -0.511 pp | -0.46 pp | 20 | 2 | 27 |
| Gemini 2.5 Flash − GPT-4o-mini | LLMDroid | 50 | +0.846 pp | +0.09 pp | 27 | 6 | 17 |
| Gemini 2.5 Flash − GPT-4o-mini | HybridDroid | 50 | +0.225 pp | -0.02 pp | 21 | 4 | 25 |

No p-values or significance claims are made: the design has no repeated runs for a
cell, and the measurement denominator itself often changes.

## Strict observed-class-signature subset

A strict signature is the sorted set of `(class, total_probes)` entries in the two
`code_coverage.json` files. It requires the same observed classes and the same
per-class probe totals, not merely the same aggregate denominator.

| Pair | Strict-signature n | Mean difference | Median difference | Wins | Ties | Losses |
|---|---:|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 28 | +0.107 pp | +0.00 pp | 12 | 10 | 6 |
| Gemini 2.5 Flash − Gemma 3 4B | 32 | +0.053 pp | +0.00 pp | 14 | 8 | 10 |
| Gemini 2.5 Flash − GPT-4o-mini | 32 | +0.042 pp | +0.00 pp | 10 | 11 | 11 |

### Denominator and signature agreement

| Pair | All paired | Same total probes | Same strict class signature | Same total, different signature | Different total probes |
|---|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 149 | 28 | 28 | 0 | 121 |
| Gemini 2.5 Flash − Gemma 3 4B | 149 | 32 | 32 | 0 | 117 |
| Gemini 2.5 Flash − GPT-4o-mini | 149 | 32 | 32 | 0 | 117 |

Most pairs use different total-probe denominators. In these data, every equal
total also matched the strict class signature. Probe coverage is a share of
classes observed in that run, not a fixed whole-app denominator, so uncontrolled
percentages should not be read as directly interchangeable.

## Work volume

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

Call policies differ by design. GPTDroid asks frequently; LLMDroid combines its own
exploration with model calls; HybridDroid calls a model only when its tarpit
detector fires. More calls therefore do not mean the same intervention across
these tools.

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

The pooled declaration-set analysis reaches **360 of
987** declared activities; **627**
were not observed under any of the three models and tools. Per-run percentage
summaries and the pooled union answer different questions.

## Token limitations

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

GPTDroid and HybridDroid retain aggregate input/output tokens where the fields are
present. LLMDroid records call
latency and response character length, not provider token counts; omitted and zero
fields are unavailable placeholders. For that reason this report does not aggregate
tokens across all tools and cannot reconstruct a complete all-tool dollar cost.

## Methods, controls, and fidelity

| Pair | Cells | Source APK hash matched | Installed hash matched | Installed hash differed | Installed hash unavailable |
|---|---:|---:|---:|---:|---:|
| GPT-4o-mini − Gemma 3 4B | 150 | 150 | 0 | 150 | 0 |
| Gemini 2.5 Flash − Gemma 3 4B | 150 | 150 | 0 | 150 | 0 |
| Gemini 2.5 Flash − GPT-4o-mini | 150 | 150 | 0 | 150 | 0 |

- **External coverage.** The harness reads JaCoCo probes for reporting. None of the
  tools receives this measured coverage as an action-selection signal in these
  runs. Similar model coverage is therefore expected.
- **GPTDroid.** Gemma and OpenAI records warn that the public reconstruction is
  partial. Gemini records say functionality-aware memory and all five operation
  primitives are implemented. GPTDroid's three campaigns are consequently not a
  model-only intervention.
- **LLMDroid.** All campaigns use `time` mode, which bypasses the paper's
  coverage-growth trigger. These runs do not test that trigger.
- **HybridDroid.** The model is queried only after its detector fires; most work is
  the tool's own exploration.
- **Execution concurrency.** Gemini used 14 workers; the two older campaigns used
  10. Source APK hashes match, but installed APK hashes differ as shown above.
- **Replication.** There is one run per cell. Cell-level variation cannot be
  separated from repeat-run variation.

## Why the model medians are near equal

Launching an app loads initialization and first-screen code before many model
choices occur. Subsequent model decisions optimize UI actions rather than measured
probe coverage. LLMDroid's coverage trigger was inactive in time mode, and
HybridDroid only uses a model after a detector event. Finally, the observed-class
denominator changes between most paired runs. Together these facts explain why
better or different single-step model behavior need not move the reported median.
They do not prove that models are equivalent, and they do not support declaring a
winner.
