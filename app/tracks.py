from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .models import ResolvedTrack, TrackSpec, extract_track_id, track_uri
from .spotify_client import SpotifyAPIError


VERSION_TERMS = {"remix", "sped up", "slowed", "live", "acoustic", "instrumental", "karaoke"}
MIN_SCORE = 0.84
MIN_TITLE_SCORE = 0.88
MIN_ARTIST_SCORE = 0.80
AMBIGUITY_MARGIN = 0.055


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in unaccented).split())


def normalized_title(value: str) -> str:
    feature = r"(?:feat(?:uring)?\.?|ft\.?)"
    value = re.sub(rf"\s*\({feature}\s+.*?\)\s*", " ", value, flags=re.I)
    value = re.sub(rf"\s*\[{feature}\s+.*?\]\s*", " ", value, flags=re.I)
    value = re.sub(rf"\s+{feature}\s+.*$", " ", value, flags=re.I)
    return normalize_text(value)


def title_version_terms(value: str) -> set[str]:
    normalized = f" {normalize_text(value)} "
    return {term for term in VERSION_TERMS if f" {term} " in normalized}


def duplicate_key(spec: TrackSpec) -> str:
    if spec.spotify_uri:
        return "uri:" + track_uri(extract_track_id(spec.spotify_uri))
    if spec.spotify_url:
        return "uri:" + track_uri(extract_track_id(spec.spotify_url))
    if spec.isrc:
        return "isrc:" + spec.isrc.upper()
    return f"text:{normalize_text(spec.artist or '')}|{normalized_title(spec.title or '')}"


def remove_config_duplicates(tracks: list[TrackSpec]) -> tuple[list[TrackSpec], int]:
    output: list[TrackSpec] = []
    seen: set[str] = set()
    duplicates = 0
    for spec in tracks:
        key = duplicate_key(spec)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        output.append(spec)
    return output, duplicates


