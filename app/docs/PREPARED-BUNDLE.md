# Prepared bundle contract

`run-android` accepts only an immutable directory created by `prepare-apk` or `import-prepared`. Both paths converge on the same schema-3 manifest, schema-2 proof, and independent verifier. Bundles produced by `prepare-apk` additionally retain hashed instrumenter config, receipt, and invocation artifacts. A backend may also mandate a retained structural audit; `smali-logcat-method-v1` does, while legacy AndroLog bundles do not.

- `prepare-apk` invokes a pinned **external** transformer/static enumerator, verifies its exact completion receipt, generates proof, imports, and re-verifies.
- `import-prepared` does not instrument; it verifies/copies artifacts and proof produced by another audited pipeline.

VALOR-Droid supplies the adapter and verifier, not an AndroLog transformer artifact.

## Preferred pinned-instrumenter path

The current schema-3 config is illustrated by [`../instrumenter-config.example.json`](../instrumenter-config.example.json). It adds an explicit registered `backend`. Legacy schema-2 configs are accepted only as `androlog-logcat-method-v1`; they cannot select a different backend by omission. A config binds:

| Field | Contract |
|---|---|
| `protocol` | Exactly `valordroid-instrumenter-v1` |
| `producer`, `producer_version` | External transformer/enumerator identity |
| `source_revision` | Lowercase 40-hex source commit |
| `command` | Explicit argv prefix; no shell |
| `executable_sha256` | SHA-256 of the resolved first command executable |
| `toolchain` | Sorted unique names, regular non-symlink paths, and SHA-256 |
| `timeout_seconds` | Positive bounded invocation duration |
| `environment` | Exact allowlisted child environment; the caller's environment is never inherited |
| backend/policy/tag/prefix | Exact runtime/static measurement contract |

File-valued command arguments after the executable must be declared in `toolchain` and referenced as `{tool:NAME}`. VALOR-Droid resolves paths relative to the config and checks all hashes both before and after invocation.

A configured **tool** path that is itself a symbolic link is rejected on the literal path before any resolution, because nothing else rechecks the path the operator wrote. The **executable** is treated differently: `command[0]` is looked up on `PATH`, resolved, recorded, and pinned by `executable_sha256`, which is verified against that resolved path both before and after the run. An alternatives-managed interpreter such as `/usr/bin/java` is therefore accepted. Note that this lookup uses the caller's ambient `PATH`; the hermetic environment applies to the child process, not to resolving the executable.

The invocation is hermetic. The child never inherits the caller's environment; it receives only fixed `LC_ALL`/`LANG`/`TZ` values plus the exact `environment` mapping declared in the config, whose names and values are type-checked.

That invocation is retained, not just reported. `prepare-apk` writes the normalized config, the exact receipt, and an invocation record holding the command, environment, exit status, stdout, stderr, verified artifact digests, and an `invocation_sha256` over them into the bundle, where all three participate in `bundle_sha256`. `verify-prepared` recomputes that digest and rechecks the receipt's backend, policy, tag, prefix, APK hashes, unit count/hash, and the three required boolean assertions against the bundle by exact type and value, so retained provenance cannot be edited independently of the artifacts it describes.

The adapter appends explicit input/output/protocol/backend arguments. Every backend must produce one regular instrumented APK, unit JSON, and its registered exact receipt schema. The legacy AndroLog receipt is schema 1. `smali-logcat-method-v1` uses receipt schema 2 and must additionally produce `structural-audit.json`; its receipt binds that artifact's SHA-256 and a positive verified-probe count equal to the units. Both receipt schemas bind producer/source/backend/policy/tag/prefix; original/instrumented hashes; canonical unit count/hash; and these three required assertions:

```json
{
  "instrumentation_complete": true,
  "enumeration_complete": true,
  "one_probe_per_unit": true
}
```

For the smali backend, the required audit records every verified `{dex_file, unit_id}` site, exact original/final DEX arrays, original class/method inventory hashes, helper identity, per-dex counts, and alignment/signature checks. The verifier requires unchanged original inventories, one extra primary helper class with two methods, exactly one site for every unit, and at least one site. See [the all-DEX fallback contract](SMALI-FALLBACK.md).

The adapter independently hashes outputs, normalizes units, AAPT-inspects both APKs, requires equal package/version identity, and checks every receipt value by exact type and value, so JSON `1` cannot satisfy a required boolean assertion. It then generates schema-2 proof. `producer_version` in that proof contains the external version, source commit, and aggregate executable/toolchain digest. Import and a second verification must succeed before output is returned.

Missing/mutated tools, timeout/nonzero exit, missing/symlink output, malformed or incomplete receipt, identity change, or any import/verification mismatch fails closed and removes temporary artifacts. The repository currently contains no real pinned AndroLog/Soot artifact, so the example's placeholder hashes must not be treated as usable inputs.

## Qualifying a transformer before importing anything

`instrumenter-check` runs exactly the invocation `prepare-apk` would — same argv, same hermetic environment, same before-and-after digest verification — in a throwaway directory, and reports every protocol assertion individually instead of stopping at the first failure:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid instrumenter-check \
  --original-apk /path/to/app.apk \
  --instrumenter-config valordroid/instrumenter-config.json
