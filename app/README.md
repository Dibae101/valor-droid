# VALOR-Droid

**V**erified **A**ction **L**edgers and **O**utcome-aware **R**outing for Android.

VALOR-Droid is the implementation successor to the historical CARBIDE design. It is an evidence-first Android runner with bounded model assistance, auditable setup profiles, coverage-guided recovery, a multi-instance reDroid fleet layer, and a fail-closed adapter for pinned external APK instrumenters. It does **not** silently bypass login, alter the fixed method denominator, fabricate model sessions, or claim a coverage increase without matched fixed-universe evidence.

## Current status

### Built and locally validated

- Schema-v3 exact records, strict decoders, frozen package/APK/unit universe, monotone coverage, conservative post-dispatch temporal association, and independently validated finished/aborted terminal evidence.
- WAL-first action/background transactions, deterministic projections, restart reconciliation, run locking, immutable evidence heads, and recoverable terminal lifecycle intent.
- Prepared schema 3 with proof schema 2, retained original and instrumented APK hashes, fresh AAPT identity for both endpoints, equal package/version requirement, instrumented launchers, normalized units, backend/policy binding, retained instrumenter config/receipt/invocation provenance for `prepare-apk`, and bundle/universe checksums.
- A `prepare-apk` adapter that invokes one pinned external transformer/enumerator command without a shell or inherited environment, verifies executable/source/toolchain identity, enforces a bounded type-exact receipt protocol, generates the schema-2 proof, retains checksum-bound invocation provenance, imports, and immediately re-verifies the bundle.
- A registered `smali-logcat-method-v1` fallback for Soot-blocked APKs: all root DEX files are decoded and transformed, every concrete method under the explicitly audited app-class prefix is instrumented or the APK is rejected, a bounded first-hit helper limits log flooding, and the final aligned/signed APK is re-decoded into a required structural audit proving unchanged original inventories and exactly one positive probe site per frozen unit.
- Explicit-serial ADB, device locking, one fresh-install path, verified launch/foreground state, UIAutomator observation, optional screenshots, stable candidates, target re-resolution, fixed-command/stdin deep-link dispatch, dispatch-timed action windows, target-bound outcomes, replay-derived frontier/recovery, package-scoped cleanup, and bounded commands.
- Opt-in semantic state refinement, allowlisted AUT-owned WebView enrichment, and a pinned persistent UIAutomator2 fallback. The fallback activates only after repeated native idle refusals, explicitly confirms `waitForIdleTimeout=0`, keeps one process-isolated owner across fresh hierarchy requests, reconnects within a bounded generation, records fixed lifecycle evidence, and stops before package cleanup. Disabled controls preserve historical schema-4 serialization.
- Strict environment-backed setup/persona profiles with checksum-bound fixtures, typed actions and postconditions, fixed-argv/stdin secret input, persistent observation redaction, state-preserving restart, lifecycle digest binding, and separately committed unattributed setup coverage.
- Bounded model action selection and field synthesis with retained prompts/replies/token counts. A successful action-selection call and its `produced_attempt_id` commit inside the canonical action WAL record, with `model_calls.jsonl` repaired as a checked projection after restart; refusals and actions lacking a canonical transaction remain retained but unlinked.
- Checksummed `coverage-context.json` inference from Soot/JVM-like method IDs to exact or nested activity owners. Frozen unused deep-link/component routes are ranked by currently uncovered inferred methods while route validation still requires an authorized, previously unused target.
- Optional pre-run deep-link synthesis using the same run-budgeted gateway: generated URIs are structurally parsed, stripped of known links, rejected for raw whitespace, controls, credentials, and secret-like query keys, host-constrained for HTTP(S), deterministically capped, frozen before `run.json`, and validated by replaying the exact retained prompt/response and pre-run timestamp against route discovery.
- Continuous epoch logcat collection. A `METHOD=` hit is accepted only when observations before and after receipt show the same prepared-package process name, PID, boot ID, and process start token; ambiguous, reused, or retroactively owned PIDs are rejected.
- PID/tag/time-bounded `AndroidRuntime` fatal-block filtering and recomputable package-owned crash signatures.
- Fleet schema 2 with `attach` and ownership-safe `manage` modes, digest-pinned reDroid images, loopback-only distinct ADB ports, resource limits, fresh per-attempt data paths, host-global slot leases, per-execution container ownership, container-ID-revalidated teardown, exact device/image/container identity, and fail-closed readiness/property checks.
- Immutable `sorted-round-robin-v1` assignment, bounded per-device queues, unchanged `run-android` child boundaries, new process groups, process-group emptiness supervision, host `SIGINT`/`SIGTERM`/`SIGHUP` capture, staged `SIGINT → SIGTERM → SIGKILL`, restricted retries, taint/quarantine, hash-chained campaign events, replayed schema-2 campaign state machines, recoverable terminal campaign manifests, and control-compatible summaries.
- CLI commands: `prepare-apk`, `instrumenter-check`, `import-prepared`, `verify-prepared`, `run-android`, `fleet-doctor`, `fleet-plan`, `fleet-run`, `fleet-accept`, `fleet-validate`, `fleet-summary`, `campaign-compare`, `demo`, `validate`, `summary`, `doctor`, `create-universe`, and `init-run`.
- `instrumenter-check` qualifies a candidate external instrumenter against `valordroid-instrumenter-v1` without importing anything: it runs the real hermetic invocation, verifies toolchain digests before and after, and reports every receipt/output assertion individually.
- `fleet-accept` provisions every configured reDroid slot for real, proves boot completion, identity, ADB round trip, and slot distinctness, then tears down by revalidated container ID and releases each lease. It installs nothing and makes no coverage claim.
- `campaign-compare` compares a treatment campaign against a baseline and refuses to report a difference unless both are independently valid, measure the same prepared bundle and universe, and differ only in controls explicitly declared as the treatment.

