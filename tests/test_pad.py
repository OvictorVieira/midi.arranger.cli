"""Testes do gerador de pad (US-014)."""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.constants import REGISTER_BANDS
from tools.palette.harmonic import (
    CHORUS_KIND,
    DEFAULT_PAD_REGISTER,
    FADE_IN_FLOOR,
    OPEN_AT_CHORUS_BONUS,
    PadLayer,
    _chord_degrees,
    _pad_base_velocity,
    _parse_fade_in_bars,
    _velocity_for_bar,
    _voice_pad_chord,
    generate_pad,
)
from tools.plan import PlanSection

# --- fixtures ---------------------------------------------------------------

BAR_S = 2.0  # 2s por bar — arbitrario mas estavel para asserts em segundos.


def _bar(index: int, chord: Chord | None) -> BarAnalysis:
    return BarAnalysis(
        index=index,
        start=index * BAR_S,
        end=(index + 1) * BAR_S,
        chord=chord,
    )


def _analysis_with_chord_run(
    chord: Chord,
    start_bar: int = 0,
    n_bars: int = 4,
) -> Analysis:
    """Fabrica Analysis com `n_bars` bars consecutivos carregando o mesmo acorde."""
    bars = [_bar(i, chord) for i in range(start_bar, start_bar + n_bars)]
    return Analysis(
        key_root=chord.root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(
    label: str = "CHORUS 1",
    kind: str = "chorus",
    start_bar: int = 0,
    end_bar: int = 4,
) -> PlanSection:
    return PlanSection(
        label=label,
        kind=kind,
        start_bar=start_bar,
        end_bar=end_bar,
        source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 5},
    )


# --- helpers puros ----------------------------------------------------------

def test_chord_degrees_major_is_triad():
    assert _chord_degrees(Chord(root=0, quality="major")) == [0, 4, 7]


def test_chord_degrees_minor_is_triad():
    assert _chord_degrees(Chord(root=9, quality="minor")) == [0, 3, 7]


def test_chord_degrees_power_and_unknown_have_no_third():
    """Sem terca em power/unknown — mais seguro que assumir maior ou menor
    e errar tonalidade em cima do baixo/guitarra."""
    assert _chord_degrees(Chord(root=4, quality="power")) == [0, 7]
    assert _chord_degrees(Chord(root=4, quality="unknown")) == [0, 7]


def test_parse_fade_in_bars_extracts_number():
    assert _parse_fade_in_bars("fade_in_2bars") == 2
    assert _parse_fade_in_bars("fade_in_8bars") == 8


def test_parse_fade_in_bars_unknown_shape_returns_zero():
    assert _parse_fade_in_bars(None) == 0
    assert _parse_fade_in_bars("hold") == 0
    assert _parse_fade_in_bars("fade_in_XXbars") == 0
    assert _parse_fade_in_bars("fade_in_bars") == 0


def test_pad_base_velocity_is_tied_soft_midpoint():
    # tied_soft = (50, 75) -> midpoint 62.
    assert _pad_base_velocity() == 62


# --- forma dinamica ---------------------------------------------------------

def test_velocity_hold_is_constant_base():
    base = _pad_base_velocity()
    for bar_pos in (0, 1, 2, 7):
        assert _velocity_for_bar(bar_pos, {"shape": "hold"}, "verse", base) == base


def test_velocity_fade_in_ramps_from_floor_to_base():
    """AC: 'Suporta forma dinamica: fade_in_Nbars'.

    Ramp linear: bar 0 -> 1/N, bar N-1 -> N/N (base).
    """
    base = _pad_base_velocity()
    dyn = {"entry": "fade_in_4bars", "shape": "hold"}
    v0 = _velocity_for_bar(0, dyn, "verse", base)
    v1 = _velocity_for_bar(1, dyn, "verse", base)
    v3 = _velocity_for_bar(3, dyn, "verse", base)
    v4 = _velocity_for_bar(4, dyn, "verse", base)

    # Cada barra e maior que a anterior dentro do fade.
    assert FADE_IN_FLOOR < v0 < v1 < v3 == base
    # Passado o fade, volta ao base normal.
    assert v4 == base


