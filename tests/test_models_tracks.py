import pytest

from app.models import PlaylistConfig, TrackSpec, extract_track_id, track_uri
from app.tracks import duplicate_key, normalize_text, normalized_title, remove_config_duplicates, score_candidate, title_version_terms


def test_normalization_handles_accents_case_punctuation_and_featured_artists():
    assert normalize_text("Beyoncé feat. Bad Bunny!") == "beyonce feat bad bunny"
    assert normalized_title("Dákiti (feat. Jhay Cortez)") == "dakiti"
    assert normalized_title("Song ft. Another Artist") == "song"
    assert normalize_text("東京—音楽") == "東京 音楽"


def test_extract_track_id_from_uri_and_localized_spotify_url():
    assert extract_track_id("spotify:track:AbC123") == "AbC123"
    assert extract_track_id("https://open.spotify.com/intl-es/track/AbC123?si=secret") == "AbC123"
    assert track_uri("AbC123") == "spotify:track:AbC123"


@pytest.mark.parametrize("value", ["https://example.com/track/AbC", "https://open.spotify.com/album/AbC", "spotify:album:AbC"])
def test_reject_non_track_ids(value):
    with pytest.raises(ValueError):
        extract_track_id(value)


def test_detect_duplicate_json_entries_by_uri_or_normalized_artist_title():
    tracks = [
        TrackSpec(artist="Beyoncé", title="Halo"),
        TrackSpec(artist="BEYONCE", title="Halo!"),
        TrackSpec(spotify_uri="spotify:track:RealId"),
        TrackSpec(spotify_url="https://open.spotify.com/track/RealId"),
    ]
    unique, duplicates = remove_config_duplicates(tracks)
    assert len(unique) == 2
    assert duplicates == 2
    assert duplicate_key(TrackSpec(artist="Beyoncé", title="Halo")) == duplicate_key(TrackSpec(artist="BEYONCE", title="Halo!"))


def test_version_mismatch_is_detected_in_candidate_scoring():
    requested = TrackSpec(artist="Example Artist", title="Song")
    candidate = {"name": "Song - Live", "artists": [{"name": "Example Artist"}]}
    score, title_score, artist_score, version_error = score_candidate(requested, candidate)
    assert score > 0
    assert 0 < title_score < 1.0
    assert artist_score == 1.0
    assert version_error


def test_requested_featured_artist_must_match_spotify_credit():
    requested = TrackSpec(artist="Example Artist", title="Song feat. Guest Artist")
    wrong = {"name": "Song (feat. Other Guest)", "artists": [{"name": "Example Artist"}, {"name": "Other Guest"}]}
    right = {"name": "Song (feat. Guest Artist)", "artists": [{"name": "Example Artist"}, {"name": "Guest Artist"}]}
    assert score_candidate(requested, wrong)[3] == "candidate does not include the requested featured artist"
    assert score_candidate(requested, right)[3] is None


def test_version_markers_match_words_not_substrings():
    assert title_version_terms("Olive") == set()
    assert title_version_terms("Song (Sped-Up Remix)") == {"sped up", "remix"}


def test_playlist_config_validation_accepts_rotation_and_markets():
    config = PlaylistConfig.model_validate(
        {
            "slug": "test-playlist",
            "name": "Test",
            "description": "Description",
            "markets": ["CL", "MX"],
            "rotation": {"enabled": False, "keep_anchor_tracks": 20, "growth_slots": 40, "discovery_slots": 20},
            "tracks": [{"artist": "A", "title": "B", "priority": "anchor", "locked": True}],
        }
    )
    assert config.rotation.discovery_slots == 20
    assert config.markets == ["CL", "MX"]
    assert config.tracks[0].locked


def test_track_config_requires_a_usable_identifier():
    with pytest.raises(ValueError):
        TrackSpec(artist="Only an artist")
