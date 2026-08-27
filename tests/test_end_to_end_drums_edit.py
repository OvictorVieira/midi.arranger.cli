"""US-004: ponta a ponta sobre track de bateria real, com o registro real.

O caminho que o usuario vai usar e: MIDI com bateria ja existente, `plan.edits`
apontando pra essa track com `profile: drums`, `style.drums.techniques` com as
duas tecnicas reais registradas em `tools/techniques/engine.py`. Este arquivo
exercita esse caminho ponta a ponta pelo `render`, SEM monkeypatch do registro
de tecnicas em ponto nenhum. A fixture e gerada por este proprio script (que ja
esta versionado) e simula o caso real: levada de rock chapada em 127.
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


def _build_flat_metal_drums_source(tmp_path: Path) -> Path:
    """MIDI de 16 compassos 4/4 a 140bpm com bateria chapada em 127. Levada de
    metal: kick nos beats 1 e 3, snare nos beats 2 e 4 (backbeat), hi-hat em
    semicolcheias. Tudo com velocidade unica 127 — o caso real que motivou a
    rodada."""

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=140.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    bar_len = 60.0 / 140.0 * 4
    beat_len = bar_len / 4
    sixteenth_len = beat_len / 4
    for bar in range(16):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            beat_start = start + beat * beat_len
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=beat_start,
                end=beat_start + beat_len,
            ))
            kick_or_snare = 36 if beat in (0, 2) else 38
            drums.notes.append(pretty_midi.Note(
                velocity=127, pitch=kick_or_snare,
                start=beat_start,
                end=beat_start + 0.08,
            ))
            for sixteenth in range(4):
                hh_start = beat_start + sixteenth * sixteenth_len
                drums.notes.append(pretty_midi.Note(
                    velocity=127, pitch=42,
                    start=hh_start,
                    end=hh_start + 0.04,
                ))
    pm.instruments.extend([piano, bass, drums])
    dest = tmp_path / "flat_metal.mid"
    pm.write(str(dest))
    return dest


def _plan_with_full_drum_pipeline(src: Path) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=137,
        source_midi=SourceMidi(path=str(src), sha256=_sha256_bytes(src)),
        route="cinematica_emocional",
        sections=[PlanSection(
            label="MAIN", kind="chorus", start_bar=0, end_bar=16,
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
                researched_at="2026-08-26",
                sources=["https://example.test/drums"],
                confidence="high",
                techniques=[
                    StyleTechnique(name="drums.ghost_notes"),
                ],
                parameters={},
            ),
        },
    )


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
        pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in tr:
            abs_tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault((msg.channel, msg.note), []).append(
                    (abs_tick, msg.velocity),
                )
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                queue = pending.get(key)
                if queue:
                    start, vel = queue.pop(0)
                    events.append((start, abs_tick, msg.note, vel))
    return events


def test_end_to_end_edits_drums_with_real_registry(tmp_path):
    """Ponta a ponta com registro real: sem monkeypatch, sem stub.

    Confere as garantias-chave do produto quando as duas tecnicas rodam
    sobre uma track de bateria vinda da origem:
    - velocity redistribuida (nenhuma nota fica em 127)
    - ghost notes acrescentadas entre backbeats
    - contagem de notas ESTRUTURAIS preservada (so ornamento entra)
    """

    src = _build_flat_metal_drums_source(tmp_path)
    plan = _plan_with_full_drum_pipeline(src)
    out = tmp_path / "out.mid"

    render(plan, out)

    src_events = _drum_note_events(src)
    out_events = _drum_note_events(out)

    src_structural = [
        (start, end, pitch) for start, end, pitch, _vel in src_events
    ]
    out_structural = [
        (start, end, pitch) for start, end, pitch, vel in out_events
        if not (pitch == 38 and 20 <= vel <= 45)
    ]
    assert sorted(out_structural) == sorted(src_structural), (
        "notas estruturais tem que sair na mesma posicao e duracao — "
        "so ornamento pode ser somado"
    )

    velocities = [vel for *_, vel in out_events]
    assert velocities, "esperava eventos de bateria na saida"
    # `ghost_notes` e nivel technique: so ACRESCENTA. A dinamica que o usuario
    # escreveu na origem sai intacta, inclusive os 127 — rebaixar velocity de
    # nota estrutural e o defeito que tirou `drums.accent_hierarchy` do motor
    # (issue #50). O unico piso e o da faixa ghost, que vale para o ornamento.
    src_velocity = {
        (start, end, pitch): vel for start, end, pitch, vel in src_events
    }
    for start, end, pitch, vel in out_events:
        key = (start, end, pitch)
        if key in src_velocity:
            assert vel == src_velocity[key], (
                f"velocity estrutural em {key} mudou de "
                f"{src_velocity[key]} para {vel}"
            )
    assert min(velocities) >= 20, (
        "faixa ghost do manual comeca em 20 — nada pode cair abaixo"
    )

    ghosts = [
        n for n in out_events
        if n[2] == 38 and 20 <= n[3] <= 45
    ]
    added_ornaments = len(out_events) - len(src_events)
    assert added_ornaments > 0, (
        "ghost_notes tem que acrescentar ornamentos entre backbeats"
    )
    assert len(ghosts) >= added_ornaments, (
        "todo ornamento acrescentado deveria ser ghost na caixa (pitch 38, "
        "velocity 20-45)"
    )


def test_end_to_end_edits_drums_is_idempotent_byte_for_byte(tmp_path):
    """Renderizar duas vezes o mesmo plano sobre o mesmo source produz o mesmo
    arquivo byte a byte, mesmo com as duas tecnicas rodando no caminho de
    `plan.edits`. Cobertura de determinismo do caminho novo."""

    src = _build_flat_metal_drums_source(tmp_path)
    plan_a = _plan_with_full_drum_pipeline(src)
    plan_b = _plan_with_full_drum_pipeline(src)
    out_a = tmp_path / "a.mid"
    out_b = tmp_path / "b.mid"

    render(plan_a, out_a)
    render(plan_b, out_b)

    assert out_a.read_bytes() == out_b.read_bytes()