def test_velocity_open_at_chorus_only_boosts_when_kind_is_chorus():
    """AC: 'Suporta forma dinamica: open_at_chorus'."""
    base = _pad_base_velocity()
    dyn = {"shape": "open_at_chorus"}
    assert _velocity_for_bar(0, dyn, CHORUS_KIND, base) == base + OPEN_AT_CHORUS_BONUS
    assert _velocity_for_bar(0, dyn, "verse", base) == base


# --- voicing ----------------------------------------------------------------

def test_voice_pad_chord_places_root_at_top_octave_up():
    """AC: 'Voicing sem fundamental ou com a fundamental uma oitava acima'.

    Aqui a raiz vira o topo do voicing — o corpo (terca/quinta) fica logo
    abaixo. Nao ha nota de raiz no grave.
    """
    # C major dentro da banda mid (48-71).
    pitches = _voice_pad_chord(Chord(root=0, quality="major"), (48, 71))
    # Menor pitch >= 48 com pc==0 -> C4 (48).
    # Corpo = [48+4=52, 48+7=55]; topo = 48+12=60.
    assert pitches[0] == 52  # terca (E) na base
    assert pitches[-1] == 60  # raiz (C) no topo, uma oitava acima
    # Nao ha C48 (raiz grave) no voicing.
    assert 48 not in pitches
    # Todas as notas dentro do registro.
    assert all(48 <= p <= 71 for p in pitches)


def test_voice_pad_chord_stays_within_register():
    """Voicing cabe dentro do registro solicitado (transpoe oitavas para caber)."""
    # A menor cabendo em (60, 84).
    pitches = _voice_pad_chord(Chord(root=9, quality="minor"), (60, 84))
    assert all(60 <= p <= 84 for p in pitches)


def test_voice_pad_chord_below_c2_reduces_to_root_or_fifth():
    """AC: 'Teste verifica ausencia de acorde denso abaixo de C2'."""
    # Registro completamente sub (0-35).
    pitches = _voice_pad_chord(Chord(root=0, quality="major"), (0, 35))
    assert len(pitches) <= 2
    # Todas abaixo de C2.
    assert all(p < 36 for p in pitches)


# --- generator: shape e escopo ---------------------------------------------

def test_generate_pad_produces_exactly_n_layers():
    """AC: 'Teste verifica que layers: 3 produz exatamente 3 tracks'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)

    layers = generate_pad(ana, sec, layers=3, seed=0)

    assert len(layers) == 3
    # Cada camada e uma track separada (index 0..N-1) e traz notas.
    assert [layer.index for layer in layers] == [0, 1, 2]
    for layer in layers:
        assert isinstance(layer, PadLayer)
        assert len(layer.notes) > 0


def test_generate_pad_layers_zero_raises():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=2)
    sec = _section(end_bar=2)
    with pytest.raises(ValueError):
        generate_pad(ana, sec, layers=0)


def test_generate_pad_default_register_is_mid_band():
    """AC: 'Registro configuravel com default na banda mid'."""
    assert REGISTER_BANDS["mid"] == DEFAULT_PAD_REGISTER

    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=1)
    sec = _section(end_bar=1)
    layers = generate_pad(ana, sec, layers=1, seed=0)

    lo_mid, hi_mid = REGISTER_BANDS["mid"]
    for note in layers[0].notes:
        assert lo_mid <= note.pitch <= hi_mid


# --- generator: sustentacao ate limite de compasso -------------------------

def test_generate_pad_notes_sustain_until_bar_boundary():
    """AC: 'Teste verifica que as notas sustentam ate limite de compasso'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)

    layers = generate_pad(ana, sec, layers=1, seed=0)

    # Todas as notas terminam exatamente em um multiplo do tamanho do bar.
    for note in layers[0].notes:
        assert note.end_s == pytest.approx(round(note.end_s / BAR_S) * BAR_S)
    # Especificamente: pelo menos uma nota fecha no fim de cada bar da secao.
    ends = {note.end_s for note in layers[0].notes}
    assert ends == {BAR_S, 2 * BAR_S, 3 * BAR_S, 4 * BAR_S}


# --- generator: ataque desencontrado entre camadas -------------------------

