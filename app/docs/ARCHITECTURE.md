# VALOR-Droid architecture

**VALOR-Droid** means **V**erified **A**ction **L**edgers and **O**utcome-aware **R**outing for Android. It is the implementation successor to the historical CARBIDE design notes.

## Claim boundary

The implementation enforces preparation, fleet, execution, and evidence contracts. It does not ship an APK transformer artifact, configure Vertex AI, substitute fake tests for real reDroid acceptance, or promise a coverage increase.

| Implemented invariant | Still requires external evidence |
|---|---|
| Pinned command/source/tool hashes, explicit backend registry, exact completion receipt, generated proof, dual retained APK hashes/identities, required final-DEX structural audit for smali, prepared checksum, frozen universe | Real-device probe behavior, event loss/overhead, and evidence that the chosen static policy is scientifically appropriate |
| Immutable sorted job/device assignment, digest-pinned managed image, loopback ports, ownership labels, fresh per-attempt data, identity checks | Successful multi-container behavior on the actual host kernel, image, APK, and resource limits |
| Explicit serial/device lock, bounded ADB, deterministic candidate order and recovery | Better exploration than matched controls or prior tools |
| Same-generation package-process method hits with host/device timestamps and ownership intervals | Instrumentation overhead/event loss and `/proc` behavior across the study matrix |
| WAL-first run evidence plus hash-chained campaign evidence and independent validation | Repeat-run variance, effect sizes, cost effectiveness, and defect yield |

[Architecture diagram source](../diagrams/architecture.mmd)

## Boundaries and sequence

### 1. External instrumenter, fail-closed adapter, verified bundle

Two preparation paths converge on prepared schema 3 with proof schema 2:

- `prepare-apk` invokes one externally supplied transformer/static enumerator through `valordroid-instrumenter-v1`;
- `import-prepared` accepts already audited original/instrumented APKs, units, and proof.

The schema-3 adapter configuration freezes an explicit registered backend plus exact producer/version, 40-hex source revision, resolved executable SHA-256, every file-valued tool/JAR SHA-256, timeout, backend version, inclusion policy, log tag, and method prefix. Legacy schema-2 configs map only to AndroLog. Invocation is an explicit argument vector without a shell. Tool files are declared through `{tool:NAME}` placeholders; all hashes are checked before and after execution.

Every external process must produce one instrumented APK, one complete unique unit list, and its backend's exact receipt. Both registered backends bind producer/source/config, APK hashes, canonical unit hash/count, and `instrumentation_complete=true`, `enumeration_complete=true`, and `one_probe_per_unit=true`. The smali backend additionally requires receipt schema 2 and `structural-audit.json`: it decodes every root DEX, deterministically selects an app namespace under `app-package-methods-v1` or `app-classes-methods-v1`, instruments every concrete method under that audited class prefix or aborts, then re-decodes the final signed APK and records the selected prefix/rule, unchanged original inventories, and exactly one positive physical probe site per unit. The host validates that audit before generating proof, importing, and independently re-verifying the bundle. Any absence, timeout, mismatch, zero-probe result, incomplete receipt/audit, or changed tool fails closed with no output bundle.

This proves invocation/output consistency and, for smali, the physical final-DEX structure claimed by pinned adapter code. It does not prove runtime reachability, loss-free logcat, negligible overhead, or a coverage gain. See [the prepared-bundle contract](PREPARED-BUNDLE.md) and [smali fallback contract](SMALI-FALLBACK.md).

### 2. Immutable fleet plan and reDroid ownership

Fleet schema 2 has exact `attach` and `manage` modes. Planning resolves paths relative to the source config, verifies every prepared/runtime/core input, writes normalized snapshots, derives the assigned serial into each runtime config, and freezes `sorted-round-robin-v1`: sorted jobs are assigned round-robin over sorted device IDs. The checksum-bound plan is independent of ADB order, readiness, free-device timing, and completion order.

Manage mode requires:

- a reDroid image reference pinned by repository digest and rechecked against local Docker image ID;
- distinct `127.0.0.1:PORT` ADB serials, unique names/data roots, CPU/memory limits, and expected Android properties;
- one fresh `DATA_ROOT/CAMPAIGN/JOB/attempt-NNNN` directory and recreated container per attempt;
- exact managed/owner/campaign/plan/device/execution/job/attempt labels.

