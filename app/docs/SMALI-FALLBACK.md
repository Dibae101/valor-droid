# Auditable all-DEX smali fallback

`smali-logcat-method-v1` is the fail-closed fallback for APKs that the AndroLog/Soot path cannot serialize. It is a separate registered backend; its outputs are never labelled as AndroLog. The fallback changes instrumentation mechanics, not the study denominator rule: each prepared APK freezes one pre-run static method universe, and every run using that bundle keeps it unchanged.

## Select and pin the backend

Generate a schema-3 instrumenter config from the exact files installed on the host. The generator resolves symlinks and records SHA-256 for the adapter, Java, Apktool, AAPT, zipalign, apksigner, and keystore:

```bash
python3 valordroid/tools/make_smali_instrumenter_config.py \
  --adapter valordroid/tools/smali_logcat_instrumenter.py \
  --valordroid-repository . \
  --apktool-jar /absolute/path/to/apktool.jar \
  --build-tools /absolute/path/to/android-sdk/build-tools/VERSION \
  --keystore /absolute/path/to/dedicated-test.keystore \
  --output /absolute/path/to/smali-instrumenter.json
```

The emitted config explicitly binds:

```json
{
  "schema_version": 3,
  "backend": "smali-logcat-method-v1",
  "inclusion_policy": "app-classes-methods-v1",
  "log_tag": "VALORDROID",
  "method_prefix": "METHOD="
}
```

Schema-2 configs remain readable only as legacy `androlog-logcat-method-v1` configs. A schema-3 config without `backend`, or a schema-2 config attempting to select smali, is rejected rather than inferred. The generator defaults to `app-classes-methods-v1`; pass `--inclusion-policy app-package-methods-v1` only when the declared application ID is known to be the exact class namespace.

Use a dedicated non-production signing key. The generated config retains its exact child environment as provenance, including configured signing arguments; do not put a production keystore password or account credential there. Re-signing changes the APK certificate, so install it as a fresh test app rather than as an upgrade over the upstream-signed package.

Qualify the pinned command first, then prepare:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid instrumenter-check \
  --original-apk /absolute/path/to/app.apk \
  --instrumenter-config /absolute/path/to/smali-instrumenter.json \
  --aapt /absolute/path/to/aapt

PYTHONPATH=valordroid/src python3 -m valordroid prepare-apk \
  --original-apk /absolute/path/to/app.apk \
  --instrumenter-config /absolute/path/to/smali-instrumenter.json \
  --package org.example.app \
  --aapt /absolute/path/to/aapt \
  --output /absolute/path/to/prepared/app