def test_layers_have_staggered_attack_between_them():
    """AC: 'Ataques desencontrados entre camadas do pad e nao entre notas
    do mesmo voicing'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=1)
    sec = _section(end_bar=1)

    layers = generate_pad(ana, sec, layers=3, seed=1)

    # Dentro de cada camada, TODAS as notas de um mesmo bar comecam juntas
    # (voicing simultaneo para pad).
    for layer in layers:
        starts = {note.start_s for note in layer.notes}
        assert len(starts) == 1, (
            f"pad layer {layer.index}: notas do mesmo voicing devem "
            f"atacar juntas, mas foram encontrados starts {starts}"
        )

    # Entre camadas o primeiro ataque cresce estritamente.
    first_starts = [layer.notes[0].start_s for layer in layers]
    assert first_starts[0] < first_starts[1] < first_starts[2]
    # E o stagger e da ordem de milissegundos (<= 100ms para 3 camadas).
    assert first_starts[-1] - first_starts[0] < 0.100


# --- generator: seguir acordes detectados ----------------------------------

def test_generate_pad_follows_detected_chord_root():
    """AC: 'gera pad com notas sustentadas seguindo os acordes detectados
    pela analise'."""
    # A menor tem pc 9. O voicing deve ter alguma nota de A.
    ana = _analysis_with_chord_run(Chord(root=9, quality="minor"), n_bars=1)
    sec = _section(end_bar=1)
    layers = generate_pad(ana, sec, layers=1, seed=0)

    pcs = {note.pitch % 12 for note in layers[0].notes}
    assert 9 in pcs  # raiz (A) presente no voicing (no topo por regra do pad)
    assert 0 in pcs  # terca menor (C) presente
    assert 4 in pcs  # quinta (E) presente


def test_generate_pad_skips_bars_without_chord():
    """Bars sem chord (ex.: silencio absoluto) nao geram notas — sem levantar."""
    bars = [
        _bar(0, Chord(root=0, quality="major")),
        _bar(1, None),
        _bar(2, Chord(root=0, quality="major")),
    ]
    ana = Analysis(
        key_root=0,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )
    sec = _section(end_bar=3)
    layers = generate_pad(ana, sec, layers=1, seed=0)

    starts = sorted({n.start_s for n in layers[0].notes})
    # Ha nota no bar 0 e no bar 2, e NENHUMA nota comeca dentro do bar 1.
    assert starts == [0.0, 2 * BAR_S]


def test_generate_pad_empty_section_returns_empty_layers():
    """Secao fora do range dos bars: layers vazias mas com count correto."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), start_bar=0, n_bars=2)
    # Secao referencia bars 10-12 que nao existem na analise.
    sec = _section(start_bar=10, end_bar=12)
    layers = generate_pad(ana, sec, layers=2, seed=0)
    assert len(layers) == 2
    assert all(layer.notes == () for layer in layers)


# --- generator: determinismo -----------------------------------------------

def test_same_seed_produces_same_output():
    ana = _analysis_with_chord_run(Chord(root=7, quality="major"), n_bars=4)
    sec = _section(end_bar=4)
    a = generate_pad(ana, sec, layers=3, seed=42)
    b = generate_pad(ana, sec, layers=3, seed=42)
    assert a == b


def test_different_seeds_diverge_on_stagger():
    ana = _analysis_with_chord_run(Chord(root=7, quality="major"), n_bars=1)
    sec = _section(end_bar=1)
    a = generate_pad(ana, sec, layers=3, seed=1)
    b = generate_pad(ana, sec, layers=3, seed=999)
    # Layer 0 sempre no onset; layers > 0 divergem porque o stagger sai
    # de seeds distintos.
    assert a[0].notes[0].start_s == b[0].notes[0].start_s == 0.0
    assert a[1].notes[0].start_s != b[1].notes[0].start_s


# --- generator: dynamics x velocity ----------------------------------------

def test_fade_in_makes_early_bar_softer_than_later_bar():
    """Integracao: fade_in_2bars faz o bar 0 ter velocity < bar 1 no mesmo
    acorde sustentado."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)
    layers = generate_pad(
        ana, sec,
        layers=1,
        seed=0,
        dynamics={"entry": "fade_in_2bars", "shape": "hold"},
    )
    # Notas ordenadas por start_s por construcao do gerador.
    notes = layers[0].notes
    # Pega uma nota por bar (a de topo por bar, por exemplo).
    by_bar_start = {}
    for n in notes:
        by_bar_start.setdefault(n.start_s, []).append(n)
    v_bar0 = by_bar_start[0.0][0].velocity
    v_bar1 = by_bar_start[BAR_S][0].velocity
    assert v_bar0 < v_bar1
