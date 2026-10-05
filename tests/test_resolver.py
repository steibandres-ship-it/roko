from app.models import TrackSpec
from app.tracks import TrackResolver


def result(name, track_id, artist="Example Artist", playable=True):
    return {
        "id": track_id,
        "uri": f"spotify:track:{track_id}",
        "name": name,
        "artists": [{"name": artist}],
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
        "is_playable": playable,
    }


class SearchAPI:
    def __init__(self, pages, direct=None):
        self.pages = pages
        self.direct = direct or {}
        self.calls = []

    def search_tracks(self, query, *, limit, offset):
        self.calls.append((query, limit, offset))
        return self.pages.get(offset, [])

    def get_track(self, track_id):
        return self.direct[track_id]


def test_resolver_does_not_choose_live_or_remix_over_exact_studio_match():
    api = SearchAPI({0: [result("Song - Live", "liveid"), result("Song", "studioid")]})
    track, candidates, reason = TrackResolver(api).resolve(TrackSpec(artist="Example Artist", title="Song"))
    assert reason is None
    assert track.id == "studioid"
    assert track.score > 0.9
    assert len(candidates) == 2


def test_resolver_paginates_to_current_ten_result_limit_when_first_page_uncertain():
    api = SearchAPI(
        {
            0: [result("Unrelated", f"weak{i}", artist="Other") for i in range(10)],
            10: [result("Requested Song", "goodid")],
        }
    )
    track, _, reason = TrackResolver(api).resolve(TrackSpec(artist="Example Artist", title="Requested Song"))
    assert reason is None
    assert track.id == "goodid"
    assert api.calls == [("track:Requested Song artist:Example Artist", 10, 0), ("track:Requested Song artist:Example Artist", 10, 10)]


def test_resolver_leaves_close_candidates_unresolved():
    api = SearchAPI({0: [result("Song", "one"), result("Song", "two")]})
    track, candidates, reason = TrackResolver(api).resolve(TrackSpec(artist="Example Artist", title="Song"))
    assert track is None
    assert len(candidates) == 2
    assert "nearly identical" in reason


def test_resolver_rejects_unplayable_direct_track():
    api = SearchAPI({}, direct={"id1": result("Song", "id1", playable=False)})
    track, candidates, reason = TrackResolver(api).resolve(TrackSpec(spotify_uri="spotify:track:id1"))
    assert track is None
    assert candidates[0]["uri"] == "spotify:track:id1"
    assert "unavailable" in reason


def test_resolver_trusts_uri_only_when_catalog_verification_is_recorded():
    api = SearchAPI({})
    track, candidates, reason = TrackResolver(api).resolve(
        TrackSpec(spotify_uri="spotify:track:verified1", spotify_catalog_verified=True)
    )
    assert reason is None
    assert track.id == "verified1"
    assert track.uri == "spotify:track:verified1"
    assert api.calls == []
    assert candidates[0]["uri"] == "spotify:track:verified1"
