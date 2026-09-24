# Adding an APK to the dataset

Every command here was run against the real 50-app dataset. The order matters:
instrumentation succeeding tells you nothing about whether the app still starts,
and a bundle whose universe is wrong will inflate every percentage computed from
it. Both failures happened, so both have a gate.

    1. drop the APK in the dataset
    2. instrument it, twice if necessary, once per backend
    3. check the universe is credible
    4. smoke-test that the instrumented app still runs
    5. only then let a campaign use it

## 1. Drop the APK in

    dataset/apks/<Name>.apk

`<Name>` is the app's identity everywhere afterwards: run directories, result
tables, and the `--apks` argument. Use one without spaces.

Confirm the package and launcher resolve, because a missing launcher fails
preparation later with a less obvious message:

    aapt dump badging dataset/apks/<Name>.apk | grep -E "^package|launchable-activity"

## 2. Instrument it

There are two backends and they fail on different apps, so the choice is per app,
not per campaign.

**AndroLog/Soot** rewrites the dex through Soot. It resolves app namespaces well,
and it is the only backend that can neutralise a startup-blocking method. It also
re-serialises classes it did not touch, which is how it produced APKs that ART
refuses to load.

**smali** disassembles with apktool and edits only the methods it probes, so
untouched code is never re-serialised. It survives apps Soot cannot handle, but its
app-namespace detection is weaker and it has no unblock mechanism.

Generate a config once per backend. Both pin every tool by checksum, so a rebuilt
jar invalidates the config rather than silently changing results.

    # smali
    python3 tools/make_smali_instrumenter_config.py \
      --adapter tools/smali_logcat_instrumenter.py \
      --valordroid-repository . \
      --apktool-jar /home/ubuntu/apktool_2.9.3.jar \
      --build-tools /usr/lib/android-sdk/build-tools/debian \
      --keystore /home/ubuntu/vd-instrumenter.keystore \
      --keystore-alias androidkey --keystore-password android \
      --aapt /usr/lib/android-sdk/build-tools/debian/aapt \
      --output /home/ubuntu/vd-campaign/instr-smali.json

    # AndroLog/Soot
    python3 tools/make_instrumenter_config.py \
      --adapter tools/androlog_instrumenter.py \
      --androlog-jar /home/ubuntu/test-jacoco/AndroLog/target/androlog-0.1-jar-with-dependencies.jar \
      --androlog-repository /home/ubuntu/test-jacoco/AndroLog \
      --build-tools /usr/lib/android-sdk/build-tools/debian \
      --keystore /home/ubuntu/vd-instrumenter.keystore \
      --keystore-alias androidkey --keystore-password android \
      --platforms /home/ubuntu/android-platforms \
      --aapt /usr/lib/android-sdk/build-tools/debian/aapt \
      --inclusion-policy app-classes-methods-v1 \
      --heap 6g --soot-threads 1 \
      --output /home/ubuntu/vd-campaign/instr-v9.json

The keystore alias is not optional. Both generators default to `android` while the
project keystore uses `androidkey`, and the mismatch surfaces late as
`apksigner sign: entry "android" does not contain a key`.

Then prepare. `--prepare-only` instruments and verifies without running anything:

    python3 tools/run_coverage_experiment.py \
      --apks <Name> --serials 127.0.0.1:5561 \
      --instrumenter-config /home/ubuntu/vd-campaign/instr-smali.json \
      --work /home/ubuntu/vd-campaign/bundles-new --prepare-workers 1 \
      --prepare-timeout 3000 --arms gemma \
      --output /home/ubuntu/vd-campaign/bundles-new/prepared.json --prepare-only

Several configs may be passed and are tried in order, so a single invocation can
fall back from one backend to another.

### Failures seen in practice

| Message | Meaning | What to do |
| --- | --- | --- |
| `instrumenter tool checksum mismatch` | a pinned tool was rebuilt | regenerate the config |
| `entry "android" does not contain a key` | wrong keystore alias | pass `--keystore-alias androidkey` |
| `no decoded classes match the declared application id` | smali cannot find the app namespace | use the Soot backend |
| `class descriptor appears in more than one dex` | an R8 synthetic is duplicated | use the Soot backend |
| `Interface field is not public final static` | Soot dropped field flags | use the smali backend |
| `expected to be within a catch-all ... monitor is held` | Soot re-serialised a synchronized method | use the smali backend |

