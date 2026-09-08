"""Regressao issue #123: `StyleTechnique.intensity` COMPOE com o eixo
`plan.sections[].energy.densidade` — nunca o substitui.

O defeito: `intensity` caia no mesmo canal de `density` no despacho de
`tools/render.py`, e `drums.ghost_notes` trata `density` como override total
da fracao por compasso. Como `influence.compile` SEMPRE emite `intensity`
(traducao de `off|subtle|medium|strong`), TODO plano montado pelo fluxo real
de pesquisa perdia a modulacao por secao da issue #45, sem warning nenhum:
trocar `densidade` de 9 para 1 entre as metades da musica devolvia MIDI
byte-identico.

A correcao separa os dois canais em `render._style_technique_parameters`
(`density` declarado vs eco de `intensity`, sinalizado por
`density_declared`) e faz `_apply_drums_ghost_notes` multiplicar a fracao
derivada da secao por `intensity`. Os dois parametros comandam: mexer em
qualquer um dos dois muda o resultado, e zero em qualquer um desliga.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mido

from tests.test_drums_ghost_notes_section_density import (
    DEIXE_IR,
    TICKS_PER_BAR,
    _drum_note_starts,
    _drums_with_backbeats,
    _ghost_starts,
    _midi_bytes,
)
from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    SourceMidi,
    StyleTechnique,
)
from tools.render import render
from tools.techniques import apply_technique


def _sections(high_first: bool) -> tuple[dict, ...]:
    first, second = (9, 1) if high_first else (1, 9)
    return (
        {"start_tick": 0, "end_tick": TICKS_PER_BAR * 8,
         "kind": "verse", "densidade": first},
        {"start_tick": TICKS_PER_BAR * 8, "end_tick": TICKS_PER_BAR * 16,
         "kind": "verse", "densidade": second},
    )


def _apply_with_intensity(
    source: mido.MidiFile, *, seed: int, intensity: float | None, high_first: bool,
) -> mido.MidiFile:
    """Reproduz o dicionario que `render._style_technique_parameters` monta
    para uma tecnica que declara `intensity` e NAO declara `density`."""

    parameters: dict = {"sections": _sections(high_first)}
    if intensity is not None:
        parameters["density"] = intensity
        parameters["density_declared"] = False
        parameters["intensity"] = intensity
    return apply_technique(
        "drums.ghost_notes", source, seed=seed, parameters=parameters,
    )


def _halves(source: mido.MidiFile, result: mido.MidiFile) -> tuple[int, int]:
    ghosts = _ghost_starts(source, result)
    first = sum(1 for tick in ghosts if tick < TICKS_PER_BAR * 8)
    return first, len(ghosts) - first


# --- nivel motor ---------------------------------------------------------

def test_intensity_does_not_erase_the_section_axis():
    """O repro da issue: com `intensity` declarada, trocar o eixo
    `densidade` entre as metades tem que trocar tambem onde cai o material.
    Antes da correcao as duas metades saiam com a MESMA quantidade."""

    source = _drums_with_backbeats(16)
    alto, _ = _halves(
        source, _apply_with_intensity(source, seed=3, intensity=0.55, high_first=True),
    )
    baixo, _ = _halves(
        source, _apply_with_intensity(source, seed=3, intensity=0.55, high_first=False),
    )
    assert alto > baixo, (
        f"com intensity=0.55, a primeira metade recebeu {alto} ghosts com "
        f"densidade=9 e {baixo} com densidade=1 — o eixo de secao foi ignorado"
    )


def test_intensity_commands_the_amount_within_the_same_sections():
    """O outro lado da composicao: com as MESMAS secoes, baixar
    `intensity` tem que reduzir o material. Se so a secao comandasse,
    `intensity` seria parametro mentiroso."""

    source = _drums_with_backbeats(16)
    forte = _ghost_starts(
        source, _apply_with_intensity(source, seed=4, intensity=0.9, high_first=True),
    )
    fraca = _ghost_starts(
        source, _apply_with_intensity(source, seed=4, intensity=0.2, high_first=True),
    )
    assert len(fraca) < len(forte), (
        f"intensity=0.2 rendeu {len(fraca)} ghosts e intensity=0.9 rendeu "
        f"{len(forte)} — a intensidade declarada nao comandou"
    )


def test_intensity_one_is_the_neutral_factor_of_the_composition():
    """`intensity=1.0` e fator neutro: byte-identico ao plano que nao
    declara o campo. E a prova de que a composicao NAO altera o caminho
    historico (plano v1 nunca declara `intensity`)."""

    source = _drums_with_backbeats(16)
    com = _apply_with_intensity(source, seed=5, intensity=1.0, high_first=True)
    sem = _apply_with_intensity(source, seed=5, intensity=None, high_first=True)
    assert _midi_bytes(com) == _midi_bytes(sem)


def test_explicit_density_still_overrides_the_section_axis():
    """Retrocompatibilidade: `density` DECLARADO continua sendo override
    total (precedencia do AGENTS.md, `parameters` do plano manda). Trocar o
    eixo de secao sob override nao pode mudar nada."""

    source = _drums_with_backbeats(16)
    a = apply_technique(
        "drums.ghost_notes", source, seed=6,
        parameters={"sections": _sections(True), "density": 0.4,
                    "density_declared": True},
    )
    b = apply_technique(
        "drums.ghost_notes", source, seed=6,
        parameters={"sections": _sections(False), "density": 0.4,
                    "density_declared": True},
    )
    assert _midi_bytes(a) == _midi_bytes(b)
    # E a forma historica (sem a chave `density_declared`, como chamador
    # direto do motor sempre fez) tem que dar exatamente o mesmo resultado.
    legado = apply_technique(
        "drums.ghost_notes", source, seed=6,
        parameters={"sections": _sections(True), "density": 0.4},
    )
    assert _midi_bytes(legado) == _midi_bytes(a)


# --- nivel render, corpus real -------------------------------------------

def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_deixe_ir(
    tmp_path: Path,
    *,
    seed: int,
    verse_densidade: int,
    chorus_densidade: int,
    intensity: float | None,
    density: float | None = None,
    sufixo: str,
) -> Path:
    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    half = total_bars // 2

    brief_path = tmp_path / f"brief_{sufixo}.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
        encoding="utf-8",
    )
    plan = ArrangementPlan(
        version=1,
        seed=seed,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="VERSE", kind="verse", start_bar=0, end_bar=half,
                source="marker", protagonist="texture",
                energy={"densidade": verse_densidade, "impacto": 5, "largura": 5,
                        "altura": 5, "instabilidade": 3},
            ),
            PlanSection(
                label="VERSE2", kind="verse", start_bar=half, end_bar=total_bars,
                source="marker", protagonist="texture",
                energy={"densidade": chorus_densidade, "impacto": 5, "largura": 5,
                        "altura": 5, "instabilidade": 3},
            ),
        ],
        elements=[],
        edits=[PlanEdit(track="MIDI", profile="drums", intensity=0.0)],
        style={
            "drums": FamilyStyle(
                reference="Research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(
                    name="drums.ghost_notes", density=density, intensity=intensity,
                )],
                parameters={},
            ),
        },
        brief_ref=BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path)),
    )
    out_path = tmp_path / f"out_{sufixo}.mid"
    render(plan, out_path)
    return out_path


def _ghosts_by_half(out_path: Path, half_tick: int) -> tuple[int, int]:
    src_starts = set(_drum_note_starts(mido.MidiFile(str(DEIXE_IR))))
    ghosts = [
        tick for tick in _drum_note_starts(mido.MidiFile(str(out_path)))
        if tick not in src_starts
    ]
    first = sum(1 for tick in ghosts if tick < half_tick)
    return first, len(ghosts) - first


def test_render_with_intensity_still_follows_the_section_energy(tmp_path):
    """Ponta a ponta pelo `render()`, corpus real: plano que declara
    `intensity` (o que `influence.compile` sempre emite) continua
    respondendo ao eixo `densidade` das secoes."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    half = len(analysis.bars) // 2
    half_tick = int(round(
        mido.MidiFile(str(DEIXE_IR)).ticks_per_beat * 4 * half
    ))

    alta = _render_deixe_ir(
        tmp_path, seed=7, verse_densidade=9, chorus_densidade=1,
        intensity=0.55, sufixo="alta",
    )
    baixa = _render_deixe_ir(
        tmp_path, seed=7, verse_densidade=1, chorus_densidade=9,
        intensity=0.55, sufixo="baixa",
    )
    a_alta, _ = _ghosts_by_half(alta, half_tick)
    a_baixa, _ = _ghosts_by_half(baixa, half_tick)
    assert a_alta > a_baixa, (
        f"primeira metade: {a_alta} ghosts com densidade=9 e {a_baixa} com "
        "densidade=1 — a energia da secao nao comandou sob `intensity`"
    )