A committed regression suite in `tests/` pins every defect found during review. It uses only the standard library, needs no device, Docker, or instrumenter artifact, and runs offline:

```bash
PYTHONPATH=valordroid/src python3 -m unittest discover -s valordroid/tests -t valordroid
```

The suite covers campaign replay and terminal sealing, attempt timing anchored to recorded events, forged-evidence rejection, slot-lease scope and contention, process-group supervision including orphaned descendants, the monotone event clock, fleet schema compatibility, prepared-bundle provenance cross-binding, instrumenter protocol conformance, cumulative and immutable coverage, crash time ordering, outcome/execution consistency, authoritative activity resolution, and matched-control comparison refusals.

### External or experimentally unproven

1. **AndroLog/Soot remains external; the smali fallback is implemented.** This repository still has no portable pinned AndroLog dependency closure. It now includes a separately attributed all-DEX smali adapter and config generator, but each host must pin its exact Java/Apktool/Android signing toolchain, and real-device behavior remains experimental evidence rather than a code-level guarantee.
2. **Real reDroid/device acceptance is open.** No connected reDroid/device is available in this checkout. The Docker/ADB fleet passed fake failure injection, and the optional observation extensions passed local protocol, lifecycle, validator, and summary smokes, but no claim is made that UIAutomator2 or WebView completed a real APK workflow here.
3. **Instrumentation science remains external evidence.** Hashes and a complete receipt bind the producer claim; they do not independently prove transformation lineage, static-policy correctness, probe reachability, event-loss rate, or overhead.
4. **Recovery remains intentionally bounded.** Replay-derived zero-yield exhaustion, known-GUI path replay, Back/reveal, frozen deep links, exported components, separately authorized forced components, bounded model escalation, and reset-seed recovery are implemented. They improve reachability policy but do not prove that a specific target is semantically usable.
5. **Live model service acceptance is environment-dependent.** Bedrock Mantle, Vertex AI, and OpenAI-compatible providers are integrated behind strict budgets and environment-only credentials; a real campaign still needs the operator's endpoint/account and retains every call for audit.
6. **The >40% fixed-universe goal is not yet verified.** Existing campaign evidence remains below that threshold. A higher result must come from valid finished runs over the same frozen denominator, not route/setup attribution or denominator changes.

