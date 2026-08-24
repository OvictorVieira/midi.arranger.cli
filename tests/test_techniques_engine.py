"""Testes do registro de tecnicas aplicaveis."""

from __future__ import annotations

from io import BytesIO
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
    assert tech.allow_structural_velocity_change is False
    assert tech.allow_structural_duration_change is False


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


def test_registered_techniques_preserve_structural_notes_by_default():
    for tech in registered_techniques():
        if tech.level != "technique":
            continue

        source = _midi_with_two_notes()
        result = apply_technique(tech.canonical, source)

        assert _note_tuples(result) == _note_tuples(_midi_with_two_notes())


def test_technique_allows_ornamental_notes_cc_and_pitch_bend():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(mid: mido.MidiFile) -> mido.MidiFile:
        track = mid.tracks[1]
        track.append(mido.Message(
            "control_change", channel=9, control=4, value=48, time=0
        ))
        track.append(mido.Message("pitchwheel", channel=9, pitch=-120, time=0))
        track.append(mido.Message(
            "note_on", channel=9, note=38, velocity=32, time=0
        ))
        track.append(mido.Message(
            "note_off", channel=9, note=38, velocity=0, time=120
        ))
        return mid

    result = registry.apply("drums.ghost_notes", _midi_with_two_notes())

    note_ons = [
        msg for msg in result.tracks[1]
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    control_changes = [
        msg for msg in result.tracks[1]
        if not msg.is_meta and msg.type == "control_change"
    ]
    pitch_bends = [
        msg for msg in result.tracks[1]
        if not msg.is_meta and msg.type == "pitchwheel"
    ]
    assert [(msg.channel, msg.note, msg.velocity) for msg in note_ons] == [
        (9, 60, 96),
        (9, 64, 88),
        (9, 38, 32),
    ]
    assert [(msg.channel, msg.control, msg.value) for msg in control_changes] == [
        (9, 4, 48),
    ]
    assert [(msg.channel, msg.pitch) for msg in pitch_bends] == [(9, -120)]


def test_technique_contract_rejects_structural_pitch_or_position_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def change_pitch(_mid: mido.MidiFile) -> mido.MidiFile:
        return _midi_with_two_notes(first_note=61, second_note=64)

    with pytest.raises(TechniqueContractError, match="pitch ou posicao"):
        registry.apply("drums.ghost_notes", _midi_with_two_notes())

    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def change_position(mid: mido.MidiFile) -> mido.MidiFile:
        first_on = mid.tracks[1][1]
        mid.tracks[1][1] = first_on.copy(time=24)
        return mid

    with pytest.raises(TechniqueContractError, match="pitch ou posicao"):
        registry.apply("drums.ghost_notes", _midi_with_two_notes())


def test_technique_contract_rejects_structural_velocity_without_permission():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(mid: mido.MidiFile) -> mido.MidiFile:
        first_on = mid.tracks[1][1]
        mid.tracks[1][1] = first_on.copy(velocity=72)
        return mid

    with pytest.raises(TechniqueContractError, match="velocity"):
        registry.apply("drums.ghost_notes", _midi_with_two_notes())


def test_technique_contract_rejects_structural_duration_without_permission():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(mid: mido.MidiFile) -> mido.MidiFile:
        first_off = mid.tracks[1][2]
        mid.tracks[1][2] = first_off.copy(time=300)
        return mid

    with pytest.raises(TechniqueContractError, match="duracao"):
        registry.apply("drums.ghost_notes", _midi_with_single_note())


def test_technique_can_declare_structural_velocity_and_duration_changes():
    registry = TechniqueRegistry()

    @registry.register(
        "bass.ghost_notes",
        "technique",
        allow_structural_velocity_change=True,
        allow_structural_duration_change=True,
    )
    def apply(mid: mido.MidiFile) -> mido.MidiFile:
        first_on = mid.tracks[1][1]
        first_off = mid.tracks[1][2]
        mid.tracks[1][1] = first_on.copy(velocity=72)
        mid.tracks[1][2] = first_off.copy(time=300)
        return mid

    result = registry.apply("bass.ghost_notes", _midi_with_single_note())

    assert result.tracks[1][1].velocity == 72
    assert result.tracks[1][2].time == 300


def test_technique_application_is_idempotent_in_memory_byte_for_byte():
    registry = _ornament_registry()
    once = _apply_two_ornament_techniques(registry, _midi_with_drums_and_bass())
    once_bytes = _midi_bytes(once)

    twice = _apply_two_ornament_techniques(registry, once)

    assert _midi_bytes(twice) == once_bytes
    assert _note_tuples(twice) == [
        (1, 9, 38, 120, 180, 32),
        (1, 9, 60, 0, 480, 96),
        (2, 0, 35, 240, 300, 28),
        (2, 0, 40, 0, 480, 96),
    ]


def test_technique_application_is_idempotent_after_saved_round_trip(
    tmp_path: Path,
):
    registry = _ornament_registry()
    once = _apply_two_ornament_techniques(registry, _midi_with_drums_and_bass())
    once_path = tmp_path / "once.mid"
    once.save(once_path)

    reloaded = mido.MidiFile(once_path)
    twice = _apply_two_ornament_techniques(registry, reloaded)

    assert _midi_bytes(twice) == once_path.read_bytes()
    assert _note_tuples(twice) == _note_tuples(once)


def test_every_registered_technique_exists_in_manual_index():
    idx = build_index(MANUALS_DIR)

    validate_registry_against_index(idx)
    for canonical in SUPPORTED_TECHNIQUES:
        assert idx.get(canonical) is not None


def _note_tuples(mid: mido.MidiFile) -> list[tuple[int, int, int, int, int, int]]:
    notes: list[tuple[int, int, int, int, int, int]] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault((msg.channel, msg.note), []).append(
                    (tick, msg.velocity)
                )
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = open_notes.get((msg.channel, msg.note))
                if not stack:
                    continue
                start_tick, velocity = stack.pop(0)
                notes.append((
                    track_index,
                    msg.channel,
                    msg.note,
                    start_tick,
                    tick,
                    velocity,
                ))
    return notes


def _ornament_registry() -> TechniqueRegistry:
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply_drums(mid: mido.MidiFile) -> mido.MidiFile:
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=38,
            velocity=32,
            start_tick=120,
            end_tick=180,
        )
        return mid

    @registry.register("bass.ghost_notes", "technique")
    def apply_bass(mid: mido.MidiFile) -> mido.MidiFile:
        _insert_note(
            mid.tracks[2],
            channel=0,
            note=35,
            velocity=28,
            start_tick=240,
            end_tick=300,
        )
        return mid

    return registry


