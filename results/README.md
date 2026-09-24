# Results index: three canonical campaigns

The final analysis reads exactly one directory per app and literal tool:
`<campaign>/<app>/<tool>/r1`. It never recursively discovers results and never
includes `r1.attempt-*`, backups, or pre-fidelity directories.

| Model | Canonical raw campaign | Apps | Canonical tests | Finished | Valid coverage | Published mirror |
|---|---:|---:|---:|---:|---:|---:|
| Gemma 3 4B | [`reproduction/results/run-60m/`](../reproduction/results/run-60m/) | 50 | 150 | 150 | 149 | [`gemma-local-result/`](gemma-local-result/) |
| GPT-4o-mini | [`reproduction/results/openai-60m/`](../reproduction/results/openai-60m/) | 50 | 150 | 150 | 149 | [`openai-commercial-result/`](openai-commercial-result/) |
| Gemini 2.5 Flash | [`reproduction/results/gemini-2-5-flash-60m/`](../reproduction/results/gemini-2-5-flash-60m/) | 50 | 150 | 150 | 149 | none |

The canonical raw campaigns are the source for result, coverage, timeline,
activity, and native accounting data. Published mirrors currently exist only for
the first two campaigns. They retain compressed historical GPTDroid action logs
and crash logs used by the gap analysis. **There is no copied Gemini result
folder in `results/`; the generator does not imply or create one.**

## Reports

- [Primary final report](../results.md)
- [Three-model comparison](../model-comparison.md)
- [Operational gaps and fixes](../carbide/GAPS-AND-FIXES.md)
- [Repository overview](../README.md)

All report text is computed by
`carbide/analysis/render_final_reports.py`. Check mode compares exact text without
writing:

```bash
python3 carbide/analysis/render_final_reports.py --check
```
