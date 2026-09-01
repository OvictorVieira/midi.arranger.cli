"""Carimbo de plugin/preset/role/verified/tecnicas em meta-text (US-003).

Testa: elementos gerados carregam nome (track_name) + carimbo (meta text);
tracks de `plan.edits` carregam carimbo com tecnicas aplicadas e sugestao
opcional; sugestao nunca altera nota; plugin proibido na sugestao e recusado;
track intocada NAO recebe carimbo.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mido
import pretty_midi
import pytest

from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    PlanValidationError,
    SourceMidi,
    StyleTechnique,
    from_dict,
    to_dict,
    validate,
)
from tools.render import STAMP_PREFIX, render


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attach_authorized_brief(plan: ArrangementPlan, tmp_path: Path) -> None:
    authorized: dict[str, dict[str, list[str]]] = {}
    if isinstance(plan.style, dict):
        for family, entry in plan.style.items():
            names = [t.name for t in entry.techniques if isinstance(t, StyleTechnique)]
            if names:
                authorized[family] = {"authorized_techniques": names}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps({"style": authorized}, indent=2), encoding="utf-8")
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))


def _build_source(tmp_path: Path) -> Path:
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
                velocity=100, pitch=pitch,
                start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.extend([piano, bass, drums])
    dest = tmp_path / "src.mid"
    pm.write(str(dest))
    return dest


def _base_plan(src: Path) -> ArrangementPlan:
    return ArrangementPlan(
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
    )


def _iter_text_meta(mid_path: Path, track_name: str) -> list[str]:
    mid = mido.MidiFile(str(mid_path))
    for tr in mid.tracks:
        name = None
        texts: list[str] = []
        for msg in tr:
            if msg.is_meta and msg.type == "track_name":
                name = msg.name
            elif msg.is_meta and msg.type == "text":
                texts.append(msg.text)
        if name == track_name:
            return texts
    return []


def _track_bytes(mid_path: Path, track_name: str) -> bytes:
    mid = mido.MidiFile(str(mid_path))
    for tr in mid.tracks:
        for msg in tr:
            if msg.is_meta and msg.type == "track_name" and msg.name == track_name:
                return bytes(tr[0].bytes() if False else b"") + b"|".join(
                    bytes(m.bytes()) for m in tr
                )
    return b""


def test_generated_element_track_carries_both_name_and_stamp(tmp_path):
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    out = tmp_path / "out.mid"
    render(plan, out)

    texts = _iter_text_meta(out, "pad_main - Omnisphere / Desert Wind *")
    assert texts, "elemento gerado precisa carregar carimbo em meta text"
    stamp = texts[0]
    assert stamp.startswith(STAMP_PREFIX)
    assert "role=pad" in stamp
    assert "plugin=Omnisphere" in stamp
    assert "preset=Desert Wind" in stamp
    assert "verified=true" in stamp


def test_edit_track_stamp_reflects_tool_as_plugin(tmp_path):
    """Achado do Codex na PR: `edit.tool` determinava a receita de tecnica
    de fato aplicada (ex.: keyswitch do MODO BASS gravado nas notas), mas o
    carimbo continuava com `plugin=None` — a track carregava dado
    estrutural amarrado a uma ferramenta que o carimbo nao mencionava."""
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    plan.edits = [PlanEdit(
        track="Bass", profile="bass", intensity=0.5, tool="MODO Bass",
    )]
    plan.style = {
        "bass": FamilyStyle(
            reference="Baixo com fingers", researched_at="2026-08-24",
            sources=["https://example.test/bass"], confidence="high",
            techniques=[
                StyleTechnique(name="bass.attack_style", style="dedo"),
            ],
            parameters={},
        ),
    }
    _attach_authorized_brief(plan, tmp_path)
    out = tmp_path / "out.mid"
    render(plan, out)

    texts = _iter_text_meta(out, "Bass")
    assert len(texts) == 1
    stamp = texts[0]
    assert "plugin=MODO Bass" in stamp
    assert "verified=false" in stamp


def test_edit_track_stamp_includes_applied_techniques(tmp_path):
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    plan.edits = [PlanEdit(track="Drums", profile="drums", intensity=0.0)]
    plan.style = {
        "drums": FamilyStyle(
            reference="Drummer", researched_at="2026-08-24",
            sources=["https://example.test/drums"], confidence="high",
            techniques=[
                StyleTechnique(name="drums.ghost_notes"),
            ],
            parameters={},
        ),
    }
    _attach_authorized_brief(plan, tmp_path)
    out = tmp_path / "out.mid"
    render(plan, out)

    texts = _iter_text_meta(out, "Drums")
    assert len(texts) == 1, "edit track precisa carregar exatamente um carimbo"
    stamp = texts[0]
    assert stamp.startswith(STAMP_PREFIX)
    assert "role=drums" in stamp
    assert "techniques=[drums.ghost_notes]" in stamp
    # sem sugestao: nao aparece
    assert "suggested_plugin" not in stamp


def _drum_notes(mid_path: Path) -> list[tuple[int, int, int, int]]:
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


def test_suggested_instrument_stamps_and_leaves_notes_intact(tmp_path):
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    plan.edits = [PlanEdit(
        track="Drums", profile="drums", intensity=0.0,
        suggested_instrument={
            "plugin": "Superior Drummer",
            "preset": "Metal Foundry",
            "verified": False,
        },
    )]
    out = tmp_path / "out.mid"
    render(plan, out)

    texts = _iter_text_meta(out, "Drums")
    assert len(texts) == 1
    stamp = texts[0]
    assert "suggested_plugin=Superior Drummer" in stamp
    assert "suggested_preset=Metal Foundry" in stamp
    assert "suggested_verified=false" in stamp
    # sem style.drums.techniques: sem tecnicas no carimbo
    assert "techniques=" not in stamp
    # conteudo musical intacto: mesmas notas do source
    src_notes = _drum_notes(src)
    out_notes = _drum_notes(out)
    assert src_notes == out_notes, (
        "sugestao e so metadado: nao pode alterar nota nenhuma"
    )


def test_suggested_instrument_rejects_forbidden_plugin(tmp_path):
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    plan.edits = [PlanEdit(
        track="Drums", profile="drums", intensity=0.0,
        suggested_instrument={
            "plugin": "Trigger 2",
            "preset": "Whatever",
            "verified": False,
        },
    )]
    with pytest.raises(PlanValidationError, match="forbidden by FR-24"):
        validate(plan)


def test_untouched_source_track_receives_no_stamp(tmp_path):
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    plan.edits = [PlanEdit(track="Drums", profile="drums", intensity=0.0)]
    plan.style = {
        "drums": FamilyStyle(
            reference="Drummer", researched_at="2026-08-24",
            sources=["https://example.test/drums"], confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    _attach_authorized_brief(plan, tmp_path)
    out = tmp_path / "out.mid"
    render(plan, out)

    # Piano e Bass NAO estao em plan.edits → tem que sair byte-identicos
    def _track_msgs(path: Path, name: str) -> list[str]:
        mid = mido.MidiFile(str(path))
        for tr in mid.tracks:
            for msg in tr:
                if msg.is_meta and msg.type == "track_name" and msg.name == name:
                    return [str(m) for m in tr]
        return []

    for other in ("Piano", "Bass"):
        assert _track_msgs(src, other) == _track_msgs(out, other), (
            f"{other} nao foi declarada em edits — tem que sair byte-identica"
        )
        assert _iter_text_meta(out, other) == []


def test_stamp_is_deterministic_byte_for_byte(tmp_path):
    src = _build_source(tmp_path)

    def _build_plan() -> ArrangementPlan:
        plan = _base_plan(src)
        plan.edits = [PlanEdit(
            track="Drums", profile="drums", intensity=0.0,
            suggested_instrument={
                "plugin": "Superior Drummer",
                "preset": "Metal Foundry",
                "verified": False,
            },
        )]
        plan.style = {
            "drums": FamilyStyle(
                reference="Drummer", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes")],
                parameters={},
            ),
        }
        _attach_authorized_brief(plan, tmp_path)
        return plan

    out_a = tmp_path / "a.mid"
    out_b = tmp_path / "b.mid"
    render(_build_plan(), out_a)
    render(_build_plan(), out_b)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_suggested_instrument_survives_plan_roundtrip(tmp_path):
    src = _build_source(tmp_path)
    plan = _base_plan(src)
    plan.edits = [PlanEdit(
        track="Drums", profile="drums", intensity=0.0,
        suggested_instrument={
            "plugin": "Superior Drummer",
            "preset": "Metal Foundry",
            "verified": True,
        },
    )]
    data = to_dict(plan)
    reloaded = from_dict(data)
    assert reloaded.edits[0].suggested_instrument == {
        "plugin": "Superior Drummer",
        "preset": "Metal Foundry",
        "verified": True,
    }
