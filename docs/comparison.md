# How this study compares with the three papers

Read this before quoting any percentage from either side against the other. The
short version: **the coverage percentages are not comparable, the rankings are.**

*Numbers about this study are generated from [`dataset/tests.tsv`](dataset/tests.tsv).
Numbers about the papers were read from the PDFs in
[`related_papers/`](related_papers) and are the only hand-entered figures here; each
row says where it was read from.*

## What each paper reports

| Tool | Paper | Apps tested | Code coverage | Activity coverage | How coverage was measured | Time per test | Runs per app | Read from |
|---|---|---|---|---|---|---|---|---|
| **GPTDroid** | Liu et al., ICSE 2024 | 93 apps, from 407 crawled; 101 filtered out | 66% code coverage (average) | 75% activity coverage (average) | not stated in the paper | 60 minutes per app | 3 runs per app, highest taken | abstract and §4 RQ1 |
| **LLMDroid** | Wang et al., FSE 2025 | 14 popular Google Play apps | 11.69% for the Droidbot variant (9.21% before LLMDroid, +26.90%) | 22.46% for the Droidbot variant (15.90% before, +41.29%) | AndroLog, method-level, static instrumentation, black-box | not stated as a fixed budget | 3 runs per app, highest taken | Table 3, §4.2 |
| **HybridDroid (UI-Tarpit)** | UI-Tarpit paper, `related_papers/UITarpit.pdf` | 13 apps in the benchmark; coverage reported on 12 | 40.3% line, 28.0% branch, 43.7% method, 52.4% class | 43.2% activity coverage | line / branch / method / class, from app source | 3 hours per run | 3 independent runs averaged (10 for one experiment) | Table 2, column Hd, §5 |

## What this study measured

| Tool | Apps tested | Code coverage | Activity coverage | How coverage was measured | Time per test | Runs per app | Evidence |
|---|---|---|---|---|---|---|---|
| **GPTDroid** | 50 apps | 30.01% median, 31.37% mean | 11.65% median, 17.31% mean | JaCoCo probe coverage, read live from the running app | 60 minutes | 1 run | [result.md](result.md) |
| **LLMDroid** | 50 apps | 32.28% median, 31.50% mean | 25.66% median, 30.30% mean | JaCoCo probe coverage, read live from the running app | 60 minutes | 1 run | [result.md](result.md) |
| **HybridDroid** | 50 apps | 38.50% median, 38.54% mean | 32.45% median, 33.92% mean | JaCoCo probe coverage, read live from the running app | 60 minutes | 1 run | [result.md](result.md) |

## Why the code coverage numbers cannot be compared

**Four different metrics wear the same word.** GPTDroid's 66% and this study's
30.01% for the same tool are not the same measurement:

- **here** — JaCoCo *probe* coverage: probes reached in the app's own bytecode;
- **LLMDroid's paper** — AndroLog *method* coverage: a method counts as covered the
  moment it is entered once;
- **the UI-Tarpit paper** — line, branch, method and class coverage from source;
- **GPTDroid's paper** — does not say which metric its 66% is.

Method and class coverage read far higher than probe coverage on the identical run,
because one call marks a whole method or class covered while leaving most of its
probes untouched. In the UI-Tarpit paper's own table the same runs score 28.0% on
branch and 52.4% on class — a 24-point spread on one tool, from the choice of metric
alone. That spread is the size of the effect being discussed when tools are compared
across papers.

**Three more things push the published figures above ours:**

| | The papers | Here |
|---|---|---|
| Runs per app | 3, and the **highest** is reported (GPTDroid, LLMDroid) | 1 run, reported as it came |
| Time per test | 3 hours (UI-Tarpit) | 1 hour |
| App set | crawled Google Play apps, or 14 popular ones | 50 apps that can report coverage, which skews to smaller open-source apps |

Taking the best of three runs is systematically higher than a single run. The
UI-Tarpit paper does note that GPTDroid and HybridDroid plateau within 60 minutes,
so its 3-hour budget is worth less than 3× — but it is not worth nothing.

## The one comparison that is close to like-for-like

Activity coverage means the same thing in every one of these papers: the app's own
screens that were reached, out of those it declares. It is the only metric where the
definitions line up.

| Tool | The paper | Here | Apps |
|---|---|---|---|
| GPTDroid | 75% activity coverage (average) | 11.65% median, 17.31% mean | 93 vs 50 |
| LLMDroid | 22.46% for the Droidbot variant (15.90% before, +41.29%) | 25.66% median, 30.30% mean | 14 vs 50 |
| HybridDroid | 43.2% activity coverage | 32.45% median, 33.92% mean | 12 vs 50 |

LLMDroid is the striking one: **25.7% here against 22.46% in its paper**,
on 50 apps rather than 14, in one run rather than the best of three. That is close
agreement, and it is the strongest evidence in this study that the harness drives
these tools the way their authors intended.

HybridDroid reaches 43.2% in its paper against 32.5% here, and the gap is
mostly budget and averaging: three hours per run and three to ten runs, against one
hour and one run.

## What can be taken from this study instead

The value here is not a better absolute number. It is that **all three tools ran on
the same 50 apps, the same hardware, the same hour, and the same metric**, which no
published paper does — each evaluates its own tool against baselines, on its own
apps, with its own coverage tool.

On that footing:

- **HybridDroid led on 37 of 50 apps**, LLMDroid on
  6, GPTDroid on 7.
- **The tool that called a model most often finished last.** GPTDroid made
  30,218 calls; HybridDroid made
  283 and led.
- GPTDroid's published 66% sits about three times above the other two papers' own
  figures for their own tools. On one metric, one hour and one set of apps, it comes
  last. That is a reason to treat cross-paper coverage percentages as
  incomparable rather than a claim that the paper is wrong.

## Two caveats that belong on our side of any comparison

1. **GPTDroid here is a reconstruction.** No runnable source is published. The
   paper's functionality-aware memory (§2.3) and its five operation primitives
   (§2.2) are implemented, so its numbers are no longer the floor they were while
   that memory was missing — but they cannot be diffed against an official
   implementation. See [FIDELITY.md](reproduction/runner/FIDELITY.md).
2. **LLMDroid ran in time mode**, so its session ends on the clock rather than by the
   paper's coverage-growth trigger. The variants match otherwise: LLMDroid-Droidbot
   and HybridDroidbot, the same ones the papers report in the columns quoted above.
