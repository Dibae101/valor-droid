# Smali-bypass targets (local-gate apps)

**Shortlist is empty.** All 50 apps were classified; none is `local-gate`.
Every forced gate in this dataset is server-side, where forcing auth checks to
true yields 401s and empty screens instead of coverage.

## Near-misses (opt-in locks, NOT enforced — bypass gains nothing on fresh install)

- Money Manager Ex — passcode/fingerprint only if the user sets one
  (`MainActivity.java` gates on `passcode.hasPasscode()`).
- Scarlet Notes FD — app lock is opt-in; default auth is `NullAuthenticator`.
- Omni Notes Alpha — no lock found in tree at all.
- Orgzly Revived — biometric auth is opt-in device auth, not a launcher gate.

## If a future dataset contains local-gate apps, the patch approach is

1. During the existing smali instrumentation pass, rewrite boolean methods whose
   names match `isLoggedIn|isAuthenticated|checkPin|hasPasscode|isUnlocked` (and
   callers' branch conditions) to `const/4 v0, 0x1; return v0`.
2. If the launcher activity is a lock/login screen, redirect its post-check
   intent to the app's main activity (resolve target from the manifest).
3. Verify per app: a run must enter activities beyond the gate; if not, the
   patch missed the real check and the app drops out of the bypass set.
4. Apply the patched APK uniformly to VALOR-Droid AND the baseline tools, and
   disclose it — otherwise the JaCoCo comparison is invalid.