Every slot is guarded by a host-global `flock` lease under `lease_root`, keyed by the normalized host and parsed ADB port, so `localhost:5555` and `127.0.0.1:5555` contend and an attach execution cannot run beside a manage execution on one physical instance. The lease root is private to one host user. A second account cannot create or open the lease and is refused with an explicit message naming the single-user assumption, rather than silently taking an independent lock. The lease is taken before any ADB connect or Docker inspect/replace/stop, and held until the child process group is proven empty and provider cleanup has finished. Two campaigns that share a slot therefore cannot both own it, even when one is a copied campaign directory with identical plan labels. Each `fleet-run` also generates one `execution-<32 hex>` identifier that is recorded in `campaign_started`, stamped into container labels, and required by device-identity validation, so a container from another execution is never treated as owned.

Destructive Docker actions never use the mutable container name. Provisioning captures the full 64-hex container ID, and stop/remove re-inspect that exact ID and revalidate the complete VALOR-Droid ownership label set immediately before acting. If the lease cannot be acquired, provider cleanup is not attempted at all.

Because ownership is execution-scoped, a container left behind by a crashed execution no longer matches. That leftover fails closed by default and `fleet-doctor` reports its recorded execution ID, whether it is reclaimable, and the exact remedy. Setting `reclaim_abandoned_containers` permits removing one such container, and only when it carries the complete ownership label set, its plan-stable labels match this configuration, and its recorded execution differs from the current one; the caller already holds the slot lease, so it cannot belong to a live peer. Data directories are retained, never silently reset or deleted. Attach mode only connects/checks explicit externally reset serials; it permits one job per device, no automatic retry, and never manages a container.

Readiness requires one exact ADB inventory entry in `device` state, `sys.boot_completed=1`, and matching configured properties. Captured managed evidence is produced from one post-run inspect by container ID and includes fingerprint, release, SDK, ABI, serial number, pinned image identity, full container ID, the exact label map, running and privileged state, CPU/memory limits, the androidboot command, the `/data` bind source, and the loopback ADB binding.

### 3. Isolated process-level scheduler

Every job is an unchanged `run-android` child with its planned prepared bundle, run ID, derived runtime/core configs, ADB/AAPT executables, output, and logs. It starts in a new process group. Per-device queues serialize jobs assigned to one slot while independent slots run up to `max_parallel`.

Cancellation or timeout sends `SIGINT` first so the child can seal an aborted run, then bounded `SIGTERM` and `SIGKILL` only if required. Supervision tracks the process group rather than only the group leader. While the leader is unreaped the attempt still owns the group. Once the leader is reaped, survival is decided by exact `/proc` membership: a live process whose process group is this group and whose PID is not the former leader's. A leader is always a member of its own group, so group existence alone would prove nothing, and a recycled group ID claimed by an unrelated session leader is therefore not mistaken for a surviving descendant. A cheap `killpg(pgid, 0)` probe short-circuits the walk when nothing holds the group at all. Signals are still delivered when the leader has exited but descendants remain, and a surviving group is recorded as an operational failure that withholds provider cleanup and quarantines the device. Only child exit `2` can be retried, and only in manage mode with attempts remaining, no escalation, successful cleanup, and a fresh container/data path. Exit `10`, malformed evidence, timeout, or interruption is not retried into apparent success.

`fleet-run` installs main-thread `SIGINT`, `SIGTERM`, and `SIGHUP` handlers. A handler only sets cancellation and retains the first reason; the ordinary control path appends exactly one `campaign_interrupted` event, refuses to start further attempts, and restores the previous handlers. A host `SIGTERM` or `SIGHUP` therefore produces sealed, replayable abort evidence instead of orphaned children.

Identity ambiguity, timeout/interruption, escalation beyond graceful handling, or provider cleanup failure taints the slot. Later jobs frozen to that slot become `quarantined`; they are never moved to a different device.

### 4. Owned Android session

`run-android` requires one exact runtime serial. Every device command is `adb -s SERIAL`; a host `flock` prevents two proper VALOR-Droid children from owning the same serial. The local APK is re-hashed, any installed package is removed, one SDK-aware `adb install -g` path is used, and package-manager presence, `am start -W`, and prepared-package foreground activity are checked.

