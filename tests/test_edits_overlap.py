"""Regressao: edits precisam parear notas sobrepostas por pilha.

US-001 congela o bug antes do conserto: quando duas notas da mesma altura
e canal se sobrepoem, o motor atual sobrescreve o note_on aberto anterior
e deixa parte das notas sem humanizacao.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import mido
import pretty_midi

from tools.edits import PROFILE_PARAMS, apply_edit
from tools.plan import (
    ArrangementPlan,
    PlanEdit,
    PlanSection,
    SourceMidi,
)
from tools.render import render

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _track_name(track: mido.MidiTrack) -> str:
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            return str(msg.name)
    return ""


def _note_on_and_overlap_counts(
    path: Path,
    track_name: str,
) -> tuple[int, int]:
    """Conta note_on e sobreposicoes usando pilha por (canal, altura)."""
    mid = mido.MidiFile(str(path))
    for track in mid.tracks:
        if _track_name(track) != track_name:
            continue
        total = 0
        overlaps = 0
        open_notes: dict[tuple[int, int], int] = {}
        for msg in track:
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                if open_notes.get(key, 0) > 0:
                    overlaps += 1
                open_notes[key] = open_notes.get(key, 0) + 1
                total += 1
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if open_notes.get(key, 0) > 0:
                    open_notes[key] -= 1
        return total, overlaps
    raise AssertionError(f"track {track_name!r} not found in {path}")


def _build_overlapping_source(tmp_path: Path) -> Path:
    """MIDI sintetico com duas notas C3 sobrepostas no mesmo canal."""
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120)))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta)

    bass = mido.MidiTrack()
    bass.append(mido.MetaMessage("track_name", name="Bass", time=0))
    bass.append(mido.Message("program_change", channel=0, program=32, time=0))
    bass.append(mido.Message("note_on", channel=0, note=48, velocity=90, time=0))
    bass.append(
        mido.Message("note_on", channel=0, note=48, velocity=80, time=240),
    )
    bass.append(
        mido.Message("note_off", channel=0, note=48, velocity=0, time=240),
    )
    bass.append(
        mido.Message("note_off", channel=0, note=48, velocity=0, time=240),
    )
    bass.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(bass)

    path = tmp_path / "overlap.mid"
    mid.save(path)
    return path


def _plan_with_edit(source: Path, track: str) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path=str(source), sha256=_sha256_bytes(source)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN",
                kind="chorus",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={
                    "densidade": 5,
                    "impacto": 5,
                    "largura": 5,
                    "altura": 5,
                    "instabilidade": 3,
                },
            ),
        ],
        elements=[],
        edits=[PlanEdit(track=track, profile="generic", intensity=1.0)],
    )


def test_apply_edit_touches_overlapping_notes_with_same_pitch(tmp_path):
    src = _build_overlapping_source(tmp_path)
    assert _note_on_and_overlap_counts(src, "Bass") == (2, 1)

    mid = mido.MidiFile(str(src))
    pm = pretty_midi.PrettyMIDI(str(src))
    bass = next(track for track in mid.tracks if _track_name(track) == "Bass")

    touched, _mean_offset = apply_edit(
        bass,
        PROFILE_PARAMS["generic"],
        intensity=1.0,
        seed=1,
        pm=pm,
    )

    assert touched == 2


def test_rhythm_guitar_edit_touches_every_note_in_ancora_fixture(tmp_path):
    src = FIXTURES_DIR / "ancora_arranjo_atual.mid"
    assert _note_on_and_overlap_counts(src, "Rhythm Guitar") == (1428, 320)

    report = render(
        _plan_with_edit(src, "Rhythm Guitar"),
        tmp_path / "ancora_arranged.mid",
    )

    assert len(report.edits) == 1
    assert report.edits[0].notes_touched == 1428
