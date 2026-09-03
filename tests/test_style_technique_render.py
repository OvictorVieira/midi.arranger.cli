"""`tools.render` honra `StyleTechnique.parameters`/`intensity` (issue #72).

Prova que os novos campos nao sao "parametro mentiroso": o aplicador de
`drums.ghost_notes` realmente le `context.parameters["velocity"]`, e quando
o nivel de tecnica e o nivel legado de familia declaram o mesmo nome, o
valor da tecnica e o que efetivamente sai no MIDI renderizado.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mido
import pretty_midi

from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
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
    """Mesma fixture de `tests/test_style_on_edits.py`: bateria chapada em
    127, sem ghost note nenhuma — `drums.ghost_notes` tem material para
    acrescentar."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
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
            pitch = 36 if beat in (0, 2) else 38
            drums.notes.append(pretty_midi.Note(
                velocity=127, pitch=pitch,
                start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.extend([piano, drums])
    dest = tmp_path / "flat.mid"
    pm.write(str(dest))
    return dest


def _plan_with_ghost_notes(
    src: Path,
    *,
    technique: StyleTechnique,
    family_parameters: dict[str, object],
) -> ArrangementPlan:
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
        edits=[PlanEdit(track="Drums", profile="drums", intensity=0.0)],
        style={
            "drums": FamilyStyle(
                reference="Drummer research",
                researched_at="2026-08-24",
                sources=["https://example.test/drums"],
                confidence="high",
                techniques=[technique],
                parameters=family_parameters,
            ),
        },
    )
    return plan


def _attach_authorized_brief(plan: ArrangementPlan, tmp_path: Path) -> None:
    names = [t.name for t in plan.style["drums"].techniques]
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": names}}}),
        encoding="utf-8",
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))


def _ghost_note_velocities(mid_path: Path) -> list[int]:
    """Velocity de cada nota de caixa (pitch 38) ADICIONADA pela tecnica —
    a origem so tem pitch 36/38 nos tempos fortes (0, 2); ghost note cai em
    subdivisao fraca e portanto e facil de isolar pelo pitch 38 sozinho
    (a origem ja usa 38 nos tempos 1 e 3, mas com velocity 127 — o filtro
    de faixa abaixo isola so o que a tecnica escreveu)."""
    mid = mido.MidiFile(str(mid_path))
    velocities: list[int] = []
    for tr in mid.tracks:
        name = None
        for msg in tr:
            if msg.is_meta and msg.type == "track_name":
                name = msg.name
                break
        if name != "Drums":
            continue
        for msg in tr:
            if msg.type == "note_on" and msg.note == 38 and 0 < msg.velocity < 127:
                velocities.append(msg.velocity)
    return velocities


def test_technique_level_velocity_parameter_is_honored_by_the_engine(tmp_path: Path):
    """`drums.ghost_notes` declarado SEM `style.drums.parameters` nenhum usa
    a faixa do manual (20-45, Toontrack). Com `parameters` NO NIVEL DA
    TECNICA declarando uma faixa diferente (mas ainda dentro do range do
    manual), o motor tem que honrar a faixa da tecnica — do contrario o
    campo seria aceito pelo schema e ignorado pelo motor."""
    src = _build_flat_drum_source(tmp_path)
    plan = _plan_with_ghost_notes(
        src,
        technique=StyleTechnique(
            name="drums.ghost_notes", parameters={"velocity": [40, 45]},
        ),
        family_parameters={},
    )
    _attach_authorized_brief(plan, tmp_path)
    out = tmp_path / "out.mid"
    render(plan, out)

    velocities = _ghost_note_velocities(out)
    assert velocities, "esperado ao menos um ghost note"
    assert all(40 <= v <= 45 for v in velocities), velocities


def test_technique_level_parameter_wins_over_conflicting_family_level_parameter(
    tmp_path: Path,
):
    """`style.drums.parameters.velocity` (legado, [20, 25]) e
    `techniques[0].parameters.velocity` (tecnica, [40, 45]) conflitam no
    mesmo nome. `plan.validate()` ja prova o warning em
    `tests/test_style_technique_contract.py`; aqui provamos que o RENDER
    de fato aplica a precedencia: o velocity que sai no MIDI e o da
    tecnica, nunca o legado da familia."""
    src = _build_flat_drum_source(tmp_path)
    plan = _plan_with_ghost_notes(
        src,
        technique=StyleTechnique(
            name="drums.ghost_notes", parameters={"velocity": [40, 45]},
        ),
        family_parameters={"velocity": [20, 25]},
    )
    _attach_authorized_brief(plan, tmp_path)
    out = tmp_path / "out.mid"
    render(plan, out)

    velocities = _ghost_note_velocities(out)
    assert velocities, "esperado ao menos um ghost note"
    assert all(40 <= v <= 45 for v in velocities), (
        "technique-level parameters devem vencer o legado de familia", velocities,
    )
    assert not any(20 <= v <= 25 for v in velocities)
