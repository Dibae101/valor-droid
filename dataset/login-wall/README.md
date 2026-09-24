# Login-wall classification — 50-app dataset

Date: 2026-09-23. Method: shallow git tree fetch (`--depth 1 --filter=blob:none`) of each
app's upstream repo at the dataset's pinned ref, path-grep for
login|signin|oauth|auth|account|passcode|biometric|credential, confirmation via
`git show` on hit files + README, F-Droid API as backup. The 15 tested apps use run
evidence as ground truth. One app (Chess) is unverified: upstream repo 404s.

## Blocker classes

- `none` — no login/account/server gate; fully usable offline or anonymously.
- `local-gate` — PIN / app password / biometric / onboarding lock enforced on-device.
  Smali bypass (force auth checks true at repackage time) is VIABLE here.
- `server-account` — requires a real account on a remote server, OAuth IdP, or a
  server URL + credentials before the app is useful. Smali bypass NOT viable
  (no session = 401s / empty screens).
- `optional-account` — fully usable without login; login only unlocks extras.
- `unknown` — could not verify; not guessed.

## Counts (n=50)

| blocker_class    | count | tested | untested |
|------------------|-------|--------|----------|
| none             | 25    | 8      | 17       |
| optional-account | 12    | 1      | 11       |
| server-account   | 12    | 6      | 6        |
| local-gate       | 0     | 0      | 0        |
| unknown          | 1     | 0      | 1        |

## Headline finding

**Zero `local-gate` apps in the dataset.** The smali-bypass technique has no target
among these 50 apps — every forced gate is server-side. Do not build the bypass
pass for this dataset; keep it in the toolbox for future datasets.

The 12 `server-account` apps split into two solvable groups (see
targets-credentials.md): dockerized test servers with seeded creds (Nextcloud,
ownCloud, Jellyfin, Wallabag, Kore, OwnTracks) and throwaway real accounts
(Fedilab, Commons, Vespucci, StreetComplete, WordPress). FrostDebug/MaterialFBook
need real facebook.com sessions — infeasible, skip.

## Files

- `login-wall.tsv` — all 50 apps: app, slug, package, tested, blocker_class,
  blocker_detail, evidence, bypass_candidate, best_probe_pct.
- `targets-bypass.md` — smali-bypass shortlist (empty for this dataset).
- `targets-credentials.md` — server-account apps grouped by what they need.
- `partials/` — per-batch raw TSVs from the 4 classification workers.
