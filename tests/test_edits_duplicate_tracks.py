"""Regressao: edits por nome atingem todas as tracks homonimas."""

from __future__ import annotations

import hashlib
from pathlib import Path

import mido
import pytest

from tools.plan import (
    ArrangementPlan,
    PlanEdit,
    PlanSection,
    PlanValidationError,
    SourceMidi,
    validate,
    validate_edits_against_midi,
)
from tools.render import format_render_report, render


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_duplicate_name_source(tmp_path: Path) -> Path:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120)))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta)

    for channel, pitch in ((0, 60), (1, 67)):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage(
            "track_name",
            name="Steinway Grand Piano",
            time=0,
        ))
        track.append(mido.Message(
            "program_change",
            channel=channel,
            program=0,
            time=0,
        ))
        for _ in range(2):
            track.append(mido.Message(
                "note_on",
                channel=channel,
                note=pitch,
                velocity=90,
                time=0,
            ))
            track.append(mido.Message(
                "note_off",
                channel=channel,
                note=pitch,
                velocity=0,
                time=480,
            ))
        track.append(mido.MetaMessage("end_of_track", time=0))
        mid.tracks.append(track)

    untouched = mido.MidiTrack()
    untouched.append(mido.MetaMessage("track_name", name="Wide Suitcase", time=0))
    untouched.append(mido.Message("program_change", channel=2, program=4, time=0))
    untouched.append(mido.Message(
        "note_on",
        channel=2,
        note=72,
        velocity=80,
        time=0,
    ))
    untouched.append(mido.Message(
        "note_off",
        channel=2,
        note=72,
        velocity=0,
        time=960,
    ))
    untouched.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(untouched)

    path = tmp_path / "duplicate_names.mid"
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
                end_bar=2,
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


def _track_name(track: mido.MidiTrack) -> str:
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            return str(msg.name)
    return ""


def _track_messages_by_name(path: Path, name: str) -> list[list[str]]:
    mid = mido.MidiFile(str(path))
    return [
        [str(msg) for msg in track]
        for track in mid.tracks
        if _track_name(track) == name
    ]


def _note_on_count(path: Path, name: str) -> int:
    total = 0
    mid = mido.MidiFile(str(path))
    for track in mid.tracks:
        if _track_name(track) != name:
            continue
        for msg in track:
            if (
                not msg.is_meta
                and msg.type == "note_on"
                and msg.velocity > 0
            ):
                total += 1
    return total


def test_edit_by_name_touches_all_homonymous_tracks_and_reports_count(tmp_path):
    src = _build_duplicate_name_source(tmp_path)
    plan = _plan_with_edit(src, "Steinway Grand Piano")
    validate(plan)
    validate_edits_against_midi(
        plan,
        ["Steinway Grand Piano", "Steinway Grand Piano", "Wide Suitcase"],
    )

    out = tmp_path / "out.mid"
    report = render(plan, out)

    assert len(report.edits) == 1
    edit_report = report.edits[0]
    assert edit_report.track == "Steinway Grand Piano"
    assert edit_report.tracks_matched == 2
    assert edit_report.notes_touched == _note_on_count(src, "Steinway Grand Piano")

    src_pianos = _track_messages_by_name(src, "Steinway Grand Piano")
    out_pianos = _track_messages_by_name(out, "Steinway Grand Piano")
    assert len(src_pianos) == len(out_pianos) == 2
    assert all(before != after for before, after in zip(src_pianos, out_pianos, strict=True))

    assert (
        _track_messages_by_name(src, "Wide Suitcase")
        == _track_messages_by_name(out, "Wide Suitcase")
    )

    text = format_render_report(report)
    assert "across 2 tracks" in text


def test_duplicate_plan_edits_for_same_name_still_rejected(tmp_path):
    src = _build_duplicate_name_source(tmp_path)
    plan = _plan_with_edit(src, "Steinway Grand Piano")
    plan.edits.append(PlanEdit(
        track="Steinway Grand Piano",
        profile="generic",
        intensity=0.5,
    ))

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "edits[1].track"