A launcher override must be a prepared instrumented-AAPT candidate. Without an override there must be exactly one candidate. Cleanup is package-scoped force-stop and optional uninstall.

### 5. Observe, choose, re-resolve, dispatch, verify

Foreground activity is resolved only from authoritative ActivityTaskManager markers: per-display `topResumedActivity` when present, otherwise per-task `mResumedActivity`. Window focus is never treated as activity authority and arbitrary dump lines are never scanned. Explicit `null` markers and unparsable lines are skipped, because unfocused tasks and secondary displays legitimately report them.

One resolution feeds both consumers. Launch waiting requires the prepared package to own exactly one resumed activity, which is unambiguous even when several tasks each report one. State identity binds the complete sorted resumed set alongside the single resolved activity, so an ambiguous frame is never confused with "no activity" and two ambiguous frames with identical hierarchies remain distinct states.

The observer combines foreground activity, compressed UIAutomator XML, and optional screenshot hashes. Candidate selectors contain resource ID, class, text, content description, package, and tree path. Candidate/action IDs are stable hashes.

The frontier deterministically orders untried, productive, bounded-retry, and cooldown-expired candidates. Once a replay-derived entry reaches `max_zero_gain_candidate_attempts` with zero associated methods, ordinary selection marks it permanently exhausted before considering screen-change effects; no separate mutable exhaustion state exists. Before execution the adapter captures again and requires the expected state plus exactly one matching selector:

- changed state, missing/ambiguous target, or invalid text becomes `not_executed`;
- an ADB operation error becomes `failed`;
- there is no hidden Back substitution;
- text is restricted to validated safe ASCII until a controlled IME exists.

The action window starts immediately before the first input/route command, never before re-resolution. Input acceptance is checked only against a uniquely rebound selected field. A post-action observation produces `effect`, `no_effect`, `failed`, or `not_executed`.

Canonical replay validates route-free GUI attempts in prepared runs rather than accepting them because they carry no route. The requested action must be one of tap, long press, text input, or forward/backward scroll; its parameters must be exactly the expected state and selector, plus the configured input value for text entry; the selector must carry the exact six observer fields, name the prepared package, and hash to the recorded target ID; and the expected state must equal the attempted state. Outcomes must also agree with their execution: not-executed and failed executions require the matching outcome kind and an error, executed actions must retain the identical dispatched action, the recorded before-state must match the attempt and the after-state must be present, `state_changed` must agree with those two IDs, field acceptance may appear only for an executed text input, and the effect classification must follow from that evidence.

### 6. Continuous measurement before canonical sampling

The collector starts before launch and retains epoch logcat. A tag/prefix match is provisional until ownership polls before and after host receipt agree on package/subprocess name, PID, boot ID, and process start token. PID reuse, retroactive ownership, missing prior ownership, and unconfirmed shutdown hits are rejected. Inability to establish process generation fails closed.

Each snapshot first appends typed `CoverageEvent` evidence: explicit success/failure; cumulative sorted/unknown IDs; host receipt times; device times; PID/name/start token; ownership bounds; and package/backend/session/universe identity. A failed event has no sample payload. Any outside-universe ID is retained as backend violation and prevents canonical commit.

Shutdown stops polling, drains logcat, confirms ownership one final time while the package remains available, checks collector health, fsyncs diagnostics, and emits the final synchronized event.

### 7. WAL commit, projection, and run recovery

Every action/background transaction references one prior successful unused collector event. Replay requires exact equality between the event and derived sample.

`transactions.jsonl` append/fsync is the canonical commit point. Coverage, association, action, and successful action-selection model-call projections follow. Restart accepts exact prefixes, appends missing transaction-derived model calls by immutable call ID, appends only missing suffixes for ordered projections, and rejects conflicts/orphans. A collector event left before a crash without a referencing transaction remains visible instead of becoming manufactured coverage.

A durable terminal lifecycle record is finalization intent. If `run.json` replacement fails afterward, reopen derives and installs only the exact terminal projection. Finished/aborted manifests are immutable and seal every ledger count/head.

### 8. Recovery, crash, and terminal rules