```

`instrumenter-check` proves protocol and artifact consistency without retaining a bundle. `prepare-apk` additionally generates the schema-2 static proof, retains all inputs/claims, imports the bundle, and runs the independent prepared-bundle verifier.

## Transformation

The pinned adapter performs these bounded stages:

1. Read the APK ZIP and enumerate root `classes.dex`, `classes2.dex`, … entries in numeric order. Duplicate ZIP names, a missing primary dex, or an unrecognized dex name fail.
2. Decode sources with the pinned Apktool jar and require an exact one-to-one `classes.dex → smali`, `classesN.dex → smali_classesN` directory set.
3. Parse every smali class and method. `app-package-methods-v1` selects the exact AAPT application-ID namespace. `app-classes-methods-v1` considers only that namespace and, when the ID ends in `.debug`, `.dev`, `.alpha`, `.beta`, `.nightly`, `.free`, `.fdroid`, or `.test`, the one suffix-stripped namespace. It counts decoded classes under each class-bearing candidate and deterministically chooses the greatest count, then the most specific prefix, then lexical order. It never falls back to an unrelated dominant library root. Every concrete, non-native, non-abstract method under the chosen prefix is selected across every dex; selecting zero methods fails.
4. Add one local register and one literal-bound call to a unique generated helper at method entry. Both `.locals` and `.registers` are supported. When adding the local shifts physical parameter slots, typed `/16` moves copy every parameter back to its old physical slot so code using explicit `v` parameter aliases remains valid.
5. Add one unique helper class to primary dex. Its `hit(String)` method is `declared-synchronized`, catches `Throwable`, stores each finite callsite string in a `HashSet`, logs only the first occurrence, and refuses additions after the exact unit count. It neither calls itself nor instruments platform/library methods.
6. Rebuild with the pinned Apktool, run zipalign, sign with the pinned test key, verify alignment/signature, and require unchanged AAPT package identity.
7. Re-decode the **final signed APK**, not an intermediate. Require the same dex set; identical original class and method inventories in their original dex; exactly one extra primary-dex helper class with exactly two code-bearing methods; and exactly one helper callsite with the exact `METHOD=<unit>` literal in every selected method. Any call outside the universe, missing call, duplicate call, changed method, or zero probes fails.

The adapter does not silently skip a method. A selected method that cannot satisfy the register/parser/build/audit contract aborts the entire APK.

## Retained structural proof

This backend must emit receipt schema 2 and `structural-audit.json`. The receipt binds the audit SHA-256 and a positive `verified_probe_count` equal to the normalized unit count. The audit is a required prepared artifact and binds:

- original and instrumented APK SHA-256;
- backend/version, policy, tag, and prefix;
- the exact selected class-descriptor prefix and deterministic selection rule;
- exact original/final dex filename arrays;
- original/final class and method inventory hashes and counts;
- the generated helper descriptor and final decoded smali hash;
- every verified `{dex_file, unit_id}` probe site;
- per-dex original/final class, method, selected-unit, and probe counts;
- successful zipalign and signature verification.

The independent verifier checks exact field sets and types, all endpoint/config/unit bindings, the policy-specific selection-rule shape, that every unit begins with the audited class prefix, sorted unique probe sites, one site for every unit, per-dex totals, unchanged inventory hashes, a primary-only `+1` class/`+2` code-method delta, and `verified_probe_count > 0`. Removing the audit, changing it independently, or importing it under a different backend is rejected.

For a manually audited smali output, supply both backend and audit explicitly:

```bash
PYTHONPATH=valordroid/src python3 -m valordroid import-prepared \
  --original-apk original.apk \
  --instrumented-apk instrumented.apk \
  --units units.json \
  --proof static-unit-proof.json \
  --backend smali-logcat-method-v1 \
  --structural-audit structural-audit.json \
  --backend-version 1.0 \
  --inclusion-policy app-classes-methods-v1 \
  --log-tag VALORDROID \
  --method-prefix 'METHOD=' \
  --output prepared/app
```

The audit is forbidden for a backend that does not require it, and mandatory for this one.

## Explicit limitations

- The fallback supports `app-package-methods-v1` and `app-classes-methods-v1`. The latter only compares the declared ID with one known variant-suffix-stripped candidate; it rejects zero-class selection and does not guess unrelated or dominant library roots to enlarge the denominator.
- Concrete methods must have exactly one `.locals` or `.registers` directive and at most 255 existing locals; adding one register must remain within the DEX register limit. Unsupported leading directives or malformed descriptors abort the APK.
- Apktool/smali must understand every input dex opcode/version and must rebuild the APK without changing original class/method placement. Build failure, DEX reference overflow, or inventory drift aborts.
- The physical audit proves what is present in the final signed APK. It does not prove that Android will load every class, that every method is reachable, that logcat never drops an event, or that instrumentation overhead is negligible.
- First-hit deduplication bounds log volume and helper memory by the static unit count, but high startup concurrency and device/runtime behavior still require real-device acceptance.
- The fallback does not bypass login, patch app behavior, stub methods, credit setup execution to exploration, alter a frozen universe after preparation, or establish a coverage gain. The >40% claim still requires valid finished device runs over matched fixed universes.
