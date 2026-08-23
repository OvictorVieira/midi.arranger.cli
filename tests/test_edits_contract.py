"""Regressao de fechamento para as garantias mecanicas de edits."""

from __future__ import annotations

import hashlib
from pathlib import Path

import mido

from tools.plan import ArrangementPlan, PlanEdit, PlanSection, SourceMidi
from tools.render import render

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _track_name(track: mido.MidiTrack) -> str:
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            return str(msg.name)
    return ""


def _track_messages(path: Path, track_name: str) -> list[str]:
    mid = mido.MidiFile(str(path))
    for track in mid.tracks:
        if _track_name(track) == track_name:
            return [str(msg) for msg in track]
    raise AssertionError(f"track {track_name!r} not found in {path}")


def _note_on_identity(path: Path, track_name: str) -> list[tuple[int, int]]:
    mid = mido.MidiFile(str(path))
    result: list[tuple[int, int]] = []
    for track in mid.tracks:
        if _track_name(track) != track_name:
            continue
        for msg in track:
            if (
                not msg.is_meta
                and msg.type == "note_on"
                and msg.velocity > 0
            ):
                result.append((msg.channel, msg.note))
        return result
    raise AssertionError(f"track {track_name!r} not found in {path}")


def _plan_with_rhythm_guitar_edit(source: Path) -> ArrangementPlan:
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
        edits=[PlanEdit(track="Rhythm Guitar", profile="generic", intensity=1.0)],
    )


def test_fixture_edit_contract_keeps_core_guarantees(tmp_path):
    src = FIXTURES_DIR / "ancora_arranjo_atual.mid"
    source_hash_before = _sha256_bytes(src)
    plan = _plan_with_rhythm_guitar_edit(src)
    out1 = tmp_path / "arranged-1.mid"
    out2 = tmp_path / "arranged-2.mid"

    report1 = render(plan, out1)
    report2 = render(_plan_with_rhythm_guitar_edit(src), out2)

    assert report1.edits[0].notes_touched == 1428
    assert report2.edits[0].notes_touched == 1428
    assert _sha256_bytes(src) == source_hash_before
    assert out1.read_bytes() == out2.read_bytes()
    assert _track_messages(src, "Lead Guitar") == _track_messages(out1, "Lead Guitar")
    assert (
        _note_on_identity(src, "Rhythm Guitar")
        == _note_on_identity(out1, "Rhythm Guitar")
    )
