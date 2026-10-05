# WORLD MUSIC OS

**Repositorio del proyecto.** Incluye código, pruebas, documentación, portadas y definiciones de playlists. Las credenciales, sesiones OAuth, base de datos, registros de publicación y observaciones de proveedores se mantienen exclusivamente en la instalación local. Una copia nueva del repositorio necesita su propia configuración; no hereda las sesiones ni los permisos de uso de datos. Consulta [la instalación desde GitHub](docs/GITHUB.md) y [la colección editorial en diez idiomas](docs/editorial-localized-playlists.md).

WORLD MUSIC OS is a local-first music discovery and market intelligence system. Spotify is an authorized catalog-resolution and playlist-publication destination; it is not the intelligence engine or a source of audience metrics. The app uses Spotify OAuth Authorization Code with PKCE, never asks for a Spotify password, and never starts playback or creates artificial activity.

The existing Spotify publication layer maintains ten public playlists and their local playlist definitions. Its tracks are catalog-resolved locally and no track IDs are invented. Ten original square cover images are included under `covers/`.

Priority labels (`anchor`, `growth`, `discovery`) record the search bucket used for curation. Playlist-level neuromental profiles and pacing arcs are included. Individual track ratings remain unscored until a human curator listens and adds explicit 1–5 annotations; the app does not infer mood or clinical effects from Spotify catalog data.

## What it does

- Creates playlists once and records Spotify IDs locally so later runs do not rely on names.
- Resolves Spotify track URIs, track URLs, or artist plus title against Spotify.
- Refuses low-confidence and version-mismatched search results, saving candidates to `data/unresolved_tracks.json` for review.
- Detects duplicate entries in the playlist JSON and duplicate Spotify track IDs.
- Supports `append` mode and `exact` mode, with exact ordering, dry runs, confirmations, backups, and restore.
- Updates playlist name, description, and public/private setting from each JSON definition.
- Uploads optional JPEG covers after validating and preparing an upload copy.
- Handles Spotify rate limits and temporary server errors with bounded retries.
- Provides a guarded, separately curated external-chart rotation command with backups; it does not generate candidates from the Last.fm dashboard.
- Adds a non-clinical listening-context layer for energy, mood tone, focus, social energy, discovery, and playlist pacing.
- Adds FastAPI, SQLAlchemy, Alembic, SQLite local storage, PostgreSQL Docker Compose, an ISO market tree, weighted genre links, provenance, and ISRC-first identity resolution.
- Imports only datasets whose provenance declares owned, consented, or licensed rights. Imports are content-hashed and repeatable.

## Music metrics providers

The dashboard now shows Soundcharts, Chartmetric, and Last.fm as distinct sources, their setup blockers, declared signals, local observation counts, and freshness. The metrics table can show Last.fm charts or source-attributed Chartmetric/Soundcharts snapshots after those are captured. No account password is stored, and no web dashboard is scraped.

The dashboard's **Orden de señales** queue (`/api/intelligence/signal-order`) applies provider-state and recorded-rights gates before review. It orders only within the same provider, metric, platform, market, unit, and captured sync; lower chart rank and higher reported growth/counts use their native direction. Unknown metrics retain a stable order, cross-provider metrics are not blended into one score, and missing provider confidence/coverage remain unknown. The queue is review-only: Spotify publication stays disabled until a track resolves to the Spotify catalog, playlist fit is established, and a human approves the edit.

### Public playlist engine

The **Playlists públicas** panel implements the WORLD MUSIC OS editorial layer for the ten existing registered lists. Each JSON definition now has a validated `world_music` DNA profile: playlist type (NOW, RISING, BREAKOUT, DISCOVERY, or NEXT), genre/subgenre and market vectors, energy/mood/tempo/language targets, artist-stage target mix, a freshness distribution, mainstream/discovery balance, a 24-hour review cadence, artist repetition cap, position-jump guard, and minimum/maximum turnover. The configured types apply the prompt's stage-mix recipes and turnover ranges; missing artist-stage data never forces a quota.

