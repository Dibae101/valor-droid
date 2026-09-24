# reDroid fleet operator runbook

This runbook covers the deterministic, no-model VALOR-Droid pipeline for multiple reDroid containers. The fleet implementation is built and is covered by the committed regression suite in `valordroid/tests/`, which drives real campaigns through the scheduler using fake ADB/AAPT executables. This checkout has not yet completed acceptance against real reDroid containers or a real instrumented APK; `fleet-accept` is the command that produces that evidence.

Vertex AI is deliberately deferred. None of these commands calls a model provider.

## 1. Host and security prerequisites

Official reDroid documentation says a Linux host needs binder/binderfs, ashmem or memfd, IPv6, ION or DMA-BUF heaps, and a 4 KiB page size. It supports multiple Docker, Podman, or Kubernetes instances. Docker examples use privileged containers, one private data mount per instance, one distinct host mapping to container ADB port `5555`, and an explicit `adb connect`. The project warns that exposing ADB publicly can compromise the container or host.

Sources: [reDroid overview and configuration](https://github.com/remote-android/redroid-doc/blob/master/README.md) and [deployment guide](https://github.com/remote-android/redroid-doc/blob/master/deploy/README.md). Content was rephrased for compliance with licensing restrictions.

Install and verify:

- Docker Engine with permission to run privileged containers;
- Android platform tools (`adb`) and build tools (`aapt`);
- the kernel facilities reported by `fleet-doctor`;
- enough CPU, RAM, disk, and distinct loopback ports for the configured slots;
- Java only when the selected external instrumenter requires it.

VALOR-Droid manage mode binds every ADB port as `127.0.0.1:HOST_PORT:5555`. It rejects a non-loopback managed serial. Do not publish these ports on a public interface.

## 2. Pin the reDroid image

Mutable tags such as `latest` are rejected. Pull the selected release, inspect its repository digest, and put the full `NAME@sha256:DIGEST` value in the fleet config:

```bash
docker pull redroid/redroid:14.0.0_64only-latest
docker image inspect redroid/redroid:14.0.0_64only-latest \
  --format '{{json .RepoDigests}}'
```

The example [`../fleet-config.example.json`](../fleet-config.example.json) uses an all-zero placeholder digest and will fail until replaced. `fleet-doctor` confirms that the local image exposes the configured digest. Every started container is then checked against the inspected immutable image ID.

## 3. Prepare the APK

### Preferred pinned adapter path

VALOR-Droid contains the proof/import/verifier host, the AndroLog protocol adapter, and an auditable all-DEX smali fallback. It does not vendor an AndroLog/Soot dependency closure. For either backend, record:

- an exact 40-hex source commit;
- the transformer version and inclusion policy;
- SHA-256 for the resolved executable and every tool/JAR;
- the backend version, log tag, and method prefix.

Copy [`../instrumenter-config.example.json`](../instrumenter-config.example.json), replace every placeholder, then run:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid prepare-apk \
  --original-apk apks/example.apk \
  --instrumenter-config valordroid/instrumenter-config.json \
  --package org.example.app \
  --aapt aapt \
  --output prepared/example
```

The adapter invokes exactly one argument-vector command without a shell and with a timeout. File-valued command arguments must be declared as hashed toolchain artifacts and referenced as `{tool:NAME}`. The adapter verifies all hashes before and after execution.

The external executable receives these appended arguments:

```text
--protocol valordroid-instrumenter-v1
--input-apk PATH
--output-apk PATH
--units-output PATH
--receipt-output PATH
[--structural-audit-output PATH]  # appended when the backend requires it
--backend REGISTERED_BACKEND
--backend-version VALUE
--inclusion-policy VALUE
--log-tag VALUE
--method-prefix VALUE
```

It must atomically produce one instrumented APK, one unique static-unit JSON array, and the registered exact receipt schema asserting matching producer/source/backend parameters, both APK hashes, canonical unit count/hash, complete instrumentation, complete enumeration, and one probe per unit. The smali backend also produces a required structural audit and binds its SHA-256/positive verified-probe count from receipt schema 2. VALOR-Droid then generates schema-2 static proof, binds source/toolchain digest into `producer_version`, imports, and immediately re-verifies. A missing executable, hash mismatch, malformed receipt/audit, timeout, changed APK identity, zero probes, incomplete assertion, or verification mismatch fails closed and leaves no prepared output.

The AndroLog path still requires an operator-supplied pinned JAR and Soot dependency closure. For Soot-blocked APKs, use `tools/make_smali_instrumenter_config.py`; it emits a strict schema-3 `smali-logcat-method-v1` config from the exact local Java, Apktool, AAPT, zipalign, apksigner, adapter, and dedicated test keystore bytes. The adapter deterministically selects one app class prefix (defaulting to the declared/known-variant comparison), records its exact prefix/rule, instruments all concrete methods below it across every root DEX or aborts, then audits the final signed APK. It never falls back to an unrelated dominant library namespace. See [the smali fallback runbook](SMALI-FALLBACK.md) for the command, register limits, signing warning, and exact proof boundary.

### Manual producer path

If another audited pipeline already produced the two APK endpoints, unit list, and schema-2 proof, use `import-prepared` as documented in [the prepared-bundle contract](PREPARED-BUNDLE.md). Name the backend explicitly. A smali import must also provide `--structural-audit`; a legacy backend rejects that artifact. Both paths end in the same independently verifiable bundle.

## 4. Configure two or more slots

Copy [`../fleet-config.example.json`](../fleet-config.example.json). Paths are resolved relative to the config file. In `manage` mode each device requires:

- a stable `device_id`;
- a unique `127.0.0.1:PORT` serial;
- a unique container name and absolute data root;
- CPU and memory limits;
- sorted Android boot arguments;
- expected immutable Android properties.

Jobs are explicit experimental units. Give every job a unique `job_id` and `run_id`, prepared bundle, base runtime config, core config, and attempt bound. Assignment is frozen as `sorted-round-robin-v1`: sorted jobs are assigned round-robin over sorted device IDs. ADB enumeration order, device readiness timing, and job completion order never alter assignment.

Manage mode recreates a container for every attempt and mounts a new path:

```text
DATA_ROOT/CAMPAIGN_ID/JOB_ID/attempt-NNNN
```

VALOR-Droid never reuses or deletes that data directory. It stops/removes only containers whose VALOR-Droid ownership labels are exactly the expected managed/owner/campaign/plan/device/execution/job/attempt set, including the current run's `execution-<32 hex>` identifier. A foreign, stale-execution, or ambiguously labelled name collision is refused by default. If a crashed execution left a labelled container behind, `fleet-doctor` names it, reports its recorded execution ID, and states whether `reclaim_abandoned_containers` can remove it or whether you must remove it manually. Teardown resolves the full container ID once, then re-inspects that ID and rechecks its labels immediately before stopping and removing it, so a container recreated under the same name is never destroyed by mistake. Archive or remove retained data manually only after evidence review.

`lease_root` must be an absolute host directory. Each slot takes a nonblocking `flock` there, keyed by the normalized host and parsed ADB port, before any ADB connect or Docker inspect. The lease is held until the child process group is proven empty and cleanup has completed, and cleanup is skipped entirely if the lease was never acquired. Two campaigns pointed at the same device therefore fail fast instead of tearing down each other's live instance. Use the same `lease_root` for every campaign on a host; a per-campaign lease root defeats the protection. The lease directory is created private to its owner, so run every campaign on a host as one operator account. A different account cannot create or open the lease and is refused with an explicit message naming this assumption, instead of silently taking an independent lock.

Manage mode also refuses at preflight, not mid-campaign, when a device configures no `androidboot_args` while the pinned image declares its own default command; `fleet-doctor` reports it so the container command stays anchored to the immutable plan.

`attach` mode is for externally provisioned instances. Set Docker-only fields and `image` to `null`, set `remove_owned_containers` and `reclaim_abandoned_containers` to `false`, allow at most one job per serial, and set `max_attempts` to `1`. Attach mode connects and checks devices but never stops containers or claims reset isolation.

## 5. Preflight, freeze, run, validate

Run strict preflight before creating a plan:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid fleet-doctor \
  --config valordroid/fleet-config.json --strict
```

Manage-mode doctor checks tools, page size, binder/IPv6/DMA-heap signals, image digest/ID, ports, and container-name collisions without starting containers. Attach-mode doctor connects each configured serial, requires exact `device` state and `sys.boot_completed=1`, captures identity properties, and enforces configured property values. It also re-verifies every prepared bundle and job config.

Freeze one campaign into a new empty directory:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid fleet-plan \
  --config valordroid/fleet-config.json \
  --output campaigns/example-redroid-2
```

Planning writes normalized `fleet-config.json`, derived per-job runtime/core configs, and checksum-bound `campaign-plan.json`. Inspect the frozen assignments before execution. Do not edit snapshots afterward.

Run, validate, and summarize:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid fleet-run \
  campaigns/example-redroid-2

PYTHONPATH=valordroid/src python3 -m valordroid fleet-validate \
  campaigns/example-redroid-2 --json

PYTHONPATH=valordroid/src python3 -m valordroid fleet-summary \
  campaigns/example-redroid-2
```

Each job launches an unchanged `run-android` child in a new process group with the exact planned serial, ADB/AAPT executables, configs, bundle, run ID, output, stdout, and stderr. A campaign timeout or interruption sends `SIGINT`, waits for Android abort sealing, then escalates through `SIGTERM` and `SIGKILL` only when required. An attempt is finished only once the leader is reaped and its process group is empty; a surviving group is recorded as an operational failure, withholds container cleanup, and quarantines the slot.

`fleet-run` must own the main thread because it installs `SIGINT`, `SIGTERM`, and `SIGHUP` handlers. A host signal sets cancellation, records exactly one `campaign_interrupted` event with that reason, prevents further attempts from starting, and lets in-flight children seal. Remaining jobs become `cancelled` and the campaign becomes `aborted` with exit `130`.

## 6. Evidence and statuses

A campaign contains:

```text
campaign-plan.json              immutable assignment and input checksums
fleet-config.json               normalized exact config snapshot
campaign-events.jsonl           append-only sequence/hash chain
campaign.json                   immutable event-derived terminal projection
configs/JOB.runtime.json        serial-derived exact runtime config
configs/JOB.core.json           exact policy config
logs/JOB.attempt-NNNN.*.log     child stdout/stderr
runs/JOB/attempt-NNNN/          complete single-run evidence when created
```

Campaign exit codes are:

| Code | Meaning |
|---:|---|
| `0` | Every job ended as independently valid `finished` evidence |
| `10` | Campaign sealed, but at least one job was invalid, failed, timed out, or quarantined |
| `130` | Campaign or child interruption produced an aborted campaign |
| `2` | CLI/configuration/operational handler error before a valid fleet result |

Only child exit `2` is automatically retryable, and only in manage mode with attempts remaining, no signal escalation, successful cleanup, and a new container/data path. Invalid/aborted evidence is not retried into success. Timeout, interruption, identity ambiguity, signal escalation beyond graceful handling, or cleanup failure taints the slot; later jobs frozen to it become `quarantined` rather than moving to another device.

`fleet-validate` replays the campaign as an ordered scheduler state machine and rechecks plan/config/generated-config/prepared hashes, the event chain, the terminal projection, every referenced log and run artifact, run ID, prepared/universe binding, configuration snapshots, and the exact assigned serial. Campaign status is derived from that replay, so a campaign containing failed, unretried, or tainted-but-unquarantined attempts cannot present itself as `finished`.

`fleet-summary` accepts only valid finished runs. It pools runs only when prepared checksum, universe checksum, and the normalized `control_sha256` are identical, and it prints the control descriptor for each group. Differing configurations, devices, images, attempt bounds, or scheduling controls are reported separately rather than averaged. It does not merge incompatible denominators or claim a coverage increase.

If `campaign_finished` was fsynced but `campaign.json` was not installed, recover only that exact projection:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid fleet-validate \
  campaigns/example-redroid-2 --recover --json
```

A campaign with prior nonterminal events is not automatically resumed because a child or device may still be owned. Resolve the process/container/device state and start a new campaign ID/output rather than rewriting the old evidence.

## 7. Real reDroid acceptance still required

Before collecting study results, retain a real two-container acceptance trace proving:

1. host prerequisite and pinned-image doctor output — `fleet-doctor --strict`;
2. distinct loopback ports, image IDs, container labels, fresh data paths, Android fingerprints, SDKs, ABIs, and serials — `fleet-accept`, whose report records each device identity and refuses to pass when concurrently provisioned managed slots overlap;
3. successful install/launch/UIAutomator/action/logcat/PID-generation behavior on both containers — `fleet-run` with a genuinely instrumented prepared bundle, since `fleet-accept` deliberately installs nothing;
4. one intentional child failure, graceful abort seal, owned cleanup, and unaffected peer job — `fleet-run`;
5. campaign validation, terminal recovery injection, and checksum tamper rejection — `fleet-validate` and `fleet-validate --recover`;
6. repeated matched runs with identical prepared/universe/device/reset/budget/concurrency controls — two campaigns paired with `campaign-compare`, which refuses to report a difference unless the controls match.

Qualify the external transformer first with `instrumenter-check`, which runs the real pinned invocation and reports each protocol assertion without importing a bundle.

```bash
PYTHONPATH=valordroid/src python3 -m valordroid instrumenter-check \
  --original-apk /path/to/app.apk \
  --instrumenter-config valordroid/instrumenter-config.json

PYTHONPATH=valordroid/src python3 -m valordroid fleet-accept \
  --config valordroid/fleet-config.json
```

`fleet-accept` provisions every configured slot, proves boot completion, expected properties, ADB and package-manager responsiveness, and managed slot distinctness, then tears each slot down by revalidated container ID and releases its lease. It retains a lease whenever teardown fails, so a slot that may still hold a container stays blocked. Steps that cannot apply in the configured mode are reported as `not_applicable` rather than as passes, and `--keep-instances` leaves the instances running for inspection — remove them manually before the next campaign or their ports and container names will collide.

This implementation makes the pipeline ready for that acceptance. It does not substitute fake-executable success for real reDroid evidence, and it does not guarantee higher coverage.
