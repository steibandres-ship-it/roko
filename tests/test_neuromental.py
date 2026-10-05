import pytest

from app.models import NeuromentalProfile, TrackNeuromentalMeta, TrackSpec
from app.neuromental import parse_annotation, plan_sequence, profile_coverage, score_neuromental_fit


def test_neuromental_fit_uses_explicit_ratings_and_intent_tags():
    profile = NeuromentalProfile(
        intents=["activation", "social-connection"],
        energy_target=4,
        valence_target=4,
        focus_target=2,
        social_energy_target=5,
    )
    track = TrackSpec(
        artist="Artist",
        title="Song",
        neuromental={
            "energy": 4,
            "valence": 4,
            "focus": 2,
            "social_energy": 5,
            "intent_tags": ["activation"],
        },
    )

    assert score_neuromental_fit(track, profile) == 88
    assert profile_coverage([track], profile) == (1, 1, 88)


def test_missing_curator_signals_are_unscored_instead_of_inferred():
    profile = NeuromentalProfile(intents=["focus"], energy_target=3)
    track = TrackSpec(artist="Artist", title="Song")

    assert score_neuromental_fit(track, profile) is None
    assert profile_coverage([track], profile) == (0, 1, None)


def test_neuromental_ratings_must_stay_on_one_to_five_editorial_scale():
    with pytest.raises(ValueError):
        TrackSpec(artist="Artist", title="Song", neuromental={"energy": 6})
    with pytest.raises(ValueError):
        NeuromentalProfile(intents=["focus"], valence_target=0)


def test_neuromental_metadata_rejects_unknown_intents():
    with pytest.raises(ValueError):
        NeuromentalProfile(intents=["diagnose"])


def test_parse_annotation_supports_keep_clear_and_set_values():
    current = TrackNeuromentalMeta(
        energy=2,
        valence=3,
        focus=1,
        social_energy=2,
        sequence_role="flow",
        intent_tags=["focus"],
        rationale="old note",
    )
    updated = parse_annotation("4,=,-,5;build;activation|uplift;reviewed", current)

    assert updated.energy == 4
    assert updated.valence == 3
    assert updated.focus is None
    assert updated.social_energy == 5
    assert updated.sequence_role == "build"
    assert updated.intent_tags == ["activation", "uplift"]
    assert updated.rationale == "reviewed"


def test_parse_annotation_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        parse_annotation("6,3,3,3")


def test_sequence_planner_reorders_only_explicitly_role_tagged_tracks():
    build_one = TrackSpec(spotify_uri="spotify:track:build1", neuromental={"sequence_role": "build"})
    unreviewed = TrackSpec(spotify_uri="spotify:track:unrated")
    discovery = TrackSpec(spotify_uri="spotify:track:discover", neuromental={"sequence_role": "discovery"})
    build_two = TrackSpec(spotify_uri="spotify:track:build2", neuromental={"sequence_role": "build"})
    profile = NeuromentalProfile(sequence_arc=["discovery", "build"])

    planned, tagged, changed = plan_sequence([build_one, unreviewed, discovery, build_two], profile)

    assert planned == [discovery, unreviewed, build_one, build_two]
    assert tagged == 3
    assert changed == 2