`GET /api/public/playlists` returns the existing lists, Spotify registry link, configured DNA, stage targets, lifecycle evidence state, and health-score availability. `POST /api/public/playlists/generate` creates local review-only drafts for all lists; `POST /api/public/playlists/{playlist_slug}/refresh` builds one list's draft. Public ranker v2.0 uses playlist-type weights for fit, relevant source-backed trend/breakout/elite/next/authenticity scores, profile-weighted freshness, and relative market fit. Each measured component is shrunk toward neutral 50 according to its confidence; source values remain visible beside calibrated contributions. It does not blend providers inside one candidate. Every accepted score must have an exact source/market snapshot with the configured rights basis, a connected provider, a score no older than 72 hours, a ready status, confidence at least 0.35, and evidence coverage at least 0.60. Candidates also need a canonical provider identifier, exact Spotify track ID, known primary artist, catalog genre fit, and a configured playlist market. Draft composition allocates turnover slots across configured markets with largest-remainder quotas, then fills unavailable market slots from the remaining score-ranked pool. Artist limits and duplicate-track checks still apply; tracks already present are excluded.

Run the same review-only engine from a daily local task or terminal with `python -m app world playlist-drafts`; add `--playlist-slug reggaeton-worldwide` to scope one list. This command only reports a draft and blockers. It does not edit playlist JSON, call Spotify, or publish.

### Twenty-playlist launch portfolio

The **Expansión planificada** panel contains 20 editorial briefs with distinct audiences, objectives, markets, genre DNA, cadence, and source-specific measurement plans. `python -m app world playlist-launch` evaluates all 20 and saves the complete review packet at `data/playlist_launch_plan.json`. Use `--slug NAME` to evaluate one. The initial seed uses the same source-rights, score-freshness, identity, genre-fit, market, and artist-cap checks as the public ranker; it is not limited by the later rotation budget. A concept needs at least 30 eligible tracks, 20 artists, mean score confidence 0.55, no more than 60% overlap with an existing playlist, and a verified release date within 30 days for NEW_MUSIC.

`python -m app world playlist-launch --apply` creates only concepts that pass every gate. Local-scene concepts additionally require at least 60% verified local artists; multiterritory concepts need at least four represented target markets. R&B Latino Chill, Techno Alemania NOW, and Global Dancefloor Pulse require a human sequence review: save `data/sequence_reviews/<slug>.json` containing a nonempty `reviewer` and an `approved_spotify_uris` array identical to the proposed sequence in the launch plan. The publisher first checks each Spotify catalog identity, seeds a private playlist, verifies the exact sequence, records the local configuration, and then publishes it. A failed seed remains private and can be resumed from its registry record. A launch is never substituted with an empty public playlist. The campaign briefs follow publicly described objective, audience, phased execution, and measurement practices; this project has no access to The Orchard's private systems or metrics.

### Organic campaign plans

The dashboard's **Campañas por playlist** section creates one seven-day organic discovery plan for each public playlist. Each plan follows the prompt's sequence: detection, validation, composition, promotion/demotion, propagation, destination, and feedback. Its market focus comes from that playlist's configured editorial market vector and is labeled as a target, not observed audience or propagation.

Each plan includes four manual publishing/review moments, suggested copy built from the playlist description and registered Spotify link, evidence gates for track promotion, and empty metrics for organic reach, link clicks, and playlist follows. Metrics remain blank until an authorized analytics source or operator-supplied measurement is available. The plans do not post to social networks, change Spotify playlists, automate follows or plays, or promise ranking or audience growth. `GET /api/campaigns` returns these plans with execution and Spotify writes disabled.

The public score reports component coverage and is explicitly marked partial when source inputs are missing. Artist-stage labels, full playlist health and authority scores, lifecycle signals, historical position scoring, and energy/tempo/mood sequence transitions remain `DATA_NOT_AVAILABLE` until the required licensed/owned data and track-level history exist. The draft only proposes additions; it never removes, reorders, creates, or edits a Spotify playlist. `spotify_writes_enabled` remains false, and this endpoint is separate from OAuth publication authorization. Last.fm observations alone do not create the licensed trend/breakout/NEXT scores needed by the current public ranker, so the engine can still return zero candidates even while Last.fm is connected.

These are different services with different access and rights:

