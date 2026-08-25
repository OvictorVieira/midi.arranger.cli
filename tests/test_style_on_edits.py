"""Estilo aplicado sobre tracks de `plan.edits` (US-002).

O motor de tecnicas so alcanca a bateria real do usuario quando `style.<familia>`
roda sobre a track vinda da origem — nao apenas sobre elemento gerado. Estes
testes cobrem esse caminho ponta-a-ponta pelo `render`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import mido
import pretty_midi

from tools.plan import (
    ArrangementPlan,
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    SourceMidi,
    StyleTechnique,
)
from tools.render import render


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_flat_drum_source(tmp_path: Path) -> Path:
    """MIDI 8 compassos 4/4 a 120bpm com bateria chapada em 127 e piano de
    apoio. Simula o caso real: levada de rock exportada com velocidade unica,
    que quebra na hierarquia de acento e nao carrega ghost note nenhuma."""

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
            pitch = 36 if beat in (0, 2) else 38
            drums.notes.append(pretty_midi.Note(
                velocity=127, pitch=pitch,
                start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.extend([piano, bass, drums])
    dest = tmp_path / "flat.mid"
    pm.write(str(dest))
    return dest


def _plan_with_drum_edit(
    src: Path,
    *,
    profile: str,
    techniques: list[str],
) -> ArrangementPlan:
    style_techniques = [StyleTechnique(name=name) for name in techniques]
    plan = ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path=str(src), sha256=_sha256_bytes(src)),
        route="cinematica_emocional",
        sections=[PlanSection(
            label="MAIN", kind="chorus", start_bar=0, end_bar=8,
            source="marker", protagonist="drum_groove",
            energy={
                "densidade": 5, "impacto": 5, "largura": 5,
                "altura": 5, "instabilidade": 3,
            },
        )],
        elements=[Element(
            id="pad_main",
            role="pad",
            sections=["MAIN"],
            register=[48, 71],
            layers=1,
            sync_role="sustain_through",
            articulation="sustained",
            harmony="follow_chords",
            dynamics={"shape": "hold"},
            instrument={
                "plugin": "Omnisphere", "preset": "Desert Wind", "verified": True,
            },
            rationale="Sustain que amarra o arranjo.",
        )],
        edits=[PlanEdit(track="Drums", profile=profile, intensity=0.0)],
        style={
            "drums": FamilyStyle(
                reference="Drummer research",
                researched_at="2026-08-24",
                sources=["https://example.test/drums"],
                confidence="high",
                techniques=style_techniques,
                parameters={},
            ),
        },
    )
    return plan


def _drum_note_events(mid_path: Path) -> list[tuple[int, int, int, int]]:
    mid = mido.MidiFile(str(mid_path))
    events: list[tuple[int, int, int, int]] = []
    for tr in mid.tracks:
        name = None
        for msg in tr:
            if msg.is_meta and msg.type == "track_name":
                name = msg.name
                break
        if name != "Drums":
            continue
        abs_tick = 0
        pending: dict[tuple[int, int], tuple[int, int]] = {}
        for msg in tr:
            abs_tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                pending[(msg.channel, msg.note)] = (abs_tick, msg.velocity)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if key in pending:
                    start, vel = pending.pop(key)
                    events.append((start, abs_tick, msg.note, vel))
    return events


def _track_messages(mid_path: Path, name: str) -> list[str]:
    mid = mido.MidiFile(str(mid_path))
    for tr in mid.tracks:
        for msg in tr:
            if msg.is_meta and msg.type == "track_name" and msg.name == name:
                return [str(m) for m in tr]
    return []


def test_accent_hierarchy_redistributes_velocity_on_source_track(tmp_path):
    src = _build_flat_drum_source(tmp_path)
    plan = _plan_with_drum_edit(
        src, profile="drums", techniques=["drums.accent_hierarchy"],
    )
    out = tmp_path / "out.mid"
    render(plan, out)

    out_notes = _drum_note_events(out)
    src_notes = _drum_note_events(src)
    assert len(out_notes) == len(src_notes), (
        "accent_hierarchy e humanize: nao pode acrescentar nem remover nota"
    )
    velocities = [vel for *_, vel in out_notes]
    assert max(velocities) < 127, "127 chapado tem que sair"
    assert max(velocities) <= 115, "hard_ceiling do manual e 115"
    kicks = [vel for _s, _e, pitch, vel in out_notes if pitch == 36]
    snares = [vel for _s, _e, pitch, vel in out_notes if pitch == 38]
    assert kicks and snares
    # accent_hierarchy: snare em backbeat vira acento (105-120, alvo 112),
    # kick em beat 1/3 fica na camada normal (80-100).
    assert min(snares) > max(kicks), (
        "snare backbeat tem que ficar acima do kick on-beat"
    )


def test_ghost_notes_adds_ornaments_between_backbeats_on_source_track(tmp_path):
    src = _build_flat_drum_source(tmp_path)
    plan = _plan_with_drum_edit(
        src, profile="drums",
        techniques=["drums.accent_hierarchy", "drums.ghost_notes"],
    )
    out = tmp_path / "out.mid"
    render(plan, out)

    src_notes = _drum_note_events(src)
    out_notes = _drum_note_events(out)
    assert len(out_notes) > len(src_notes), (
        "ghost_notes precisa acrescentar ornamentos entre backbeats"
    )
    ghosts = [
        n for n in out_notes
        if n[2] == 38 and 20 <= n[3] <= 45
    ]
    assert ghosts, "esperado pelo menos um ghost note (velocity 20-45)"


def test_neighbor_track_stays_byte_identical_when_drums_gets_style(tmp_path):
    src = _build_flat_drum_source(tmp_path)
    plan = _plan_with_drum_edit(
        src, profile="drums",
        techniques=["drums.accent_hierarchy", "drums.ghost_notes"],
    )
    out = tmp_path / "out.mid"
    render(plan, out)

    for other in ("Piano", "Bass"):
        assert _track_messages(src, other) == _track_messages(out, other), (
            f"{other} nao foi declarada em edits — tem que sair byte-identica"
        )


def test_generic_profile_does_not_receive_style_technique(tmp_path):
    """AC: `profile: generic` nao tem familia e nao recebe tecnica. Sem essa
    guarda, uma edit generic sobre a Drums cairia na familia drums e o motor
    trocaria as velocidades da bateria em silencio."""

    src = _build_flat_drum_source(tmp_path)
    plan = _plan_with_drum_edit(
        src, profile="generic", techniques=["drums.accent_hierarchy"],
    )
    out = tmp_path / "out.mid"
    render(plan, out)

    src_notes = _drum_note_events(src)
    out_notes = _drum_note_events(out)
    assert len(src_notes) == len(out_notes)
    assert [(n[2], n[3]) for n in src_notes] == [(n[2], n[3]) for n in out_notes]


def test_style_on_edits_is_deterministic_byte_for_byte(tmp_path):
    src = _build_flat_drum_source(tmp_path)
    plan_a = _plan_with_drum_edit(
        src, profile="drums",
        techniques=["drums.accent_hierarchy", "drums.ghost_notes"],
    )
    plan_b = _plan_with_drum_edit(
        src, profile="drums",
        techniques=["drums.accent_hierarchy", "drums.ghost_notes"],
    )
    out_a = tmp_path / "a.mid"
    out_b = tmp_path / "b.mid"
    render(plan_a, out_a)
    render(plan_b, out_b)
    assert out_a.read_bytes() == out_b.read_bytes()