## 3. Check the universe is credible

This gate exists because the smali backend can resolve a fraction of an app's
methods and report success. And-Bible resolved 173 methods where Soot finds 6,721;
a percentage computed against 173 would have been inflated roughly fortyfold.

Compare the two backends for the same app:

    python3 -c "
    import json
    for name in ('smali','soot'):
        p=f'/home/ubuntu/vd-campaign/bundles-{name}/prepared/<Name>/prepared.json'
        print(name, json.load(open(p))['unit_count'])"

Reject the smaller if the ratio is under about one half. `vd-campaign/assemble_50.py`
applies exactly that rule across a whole dataset and writes its decision, with the
reason, to `backend-selection.json`.

If only one backend produced a bundle there is nothing to compare against. Sanity
check the count against the app's size: a 20 MB APK with a few hundred units is
wrong.

## 4. Smoke-test that the app still runs

Instrumenting successfully says nothing about the app starting. Six apps
instrumented cleanly and then died at launch, each wasting an hour of device time
before anyone noticed.

    /home/ubuntu/vd-campaign/smoke_smali.sh 127.0.0.1:5561 <Name> <package> <launcher-class>

It passes only when the process is alive, the app is what is resumed, probes have
reached logcat, and no verification or class-loading failure appears.

When it fails, find out whose fault it is before changing anything, by installing
the untouched APK:

    /home/ubuntu/vd-campaign/original_apk_check.sh 127.0.0.1:5561 <Name> <package> <launcher-class>

If the original also dies, the app needs something the emulator lacks and the
instrumentation is not to blame. If the original runs, the instrumentation broke
it. All five apps tested this way ran perfectly uninstrumented, which redirected
the entire investigation.

### When the app needs a method neutralised

Amaze calls `UsbManager.getDeviceList()` during startup and dies on a device with
no USB service, instrumented or not. One method can be neutralised, and only by the
Soot backend:

    VD_UNBLOCK_METHODS=<com.amaze.filemanager.activities.MainActivity: boolean isUsbDeviceConnected()>

Set it in the instrumenter config's `environment` block so it is pinned and
recorded rather than passed ad hoc. Find the offending method by decoding the APK
and looking for the call:

    java -jar apktool.jar d -f -o /tmp/probe dataset/apks/<Name>.apk
    grep -rn "getDeviceList" /tmp/probe/smali*

Returning false is what the platform would have answered, but the result is a
measurement of a modified app and must be reported as such.

## 5. Let a campaign use it

Hard-link the bundle rather than copying it. Bundles are immutable and
content-hashed, so links verify identically and cost nothing; copying cost 291 MB
per campaign and filled the disk.

    cp -al /home/ubuntu/vd-campaign/bundles-final50/prepared/<Name> "$WORK/prepared/<Name>"
    find "$WORK" -type d -exec chmod u+wx {} +

The `chmod` is needed because permissions live on the shared inode, so
write-protecting the canonical bundle also write-protects every link to it.

Then add the name to `--apks`. `--reuse-prepared` verifies the existing bundle
instead of rebuilding it, which takes about three seconds rather than minutes.

## What a prepared bundle contains

    instrumented.apk          what runs on the device
    original.apk              what it was built from
    universe.json             the frozen method universe and its hash
    units.json                the unit identifiers
    prepared.json             manifest: package, unit count, launchers, hashes
    static-unit-proof.json    proof the universe matches the APK
    instrumenter-config.json  the checksum-pinned toolchain
    instrumenter-receipt.json what the instrumenter reported
    structural-audit.json     smali backend only: class and method preservation

`universe.json` is the denominator. It is frozen before the run and every run using
the bundle keeps it unchanged, which is what makes two runs comparable. See
[PREPARED-BUNDLE.md](PREPARED-BUNDLE.md) for the contract and
[SMALI-FALLBACK.md](SMALI-FALLBACK.md) for the fallback backend.

## Checklist

- [ ] APK in `dataset/apks/<Name>.apk`, package and launcher resolve
- [ ] prepared by at least one backend
- [ ] unit count compared against the other backend, or sanity-checked against size
- [ ] smoke test passes: alive, resumed, probes firing, no verification failure
- [ ] if it fails, the untouched APK was tested to establish whose fault it is
- [ ] any neutralised method pinned in the config, and recorded in the write-up
- [ ] bundle hard-linked into the campaign work directory