- **Soundcharts:** the project has an official server-side API adapter using OAuth client credentials, API-quota verification, and documented song-chart endpoints. A Soundcharts website login does not provide API credentials. Its free production API trial is limited to 1,000 calls; recurring API access is paid. Written permission received on 2026-10-05 allows local snapshots, derived trend scores, and curation of the user's own public playlists, but raw data must not be republished. Any displayed derived metric must show **“Powered by Soundcharts”** beside it. The dashboard attribution is implemented; API authentication still needs verification. The free sandbox has restricted test data and is not used as current trends.
- **Chartmetric:** the project registers its API adapter, bounded ingestion path, and source-backed score calculation. The free dashboard account is not an API token; the API access page offers a 7-day trial, after which ongoing API access is paid. The integration blocks requests outside the manually configured trial window and separately requires written permission for storing observations, deriving scores, and playlist curation.
- **Last.fm:** the project captures its global and country charts through the documented API. Its free API is narrower and is gated by its non-commercial scope, attribution/link requirements, and the extra condition for use outside the EEA. Its signals are Last.fm ranks/playcounts, not Spotify streams, playlist placements, or cross-platform audience.

The local Last.fm API integration is connected for the confirmed personal, non-commercial scope and provides attributed chart rank and playcount observations. Soundcharts and Chartmetric account sessions do not by themselves unlock licensed music-data reuse or API access. Spotify remains a separate publishing destination. The dashboard shows stored provider observations when they exist; observations alone do not modify Spotify playlists.

Never set a rights flag just to remove a blocker. Soundcharts/Chartmetric flags mean written permission is already in hand; Last.fm flags mean the operator has reviewed and confirmed the stated terms. The app cannot validate a vendor contract. The local server stays bound to localhost; public display of provider data requires the applicable written authorization.

### Run locally

~~~powershell
python -m pip install -r requirements.txt
python -m app world db-upgrade
python -m app world status
python -m app world serve
~~~

Open the visual dashboard at <http://127.0.0.1:8000/dashboard> and API documentation at <http://127.0.0.1:8000/docs>. The server binds to loopback by default. Do not expose it publicly before adding authentication and deployment security.

Useful read-only routes:

- `GET /api/health`
- `GET /api/world/overview`
- `GET /api/providers/status`
- `GET /api/intelligence/overview`
- `GET /api/public/playlists`
- `GET /api/campaigns`
- `POST /api/public/playlists/generate`
- `POST /api/public/playlists/{playlist_slug}/refresh`
- `GET /api/charts/tracks?market_code=GLOBAL`
- `GET /api/markets/{country_code}/tracks`
- `GET /api/intelligence/propagation`
- `GET /api/markets`
- `GET /api/markets/{country_code}`
- `GET /api/markets/{country_code}/genres`

Import an owned, consented, or licensed JSON catalog batch with `python -m app world ingest path/to/catalog.json`. Track records require an ISRC or a source identifier; artist and release records require provider identifiers. Parent markets and parent genres must appear before their children. A duplicate import is detected by content hash; conflicting identities are rejected rather than silently merged. `rights_basis` records the operator's declaration; the app cannot validate a provider contract, so only import data you are authorized to use.

### PostgreSQL container

Copy `.env.docker.example` to `.env.docker`, replace the password with a unique URL-safe value, then run `docker compose --env-file .env.docker up --build`. The API and database ports bind to `127.0.0.1` only. The compose file migrates the database before starting the API.

### Connect Last.fm for free chart metrics