```

It produces no bundle and imports nothing, so it is safe against a candidate transformer that is not yet trusted. Exit `0` means conformant, `10` means the report contains at least one failing assertion, and `2` means the check could not run at all. A digest mismatch is reported as a failing assertion with the instrumenter never executed. A conformant report establishes protocol conformance only: it does not prove that probes were inserted correctly or that the declared static policy is appropriate.

The full operator protocol is in [the reDroid runbook](REDROID-FLEET.md).

## Manual external inputs

`import-prepared` requires:

1. `original.apk`: uninstrumented study APK.
2. `instrumented.apk`: exact APK emitting stable method IDs through the configured logcat tag/prefix.
3. `units.json` or text: claimed complete static unit universe. IDs are nonempty/unique and VALOR-Droid sorts them.
4. `static-unit-proof.json`: exact schema-2 producer assertion.
5. `structural-audit.json`: mandatory only when the selected registered backend requires it; forbidden otherwise.

The proof fields are:

| Field | Meaning |
|---|---|
| `schema_version` | `2` |
| `producer`, `producer_version` | Transformer/enumerator identity and, for `prepare-apk`, bound source/toolchain identity |
| `original_apk_sha256` | Exact retained original bytes |
| original package/version fields | Fresh AAPT original endpoint identity |
| `instrumented_apk_sha256` | Exact retained runtime bytes |
| instrumented package/version fields | Fresh AAPT instrumented endpoint identity |
| `backend` | A registered backend, currently `androlog-logcat-method-v1` or `smali-logcat-method-v1` |
| `backend_version` | Exact external backend version |
| `inclusion_policy` | Explicit static-unit inclusion rule |
| `unit_count` | Number of normalized unique IDs |
| `unit_ids_sha256` | SHA-256 of canonical JSON for the sorted ID array |

[`../static-unit-proof.example.json`](../static-unit-proof.example.json) contains placeholders and is not proof for any APK.

## Imported directory

| File | Contract |
|---|---|
| `prepared.json` | Exact schema-3 manifest and `bundle_sha256` |
| `instrumenter-config.json`, `instrumenter-receipt.json`, `instrumenter-invocation.json` | `prepare-apk` only; all three present or all absent, each hashed into `bundle_sha256` |
| `structural-audit.json` | Required only by audited backends such as `smali-logcat-method-v1`; exact final-DEX inventory and physical probe-site report |
| `original.apk` | Copied original bytes, SHA-256, and AAPT package/version identity |
| `instrumented.apk` | Copied runtime bytes, SHA-256, AAPT identity, and launch candidates |
| `units.json` | Canonically normalized static IDs |
| `static-unit-proof.json` | Copied/generated schema-2 assertion |
| `universe.json` | Schema-v3 runtime package/metric/backend/APK/unit checksum |

Import copies APKs before hashing/inspection. Endpoint package/version identities must be exactly equal. `prepared.json` binds backend/version, inclusion policy, tag/prefix, count, every instrumented launch candidate, and every artifact hash. Artifact paths are one relative filename; verification rejects symlinks.

## What verification establishes

`verify-prepared` rechecks:

- every artifact exists, is regular/non-symlink, and matches its SHA-256;
- both retained APK hashes match manifest/proof endpoints;
- fresh AAPT package/version inspection matches recorded equal endpoints;
- fresh instrumented launch candidates equal the manifest;
- proof fields match endpoints/backend/version/policy/units;
- a required structural audit matches endpoints/config/units, has unchanged original inventory hashes, exact per-dex totals, one site per unit, and a positive verified-probe count;
- `units.json` exactly equals `universe.json`;
- runtime package, metric, backend, APK hash, and universe checksum agree;
- `bundle_sha256` is recomputable.

## What verification does not establish

Checksum/receipt/endpoint consistency alone does not independently prove correct derivation. For the smali backend, the pinned adapter's retained final-DEX audit additionally records the exact policy-selected class prefix and rule, proves that every admitted method has one physical probe callsite, and proves that original class/method inventories survived rebuilding; this remains a claim produced by pinned code, not a proof of runtime reachability. Verification cannot prove that Android loads every class, every probe can emit, logcat loses no events, the inclusion policy is scientifically appropriate, or overhead is negligible. Those require source/tool audit and real APK/device experiments. VALOR-Droid refuses to replace the static list with a run-observed denominator.

## Runtime acceptance

Before installation, the retained instrumented APK is re-hashed. A method line is provisional until PID polls before and after receipt show the same package/subprocess name, PID, boot ID, and `/proc/<pid>/stat` start token. Missing prior ownership, changed generation, or absent final confirmation is rejected; inability to inspect generation fails collection closed. Accepted units retain host/device times, PID/name/start token, and ownership bounds. Any accepted ID outside the frozen universe aborts canonical sampling.

Shutdown drains logcat, performs final ownership confirmation while the package remains, and emits a final collector event. A finished run commits that event and binds it from lifecycle evidence; late failure cannot disappear behind shutdown. `collector_stopped.final_event_id` must name the last coverage event in the ledger and that event must be referenced by the last canonical transaction, which must be the closing background coverage transaction, so an earlier event cannot be relabelled as final.

Coverage snapshots are additionally required to be cumulative and immutable. A later successful event must contain every previously observed unit, must not change any retained first-observation timestamp, device time, PID, process name, process generation, or ownership bound, and may not follow a failed collection. Canonical replay enforces the same superset and first-timestamp rules on committed samples.

A configured launcher must be one of the instrumented AAPT candidates. Without override, exactly one candidate is required.
