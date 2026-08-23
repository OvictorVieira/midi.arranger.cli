"""Regressao US-002: arp / rhythmic_machine / motor nao disparam
`grid_perfect` no validador de artificialidade.

O bug original: `RHYTHMIC_TIMING_JITTER_MS` e `MOTOR_TIMING_JITTER_MS`
valiam `(0.5, 2.0)` ms e cabiam inteiros dentro do
`artifice.GRID_TOLERANCE_S = 2ms`. Todo onset era certificado como
"grade matematica perfeita" — anti-padrao PATTERN_GRID.

Este teste roda o gerador com o mesmo shape de bars da integracao (bars
de 2s = grid step de 125ms), passa a saida pelo `_check_grid_perfect` e
exige que a checagem NAO retorne issue.
"""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.palette.rhythmic import (
    MOTOR_TIMING_JITTER_MS,
    RHYTHMIC_TIMING_JITTER_MS,
    generate_motor,
    generate_rhythmic,
)
from tools.plan import PlanSection
from tools.validators.artifice import GRID_TOLERANCE_S, _check_grid_perfect
from tools.validators.harmony import RenderedNote, RenderedTrack

BAR_S = 2.0


def _analysis(n_bars: int) -> Analysis:
    bars = [
        BarAnalysis(
            index=i,
            start=i * BAR_S,
            end=(i + 1) * BAR_S,
            chord=Chord(root=0, quality="minor"),
        )
        for i in range(n_bars)
    ]
    return Analysis(
        key_root=0,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=[],
    )


def _section(n_bars: int) -> PlanSection:
    return PlanSection(
        label="S1",
        kind="verse",
        start_bar=0,
        end_bar=n_bars,
        source="marker",
        protagonist="rhythm_machine",
        energy={
            "densidade": 5, "impacto": 5, "largura": 5,
            "altura": 5, "instabilidade": 3,
        },
    )


def _as_rendered_track(role: str, notes) -> RenderedTrack:
    return RenderedTrack(
        element_id=f"{role}_main",
        track_name=role,
        notes=tuple(
            RenderedNote(
                pitch=n.pitch,
                velocity=n.velocity,
                start_s=n.start_s,
                end_s=n.end_s,
            )
            for n in notes
        ),
    )


# --- constantes: minimo respeitado ------------------------------------------

def test_rhythmic_jitter_min_exceeds_grid_tolerance():
    """Guarda contra regressao: o menor valor de jitter tem que ficar
    ACIMA da tolerancia do validador. Se alguem restaurar (0.5, 2.0), este
    teste falha primeiro e explica por que."""
    assert RHYTHMIC_TIMING_JITTER_MS[0] / 1000.0 > GRID_TOLERANCE_S


def test_motor_jitter_min_exceeds_grid_tolerance():
    assert MOTOR_TIMING_JITTER_MS[0] / 1000.0 > GRID_TOLERANCE_S


# --- comportamento observavel via validador ---------------------------------

@pytest.mark.parametrize("role", ["arp", "rhythmic_machine"])
def test_rhythmic_role_does_not_trigger_grid_perfect(role):
    """Renderiza arp/rhythmic_machine com 8 bars e confirma que
    `_check_grid_perfect` nao retorna issue."""
    ana = _analysis(8)
    layers = generate_rhythmic(ana, _section(8), role=role, seed=42)
    track = _as_rendered_track(role, layers[0].notes)
    assert len(track.notes) >= 8, "cenario precisa ter notas suficientes"
    issue = _check_grid_perfect(f"{role}_main", track, ana)
    assert issue is None, (
        f"{role} still classified as grid_perfect: {issue.message}"
    )


def test_motor_does_not_trigger_grid_perfect():
    """Mesma checagem para o motor (rhythmic.py:758)."""
    ana = _analysis(8)
    layers = generate_motor(ana, _section(8), seed=42)
    track = _as_rendered_track("motor", layers[0].notes)
    assert len(track.notes) >= 8, "cenario precisa ter notas suficientes"
    issue = _check_grid_perfect("motor_main", track, ana)
    assert issue is None, (
        f"motor still classified as grid_perfect: {issue.message}"
    )


def test_jitter_keeps_notes_on_intended_step():
    """Sanity: o jitter e pequeno o suficiente para nao mudar a coluna
    do grid a que a nota pertence — 4.5ms << step_dur/2 = 62.5ms para
    bar de 2s. Garante que aumentar o jitter nao borrou o padrao."""
    ana = _analysis(2)
    layers = generate_rhythmic(ana, _section(2), role="arp", seed=42)
    step_dur = BAR_S / 16
    for n in layers[0].notes:
        bar_start = int(n.start_s / BAR_S) * BAR_S
        rel = n.start_s - bar_start
        nearest_step = round(rel / step_dur)
        drift = abs(rel - nearest_step * step_dur)
        assert drift < step_dur / 2, (
            f"jitter deslocou nota para outra coluna: drift={drift * 1000:.2f}ms"
        )