1. Create a free API key at [last.fm/api/account/create](https://www.last.fm/api/account/create).
2. Review [Last.fm's API terms](https://www.last.fm/api/tos). In particular, confirm that your intended use is non-commercial and that the condition for using data outside the EEA is satisfied. If unclear, ask Last.fm at `partners@last.fm`; do not enable collection until scope is confirmed.
3. Add the key to the local ignored `.env` as `LASTFM_API_KEY=...`. After confirming the terms, set `LASTFM_NONCOMMERCIAL_USE=true` and `LASTFM_NON_EEA_PERMISSION_CONFIRMED=true`. Those values are local acknowledgements, not proof that Last.fm approved the use.
4. Verify the key and capture up to 100 tracks from GLOBAL and the selected countries:

~~~powershell
python -m app world provider-check
python -m app world sync --markets CL,MX,AR,CO,ES,US,BR --limit 100 --max-requests 8
python -m app world scores
~~~

`world provider-check` reads one global top-tracks endpoint and saves no chart rows. `world sync` is blocked until both scope flags are true; it makes at most one capture per market per Santiago calendar day, up to one request per uncaptured selected market (including GLOBAL). The API does not report a current rate limit, so the app does not invent one. Snapshots older than 30 days are deleted. The app does not scrape charts or fabricate metrics.

The dashboard shows captured charts, raw Last.fm ranks/playcounts, capture freshness, and rank movement after a prior snapshot exists. The API does not expose cross-platform or playlist-placement signals, so composite industry scores and predicted Spotify placement remain unavailable. Metric sync never changes Spotify playlists.

### Soundcharts free API trial

Soundcharts offers a finite free API trial of 1,000 requests; recurring production access is paid. Create API credentials through the [Soundcharts developer console](https://developers.soundcharts.com/api/authorization), then store `SOUNDCHARTS_CLIENT_ID` and `SOUNDCHARTS_CLIENT_SECRET` in the ignored local `.env`. Do not paste the secret into chat. Only after enabling that free trial, set `SOUNDCHARTS_ACCESS_MODE=free_1000_request_trial`. The application enforces a local lifetime cap across syncs and the external bridge. Its open sandbox is sample data and is not treated as today's trend signal.

Written permission for saved history and playlist research remains separate from API trial access. Set `SOUNDCHARTS_RIGHTS_CONFIRMED=true` only if a written grant covers those uses. The `world soundcharts-check` command calls the quota/usage endpoint; to see exact chart IDs and then capture only explicitly selected charts:

~~~powershell
python -m app world soundcharts-charts --platform spotify --country-code CL
python -m app world soundcharts-sync --charts exact-slug-1,exact-slug-2 --max-requests 2
~~~

Each selected chart uses a request from the finite free quota. The local cap cannot account for calls made outside this installation, so also check the quota reported by Soundcharts. A `--dry-run` bridge still reads source APIs and consumes requests.

### Chartmetric free API trial

The free dashboard account does not provide API access. Chartmetric offers API access through a 7-day trial; it is not a permanent free API. Start it manually, then configure `CHARTMETRIC_REFRESH_TOKEN`, `CHARTMETRIC_ACCESS_MODE=free_7_day_trial`, and the actual UTC timestamps `CHARTMETRIC_FREE_TRIAL_STARTED_AT` / `CHARTMETRIC_FREE_TRIAL_ENDS_AT`. The app rejects missing, expired, future, or longer-than-seven-day windows and makes no post-trial requests.

Obtain written authorization for stored observations, derived scores, and use to curate public playlists before setting `CHARTMETRIC_RIGHTS_CONFIRMED=true`. API trial access does not itself confirm those data rights. Then run:

~~~powershell
python -m app world chartmetric-check
python -m app world chartmetric-sync --markets CL,MX,AR,CO,ES,US,BR --max-requests 20
~~~

The sync has a hard per-run request limit and records provider freshness/quota status. It does not edit Spotify playlists. The dashboard's source selector can show stored Chartmetric observations; score rows remain source-specific and low confidence when the provider does not report per-observation confidence.

## Organic discovery

The ten playlists are public, with distinct descriptions that name each genre and listening context and invite interested listeners to follow. Each also has original artwork with a consistent editorial direction and its own genre palette. Spotify says public playlists appear on the owner's profile and can appear in search results; this improves availability but does not guarantee ranking or new followers. [Spotify playlist visibility](https://developer.spotify.com/documentation/web-api/concepts/playlists)

To upload the included covers, first authorize the optional Spotify `ugc-image-upload` scope. The upload command checks playlist ownership and sends only the cover image; it does not read, reorder, add, or remove tracks:

~~~powershell
python -m app auth --with-covers
python -m app covers upload --all --dry-run
python -m app covers upload --all
~~~

For genuine growth, keep each playlist focused, review new additions by listening, and share its Spotify link in places where that style of music is already discussed. Pin a playlist through Spotify for Artists only if you manage the matching artist profile. Do not buy followers or streams, run follow/play bots, or promise placement: Spotify prohibits artificial engagement and warns against services that guarantee followers or streams. [Spotify Developer Policy](https://developer.spotify.com/policy) · [Spotify for Artists: Artificial Streaming](https://artists.spotify.com/en/artificial-streaming)

The app does not measure or automate follower growth. Its smart curation comes from the playlist-level listening profiles plus ratings and sequence roles entered by a person who has listened to each track. Use `python -m app neuromental report` to see rating coverage, `python -m app neuromental rate <slug>` to review tracks, and `python -m app neuromental sequence <slug>` to preview an order. Unrated songs keep their current positions.

## Daily trend rotation

The earlier chart rotator is a guarded editorial workflow based on candidate rows supplied for human review. A chart rank is not a stream count, velocity, acceleration, or cross-market growth metric. It is separate from the Last.fm API capture and does not automatically consume Last.fm metrics.

Each candidate must be in a compatible chart position (top 50) and pass its playlist's genre-fit tags. The app resolves the external artist/title against Spotify locally using its deterministic version-aware resolver; Spotify search results and catalog metadata are not sent to an AI model or printed by the trend command. It preserves anchors, human-rated or locked songs, holds newly added tracks for seven days, and requires the saved Spotify order to match the local file before rotation. A high-ranking candidate replaces one eligible growth slot; a `Next ...` list replaces one discovery slot. The newcomer is placed at the front of that same priority block. Every exact sync creates the normal local backup before changing Spotify. Failed or ambiguous catalog matches are skipped.

The daily run is idempotent per playlist and date, caps changes at ten network-wide, and records chart sources, ranks, the additions, and the removed Spotify URI in `data/trend_rotation_history.json`. It never changes followers, playback, or engagement. It cannot guarantee Spotify recommendations or follower growth.

Put the external chart review for the current Santiago date in `data/daily_trends.json`. The file shape is:

~~~json
{
  "as_of": "YYYY-MM-DD",
  "candidates": [
    {
      "playlist": "reggaeton-worldwide",
      "artist": "ARTIST FROM THE EXTERNAL CHART",
      "title": "TRACK FROM THE EXTERNAL CHART",
      "fit_tags": ["latin", "reggaeton"],
      "signals": [
        {
          "source": "Shazam",
          "chart": "Global Latin",
          "kind": "genre",
          "rank": 1,
          "url": "https://www.shazam.com/charts/genre/world/latin",
          "observed_on": "YYYY-MM-DD"
        }
      ]
    }
  ]
}
~~~

Preview first; add `--apply` to modify Spotify:

~~~powershell
python -m app trends rotate
python -m app trends rotate --apply
~~~

The daily Codex task runs at 09:00 in `America/Santiago` and stays provider-gated. It must not scrape charts or modify Spotify playlists while API access, plan limits, and the applicable rights scopes are missing. The Soundcharts usage check does not collect chart rows; the free trial is finite. Last.fm alone does not supply a Spotify-specific trend, while Chartmetric scores require its API and written reuse permission. No playlist candidates are generated from a disconnected source. The legacy `trends rotate` command remains a separate, explicitly curated workflow and previews by default.

## Neuromental listening-context curation

Each starter playlist has an editorial profile describing its intended listening context and a suggested sequence arc. These are creative curation targets, not neuroscience measurements, treatment advice, or promises about how a listener will feel.

Optional per-track ratings can be added by a human curator in the playlist JSON. Ratings use a 1–5 editorial scale: energy (quiet to high-energy), valence (reflective to upbeat), focus (less to more focus-friendly), and social energy (introspective to communal). Intent tags and a sequence role can explain why a track fits. Spotify audio features are not inferred, and a missing rating stays unscored. The report never adds, removes, or reorders tracks.

Catalog results are handled locally by the application and are not sent to an AI model. A track entry may set `spotify_catalog_verified: true` only when its URI came directly from a successful Spotify catalog search; this avoids repeating one catalog lookup per track during sync. Ordinary user-entered URIs are still checked against Spotify before use.

Inspect the ten profiles and annotation coverage with:

~~~powershell
python -m app neuromental report
python -m app neuromental report reggaeton-worldwide
~~~

To rate tracks after listening, run the guided local curator and open each track link in Spotify:

~~~powershell
python -m app neuromental rate reggaeton-worldwide
python -m app neuromental rate reggaeton-worldwide --start 41
~~~

Enter `energy,valence,focus,social_energy;sequence_role;intent|intent;rationale`. Each dimension uses 1–5, and the role should match one in that playlist's sequence arc. Use `-` to clear a field and `=` to keep its current value. Press Enter to skip or `q` to stop. The command saves each human annotation locally as you go.

Preview a sequence based only on those explicit role annotations:

~~~powershell
python -m app neuromental sequence reggaeton-worldwide
~~~

Unrated tracks stay in their current positions. Add `--apply` to show the exact-sync diff and confirm a Spotify reorder; the normal exact-sync backup and confirmation safeguards still apply.

Example playlist profile:

~~~json
"neuromental": {
  "intents": ["activation", "social-connection", "uplift"],
  "energy_target": 4,
  "valence_target": 4,
  "focus_target": 1,
  "social_energy_target": 5,
  "sequence_arc": ["warm-up", "build", "peak", "cool-down"],
  "curation_note": "Movement-oriented, shared energy with clear pacing."
}
~~~

Optional track annotations:

~~~json
{
  "artist": "ARTISTA",
  "title": "CANCIÓN",
  "priority": "growth",
  "neuromental": {
    "energy": 4,
    "valence": 4,
    "focus": 2,
    "social_energy": 5,
    "intent_tags": ["activation", "uplift"],
    "sequence_role": "build",
    "rationale": "Curator-provided note about pacing and listening context."
  }
}
~~~

The deterministic fit score compares only supplied ratings and tags against the playlist profile. It is an editorial aid; it does not read Spotify audio features or infer anything about a person's mental state. New songs still need a real Spotify URI or artist and title, and the resolver continues to reject uncertain matches.

## Before you begin

Spotify's current Development Mode requires the app owner to have active Premium, and its API access is subject to the current supported-endpoint and allowlist rules. You previously said this Spotify account is Free, so treat Spotify publishing as unavailable until Spotify confirms the app owner's eligibility; the local World Music OS API and database work without Spotify. [Spotify quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) · [Spotify's 2026 Development Mode update](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)

Use Python 3.12 or newer. You will need internet access while installing packages and while using Spotify commands.

## Set up Spotify, step by step

### 1. Open Spotify for Developers

Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard/) and sign in to the Spotify account that will own the playlists.

### 2. Create an application

Create an app for personal playlist management. The app is a local Development Mode app; you do not need to publish it or request Extended Quota Mode for your own account.

### 3. Copy the Client ID

Open the app's settings and copy its **Client ID**. Do not copy or use the Client Secret. This project uses PKCE and has no Client Secret setting.

### 4. Add the local redirect URI

In the app's redirect URI list, add this exact value and save the app:

~~~text
http://127.0.0.1:8888/callback
~~~

Spotify currently permits HTTP for an explicit loopback IP such as `127.0.0.1`; `localhost` is not accepted. This project binds only to `127.0.0.1`, and the redirect URI in the authorization request must match the saved value exactly. [Spotify redirect URI requirements](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)

### 5. Put the Client ID in `.env`

Open a terminal in this project folder and copy the example file:

~~~powershell
Copy-Item .env.example .env
notepad .env
~~~

Paste the Client ID after `SPOTIFY_CLIENT_ID=`. The file should look like this:

~~~dotenv
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
~~~

Save and close Notepad. `.env` is excluded from Git. Never share it publicly.

### 6. Install Python and project dependencies

Install Python 3.12+ from [python.org](https://www.python.org/downloads/) if you do not already have it. Then, from the project folder:

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
~~~

If PowerShell blocks environment activation, open Command Prompt and use:

~~~bat
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
~~~

On macOS or Linux, activate with `source .venv/bin/activate`.

### 7. Authorize Spotify

Run:

~~~powershell
python -m app auth
~~~

Your browser opens Spotify's own authorization page. Sign in there and approve the requested playlist and private-profile permissions. The local callback checks OAuth state and exchanges the authorization code using PKCE. The terminal then verifies the account with Spotify's `/me` endpoint and shows its display name. The access token is never printed.

The session is saved in the operating-system keyring when available. If the keyring cannot be used, it is saved in a local ignored file under `data` with restricted permissions. The default authorization does not ask for the image-upload permission. If you later add cover images, authorize the extra scope with `python -m app auth --with-covers`.

### 8. Check the account and validate the initial playlist files

~~~powershell
python -m app account
python -m app validate
python -m app playlists list
~~~

All ten playlist definitions are public by default. Each can be made private by changing its own `public` property to `false`.

### 9. Preview synchronization safely

`dry-run` reads the current playlist and resolves configured songs, then shows the proposed changes without sending playlist modifications to Spotify:

~~~powershell
python -m app sync reggaeton-worldwide --mode exact --dry-run
~~~

With the starter files, each song list is empty, so exact mode will show an empty target. Do not run exact mode against a populated playlist until you have reviewed the diff and the JSON entries.

### 10. Create the ten playlists

~~~powershell
python -m app playlists create --all
~~~

This creates only definitions that are not already registered and prints each Spotify URL. Playlist IDs and ownership are stored in `data/playlist_registry.json`. If an unregistered same-name playlist already exists, creation stops and tells you to use the explicit recovery flow rather than guessing which playlist is yours.

### 11. Add songs by editing a playlist JSON

Open a file in `playlists`, for example `playlists/reggaeton-worldwide.json`. A Spotify URI is the clearest choice:

~~~json
{
  "spotify_uri": "spotify:track:PASTE_A_REAL_TRACK_ID_HERE",
  "priority": "anchor",
  "locked": false
}
~~~

You can instead use a Spotify track URL:

~~~json
{
  "spotify_url": "https://open.spotify.com/track/PASTE_A_REAL_TRACK_ID_HERE",
  "priority": "growth"
}
~~~

Or ask Spotify to search for the artist and song:

~~~json
{
  "artist": "Artist name",
  "title": "Song title",
  "priority": "discovery",
  "locked": false
}
~~~

Add each object inside the file's `tracks` array, with commas between objects. Allowed priorities are `anchor`, `growth`, and `discovery`. The optional `isrc` can narrow a search. The optional `locked: true` marks a track that future exact rotations should retain. Rotation is not automated in this version.

### 12. Resolve songs, then synchronize

To inspect Spotify matches without changing a playlist:

~~~powershell
python -m app tracks resolve --all
~~~

Review unresolved candidate names and Spotify links in the terminal and in `data/unresolved_tracks.json`. Choose a correct candidate yourself and paste its Spotify URI into the playlist JSON. Uncertain matches are not added automatically.

Append mode only adds songs missing from Spotify and keeps existing songs:

~~~powershell
python -m app sync --all --mode append --dry-run
python -m app sync --all --mode append
~~~

Exact mode makes Spotify's ordered track list match the JSON. It shows additions, removals, and moves first, creates a local backup before a change, and asks for confirmation when it would remove or reorder existing songs. Use `--yes` only after reviewing a dry run:

~~~powershell
python -m app sync --all --mode exact --dry-run
python -m app sync --all --mode exact
python -m app sync --all --mode exact --yes
~~~

Exact mode appends directly when the only change is adding songs at the end. For other changes, it replaces the playlist with up to 100 items in the first request, then appends additional 100-item batches to preserve order. It avoids track writes when the ordered contents are already correct. If `locked_uris` are present in the registry, exact mode keeps them unless you explicitly pass `--remove-locked`.

### 13. Recover or restore a playlist

If the registry is missing an ID, search only the connected account's owned playlists and confirm an exact-name match:

~~~powershell
python -m app playlists recover reggaeton-worldwide
~~~

Backups are saved under `data/backups/YYYY-MM-DD/` before exact changes and restores. Preview a backup restore first, then run the same command without `--dry-run` and confirm at the prompt:

~~~powershell
python -m app restore .\data\backups\2026-09-30\reggaeton-worldwide-120000.json --dry-run
python -m app restore .\data\backups\2026-09-30\reggaeton-worldwide-120000.json
~~~

The restore command checks that the backup is inside `data/backups` and that its Spotify playlist ID matches the current registry. It creates a pre-restore backup before making changes.

## Useful commands

~~~text
python -m app auth [--with-covers]
python -m app account
python -m app status
python -m app validate
python -m app playlists list
python -m app playlists create --all
python -m app playlists create reggaeton-worldwide
python -m app playlists recover reggaeton-worldwide
python -m app tracks resolve --all
python -m app covers upload --all --dry-run
python -m app covers upload --all
python -m app sync reggaeton-worldwide --mode append --dry-run
python -m app sync --all --mode exact --dry-run
python -m app sync --all --mode exact --yes
python -m app restore PATH_TO_BACKUP.json --dry-run
~~~

## Spotify API details verified for this project

This project uses the current playlist-item routes. Spotify's February 2026 migration removed the old playlist `/tracks` routes; this project uses `/items` throughout.

| Operation | Current endpoint | Success status | Current request limit used here |
| --- | --- | --- | --- |
| Current profile | `GET /me` | 200 | One profile request |
| Create playlist | `POST /me/playlists` | 201 | One playlist per request |
| Read playlist items | `GET /playlists/{id}/items` | 200 | Up to 50 items per page |
| Add items | `POST /playlists/{id}/items` | 201 | Up to 100 URIs per request |
| Replace/reorder items | `PUT /playlists/{id}/items` | 200 | Up to 100 URIs per replace request |
| Remove items | `DELETE /playlists/{id}/items` | 200 | Up to 100 item objects per request |
| Update playlist details | `PUT /playlists/{id}` | 200 | One metadata update per request |
| Search | `GET /search` | 200 | Up to 10 results per type per page |
| Custom cover | `PUT /playlists/{id}/images` | 202 | Base64 JPEG payload up to 256 KB |

The playlist mutation endpoints document `401`, `403`, and `429` error responses; Spotify can also return temporary `5xx` responses, which the client retries a bounded number of times. OAuth token exchange and refresh use the token endpoint's `200` response.

The current Search maximum is 10 results, not the older 50. Search uses pages of 10 if a first page is not strong enough. Playlist item reads are paginated at 50; mutations are batched at 100. `429` responses use Spotify's `Retry-After` value; temporary network and `5xx` failures use bounded exponential backoff with jitter. The retry count is finite. [Spotify February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide), [Add Items](https://developer.spotify.com/documentation/web-api/reference/add-items-to-playlist), [Get Playlist Items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items), [Update Playlist Items](https://developer.spotify.com/documentation/web-api/reference/reorder-or-replace-playlists-items), [Remove Playlist Items](https://developer.spotify.com/documentation/web-api/reference/remove-items-playlist), [Rate limits](https://developer.spotify.com/documentation/web-api/concepts/rate-limits), [Add Custom Playlist Cover Image](https://developer.spotify.com/documentation/web-api/reference/upload-custom-playlist-cover).

Spotify's current Create Playlist API reference does not publish a description-character limit. The local model uses a conservative 300-character ceiling for editing safety; all ten supplied descriptions are shorter than that. [Create Playlist](https://developer.spotify.com/documentation/web-api/reference/create-playlist)

For covers, Spotify documents JPEG content encoded as base64 with a maximum payload of 256 KB. The project requires square JPEGs at least 300 × 300 pixels and caps its prepared copy at 4096 × 4096; those dimension checks are local safeguards, not Spotify-published dimension limits. A compatible compressed copy is created in memory and the original file is not modified. [Add Custom Playlist Cover Image](https://developer.spotify.com/documentation/web-api/reference/upload-custom-playlist-cover)

The implemented scopes are `playlist-modify-public`, `playlist-modify-private`, `playlist-read-private`, and `user-read-private`. `ugc-image-upload` is requested only with `auth --with-covers`. PKCE uses a generated verifier/challenge and OAuth state; no Client Secret is stored or sent. [PKCE flow](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow), [Scopes](https://developer.spotify.com/documentation/web-api/concepts/scopes)

## Local files and safety

- `.env`, OAuth token fallback files, Python caches, logs, and generated backups are excluded from Git.
- Tokens are kept in the operating-system keyring when available; the fallback is a local file with restrictive permissions where the platform supports them.
- HTTP logs include timestamp, operation, endpoint, status, track count, and result. Authorization headers, access and refresh tokens, Client Secrets, authorization codes, and PKCE verifiers are not logged.
- No playback, streaming, follower purchasing, artificial users, or artificial engagement is implemented.
- Search results and playlist metadata are handled only to resolve and manage the user's configured playlists. The project does not download audio or use Spotify content to train a model.

## Tests

The suite uses mock transports and local temporary files. It does not contact Spotify or modify any real playlist:

~~~powershell
python -m pytest
~~~

The project can scale to more JSON playlist definitions without changing its architecture. Add one new file per playlist with a unique slug.