## Does it guarantee higher coverage?

No. The implementation strengthens execution isolation, measurement, and evidence. It cannot guarantee that a policy reaches more code. A gain claim requires repeated matched experiments with identical APK/prepared/universe checksums, reDroid image and properties, reset policy, budget, concurrency, and control arms. A null or negative result remains valid.

## Quick local checks

From the repository root:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid --help
PYTHONPATH=valordroid/src python3 -m valordroid doctor
PYTHONPATH=valordroid/src python3 -m valordroid demo --output /tmp/valordroid-demo
PYTHONPATH=valordroid/src python3 -m valordroid validate /tmp/valordroid-demo
PYTHONPATH=valordroid/src python3 -m valordroid summary /tmp/valordroid-demo
```

The demo is deterministic core evidence only; it does not launch Android.

## Prepare an APK

### Pinned external adapter

Copy [`instrumenter-config.example.json`](instrumenter-config.example.json), replace every placeholder with the actual source revision and SHA-256 values, then run:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid prepare-apk \
  --original-apk apks/example.apk \
  --instrumenter-config valordroid/instrumenter-config.json \
  --package org.example.app \
  --output prepared/example
```

The external executable must implement `valordroid-instrumenter-v1` and produce the instrumented APK, complete unit list, and exact completion receipt in one bounded invocation. VALOR-Droid verifies tool hashes before and after, generates the proof, imports retained bytes, and re-verifies everything. The example is intentionally non-runnable until its all-zero hashes and placeholder metadata are replaced. See [`docs/REDROID-FLEET.md`](docs/REDROID-FLEET.md) for the argv/receipt contract.

### All-DEX smali fallback

For an APK blocked by the AndroLog/Soot serializer, generate a schema-3 config with [`tools/make_smali_instrumenter_config.py`](tools/make_smali_instrumenter_config.py), qualify it with `instrumenter-check`, then pass it to the same `prepare-apk` command. The config explicitly selects `smali-logcat-method-v1`; legacy schema-2 configs remain AndroLog-only.

The fallback instruments every concrete method under one audited app-class prefix across `classes.dex`, `classes2.dex`, and later root DEX files, or fails the entire APK. Its default `app-classes-methods-v1` policy deterministically compares the declared application-ID namespace with a known variant-suffix-stripped namespace and records the selected prefix/rule; `app-package-methods-v1` remains available for exact-ID selection. It never guesses an unrelated dominant library root. The adapter re-decodes the final signed APK and retains `structural-audit.json`, with every physical probe site and exact original/final per-dex inventory bindings. It supports `.locals` and `.registers` while preserving shifted physical parameter aliases; methods with more than 255 existing locals and unsupported smali constructs are rejected, never skipped. Use only a dedicated non-production test keystore. Full setup, guarantees, limitations, and manual audit import are in [`docs/SMALI-FALLBACK.md`](docs/SMALI-FALLBACK.md).

### Manual audited import

An external producer may instead supply original/instrumented APKs, a complete unit list, and a matching schema-2 proof:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid import-prepared \
  --original-apk original.apk \
  --instrumented-apk instrumented.apk \
  --units units.json \
  --proof static-unit-proof.json \
  --backend androlog-logcat-method-v1 \
  --backend-version EXTERNAL_VERSION \
  --inclusion-policy all_application_methods \
  --log-tag AndroLog \
  --method-prefix 'METHOD=' \
  --package org.example.app \
  --output prepared/example