def test_render_plan_without_intensity_is_unchanged_by_the_fix(tmp_path):
    """Retrocompatibilidade do plano v1 (sem `intensity`): byte-identico ao
    mesmo plano com `intensity=1.0`, o fator neutro da composicao."""

    sem = _render_deixe_ir(
        tmp_path, seed=8, verse_densidade=7, chorus_densidade=3,
        intensity=None, sufixo="sem_intensity",
    )
    neutra = _render_deixe_ir(
        tmp_path, seed=8, verse_densidade=7, chorus_densidade=3,
        intensity=1.0, sufixo="intensity_neutra",
    )
    assert sem.read_bytes() == neutra.read_bytes()


def test_render_explicit_density_keeps_precedence_over_intensity(tmp_path):
    """`density` declarado continua mandando: declarar `intensity` junto
    nao muda um byte (precedencia da issue #72, preservada)."""

    so_density = _render_deixe_ir(
        tmp_path, seed=9, verse_densidade=7, chorus_densidade=3,
        intensity=None, density=0.4, sufixo="so_density",
    )
    com_intensity = _render_deixe_ir(
        tmp_path, seed=9, verse_densidade=7, chorus_densidade=3,
        intensity=0.2, density=0.4, sufixo="density_e_intensity",
    )
    assert so_density.read_bytes() == com_intensity.read_bytes()
