"""Regressao para manter SUPPORTED_ROLES alinhado ao dispatch do renderer."""

from __future__ import annotations

import mido
import pretty_midi
import pytest

from tests.test_render import (
    _build_plan,
    _build_source_with_guitar,
    _build_synthetic_source,
    _drone_element,
    _motor_element,
    _piano_element,
    _rhythmic_element,
    _shadow_element,
    _strings_element,
)
from tools.palette.harmonic import DRONE_ROLES, KEYBOARD_ROLES, STRINGS_ROLES, PadNote
from tools.palette.rhythmic import MOTOR_ROLES, RHYTHMIC_ROLES, SHADOW_ROLES
from tools.render import SUPPORTED_ROLES, _notes_to_track, render


def _element_for_role(role: str):
    if role == "pad":
        return None
    if role in KEYBOARD_ROLES:
        return _piano_element(role=role)
    if role in STRINGS_ROLES:
        return _strings_element(role=role, layers=2)
    if role in DRONE_ROLES:
        return _drone_element()
    if role in RHYTHMIC_ROLES:
        return _rhythmic_element(role=role)
    if role in MOTOR_ROLES:
        return _motor_element()
    if role in SHADOW_ROLES:
        return _shadow_element()
    raise AssertionError(f"test helper missing role factory for {role!r}")


def _source_for_role(tmp_path, role: str):
    if role in SHADOW_ROLES:
        return _build_source_with_guitar(tmp_path)
    return _build_synthetic_source(tmp_path)


def _note_on_count(track: mido.MidiTrack) -> int:
    return sum(
        1
        for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    )


@pytest.mark.parametrize("role", sorted(SUPPORTED_ROLES))
def test_every_supported_role_is_rendered(tmp_path, role):
    src = _source_for_role(tmp_path, role)
    plan = _build_plan(src)
    element = _element_for_role(role)
    if element is not None:
        plan.elements[0] = element

    out = tmp_path / f"{role}.mid"
    report = render(plan, out)

    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(str(out), charset="utf-8")
    emitted_tracks = out_mid.tracks[len(src_mid.tracks):]

    assert report.elements[0].role == role
    assert report.elements[0].rendered is True
    assert emitted_tracks
    assert any(_note_on_count(track) > 0 for track in emitted_tracks)
    assert not any("not implemented" in warning for warning in report.warnings)


def test_notes_to_track_keeps_zero_tick_duration_audible():
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    track = _notes_to_track(
        [PadNote(pitch=60, velocity=80, start_s=0.0, end_s=0.0)],
        pm,
        "Zero Duration",
        channel=0,
    )

    note_events = [msg for msg in track if msg.type in {"note_on", "note_off"}]

    assert [msg.type for msg in note_events] == ["note_on", "note_off"]
    assert note_events[0].time == 0
    assert note_events[1].time == 1
