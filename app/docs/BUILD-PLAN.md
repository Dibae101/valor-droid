# VALOR-Droid build and evaluation plan

## Implemented: deterministic preparation, Android runtime, and reDroid fleet

### Trustworthy single-run evidence

- Schema-v3 exact records/decoders, package/APK-bound frozen universe, monotone known-unit transitions, and typed collector failure.
- Hash-chained ledgers, run lock, WAL-first transactions, exact projection reconciliation, recoverable terminal lifecycle intent, and immutable finished/aborted manifests.
- Explicit requested/executed/not-executed/failed action contract, first-command dispatch time, target-bound outcomes, conservative action-window association, configured route authorization, scoped package crashes, and finished-only summary.
- Prepared schema 3 with proof schema 2 binding retained original/instrumented APK hashes, fresh equal AAPT package/version endpoints, instrumented launchers, normalized units, producer assertion, backend/policy, universe, retained hermetic-invocation provenance, and bundle checksum.
- Explicit-serial ADB/device lock, SDK-aware fresh install, verified launch, bounded commands, UIAutomator/activity/screenshot observation, stable re-resolution, safe actions/text, deterministic frontier/recovery, package cleanup, continuous epoch logcat, same-generation PID ownership intervals, host/device timestamp provenance, and synchronized final collection.

### Pinned external-instrumenter boundary

- Exact schema-1 instrumenter config with protocol, producer/version, source commit, resolved executable SHA-256, sorted toolchain SHA-256 list, timeout, backend/version/policy, tag, and prefix.
- One no-shell argv invocation with file arguments supplied by hashed `{tool:NAME}` placeholders.
- Exact receipt binding both APK hashes, canonical unit hash/count, and explicit complete-instrumentation/enumeration/one-probe-per-unit assertions.
- Hash checks before and after invocation, fresh dual-AAPT identity checks, automatic schema-2 proof generation with source/toolchain-bound producer version, prepared import, and immediate independent verification.
- Fail-closed behavior for absent/mutated tools, timeout/nonzero exit, missing/symlink outputs, malformed/incomplete receipt, changed package/version, or bundle verification failure.

The adapter is complete; the external transformer artifact is not. This repository still has no pinned AndroLog source/JAR, Soot dependency closure, method-ID policy implementation, or their hashes.

### Deterministic reDroid campaign layer

- Exact fleet schema 2 with attach/manage modes, host-global slot leases, explicit container reclaim policy, and strict duplicate/collision checks.
- Digest-pinned managed image, local image-ID verification, loopback-only distinct ADB ports, CPU/memory limits, unique names/data roots, exact ownership labels, and fresh retained per-attempt data.
- Desired serial inventory reconciled against `adb devices -l`, boot completion, configured properties, Android identity capture, bounded Docker/ADB commands, and foreign-container refusal.
- Immutable checksum-bound `sorted-round-robin-v1` plan, normalized config snapshot, independently verified job inputs, and derived exact serial/core/runtime configs.
- Process-level scheduler preserving unchanged `run-android` transactions; bounded parallel per-device queues; dedicated process groups; `SIGINT → SIGTERM → SIGKILL`; restricted operational retry; taint/quarantine; owned cleanup.
- Hash-chain campaign events, immutable/recoverable event-derived campaign manifest, per-attempt logs/runs, independent campaign/run validation, and summary groups separated by prepared/universe checksums.
- CLI commands and examples: `prepare-apk`, `instrumenter-check`, `fleet-doctor`, `fleet-plan`, `fleet-run`, `fleet-accept`, `fleet-validate`, `fleet-summary`, `campaign-compare`, [`fleet-config.example.json`](../fleet-config.example.json), [`instrumenter-config.example.json`](../instrumenter-config.example.json), and [operator runbook](REDROID-FLEET.md).
- `instrumenter-check` qualifies an external transformer against the protocol without importing; `fleet-accept` produces a real reDroid provisioning/identity/teardown trace without installing anything; `campaign-compare` pairs a treatment campaign with a matched baseline and refuses unmatched controls.

### Local validation completed

The deterministic demo, compile checks, validator, summaries, report/link/diagram checks, and CLI surface run locally. A committed standard-library regression suite in `valordroid/tests/` pins every defect found during review and runs without a device, Docker, or an instrumenter artifact:

```bash
PYTHONPATH=valordroid/src python3 -m unittest discover -s valordroid/tests -t valordroid
```

It drives real campaigns through the scheduler with fake ADB/AAPT executables and covers campaign replay and terminal sealing, attempt timing anchored to recorded events, pre-child-start provisioning failures, forged-evidence rejection, lease scope and contention, process-group supervision including orphaned descendants, the monotone event clock, fleet schema compatibility, prepared-bundle provenance cross-binding, instrumenter protocol conformance for both conformant and nonconformant stubs, cumulative and immutable coverage, crash time ordering, outcome consistency, activity resolution, real-slot acceptance step semantics, and matched-control comparison refusals.