Implemented recovery routes are current-screen untried GUI candidates (`gui`), Back/reveal (`gui`), evidence-derived known-GUI path replay (`gui`), frozen deep links (`deep_link`), exported components (`intent`), bounded model choice among observed candidates (`gui`), separately authorized forced components (`forced`), and bounded reset seed (`gui`). Non-repeatable static routes may be selected in coverage-ranked order, but replay requires membership in the frozen authorized set and rejects reuse. Forced routes require `allow_forced_routes=true`.

`coverage-context.json` is advisory, strict, and self-checksummed. It binds the frozen universe and optional `route-discovery.json`, parses each method's declaring class/member/callback family, maps exact and `$`-nested activity classes, maps manifest links through their owning activities, and ranks unused routes by currently uncovered inferred methods. The WAL, route ledger, and association evidence remain authoritative. Coverage plateaus and permanently exhausted screens may escalate to a bounded model; temporary cooldown suppression may not. A successful action-selection call is embedded in the same canonical transaction as its action and replay proves the retained response chose that candidate; a refusal or failed post-dispatch observation remains an unlinked call. Generated deep links additionally require the exact retained pre-run synthesis prompt, response-derived authorized URI set, discovery inputs, configured limit, and call timestamp.

Crash handling uses one bounded epoch parser. It begins on an `AndroidRuntime` fatal marker and retains only same-PID/tag grammar within line/byte/time limits. The `Process:` header must name the prepared package/subprocess; any reported PID must equal the epoch PID. `owned_crash` finish requires independently reparsable retained evidence.

Normal budget completion—or retained owned crash after one complete action—may seal `finished`. Collection, device, observation, projection, recovery, cleanup, or keyboard interruption seals `aborted` when reconciliation succeeds. `summary` accepts only valid `finished` runs.

### 9. Campaign event authority and recovery

`campaign-events.jsonl` is sequence/hash chained and records campaign start, attempt/device/child lifecycle, signals, retries, cleanup failure, quarantine, job terminals, interruption, and campaign finish. `campaign.json` is an exact event-derived projection binding tool/campaign/execution/plan identity, status/reason/times, every job/attempt result, counts, and terminal event head. Its `tool_version` is the historical value recorded in the plan, not the currently installed package version.

Campaign evidence schema 2 is replayed as one ordered scheduler state machine rather than trusted from producer payloads. Each device advances its frozen job queue, and each job must move through `attempt_started`, optional `device_ready`/`child_started`/ordered `child_signal`/`provider_cleanup_failed`, `attempt_finished`, and then exactly one of an eligible `attempt_retry`, a required `device_quarantined`, or `job_finished`. Replay derives status and reason itself and compares them with `campaign_finished`, so a campaign cannot be labelled `finished` while it contains failed attempts, excess retries, unretried eligible failures, tainted-but-unquarantined attempts, attempts started on a quarantined device, or attempts started after interruption. Every managed `device_ready` identity is revalidated against the configured image digest, expected labels including the execution ID, container runtime state, resource limits, `/data` bind, and loopback port.

The terminal event is only appended after the campaign has been replayed with that prospective event included, and after the referenced log, run-manifest, and configuration-snapshot artifacts have been rechecked with the same code recovery uses. A projection that would be rejected for either reason therefore leaves the campaign nonterminal and inspectable instead of sealing a ledger that can never produce a manifest. Likewise, a scheduler failure that leaves a started job without a recorded disposition refuses to seal anything at all.

If the terminal event was fsynced but manifest replacement failed, `fleet-validate --recover` installs only the exact derived projection, and only after scheduler replay plus every referenced log/run artifact has been revalidated. Nonterminal event history is never automatically resumed because a process/device may still be owned.

Campaign validation rechecks plan/config/generated-config/prepared hashes, deterministic order/assignment, event chain/projection, job/run IDs, prepared/universe checksums, exact assigned serial and configuration snapshots, run manifest hash/status, and independent run validation. Only valid finished jobs enter summaries. Runs pool only when prepared checksum, universe checksum, and a normalized `control_sha256` are identical; that control descriptor covers core/runtime configuration, attempt bounds, fleet mode and pinned image, device resources, expected and observed properties, scheduling and grace controls, and tool/assignment versions, and it is emitted alongside each group.

