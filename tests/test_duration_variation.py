"""Regressao US-003: arp / rhythmic_machine / motor / piano / rhodes nao
disparam `duration_uniform` no validador de artificialidade.

O bug original: `_emit_snake_step_note` calculava `note_dur = step_dur_s *
gate` com `gate` fixo (media de `GATE_RATIOS[articulation]`), e
`generate_keyboard` fazia `note_dur = (boundary - chord_onset) * gate`
igual para toda voz do mesmo acorde. Em ambos os casos, toda nota saia com
a mesma duracao (dentro do bucket de 10ms do validador) e a track inteira
era certificada como `duration_uniform` — anti-padrao
PATTERN_DURATION_UNIFORM.

Correcao: motor de duracao (`tools.humanize.DurationEngine`) reescreve
`end_s` de cada nota via proporcao sorteada dentro de
`GATE_RATIOS[articulation]` * gap ate o proximo evento. Cada nota recebe
um sorteio independente, quebrando a duracao chapada mas mantendo
articulacao (ghost sai curto, sustained sai longo, na media).
"""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.palette.harmonic import generate_keyboard
from tools.palette.rhythmic import generate_motor, generate_rhythmic
from tools.plan import PlanSection
from tools.validators.artifice import (
    DURATION_BUCKET_S,
    _check_duration_uniform,
)
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
        protagonist="texture",
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


def _duration_buckets(notes) -> set[int]:
    return {round((n.end_s - n.start_s) / DURATION_BUCKET_S) for n in notes}


# --- comportamento via validador --------------------------------------------

@pytest.mark.parametrize("role", ["arp", "rhythmic_machine"])
def test_rhythmic_role_does_not_trigger_duration_uniform(role):
    ana = _analysis(8)
    layers = generate_rhythmic(ana, _section(8), role=role, seed=42)
    track = _as_rendered_track(role, layers[0].notes)
    assert len(track.notes) >= 8, "cenario precisa ter notas suficientes"
    issue = _check_duration_uniform(f"{role}_main", track)
    assert issue is None, (
        f"{role} ainda classificado como duration_uniform: {issue.message}"
    )


def test_motor_does_not_trigger_duration_uniform():
    ana = _analysis(8)
    layers = generate_motor(ana, _section(8), seed=42)
    track = _as_rendered_track("motor", layers[0].notes)
    assert len(track.notes) >= 8, "cenario precisa ter notas suficientes"
    issue = _check_duration_uniform("motor_main", track)
    assert issue is None, (
        f"motor ainda classificado como duration_uniform: {issue.message}"
    )


@pytest.mark.parametrize("role", ["piano", "rhodes"])
def test_keyboard_role_does_not_trigger_duration_uniform(role):
    ana = _analysis(8)
    layers = generate_keyboard(ana, _section(8), role=role, seed=42)
    track = _as_rendered_track(role, layers[0].notes)
    assert len(track.notes) >= 8, "cenario precisa ter notas suficientes"
    issue = _check_duration_uniform(f"{role}_main", track)
    assert issue is None, (
        f"{role} ainda classificado como duration_uniform: {issue.message}"
    )


# --- distribuicao observavel -------------------------------------------------

def test_rhythmic_duration_spans_multiple_buckets():
    """Sanidade da variacao: mais de um bucket de 10ms sendo ocupado."""
    ana = _analysis(8)
    layers = generate_rhythmic(ana, _section(8), role="arp", seed=42)
    buckets = _duration_buckets(layers[0].notes)
    assert len(buckets) > 1, (
        f"arp ainda produz duracao chapada: bucket unico {buckets}"
    )


def test_keyboard_duration_spans_multiple_buckets():
    ana = _analysis(8)
    layers = generate_keyboard(ana, _section(8), role="piano", seed=42)
    buckets = _duration_buckets(layers[0].notes)
    assert len(buckets) > 1, (
        f"piano ainda produz duracao chapada: bucket unico {buckets}"
    )
