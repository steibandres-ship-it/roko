# External provider bridge

The bridge runs as a separate Python process. It reads official provider APIs, converts responses to source-attributed observations, and sends a bounded JSON batch to the local WORLD MUSIC OS API:

```text
Soundcharts / Chartmetric / Last.fm API → tools/provider_bridge.py → POST /api/providers/ingest → local database and dashboard
```

The bridge does not access Spotify for audience metrics and does not publish or edit playlists. It stores provider IDs and labels separately; it does not claim that a provider ID is a Spotify track ID. The dashboard reports observed values and source timestamps. Trend scores stay unavailable until the scoring requirements and usable data history are present.

The dashboard's **Revisión paralela: Soundcharts + Chartmetric** panel sits between provider metrics and playlist work. It displays authorized observations side by side by platform and market. A normalized title/artist match is only a manual-review candidate unless a shared canonical ID exists. Provider metrics, units, confidence, coverage, timestamps, and links stay separate; the panel calculates no cross-provider delta, consensus, or score.

## Local setup

1. Copy the new bridge settings from `.env.example` into the ignored local `.env`. Keep the API URL on loopback, normally `http://127.0.0.1:8002` for the dashboard currently open on port 8002.
2. Set `WORLD_MUSIC_INGEST_API_TOKEN` to a private random value with at least 32 characters. The same `.env` is read by the server and bridge. Do not commit it or paste it into chat.
3. Use only the free access mode for each source. Soundcharts is gated to its finite 1,000-request API trial; Chartmetric is gated to the 7-day API trial advertised as no-card, not paid dashboard plans or the unbounded usage-based API; Last.fm has a free API with a non-commercial scope. A dashboard account alone is not API access.
4. For Soundcharts, set `SOUNDCHARTS_ACCESS_MODE=free_1000_request_trial` only after trial activation. A receiver endpoint checks its remaining local budget before the bridge makes calls, and ingestion rejects batches that would cross the local cap. Calls made outside this app are not visible to that local counter.
5. For Chartmetric, request the API refresh token from `hi@chartmetric.com`; set `CHARTMETRIC_ACCESS_MODE=free_7_day_trial` and actual timezone-aware UTC start/end timestamps only after confirming the no-card API trial is active. The application blocks calls outside that seven-day window. Do not enable metered usage-based access unless a hard $0 overage guard is available.
6. Enable a provider rights flag only after the data contract or terms permit the intended local storage, derived analysis, and playlist research. Free API access is not a data-reuse license. Last.fm additionally requires non-commercial scope and the applicable permission for use outside the EEA; its chart results retain Last.fm attribution links for up to 30 days.
7. For Soundcharts, set `SOUNDCHARTS_CHART_SLUGS` to exact chart slugs you are entitled to query. The bridge never enumerates all charts.

The API receiver binds to `127.0.0.1` by default. It accepts a bearer token, limits each JSON batch to 4 MiB, validates provider/metric/source fields, stores no raw provider response, and skips duplicate observations. Do not expose this route on a public network without adding a proper authenticated gateway and transport security.

## Commands (PowerShell)

Run these commands from the project root:

```powershell
& .\.venv\Scripts\python.exe tools\provider_bridge.py status
& .\.venv\Scripts\python.exe tools\provider_bridge.py push --providers lastfm --dry-run
& .\.venv\Scripts\python.exe tools\provider_bridge.py push --providers soundcharts,chartmetric,lastfm
```

`status` is local-only and does not contact a provider. `--dry-run` still reads the selected provider APIs and consumes their request quota, but does not send data to WORLD MUSIC OS. The normal `push` command performs the reads and sends normalized observations to the configured local API. Request budgets and result limits can be set with `WORLD_MUSIC_BRIDGE_MAX_REQUESTS` and `WORLD_MUSIC_BRIDGE_LIMIT`, or overridden with CLI flags. Markets use `LASTFM_MARKETS` and `CHARTMETRIC_MARKETS` unless `--markets` is supplied.

Start or restart the receiving app with:

```powershell
& .\.venv\Scripts\python.exe -m app world serve --host 127.0.0.1 --port 8002
```

The latest authenticated run appears in `/api/providers/status`, `/api/intelligence/overview`, and the dashboard. A successful bridge run only confirms API access and received observations. It does not certify provider coverage, confidence, playlist quality, or Spotify placement.

## Provider access notes

- [Soundcharts API docs](https://developers.soundcharts.com/api/v2/doc/reference/path/tiktok/add-user-links): the web dashboard account is distinct from API credentials; production API access is plan based.
- [Chartmetric API access](https://chartmetric.com/features/api-access): API credentials and plan access are separate from a dashboard login.
- [Last.fm API terms](https://www.last.fm/api/tos): review the current terms for attribution, cache duration, territory, and non-commercial use before enabling the integration.

The integration is free-only. The 1,000-request Soundcharts trial and 7-day Chartmetric API trial are finite; no recurring paid mode is configured. Chartmetric's metered $5 trial-credit route is intentionally disabled pending a verifiable hard cap. If a free trial is unavailable or expires, its provider remains blocked. Last.fm can be used on an ongoing free API basis only within its non-commercial, attribution, storage, territory, and approval requirements. Free access does not by itself establish rights to store or reuse data.

This project cannot create provider accounts, API keys, paid plans, or written licenses for you. If a key, plan, territory approval, or data-rights grant is missing, the bridge reports the provider as blocked and sends no metrics for that provider.