## Artifact ownership

| Data | Sole writer | Authority |
|---|---|---|
| `prepared.json`, `universe.json` | adapter/importer | Both APK endpoints, runtime backend/APK, denominator contract |
| `route-discovery.json`, `coverage-context.json` | manifest/context derivation | Frozen route authorization input and independently recomputable advisory method goals |
| `campaign-plan.json`, `fleet-config.json`, `configs/*` | fleet planner | Immutable input identity, assignment, derived child configs |
| `campaign-events.jsonl` | campaign store | Canonical fleet lifecycle/job/process evidence |
| `campaign.json` | campaign store/recovery | Exact terminal event projection and event head |
| `coverage_events.jsonl` | coverage gateway | Collector attempts/results and process-generation provenance |
| `transactions.jsonl` | transaction coordinator | Canonical run action/background authority |
| `coverage.jsonl`, `associations.jsonl`, `actions.jsonl` | transaction projections | Reconstructible run views |
| `lifecycle.jsonl`, `routes.jsonl` | typed gateways | Runtime phases/failures/terminal intent and authorized routes |
| `model_calls.jsonl` | model gateway | Complete bounded call evidence, including refusals and attempt linkage |
| `crashes.jsonl` | scoped crash pipeline | Retained package fatal blocks and signatures |
| `run.json` | run store/recovery | Exact run status, prepared/runtime binding, terminal heads |

Raw logcat, stderr, UI XML, screenshots, child stdout/stderr, and retained reDroid data are diagnostics. Semantically accepted method/crash/campaign events are copied into hash-chained authority.

## Temporal association and reporting

For each first-covered known unit, a host receipt time inside one post-dispatch executed-action window permits `first_observed_in_action_window`. Overlap, non-execution, out-of-window events, background/final samples are `unattributed`. No record claims an action caused a method.

Run percentages use one frozen denominator: primary `gui ∪ deep_link`, all-route `gui ∪ deep_link ∪ intent ∪ forced`, and total observed. Campaign summaries pool runs only within one prepared/universe/`control_sha256` group, so differing configurations, devices, images, or scheduling controls are never averaged together. These are descriptive values, not baseline effects. Every terminal manifest records `not_established_without_matched_control`.

## Schema compatibility

Version 0.4.0 uses run/universe/evidence schema 3, prepared schema 3, proof schema 2, backend-aware instrumenter config schema 3 (with legacy AndroLog config schema 2), backend receipt schemas 1/2, structural-audit schema 1, fleet configuration schema 2, campaign evidence schema 2, coverage-context schema 1, and independent plan/instrumenter-protocol schema 1. Exact older/unknown fields and schemas are rejected rather than silently reinterpreted. Provider credentials and setup secrets remain outside every retained schema.

## Operating documents

- [ADDING-AN-APK.md](ADDING-AN-APK.md) — putting a new APK into the dataset:
  choosing a backend, checking the universe is credible, and the smoke gate that
  catches an app which instruments cleanly and then dies at launch.
- [PREPARED-BUNDLE.md](PREPARED-BUNDLE.md) — the immutable bundle contract.
- [SMALI-FALLBACK.md](SMALI-FALLBACK.md) — the fallback backend for APKs Soot
  cannot serialise.
- [REDROID-FLEET.md](REDROID-FLEET.md) — running a device fleet.
- [V2-OUTCOME.md](V2-OUTCOME.md) — what the v2 designs did when they met the
  devices: which landed, which were reverted on measurement, which diagnoses were
  wrong, and the campaign-level faults that were in no gap list. Read this before
  the two documents below, which it corrects.
- [GAPS-AND-SOLUTIONS.md](GAPS-AND-SOLUTIONS.md) — every measured gap, the research
  on it, and the design chosen, each checked against whether it works on an APK
  nobody has looked at.
- [FIX-STRATEGY-V2.md](FIX-STRATEGY-V2.md) — what the first 50-app campaign showed
  and how the next one addresses it.
- [GAPS-V1.md](GAPS-V1.md) and [GAPS-V1-ALL-APPS.md](GAPS-V1-ALL-APPS.md) — the
  measured gaps that strategy answers.
