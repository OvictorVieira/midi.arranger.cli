"""Testes do registro de tecnicas aplicaveis."""

from __future__ import annotations

from pathlib import Path

import mido
import pytest

from tools.techniques import (
    SUPPORTED_TECHNIQUES,
    TechniqueContractError,
    TechniqueRegistrationError,
    TechniqueRegistry,
    UnknownTechniqueError,
    apply_technique,
    build_index,
    get_technique,
    registered_techniques,
    validate_registry_against_index,
)

MANUALS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "tecnicas"


def test_registered_technique_declares_canonical_level_and_apply_function():
    tech = get_technique("drums.ghost_notes")

    assert tech.canonical == "drums.ghost_notes"
    assert tech.level == "technique"
    assert callable(tech.apply)


def test_dispatch_by_canonical_name_calls_registered_function():
    registry = TechniqueRegistry()
    calls = []

    @registry.register("drums.ghost_notes", "technique")
    def apply(payload: dict[str, str], *, intensity: float) -> dict[str, object]:
        calls.append((payload, intensity))
        return {"payload": payload, "intensity": intensity}

    result = registry.apply("drums.ghost_notes", {"track": "Drums"}, intensity=0.5)

    assert result == {"payload": {"track": "Drums"}, "intensity": 0.5}
    assert calls == [({"track": "Drums"}, 0.5)]


def test_unknown_technique_error_names_available_techniques():
    registry = TechniqueRegistry()
    registry.register("drums.microtiming", "humanize")(lambda payload: payload)
    registry.register("drums.ghost_notes", "technique")(lambda payload: payload)

    with pytest.raises(UnknownTechniqueError) as exc:
        registry.apply("drums.flanm", object())

    assert exc.value.available == ("drums.ghost_notes", "drums.microtiming")
    assert "drums.ghost_notes" in str(exc.value)
    assert "drums.microtiming" in str(exc.value)


def test_duplicate_or_malformed_registration_fails():
    registry = TechniqueRegistry()
    registry.register("drums.ghost_notes", "technique")(lambda payload: payload)

    with pytest.raises(TechniqueRegistrationError, match="duplicada"):
        registry.register("drums.ghost_notes", "technique")(lambda payload: payload)
    with pytest.raises(TechniqueRegistrationError, match="<familia>.<nome>"):
        registry.register("ghost_notes", "technique")(lambda payload: payload)
    with pytest.raises(TechniqueRegistrationError, match="nivel"):
        registry.register("drums.microtiming", "ornament")(lambda payload: payload)
    with pytest.raises(TechniqueRegistrationError, match="chamavel"):
        registry.register("drums.microtiming", "humanize")(None)


def test_supported_techniques_is_derived_from_the_registry():
    assert tuple(t.canonical for t in registered_techniques()) == SUPPORTED_TECHNIQUES
    assert tuple(sorted(SUPPORTED_TECHNIQUES)) == SUPPORTED_TECHNIQUES
    assert set(SUPPORTED_TECHNIQUES) == {
        "bass.ghost_notes",
        "drums.ghost_notes",
        "drums.microtiming",
    }


def test_global_dispatch_uses_registered_implementation():
    payload = {"notes": [38]}

    assert apply_technique("drums.ghost_notes", payload) is payload


def test_humanize_allows_only_timing_velocity_and_duration_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(mid: mido.MidiFile) -> mido.MidiFile:
        out = _midi_with_two_notes(first_note=60, second_note=64)
        first_on = out.tracks[1][1]
        first_off = out.tracks[1][2]
        out.tracks[1][1] = first_on.copy(time=12, velocity=70)
        out.tracks[1][2] = first_off.copy(time=300)
        return out

    result = registry.apply("drums.microtiming", _midi_with_two_notes())

    assert result.tracks[1][1].time == 12
    assert result.tracks[1][1].velocity == 70
    assert result.tracks[1][2].time == 300


def test_humanize_contract_rejects_added_notes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(mid: mido.MidiFile) -> mido.MidiFile:
        mid.tracks[1].append(mido.Message(
            "note_on", channel=9, note=38, velocity=90, time=0
        ))
        mid.tracks[1].append(mido.Message(
            "note_off", channel=9, note=38, velocity=0, time=120
        ))
        return mid

    with pytest.raises(TechniqueContractError, match="contagem de note_on"):
        registry.apply("drums.microtiming", _midi_with_two_notes())


def test_humanize_contract_rejects_pitch_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(_mid: mido.MidiFile) -> mido.MidiFile:
        return _midi_with_two_notes(first_note=61, second_note=64)

    with pytest.raises(TechniqueContractError, match="multiconjunto de pitches"):
        registry.apply("drums.microtiming", _midi_with_two_notes())


def test_humanize_contract_rejects_note_on_order_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(_mid: mido.MidiFile) -> mido.MidiFile:
        return _midi_with_two_notes(first_note=64, second_note=60)

    with pytest.raises(TechniqueContractError, match="ordem dos note_on"):
        registry.apply("drums.microtiming", _midi_with_two_notes())


def test_every_registered_technique_exists_in_manual_index():
    idx = build_index(MANUALS_DIR)

    validate_registry_against_index(idx)
    for canonical in SUPPORTED_TECHNIQUES:
        assert idx.get(canonical) is not None


def _midi_with_two_notes(
    *,
    first_note: int = 60,
    second_note: int = 64,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    track.append(mido.Message(
        "note_on", channel=9, note=first_note, velocity=96, time=0
    ))
    track.append(mido.Message(
        "note_off", channel=9, note=first_note, velocity=0, time=480
    ))
    track.append(mido.Message(
        "note_on", channel=9, note=second_note, velocity=88, time=0
    ))
    track.append(mido.Message(
        "note_off", channel=9, note=second_note, velocity=0, time=480
    ))
    mid.tracks.extend([meta, track])
    return mid
