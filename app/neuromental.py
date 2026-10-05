from __future__ import annotations

from .models import NeuromentalProfile, TrackNeuromentalMeta, TrackSpec


DIMENSIONS = ("energy", "valence", "focus", "social_energy")


def score_neuromental_fit(track: TrackSpec, profile: NeuromentalProfile) -> int | None:
    """Return a transparent editorial fit score, or None when curator signals are absent.

    The score compares explicitly supplied 1–5 ratings and intent tags. It does not
    infer a listener's mental state, read audio features, or claim a health effect.
    """
    signals = track.neuromental
    if signals is None:
        return None

    dimension_scores: list[float] = []
    for dimension in DIMENSIONS:
        value = getattr(signals, dimension)
        target = getattr(profile, f"{dimension}_target")
        if value is not None and target is not None:
            dimension_scores.append(1.0 - abs(value - target) / 4.0)

    tag_score: float | None = None
    if profile.intents and signals.intent_tags:
        tag_score = len(set(profile.intents) & set(signals.intent_tags)) / len(set(profile.intents))

    if not dimension_scores and tag_score is None:
        return None
    if dimension_scores and tag_score is not None:
        fit = 0.75 * (sum(dimension_scores) / len(dimension_scores)) + 0.25 * tag_score
    elif dimension_scores:
        fit = sum(dimension_scores) / len(dimension_scores)
    else:
        fit = tag_score or 0.0
    return round(fit * 100)


def profile_coverage(tracks: list[TrackSpec], profile: NeuromentalProfile) -> tuple[int, int, float | None]:
    """Return (scored, total, mean score) for a local playlist configuration."""
    scores = [score for track in tracks if (score := score_neuromental_fit(track, profile)) is not None]
    average = round(sum(scores) / len(scores)) if scores else None
    return len(scores), len(tracks), average


def parse_annotation(value: str, current: TrackNeuromentalMeta | None = None) -> TrackNeuromentalMeta:
    """Parse an explicit human annotation: E,V,F,S;role;tag|tag;rationale.

    Empty fields or '=' keep existing values. A '-' clears the selected field.
    Validation (including the 1–5 range and allowed tags) is enforced by Pydantic.
    """
    fields = value.split(";")
    if len(fields) > 4:
        raise ValueError("use at most four sections separated by semicolons")
    fields += [""] * (4 - len(fields))
    dimensions = fields[0].split(",")
    if len(dimensions) != 4:
        raise ValueError("the first section needs four values: energy,valence,focus,social_energy")

    prior = current or TrackNeuromentalMeta()

    def dimension(raw: str, old: int | None) -> int | None:
        raw = raw.strip()
        if raw in {"", "="}:
            return old
        if raw == "-":
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError("dimension values must be 1–5, '-', or '='") from exc

    def scalar(raw: str, old: str | None) -> str | None:
        raw = raw.strip()
        if raw in {"", "="}:
            return old
        return None if raw == "-" else raw

    raw_tags = fields[2].strip()
    if raw_tags in {"", "="}:
        intent_tags = prior.intent_tags
    elif raw_tags == "-":
        intent_tags = []
    else:
        intent_tags = [tag.strip() for tag in raw_tags.split("|") if tag.strip()]

    return TrackNeuromentalMeta(
        energy=dimension(dimensions[0], prior.energy),
        valence=dimension(dimensions[1], prior.valence),
        focus=dimension(dimensions[2], prior.focus),
        social_energy=dimension(dimensions[3], prior.social_energy),
        sequence_role=scalar(fields[1], prior.sequence_role),
        intent_tags=intent_tags,
        rationale=scalar(fields[3], prior.rationale),
    )


def plan_sequence(tracks: list[TrackSpec], profile: NeuromentalProfile) -> tuple[list[TrackSpec], int, int]:
    """Reorder only tracks with human-provided roles, following the profile arc.

    Unrated and untagged tracks stay in their original slots. This prevents the
    planner from guessing how an unreviewed song should feel or where it belongs.
    Returns the proposed tracks, count of explicitly role-tagged tracks, and the
    number of positions that would change.
    """
    role_order: dict[str, int] = {}
    for index, role in enumerate(profile.sequence_arc):
        role_order.setdefault(role, index)
    positions: list[int] = []
    tagged: list[tuple[int, TrackSpec]] = []
    for index, track in enumerate(tracks):
        signals = track.neuromental
        role = signals.sequence_role if signals else None
        if role in role_order:
            positions.append(index)
            tagged.append((index, track))
    sorted_tracks = [
        track
        for _, track in sorted(
            tagged,
            key=lambda entry: (role_order[entry[1].neuromental.sequence_role], entry[0]),  # type: ignore[union-attr]
        )
    ]
    planned = list(tracks)
    for position, track in zip(positions, sorted_tracks):
        planned[position] = track
    changed = sum(before is not after for before, after in zip(tracks, planned))
    return planned, len(tagged), changed
