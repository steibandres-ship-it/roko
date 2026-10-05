# Last.fm chart metrics and trend calculations

## Current source-backed measures

`MetricSnapshot` stores a Last.fm observation with provider/entity/platform/market, metric code, observed and captured timestamps, unit, rights basis, and a Last.fm track URL. Provider-reported confidence and coverage are nullable and remain unknown.

- `lastfm_chart_rank`: chart position, lower is better.
- `lastfm_chart_playcount`: Last.fm's reported playcount. The country endpoint is explicitly for the last week; the global endpoint's period is not specified in its documentation.
- `rank_change`: previous captured rank minus current captured rank. Positive means the track moved up; null means no earlier capture is available. It is an app-derived rank difference, not a provider-provided growth percentage.

Sync captures each market at most once per Santiago calendar day, respects response cache headers in memory, and removes snapshots older than 30 days to bound retained Last.fm data. Each row links to the corresponding Last.fm catalog entry. No raw response, profile, listener history, Spotify stream metric, or Spotify playlist metric is stored from this integration.

## Scores

The dashboard does not create Trend, Breakout, NEXT, or a cross-platform score from Last.fm alone. This source gives one service's charts and does not expose the Spotify, playlist, social, search, or other independent metrics needed to support a score with those meanings. The legacy scoring module and historical Chartmetric adapter are not selected by the active provider registry or sync command.

## Regional and global interpretation

The global chart and country charts are Last.fm signals; they are not interchangeable with Spotify charts or population-wide music consumption. Cross-market ordering, listener migration, causality, recommendation placement, and follower growth are not inferred. Country chart playcounts describe Last.fm's previous-week chart per the endpoint documentation; global playcount carries no documented period label.

Last.fm terms require non-commercial use, attribution, links to Last.fm, a 100 MB maximum total data cap, and written approval before public access to API-powered pages. Use from outside the EEA carries an additional user-permission condition. The current application stays local and blocks sync until its operator confirms the configured scope.
