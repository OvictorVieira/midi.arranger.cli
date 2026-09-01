"""Regressoes da segunda passada de review conjunta com o Codex, PR #54.

Cada teste foi verificado por mutacao: falha quando o fix correspondente
e removido.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import mido
import pytest

from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    FamilyStyle,
    PlanEdit,
    PlanValidationError,
    SourceMidi,
    StyleTechnique,
    validate,
)
from tools.render import render
from tools.techniques.engine import (
    TechniqueRecipeError,
    apply_technique,
)


def _bass_track(events) -> mido.MidiFile:
    """MIDI de uma track de baixo. Ordena por tick ABSOLUTO antes de gerar
    os deltas, entao eventos de `events` podem se sobrepor livremente
    (necessario para simular ligado natural)."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    absolute = []
    for start, dur, pitch, vel in events:
        absolute.append((start, 0, mido.Message("note_on", note=pitch, velocity=vel)))
        absolute.append((start + dur, 1, mido.Message("note_off", note=pitch, velocity=0)))
    absolute.sort(key=lambda item: (item[0], item[1]))
    previous = 0
    for tick, _bias, msg in absolute:
        track.append(msg.copy(time=tick - previous))
        previous = tick
    mid.tracks.append(track)
    return mid


def test_hammer_pull_reconhece_ligado_natural_sem_keyswitch():
    """Achado 3 da segunda passada: overlap natural (baixo tocado por
    gente, sem a tecnica ter passado por ali) era tratado como "ja
    aplicado" e pulava, entao nunca recebia o keyswitch C0 do MODO BASS.
    """
    events = [(0, 500, 40, 100), (480, 240, 42, 100)]  # overlap natural
    out = apply_technique(
        "bass.hammer_pull", _bass_track(events), seed=13, tool="modo_bass",
        parameters={"density": 1.0},
    )
    has_keyswitch = any(
        msg.type == "note_on" and msg.velocity > 0 and msg.note == 12
        for msg in out.tracks[0]
    )
    assert has_keyswitch, "ligado natural sem keyswitch deveria receber C0"


def test_hammer_pull_continua_idempotente_apos_reconhecer_ligado_natural():
    events = [(0, 500, 40, 100), (480, 240, 42, 100)]
    once = apply_technique(
        "bass.hammer_pull", _bass_track(events), seed=13, tool="modo_bass",
        parameters={"density": 1.0},
    )
    twice = apply_technique(
        "bass.hammer_pull", once, seed=13, tool="modo_bass",
        parameters={"density": 1.0},
    )

    def snapshot(mid):
        return [
            (m.type, getattr(m, "note", None), getattr(m, "velocity", None), m.time)
            for m in mid.tracks[0]
        ]
    assert snapshot(once) == snapshot(twice)


def test_palm_mute_modo_bass_falha_sem_fallback_generico():
    """MODO nao pode receber gate/velocity como se fosse o CC de mute."""
    events = [(0, 480, 40, 100)]
    with pytest.raises(TechniqueRecipeError, match="MUTING Off"):
        apply_technique(
            "bass.palm_mute", _bass_track(events), seed=1, tool="modo_bass",
            parameters={"density": 1.0},
        )


def test_palm_mute_density_zero_e_noop_antes_da_guarda_modo_bass():
    """Tecnica desligada nao altera a linha nem exige mapeamento MODO."""
    events = [(0, 480, 40, 100)]
    source = _bass_track(events)
    out = apply_technique(
        "bass.palm_mute", source, seed=1, tool="modo_bass",
        parameters={"density": 0.0},
    )
    out_notes = [
        (msg.note, msg.velocity)
        for track in out.tracks for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    ]
    assert out_notes == [(40, 100)]


def test_attack_style_e_alcancavel_pelo_plano_e_render_reais():
    """Achado 1+2: `style` nao tinha como trafegar pelo plano — o schema
    so aceitava numero ou par. `bass.attack_style` estava registrada e
    testada diretamente no motor, mas inalcancavel pelo produto real.
    """
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "bass.mid"
    _bass_track([(0, 240, 40, 100), (240, 240, 42, 100),
                 (480, 240, 44, 100), (720, 240, 45, 100)]).save(str(src))

    brief = {
        "style": {
            fam: {"authorized_techniques": ["bass.attack_style"] if fam == "bass" else []}
            for fam in ("bass", "drums", "guitar", "keys")
        },
    }
    brief_path = tmp / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")

    plan = ArrangementPlan(
        version=1, seed=1,
        source_midi=SourceMidi(path=str(src), sha256="0" * 64),
        route="cinematica_emocional", sections=[], elements=[],
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.0)]
    plan.style = {
        "bass": FamilyStyle(
            reference="X", researched_at="2026-08-26",
            sources=["https://example.test/x"], confidence="high",
            techniques=[StyleTechnique(name="bass.attack_style", style="palheta")],
            parameters={},
        ),
    }

    validate(plan)  # nao levanta

    # Round-trip por JSON: e o caminho REAL de producao, plano vindo de
    # arquivo. `style` tem que sobreviver a serializacao/desserializacao,
    # nao so existir no objeto Python construido a mao no teste.
    from tools.plan import dump, load
    plan_path = tmp / "arrangement-plan.json"
    dump(plan, plan_path)
    reloaded = load(plan_path)
    assert reloaded.style["bass"].techniques[0].style == "palheta"

    out_path = tmp / "out.mid"
    render(reloaded, out_path)  # nao levanta
    assert out_path.exists()


def test_attack_style_density_zero_e_noop():
    """Achado do Codex na PR: `bass.attack_style` nao olhava `density` em
    lugar nenhum, entao `density=0.0` (que deveria desligar a tecnica,
    mesma convencao de `bass.palm_mute`/`bass.ghost_notes`) nao impedia o
    keyswitch de ser inserido quando `tool='modo_bass'` resolvia a receita
    especifica."""
    events = [(0, 480, 40, 100)]
    out = apply_technique(
        "bass.attack_style", _bass_track(events), seed=1, tool="modo_bass",
        parameters={"style": "dedo", "density": 0.0},
    )
    out_notes = [
        (msg.note, msg.velocity)
        for track in out.tracks for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    ]
    assert out_notes == [(40, 100)]


def test_attack_style_recusa_string_fora_do_vocabulario_fechado():
    """`style` e vocabulario FECHADO, nunca texto livre — a unica excecao
    numerica-only de `style.parameters`. Autoriza a tecnica no brief para
    isolar especificamente a checagem de vocabulario (sem isso, a checagem
    de autorizacao de #51 falha primeiro e mascara o que este teste quer
    provar).
    """
    tmp = Path(tempfile.mkdtemp())
    brief = {
        "style": {
            fam: {"authorized_techniques": ["bass.attack_style"] if fam == "bass" else []}
            for fam in ("bass", "drums", "guitar", "keys")
        },
    }
    brief_path = tmp / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")

    plan = ArrangementPlan(
        version=1, seed=1,
        source_midi=SourceMidi(path="/tmp/x.mid", sha256="0" * 64),
        route="cinematica_emocional", sections=[], elements=[],
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    plan.style = {
        "bass": FamilyStyle(
            reference="X", researched_at="2026-08-26",
            sources=["https://example.test/x"], confidence="high",
            techniques=[StyleTechnique(name="bass.attack_style", style="grunge")],
            parameters={},
        ),
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.bass.techniques[0].style"
