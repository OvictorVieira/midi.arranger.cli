"""Testes de `guitar.pinch_harmonic` — velocity 127 sequestra Ample."""

from __future__ import annotations

import mido
import pytest

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)
from tools.techniques.errors import TechniqueRecipeError


def _make_midi(
    velocities: list[int],
    *,
    ticks_per_beat: int = 480,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Guitar", time=0))
    beat = ticks_per_beat
    for i, vel in enumerate(velocities):
        track.append(mido.Message(
            "note_on", channel=1, note=52, velocity=vel,
            time=beat if i > 0 else 0,
        ))
        track.append(mido.Message(
            "note_off", channel=1, note=52, velocity=0, time=beat,
        ))
    mid.tracks.append(track)
    return mid


def _velocities(mid: mido.MidiFile) -> list[int]:
    return [
        msg.velocity
        for track in mid.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]


def test_guitar_pinch_harmonic_is_registered_as_supported():
    assert "guitar.pinch_harmonic" in SUPPORTED_TECHNIQUES
    entry = get_technique("guitar.pinch_harmonic")
    assert entry.canonical == "guitar.pinch_harmonic"
    assert entry.level == "technique"


def test_guitar_pinch_harmonic_requires_ample_tool():
    source = _make_midi([90, 90, 90])

    with pytest.raises(TechniqueRecipeError):
        apply_technique(
            "guitar.pinch_harmonic", source, seed=1, tool="generic",
            parameters={"density": 1.0},
        )


def test_guitar_pinch_harmonic_without_density_is_no_op():
    source = _make_midi([90, 90, 90])
    before = _velocities(source)

    out = apply_technique(
        "guitar.pinch_harmonic", source, seed=1, tool="ample",
    )

    assert _velocities(out) == before


def test_guitar_pinch_harmonic_sets_selected_notes_to_127():
    source = _make_midi([90, 90, 90, 90])
    out = apply_technique(
        "guitar.pinch_harmonic", source, seed=2, tool="ample",
        parameters={"density": 1.0},
    )

    assert _velocities(out) == [127, 127, 127, 127]


def test_guitar_pinch_harmonic_ample_metal_also_works():
    source = _make_midi([90, 90])
    out = apply_technique(
        "guitar.pinch_harmonic", source, seed=3, tool="ample_metal",
        parameters={"density": 1.0},
    )

    assert _velocities(out) == [127, 127]


def test_guitar_pinch_harmonic_preserves_pitch_and_position():
    source = _make_midi([90, 90, 90])
    original = [
        (msg.channel, msg.note)
        for track in source.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    out = apply_technique(
        "guitar.pinch_harmonic", source, seed=4, tool="ample",
        parameters={"density": 1.0},
    )
    after = [
        (msg.channel, msg.note)
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    assert after == original


def test_guitar_pinch_harmonic_is_idempotent():
    source = _make_midi([90, 90, 90])
    once = apply_technique(
        "guitar.pinch_harmonic", source, seed=5, tool="ample",
        parameters={"density": 1.0},
    )
    twice = apply_technique(
        "guitar.pinch_harmonic", once, seed=5, tool="ample",
        parameters={"density": 1.0},
    )
    assert _velocities(once) == _velocities(twice) == [127, 127, 127]
