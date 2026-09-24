# VALOR-Droid

LLM-driven Android GUI testing aimed at beating existing tools on JaCoCo code
coverage. Head-to-head JaCoCo comparison against GPTDroid, HybridDroid and
LLMDroid on identical probe denominators is the core method
(see `docs/comparison.md`).

Private research repo.

## Layout

| Path | What |
|---|---|
| `app/` | VALOR-Droid source (explorer, instrumenter, model gateway) |
| `tests/` | Test suite for the app (`PYTHONPATH=app/src python3 -m unittest discover -s tests`) |
| `dataset/` | 50-app dataset (`apps.tsv`, `tests.tsv`); `dataset/login-wall/` classifies login blockers |
| `results/` | Per-app results for the three baseline tools + campaign reports |
| `docs/` | Study docs (paper comparison, dataset notes) |
| `env/` | `.env.example` — copy to `.env`, never commit secrets |
| `requirements/` | `base.txt` (runtime), `smoke.txt` (CI smoke test) |

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements/base.txt
pip install -e app/
cp env/.env.example .env   # fill in only what your run needs
PYTHONPATH=app/src python3 -m unittest discover -s tests
```

LLM fallback is optional: without `OPENAI_BASE_URL` the explorer runs fully
deterministic. Any OpenAI-compatible endpoint works.

## Results

- `results/gemma-local-result/<App>/{gptdroid,hybriddroid,llmdroid}/` — the three
  tools, Gemma 3 4B campaign (50 apps × 3 tools).
- `results/openai-commercial-result/` — same, GPT-4o-mini campaign.
- `results/reports/` — generated final reports.
- `results/campaign/` — VALOR-Droid's own campaign runs.

## Notes

- APK binaries are not vendored here; `dataset/apps.tsv` records each app's
  origin and SHA-256.
- CI workflows are not included in this repo yet.