PYTHONPATH=valordroid/src python3 -m valordroid verify-prepared prepared/example
```

Import copies first, then hashes and AAPT-inspects both retained APKs, requires equal package/version identity, freezes `universe.json`, creates `prepared.json`, and repeats verification. The unsigned proof is still an external producer assertion. See [`docs/PREPARED-BUNDLE.md`](docs/PREPARED-BUNDLE.md).

## Run one Android device

Copy [`runner-config.example.json`](runner-config.example.json), set the explicit serial, and set `launcher` when AAPT reports multiple candidates. Runtime settings are separate from [`config.example.json`](config.example.json).

```bash
PYTHONPATH=valordroid/src python3 -m valordroid doctor \
  --device emulator-5554 --strict

PYTHONPATH=valordroid/src python3 -m valordroid run-android \
  --prepared prepared/example \
  --runtime-config valordroid/runner-config.example.json \
  --core-config valordroid/config.example.json \
  --run-id example-r1 \
  --output runs/example-r1
```

The runner verifies the bundle, locks the explicit device, freshly installs, starts logcat before launch, observes/ranks/re-resolves/executes/verifies/collects, drains a final synchronized collector event, scopes crashes, cleans up, and seals. Collection/runtime failures produce `aborted`; `summary` accepts only independently valid `finished` evidence.

### Optional observation extensions

The standard-library path remains dependency-free. Install only the transports a run enables; both direct dependencies are exactly pinned in `pyproject.toml`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e 'valordroid[webview,uiautomator2]'
```

Set `uiautomator2_fallback_enabled` to `true` in the runtime JSON to allow fallback after two consecutive idle-specific native dump failures. Activation is lazy. Once activated, the persistent worker owns UiAutomation for the remainder of the run; shell dumping does not resume. Every request is fresh and deadline-bounded, foreground stability is checked across the request, `waitForIdleTimeout=0` is read back from the server, a failed request gets at most one nonconcurrent reconnect generation, and observer cleanup stops the service before package cleanup. A finished enabled run may honestly report `terminal_disposition: not_activated` when native observation never needed fallback.

`semantic_state_enabled` independently enables bounded checked/selected/editable/list semantics. WebView observation requires `webview_enabled: true` plus a sorted explicit `webview_url_patterns` allowlist. Extension diagnostics in summaries come from the validated lifecycle hash chain; mutable per-observation sidecars are not summary authority. These controls improve observability contracts but do not themselves establish additional method coverage.

### Audited setup, login, and fixtures

Copy [`setup-profile.example.json`](setup-profile.example.json), bind it to the prepared package, and replace only selectors, logical persona metadata, and environment-variable names. Put disposable staging credentials in those environment variables—not in JSON, CLI arguments, runtime configuration, or model prompts—then add `--setup-profile`:

```bash
export VALORDROID_EXAMPLE_LOGIN='disposable-user'
export VALORDROID_EXAMPLE_PASSWORD='disposable-password'

PYTHONPATH=valordroid/src python3 -m valordroid run-android \
  --prepared prepared/example \
  --runtime-config valordroid/runner-config.example.json \
  --core-config valordroid/config.example.json \
  --setup-profile valordroid/setup-profile.example.json \
  --run-id example-setup-r1 \
  --output runs/example-setup-r1
```

Schema 1 profiles have an exact package, bounded timeout, explicit foreign-package allowlist, logical secret-slot-to-environment mapping, ordered typed steps, and deterministic success predicates. Supported steps are tap/long press, public or secret input, Back, state-preserving restart, wait/wait-for, directional scroll/scroll-until, deep link, component launch, permission/app-op, and checksum-bound file push. A missing secret or changed fixture fails before ADB. Secret input uses a fixed ADB command with stdin; once a profile has secrets, matching XML values are redacted before retention/hashing/model use and screenshots stay disabled for that run.

