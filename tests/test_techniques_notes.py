"""Testes da classificacao estrutural/ornamental do motor de tecnicas."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido
import pretty_midi
import pytest

from tools.techniques import (
    UnclassifiedNoteError,
    derive_note_classification,
    generator_note_expectations,
    source_note_expectations,
    technique_note_expectations,
)


@dataclass(frozen=True)
class NoteLike:
    pitch: int
    velocity: int
    start_s: float
    end_s: float


def test_source_notes_are_structural():
    source = _source_midi()

    notes = source_note_expectations(source)

    assert [(n.origin, n.role, n.signature.track_index) for n in notes] == [
        ("source", "structural", 1),
    ]


def test_palette_generated_notes_are_structural():
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    generated = generator_note_expectations(
        [NoteLike(pitch=64, velocity=82, start_s=0.25, end_s=0.5)],
        pm,
        track_index=2,
        track_name="Generated Bass",
        channel=0,
    )

    assert [(n.origin, n.role) for n in generated] == [
        ("generator", "structural"),
    ]
    assert generated[0].signature.start_tick == 240
    assert generated[0].signature.end_tick == 480


def test_technique_notes_are_ornamental():
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    ornaments = technique_note_expectations(
        [NoteLike(pitch=38, velocity=32, start_s=0.125, end_s=0.25)],
        pm,
        track_index=2,
        track_name="Bass",
        channel=0,
    )

    assert [(n.origin, n.role) for n in ornaments] == [
        ("technique", "ornamental"),
    ]


def test_derivation_survives_midi_round_trip_without_metadata(tmp_path: Path):
    source_path = tmp_path / "source.mid"
    final_path = tmp_path / "final.mid"
    source = _source_midi()
    source.save(source_path)

    pm = pretty_midi.PrettyMIDI(str(source_path))
    generated_note = NoteLike(pitch=64, velocity=82, start_s=0.25, end_s=0.5)
    technique_note = NoteLike(pitch=38, velocity=32, start_s=0.125, end_s=0.25)
    final_mid = mido.MidiFile(str(source_path))
    final_mid.tracks.append(_track_with_note(
        name="Generated Bass",
        note=generated_note,
        channel=0,
        pm=pm,
    ))
    final_mid.tracks.append(_track_with_note(
        name="Bass Ornaments",
        note=technique_note,
        channel=0,
        pm=pm,
    ))

    generated = generator_note_expectations(
        [generated_note],
        pm,
        track_index=2,
        track_name="Generated Bass",
        channel=0,
    )
    ornaments = technique_note_expectations(
        [technique_note],
        pm,
        track_index=3,
        track_name="Bass Ornaments",
        channel=0,
    )
    before_save = derive_note_classification(
        final_mid,
        source_mid=mido.MidiFile(str(source_path)),
        generated_notes=generated,
        technique_notes=ornaments,
    )

    final_mid.save(final_path)
    after_reload = derive_note_classification(
        mido.MidiFile(str(final_path)),
        source_mid=mido.MidiFile(str(source_path)),
        generated_notes=generated,
        technique_notes=ornaments,
    )

    assert [
        (note.origin, note.role, note.signature, note.track_name)
        for note in after_reload
    ] == [
        (note.origin, note.role, note.signature, note.track_name)
        for note in before_save
    ]
    assert [(note.origin, note.role) for note in after_reload] == [
        ("source", "structural"),
        ("generator", "structural"),
        ("technique", "ornamental"),
    ]


def test_unexpected_final_note_fails_with_actionable_error():
    source = _source_midi()
    final_mid = _source_midi()
    final_mid.tracks.append(_raw_mido_track(
        name="Unexpected",
        note=72,
        velocity=90,
        start_tick=0,
        end_tick=120,
        channel=0,
    ))

    with pytest.raises(UnclassifiedNoteError, match="sem classificacao derivavel"):
        derive_note_classification(final_mid, source_mid=source)


def _source_midi() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    bass = _raw_mido_track(
        name="Bass",
        note=40,
        velocity=96,
        start_tick=0,
        end_tick=480,
        channel=0,
    )
    mid.tracks.extend([meta, bass])
    return mid


def _track_with_note(
    *,
    name: str,
    note: NoteLike,
    channel: int,
    pm: pretty_midi.PrettyMIDI,
) -> mido.MidiTrack:
    start_tick = int(round(pm.time_to_tick(note.start_s)))
    end_tick = int(round(pm.time_to_tick(note.end_s)))
    return _raw_mido_track(
        name=name,
        note=note.pitch,
        velocity=note.velocity,
        start_tick=start_tick,
        end_tick=end_tick,
        channel=channel,
    )


def _raw_mido_track(
    *,
    name: str,
    note: int,
    velocity: int,
    start_tick: int,
    end_tick: int,
    channel: int,
) -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.Message(
        "note_on",
        channel=channel,
        note=note,
        velocity=velocity,
        time=start_tick,
    ))
    track.append(mido.Message(
        "note_off",
        channel=channel,
        note=note,
        velocity=0,
        time=end_tick - start_tick,
    ))
    return track
