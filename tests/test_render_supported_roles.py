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
    _hat_elec_element,
    _motor_element,
    _piano_element,
    _rhythmic_element,
    _shadow_element,
    _strings_element,
    _sub_drop_element,
    _sub_element,
)
from tools.palette.bass import BASS_ROLES, DEFAULT_BASS_REGISTER
from tools.palette.drums import DRUMS_ROLES
from tools.palette.electronic import HAT_ELEC_ROLES, SUB_DROP_ROLES, SUB_ROLES
from tools.palette.harmonic import DRONE_ROLES, KEYBOARD_ROLES, STRINGS_ROLES, PadNote
from tools.palette.rhythmic import MOTOR_ROLES, RHYTHMIC_ROLES, SHADOW_ROLES
from tools.palette.transitions import (
    DOWNER_ROLES,
    IMPACT_ROLES,
    REVERSE_ROLES,
    RISER_ROLES,
)
from tools.plan import Element
from tools.render import SUPPORTED_ROLES, _notes_to_track, render


def _bass_element() -> Element:
    return Element(
        id="bass_main",
        role="bass",
        sections=["MAIN"],
        register=list(DEFAULT_BASS_REGISTER),
        layers=1,
        sync_role="kick_support",
        articulation="tight",
        harmony="follow_chords",
        instrument={"plugin": "Trilian", "preset": "Fingered Bass", "verified": True},
        rationale="Baixo gerado do zero seguindo o campo harmonico.",
    )


def _drums_element() -> Element:
    return Element(
        id="drums_main",
        role="drums",
        sections=["MAIN"],
        register=[0, 127],
        layers=1,
        sync_role="exact_anchor",
        articulation="tight",
        harmony="percussion",
        instrument={"plugin": "Superior Drummer", "preset": "Metal Kit", "verified": True},
        rationale="Bateria gerada do zero coerente com o mapa de energia.",
    )


def _riser_element() -> Element:
    return Element(
        id="riser_main",
        role="riser",
        sections=["MAIN"],
        register=[48, 84],
        layers=1,
        sync_role="response",
        articulation="sustained",
        harmony="free",
        instrument={"plugin": "Serum", "preset": "Riser FX", "verified": True},
        rationale="Riser de transicao para o teste de cobertura de roles.",
    )


def _downer_element() -> Element:
    return Element(
        id="downer_main",
        role="downer",
        sections=["MAIN"],
        register=[48, 84],
        layers=1,
        sync_role="response",
        articulation="sustained",
        harmony="free",
        instrument={"plugin": "Omnisphere", "preset": "Downer FX", "verified": True},
        rationale="Downer de transicao para o teste de cobertura de roles.",
    )


def _impact_element() -> Element:
    return Element(
        id="impact_main",
        role="impact",
        sections=["MAIN"],
        register=[24, 84],
        layers=1,
        sync_role="exact_anchor",
        articulation="staccato",
        harmony="percussion",
        instrument={"plugin": "Logic Sampler", "preset": "Impact Hit", "verified": True},
        rationale="Impacto de transicao para o teste de cobertura de roles.",
    )


def _reverse_element() -> Element:
    return Element(
        id="reverse_main",
        role="reverse",
        sections=["MAIN"],
        register=[48, 72],
        layers=1,
        sync_role="response",
        articulation="sustained",
        harmony="free",
        instrument={"plugin": "Omnisphere", "preset": "Reverse Swell", "verified": True},
        rationale="Reverse/meia-lua de transicao para o teste de cobertura de roles.",
    )


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
    if role in BASS_ROLES:
        return _bass_element()
    if role in DRUMS_ROLES:
        return _drums_element()
    if role in HAT_ELEC_ROLES:
        return _hat_elec_element()
    if role in SUB_ROLES:
        return _sub_element()
    if role in SUB_DROP_ROLES:
        return _sub_drop_element()
    if role in RISER_ROLES:
        return _riser_element()
    if role in DOWNER_ROLES:
        return _downer_element()
    if role in IMPACT_ROLES:
        return _impact_element()
    if role in REVERSE_ROLES:
        return _reverse_element()
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