The canonical sanitized profile is retained as `setup-profile.json` and bound by the hash-chained `preparation/setup_profile_verified` event. Every setup step and postcondition is lifecycle-audited before/after execution. Setup gets one separate background coverage commit; its methods are explicitly `unattributed`, never credited to GUI/model actions, while the fixed universe and denominator remain unchanged. `restart_preserving_state` force-stops and relaunches without `pm clear`, reinstall, or behavior patching.

### Coverage-guided routes and bounded models

Add `--discover-routes` to derive deep links and component targets from the exact instrumented APK. The run retains checksum-bound `route-discovery.json` plus `coverage-context.json`, whose method parser supports production Soot signatures such as `<pkg.Activity: void onCreate(android.os.Bundle)>` and a conservative `pkg.Activity.onCreate()V` fallback. Exact and `$`-nested activity methods become advisory route goals. At recovery time, only frozen unused targets are considered; the target inferred to own the most currently uncovered methods wins, with lexical order as the deterministic zero-information fallback.

To enable model assistance, provide `--model-config` and set matching `model_assistance_enabled`/`max_model_calls` values in the core config. `max_zero_gain_candidate_attempts` permanently suppresses ordinary replay of repeatedly zero-yield candidates, while `max_synthesized_deep_links` caps the preflight link request. Coverage plateaus and permanently exhausted screens can escalate to the bounded model rung; a temporary frontier cooldown cannot. Preflight synthesis happens before the run directory is created, uses the same gateway budget as exploration, and is provider-free on replay because only accepted frozen URIs and the complete call evidence are retained.

Ordinary exploration text transport remains restricted to 1–128 safe ASCII characters without spaces until a controlled IME exists. Missing, ambiguous, or stale targets are `not_executed`; ADB operation errors are `failed`; there is no hidden Back fallback.

## Run multiple reDroid containers

