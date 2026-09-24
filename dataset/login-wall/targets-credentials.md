# Server-account apps — what each needs

12 apps. Grouped by the cheapest way to get them past their gate.

## Docker test server with seeded admin creds (scriptable, no manual login)

| app | server | seed mechanism |
|-----|--------|----------------|
| Nextcloud | official `nextcloud` image | `NEXTCLOUD_ADMIN_USER` / `NEXTCLOUD_ADMIN_PASSWORD` env |
| ownCloud | official `owncloud/server` image | admin user/pass env on first run |
| Jellyfin Android | official `jellyfin/jellyfin` image | setup wizard — script initial admin via API or first-run web step |
| Wallabag | official `wallabag/wallabag` image | creates admin account on first run |
| Kore | Kodi headless in docker | no account needed — point Kore at the Kodi host/port |
| OwnTracks | `eclipse-mosquitto` image | username/password_file for MQTT creds |
| Ultrasonic | Navidrome docker (optional — app ships a working demo server, so this is only for deeper coverage) | admin user via `ND_ADMINUSER` |

## Throwaway real account (manual one-time signup, then snapshot or stored creds)

| app | account |
|-----|---------|
| Fedilab | any Mastodon/Pixelfed/PeerTube instance — free signup, OAuth |
| Commons | Wikimedia account — free signup |
| Vespucci | OpenStreetMap account — free signup |
| StreetComplete | OpenStreetMap account — only needed to upload changesets (app usable offline without) |
| WordPress | wordpress.com account, or self-hosted docker wordpress |
| ODK Collect | optional — default demo server is anonymous; only needed for authenticated servers |

## Infeasible — skip

- FrostDebug, MaterialFBook — require real facebook.com sessions. No test harness,
  automating login violates ToS. Treat as permanently walled; coverage ceiling stands.

## No action needed (optional-account, fully usable anonymously)

AnkiDroid, Eventyay Attendee, Infinity For Reddit, My Expenses Debug,
Open Food Facts, RedReader, Scarlet Notes FD, Twire, Wikipedia Alpha.
Login only unlocks extras; baseline runs are valid without it.
