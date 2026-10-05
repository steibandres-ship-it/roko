# Free provider access and limits

Reviewed 2026-10-03 against provider documentation and terms; updated 2026-10-05 with the user's supplied Soundcharts rights confirmation. The system is configured for free access only; there is no paid fallback.

## Soundcharts

The API offers a finite 1,000-request free trial. It is not a recurring free feed. The adapter requires `SOUNDCHARTS_ACCESS_MODE=free_1000_request_trial`; sync, catalog discovery, and the external bridge share a local 1,000-request counter. The bridge asks the receiving app for remaining local budget before making any Soundcharts calls, and ingestion rejects a batch that exceeds it. Calls made outside this installation are not included in the local counter, so the provider's reported quota still matters. The open sandbox has static sample data and is not treated as current trends.

On 2026-10-05, Soundcharts contact Aël confirmed in writing that WORLD MUSIC OS may store API responses locally as historical snapshots, calculate and store derived trend scores, and use the insights to curate the user's own public Spotify playlists. Raw Soundcharts data must not be republished. Any displayed derived metric must carry the visible attribution **“Powered by Soundcharts”** alongside it. The free trial remains limited to 1,000 calls; a paid plan is not enabled. This provider grant is now recorded locally, but API authentication still must be verified before fetching music data.

## Chartmetric

The free dashboard is not API access. Chartmetric's current API & MCP page advertises API access in a 7-day free trial with no credit card required. Its dashboard also offers paid plans and paid day passes; do not activate those for this free-only project. Chartmetric separately lists usage-based API access starting at $0.01 per credit with $5 in trial credit. That metered option is not enabled here because route costs and a provider-enforced hard stop at the free-credit balance have not been confirmed.

The developer quickstart says an API refresh token is issued to an API user after contacting `hi@chartmetric.com`. The adapter supports only the 7-day API-trial path: a refresh token, `CHARTMETRIC_ACCESS_MODE=free_7_day_trial`, and timezone-aware UTC start/end timestamps for a window of at most seven days. It blocks a missing, future, expired, or overlong window and has no paid fallback. The dashboard account currently has no active subscription and no API token configured.

The API trial itself is not assumed to authorize persistent snapshots, derived scores, or public-playlist curation. Chartmetric's general terms restrict copying and storing service content, so written permission for these specific uses remains a separate gate. Do not fetch data until both API access and the permission scope are documented.

## Last.fm

Last.fm offers an API without a provider subscription fee, with global and country charts that supply rank and Last.fm playcounts. These are Last.fm signals, not Spotify stream counts, Spotify playlist placements, social engagement, or cross-platform audience. Its API terms say use is non-commercial only; commercial or research use requires contacting `partners@last.fm` first. They require Last.fm attribution and catalog links, HTTP-aware caching, cap stored API data at 100 MB, and state an additional user-consent condition for access/use outside the EEA. Public pages using Last.fm services need written approval. The local integration retains Last.fm chart observations for at most 30 days and stays blocked until the operator's actual scope is established.

The user's playlist-growth objective may be commercial. Do not enable Last.fm's scope flags unless the actual use fits the API terms and any needed provider permission is in hand.

## What remains unavailable in free access

No one of these free paths provides the same continuous breadth as a paid Chartmetric or Soundcharts subscription. Soundcharts and Chartmetric access described here is time/request-limited, while Last.fm is narrower and terms-limited. The app does not invent missing Spotify, social, audience, coverage, or confidence metrics, and it does not promise playlist placement. Provider ingestion stays separate from Spotify authorization and publication.

## Official sources

- [Soundcharts API free request offer and access](https://developers.soundcharts.com/api/v2/doc/reference/path/festival/summary)
- [Soundcharts sandbox dataset](https://developers.soundcharts.com/documentation/sandbox-data)
- [Chartmetric API access and trial](https://chartmetric.com/features/api-access)
- [Chartmetric API pricing](https://chartmetric.com/pricing)
- [Chartmetric API authentication and refresh tokens](https://apidocs.chartmetric.com/guides/authentication)
- [Last.fm API terms](https://www.last.fm/api/tos)
- [Last.fm API key registration](https://www.last.fm/api/account/create)