Official reDroid documentation supports many container instances and requires a distinct exposed ADB port per instance. VALOR-Droid adds deterministic ownership and evidence around that deployment. Sources: [reDroid overview](https://github.com/remote-android/redroid-doc/blob/master/README.md) and [deployment guide](https://github.com/remote-android/redroid-doc/blob/master/deploy/README.md). Content was rephrased for compliance with licensing restrictions.

Copy [`fleet-config.example.json`](fleet-config.example.json), replace the all-zero image digest, paths, resources, properties, and jobs, then execute:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid fleet-doctor \
  --config valordroid/fleet-config.json --strict

PYTHONPATH=valordroid/src python3 -m valordroid fleet-plan \
  --config valordroid/fleet-config.json \
  --output campaigns/example-redroid-2

PYTHONPATH=valordroid/src python3 -m valordroid fleet-run \
  campaigns/example-redroid-2

PYTHONPATH=valordroid/src python3 -m valordroid fleet-validate \
  campaigns/example-redroid-2 --json

PYTHONPATH=valordroid/src python3 -m valordroid fleet-summary \
  campaigns/example-redroid-2
```

Manage mode accepts only digest-pinned images and `127.0.0.1:PORT` serials. It creates one labelled container and fresh retained data directory per attempt, verifies exact identity/readiness, and stops/removes only matching owned containers. Attach mode never manages external containers and permits one no-retry job per externally reset serial. Full setup, failure handling, artifacts, recovery, and real-acceptance steps are in the [reDroid fleet operator runbook](docs/REDROID-FLEET.md).

## Evidence contracts

### One run

An Android run contains immutable `universe.json`, exact `prepared.json`, optional manifest-derived `route-discovery.json`, strict self-checksummed `coverage-context.json`, optional sanitized `setup-profile.json`, runtime binding in `run.json`, diagnostics, and nine hash-chained ledgers:

| Ledger | Role |
|---|---|
| `coverage_events.jsonl` | Typed collector successes/failures, IDs, host/device timestamps, process-generation ownership intervals |
| `transactions.jsonl` | Canonical action/background WAL referencing one successful collector event |
| `coverage.jsonl` | Deterministic sample projection |
| `associations.jsonl` | Conservative first-collection association projection |
| `actions.jsonl` | Requested/executed/outcome projection |
| `lifecycle.jsonl` | Preparation/device/install/launch/exploration/recovery/cleanup/terminal intent |
| `routes.jsonl` | Authorized recovery rung, provenance, target, and result |
| `model_calls.jsonl` | Complete model gateway; empty in deterministic runs |
| `crashes.jsonl` | Scoped prepared-package fatal blocks and signatures |

A collector event is fsynced before its WAL transaction; the transaction is fsynced before projections. Finished and aborted manifests seal every ledger count/head. `summary` reports frozen-denominator primary, all-route, and total-observed values separately.

### One campaign

A campaign contains immutable `campaign-plan.json`, normalized `fleet-config.json`, derived configs, child logs/runs, hash-chained `campaign-events.jsonl`, and event-derived immutable `campaign.json`. A missing terminal manifest can be recovered only after a complete `campaign_finished` event. Nonterminal campaigns are never automatically replayed because child/device ownership may remain ambiguous.

Campaign validation replays schema-2 campaign evidence as an ordered scheduler state machine and rechecks the plan, assignment, snapshots, prepared bundles, event chain, job/run IDs, prepared/universe checksums, exact serials, referenced logs, run manifests, and run validation. Status is derived from replay, so failed, unretried, or tainted-but-unquarantined attempts cannot appear as a finished campaign. Campaign summary includes only valid finished runs and pools them only within one prepared/universe/`control_sha256` group. Every manifest says coverage gain is not established without a matched control.

## Package map

| Module | Responsibility |
|---|---|
| `prepared.py`, `instrumentation.py`, `android/apk.py` | Pinned adapter protocol, hermetic invocation and retained provenance, prepared schema 3/proof schema 2, dual-APK/AAPT verification |
| `android/adb.py`, `android/session.py` | Explicit-serial commands, device lock, install, launch, routes, reset, cleanup |
| `android/observe.py`, `android/uiautomator2_fallback.py`, `android/webview.py` | Native state artifacts, persistent idle-bypass fallback, allowlisted DOM enrichment, stable candidates |
| `android/actions.py`, `android/outcomes.py` | Target re-resolution, execution, typed outcomes |
| `android/logcat.py` | Continuous raw log, process-generation ownership, cumulative/final method events, scoped crash signal |
| `runner.py`, `runtime_config.py` | Android loop, bounded model integration, recovery, budgets, failure cleanup/sealing |
| `coverage_context.py`, `deep_links.py` | Method/component inference, uncovered-route ranking, and pure generated-URI authorization |
| `fleet_config.py`, `fleet_plan.py`, `redroid.py` | Exact fleet schema, immutable assignment, safe attach/manage provisioning and identity |
| `fleet_runner.py`, `campaign.py`, `fleet_cli.py` | Process-level scheduler, cancellation/quarantine, campaign evidence/validation/summary, commands |
| `acceptance.py`, `baseline.py` | Real-device provisioning acceptance and matched-control campaign comparison |
| `tests/` | Committed standard-library regression suite pinning every reviewed defect |
| `universe.py`, `coverage.py`, `attribution.py` | Frozen denominator, monotone transitions, conservative temporal association |
| `transactions.py`, `ledger.py`, `store.py` | WAL authority, projection repair, hash chains, locks, terminal manifests |
| `frontier.py`, `recovery.py`, `routes.py` | Replay-derived candidate exhaustion and explicit provenance-separated recovery |
| `controller.py`, `validator.py`, `summary.py` | Evidence orchestration, abort/finish sealing, independent validation/reporting |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for invariants, [`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md) for the remaining acceptance/evaluation work, and [`docs/REDROID-FLEET.md`](docs/REDROID-FLEET.md) for operation.