Earlier temporary harnesses (since deleted, superseded by the committed suite) additionally exercised:

- prepared import/verification, valid finished/aborted orchestration, late collector failure, keyboard interruption, route forgery, PID reuse/retroactive ownership, crash scoping, selected-field input verification, and terminal write recovery;
- pinned instrumenter success/hash tamper rejection;
- two parallel fake Android jobs, sorted assignment, duplicate/offline/port failures, fake managed Docker image/label/cleanup, foreign-container refusal, timeout escalation through all three signals, taint/quarantine, campaign terminal recovery, grouped summary, and plan/event/config tamper rejection.

Fake adapters establish local control-flow and failure semantics, not real Android/reDroid acceptance. Use `fleet-accept` for the real-device half and `instrumenter-check` to qualify an external transformer.

## External prerequisite: executable transformer artifacts

Before `prepare-apk` can run for a real APK, supply and audit:

1. an exact AndroLog or equivalent source commit and built executable/JAR;
2. every Soot/runtime/dependency artifact needed by that executable, with SHA-256;
3. an explicit stable method-ID format and static inclusion/exclusion policy;
4. an implementation of `valordroid-instrumenter-v1` that produces the exact completion receipt;
5. licensing and reproducible toolchain/build records.

The producer assertion and matching hashes do not alone prove derivation lineage. Acceptance must establish transformation lineage, instrumentation success rate, static completeness under the policy, one inserted emitter per listed ID, startup/subprocess/restart event loss, and runtime/size overhead. A run-observed denominator is not an acceptable substitute.

## Open acceptance: real multi-container reDroid

Use at least two containers from one pinned image digest and retain this trace:

1. `fleet-doctor --strict` output proving host facilities, tools, free loopback ports, image digest/ID, no foreign names, and every input bundle/config;
2. frozen plan proving sorted assignment and exact derived serial configs;
3. labelled container creation with unique resource limits and fresh data paths;
4. distinct ready ADB entries plus fingerprint/release/SDK/ABI/serial identity;
5. concurrent fresh install, pre-launch logcat, exact launch/foreground, UI hierarchy/action/outcome, and same-generation method collection on both slots;
6. per-job WAL/projections/cleanup/terminal run validation;
7. one injected child failure and graceful abort seal without moving its job or disturbing the peer;
8. container ownership cleanup, retained attempt data, campaign finish, independent validation, and summary;
9. terminal-manifest recovery injection plus plan/config/event/run tamper rejection;
10. timeout/escalation and quarantine behavior on real processes/containers.

Also rerun single-run failure points before collector event, between event/WAL/projections, after a route/crash, during collector drain/cleanup, and between terminal lifecycle append/manifest replacement. Repair only exact suffixes/projections; aborted evidence remains non-reportable.

## Remaining deterministic work

- Real reDroid acceptance above, including host-specific binder/DMA-heap and `/proc` behavior.
- Known-GUI path recording/replay for its recovery rung.
- Broader widget/action semantics only with stable re-resolution and typed outcome evidence.
- Controlled IME for spaces/Unicode/password fields.
- Optional terminal hashing/indexing of auxiliary UI/raw-log/child-log/data artifacts if they become publication evidence.
- A committed reproducible adapter/fault-injection suite only if the user explicitly elects to add tests.
- Multi-host durable lease/coordinator; current fleet is intentionally one host/process with host locks and Docker ownership.

## Vertex AI: explicitly deferred

A provider gateway begins only after real deterministic reDroid acceptance. No current fleet/preparation/runtime command reads Google Cloud credentials or contacts Vertex AI.

When implemented, a model may only:

1. rank at most `max_llm_candidates` supplied candidates;
2. produce a value accepted by `input_validation.py` and controlled transport;
3. recommend one already configured recovery route.

It may not invent widgets, rewrite the universe/plan, bypass target re-resolution, move jobs between devices, or call a provider outside complete `model_calls.jsonl` evidence.

## Evaluation before any coverage claim

Use repeated blocked experiments with identical original/instrumented hashes, prepared bundle and universe checksums, reDroid image ID/properties, reset policy, budget, concurrency, and assignment algorithm. Retain every failed/aborted/quarantined run and report confidence intervals/effect sizes.

Required arms:

- deterministic action-order control;
- + soft frontier;
- + action-window yield ranking;
- + recovery ladder;
- + bounded model only after implementation;
- each external baseline under declared source/configuration fidelity.

Primary outcome: final frozen-universe `gui ∪ deep_link` coverage. Secondary outcomes: all-route and total observed coverage, app-owned crashes, productive repeats, stall false alarms, actions/hour, instrumentation eligibility/success/event loss/overhead, model calls/tokens/cost when applicable, and aborted/quarantined rates.

A higher point estimate alone is insufficient. Coverage improvement remains unproven until matched repeats exist.