def _artist_score(requested: str, artists: list[str]) -> float:
    requested = re.sub(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+.*$", " ", requested, flags=re.I)
    target = normalize_text(requested)
    individual = [normalize_text(name) for name in artists if name]
    joined = normalize_text(" & ".join(name for name in artists if name))
    return max([SequenceMatcher(None, target, candidate).ratio() for candidate in [*individual, joined]] or [0.0])


def _requested_feature_artists(*values: str) -> list[str]:
    names: list[str] = []
    pattern = re.compile(r"\b(?:feat(?:uring)?\.?|ft\.?)\s+(.+?)(?=\s*[\)\]]|$)", re.I)
    for value in values:
        for match in pattern.finditer(value):
            for name in re.split(r"\s*(?:,|&|\band\b)\s*", match.group(1), flags=re.I):
                cleaned = normalize_text(name)
                if cleaned and cleaned not in names:
                    names.append(cleaned)
    return names


def _feature_artist_score(expected: list[str], artists: list[str]) -> tuple[float, bool]:
    if not expected:
        return 1.0, True
    actual = [normalize_text(name) for name in artists]
    scores = [max([SequenceMatcher(None, name, candidate).ratio() for candidate in actual] or [0.0]) for name in expected]
    return sum(scores) / len(scores), all(score >= MIN_ARTIST_SCORE for score in scores)


def score_candidate(spec: TrackSpec, candidate: dict[str, Any]) -> tuple[float, float, float, str | None]:
    candidate_name = str(candidate.get("name") or "")
    artists = [str(artist.get("name") or "") for artist in candidate.get("artists", []) if isinstance(artist, dict)]
    requested_title = spec.title or candidate_name
    title_score = SequenceMatcher(None, normalized_title(requested_title), normalized_title(candidate_name)).ratio()
    artist_score = _artist_score(spec.artist or "", artists) if spec.artist else 1.0
    featured_artists = _requested_feature_artists(spec.artist or "", requested_title)
    feature_score, feature_match = _feature_artist_score(featured_artists, artists)
    requested_versions = title_version_terms(requested_title)
    candidate_versions = title_version_terms(candidate_name)
    version_error: str | None = None
    if candidate_versions - requested_versions:
        version_error = "candidate adds a different version marker"
    elif requested_versions - candidate_versions:
        version_error = "candidate does not match the requested version"
    elif not feature_match:
        version_error = "candidate does not include the requested featured artist"
    isrc_score = 0.0
    candidate_isrc = str((candidate.get("external_ids") or {}).get("isrc") or "").upper()
    if spec.isrc:
        if candidate_isrc == spec.isrc.upper():
            isrc_score = 0.06
        elif candidate_isrc:
            isrc_score = -0.20
    if featured_artists:
        total = 0.56 * title_score + 0.31 * artist_score + 0.13 * feature_score + isrc_score
    else:
        total = 0.59 * title_score + 0.34 * artist_score + 0.07 + isrc_score
    total = max(0.0, min(1.0, total))
    return total, title_score, artist_score, version_error


def candidate_summary(candidate: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    artists = [str(artist.get("name") or "") for artist in candidate.get("artists", []) if isinstance(artist, dict)]
    summary: dict[str, Any] = {
        "name": candidate.get("name"),
        "artists": artists,
        "uri": candidate.get("uri"),
        "spotify_url": (candidate.get("external_urls") or {}).get("spotify"),
        "id": candidate.get("id"),
    }
    if score is not None:
        summary["score"] = round(score, 3)
    return summary


class TrackResolver:
    def __init__(self, spotify: Any):
        self.spotify = spotify

    def resolve(self, spec: TrackSpec) -> tuple[ResolvedTrack | None, list[dict[str, Any]], str | None]:
        direct = spec.spotify_uri or spec.spotify_url
        if direct:
            try:
                track_id = extract_track_id(direct)
                if spec.spotify_catalog_verified:
                    candidate = {
                        "id": track_id,
                        "uri": track_uri(track_id),
                        "name": spec.title or "",
                        "artists": [{"name": spec.artist}] if spec.artist else [],
                        "external_urls": {"spotify": spec.spotify_url or f"https://open.spotify.com/track/{track_id}"},
                        "external_ids": {"isrc": spec.isrc} if spec.isrc else {},
                    }
                    return self._resolved(candidate), [candidate_summary(candidate)], None
                candidate = self.spotify.get_track(track_id)
            except ValueError as exc:
                return None, [], str(exc)
            except SpotifyAPIError as exc:
                if exc.status_code == 404:
                    return None, [], "Spotify did not find the supplied track ID"
                raise
            if candidate.get("is_playable") is False:
                return None, [candidate_summary(candidate)], "track is unavailable in the connected account's market"
            if spec.isrc and str((candidate.get("external_ids") or {}).get("isrc") or "").upper() != spec.isrc.upper():
                return None, [candidate_summary(candidate)], "supplied ISRC does not match the Spotify track"
            return self._resolved(candidate), [candidate_summary(candidate)], None

        if not spec.artist or not spec.title:
            return None, [], "artist and title are needed when no Spotify ID is supplied"
        query = f"track:{spec.title} artist:{spec.artist}"
        if spec.isrc:
            query = f"isrc:{spec.isrc}"
        candidates = self.spotify.search_tracks(query, limit=10, offset=0)
        scored = self._score(spec, candidates)
        if spec.isrc and not any(str((item.get("external_ids") or {}).get("isrc") or "").upper() == spec.isrc.upper() for item, *_ in scored):
            query = f"track:{spec.title} artist:{spec.artist}"
            candidates = self.spotify.search_tracks(query, limit=10, offset=0)
            scored = self._score(spec, candidates)
        if not self._confident(scored):
            next_page = self.spotify.search_tracks(query, limit=10, offset=10)
            scored = self._score(spec, [*candidates, *next_page])
        if not scored:
            return None, [], "no playable candidates found"
        summaries = [candidate_summary(item, score) for item, score, _, _, _ in scored[:5]]
        best = scored[0]
        _, score, title_score, artist_score, version_error = best
        if version_error:
            return None, summaries, version_error
        if score < MIN_SCORE or title_score < MIN_TITLE_SCORE or artist_score < MIN_ARTIST_SCORE:
            return None, summaries, "best candidate did not meet the confidence threshold"
        if len(scored) > 1 and score - scored[1][1] < AMBIGUITY_MARGIN:
            return None, summaries, "multiple candidates have nearly identical scores"
        return self._resolved(best[0], score), summaries, None

    @staticmethod
    def _confident(scored: list[tuple[dict[str, Any], float, float, float, str | None]]) -> bool:
        if not scored:
            return False
        _, score, title_score, artist_score, version_error = scored[0]
        ambiguous = len(scored) > 1 and score - scored[1][1] < AMBIGUITY_MARGIN
        return not version_error and score >= MIN_SCORE and title_score >= MIN_TITLE_SCORE and artist_score >= MIN_ARTIST_SCORE and not ambiguous

    def _score(self, spec: TrackSpec, candidates: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float, float, float, str | None]]:
        seen: dict[str, tuple[dict[str, Any], float, float, float, str | None]] = {}
        for item in candidates:
            if not item or item.get("is_playable") is False or not item.get("id"):
                continue
            score, title_score, artist_score, version_error = score_candidate(spec, item)
            key = str(item.get("id"))
            existing = seen.get(key)
            candidate = (item, score, title_score, artist_score, version_error)
            if existing is None or score > existing[1]:
                seen[key] = candidate
        return sorted(seen.values(), key=lambda row: (row[1], row[2], row[3], str(row[0].get("id"))), reverse=True)

    @staticmethod
    def _resolved(candidate: dict[str, Any], score: float | None = None) -> ResolvedTrack:
        return ResolvedTrack(
            id=str(candidate.get("id") or extract_track_id(str(candidate.get("uri") or ""))),
            uri=str(candidate.get("uri") or track_uri(str(candidate["id"]))),
            name=str(candidate.get("name") or ""),
            artists=[str(a.get("name") or "") for a in candidate.get("artists", []) if isinstance(a, dict)],
            spotify_url=(candidate.get("external_urls") or {}).get("spotify"),
            isrc=(candidate.get("external_ids") or {}).get("isrc"),
            score=score,
        )