def _apply_two_ornament_techniques(
    registry: TechniqueRegistry,
    mid: mido.MidiFile,
) -> mido.MidiFile:
    mid = registry.apply("drums.ghost_notes", mid)
    return registry.apply("bass.ghost_notes", mid)


def _insert_note(
    track: mido.MidiTrack,
    *,
    channel: int,
    note: int,
    velocity: int,
    start_tick: int,
    end_tick: int,
) -> None:
    absolute: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    tick = 0
    for msg in track:
        tick += msg.time
        absolute.append((tick, msg))
    absolute.append((
        start_tick,
        mido.Message("note_on", channel=channel, note=note, velocity=velocity),
    ))
    absolute.append((
        end_tick,
        mido.Message("note_off", channel=channel, note=note, velocity=0),
    ))

    rebuilt = mido.MidiTrack()
    previous_tick = 0
    for absolute_tick, msg in sorted(absolute, key=lambda item: item[0]):
        rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
        previous_tick = absolute_tick
    track[:] = rebuilt


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


def _midi_with_single_note() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.Message(
        "note_on", channel=0, note=40, velocity=96, time=0
    ))
    track.append(mido.Message(
        "note_off", channel=0, note=40, velocity=0, time=480
    ))
    mid.tracks.extend([meta, track])
    return mid


def _midi_with_drums_and_bass() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))

    drums = mido.MidiTrack()
    drums.append(mido.MetaMessage("track_name", name="Drums", time=0))
    drums.append(mido.Message(
        "note_on", channel=9, note=60, velocity=96, time=0
    ))
    drums.append(mido.Message(
        "note_off", channel=9, note=60, velocity=0, time=480
    ))

    bass = mido.MidiTrack()
    bass.append(mido.MetaMessage("track_name", name="Bass", time=0))
    bass.append(mido.Message(
        "note_on", channel=0, note=40, velocity=96, time=0
    ))
    bass.append(mido.Message(
        "note_off", channel=0, note=40, velocity=0, time=480
    ))

    mid.tracks.extend([meta, drums, bass])
    return mid


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
