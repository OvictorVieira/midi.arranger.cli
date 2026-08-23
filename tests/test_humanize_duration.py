"""Testes do motor de duracao (humanize.py US-007)."""

from __future__ import annotations

import statistics

import pytest

from tools.constants import GATE_RATIOS, LEGATO_OVERLAP_MS
from tools.humanize import (
    BAR_ALIGNED_ARTICULATIONS,
    DEFAULT_LAST_NOTE_MS,
    DURATION_ARTICULATIONS,
    DURATION_SAFETY_MS,
    MIN_DURATION_MS,
    DurationEngine,
    DurationRequest,
)

# --- vocabulario ------------------------------------------------------------

def test_articulations_cover_spec():
    """AC: 'Articulacoes suportadas: ghost, staccato, tight, open, sustained
    e let_ring'."""
    assert set(DURATION_ARTICULATIONS) == {
        "ghost", "staccato", "tight", "open", "sustained", "let_ring",
    }


def test_bar_aligned_articulations_are_sustained_and_let_ring():
    assert {"sustained", "let_ring"} == BAR_ALIGNED_ARTICULATIONS


# --- proporcao do gap -------------------------------------------------------

def test_gap_based_duration_within_gate_ratio_bounds():
    """AC: 'Duracao calculada como proporcao do gap ate o proximo evento,
    usando GATE_RATIOS'."""
    gap = 400.0
    engine = DurationEngine(seed=1)
    for articulation in ("ghost", "staccato", "tight", "open"):
        lo, hi = GATE_RATIOS[articulation]
        durations = [
            engine.compute(
                DurationRequest(articulation=articulation, gap_ms=gap)
            )
            for _ in range(200)
        ]
        for d in durations:
            assert gap * lo - 1e-6 <= d <= gap * hi + 1e-6, (
                articulation, d, lo, hi
            )


def test_gap_based_median_matches_gate_ratio_midpoint():
    """Distribuicao uniforme dentro da faixa: mediana proxima do meio."""
    gap = 1000.0
    engine = DurationEngine(seed=99)
    lo, hi = GATE_RATIOS["tight"]
    durs = [
        engine.compute(DurationRequest(articulation="tight", gap_ms=gap))
        for _ in range(2000)
    ]
    expected_mid = gap * (lo + hi) / 2
    assert abs(statistics.median(durs) - expected_mid) < 20


# --- garantia de nao-colisao -----------------------------------------------

def test_note_never_ends_exactly_at_next_onset():
    """AC: 'Nunca gera duracao que termine exatamente no onset da nota
    seguinte'."""
    gap = 500.0
    engine = DurationEngine(seed=11)
    for articulation in ("ghost", "staccato", "tight", "open", "sustained"):
        for _ in range(500):
            dur = engine.compute(
                DurationRequest(articulation=articulation, gap_ms=gap)
            )
            assert dur != gap


def test_sustained_gap_fallback_respects_safety_cap():
    """Sem limites de compasso, sustained cai no gap-ratio e ainda respeita
    a margem de seguranca."""
    gap = 1000.0
    engine = DurationEngine(seed=5)
    durs = [
        engine.compute(DurationRequest(articulation="sustained", gap_ms=gap))
        for _ in range(200)
    ]
    for d in durs:
        assert d <= gap - DURATION_SAFETY_MS + 1e-6


# --- alinhamento a limite de compasso --------------------------------------

def test_sustained_release_aligned_to_bar_boundary():
    """AC: 'Notas let_ring e sustained tem release alinhado a limite de
    compasso, nao a valor arbitrario'."""
    boundaries = (500.0, 1000.0, 1500.0)
    engine = DurationEngine(seed=1)
    for articulation in ("sustained", "let_ring"):
        for sustain_bars in (1, 2, 3):
            dur = engine.compute(DurationRequest(
                articulation=articulation,
                bar_boundaries_ms=boundaries,
                sustain_bars=sustain_bars,
            ))
            assert dur == boundaries[sustain_bars - 1] - DURATION_SAFETY_MS


def test_sustained_uses_last_boundary_if_sustain_bars_exceeds_available():
    engine = DurationEngine(seed=1)
    boundaries = (1000.0,)
    dur = engine.compute(DurationRequest(
        articulation="sustained",
        bar_boundaries_ms=boundaries,
        sustain_bars=5,
    ))
    assert dur == boundaries[-1] - DURATION_SAFETY_MS


def test_sustained_layer_median_matches_two_bars_at_174bpm():
    """AC: 'a mediana de duracao de uma camada sustained a 174bpm fica
    proxima de 2 compassos (aproximadamente 2743ms)'."""
    bar_ms = 60_000 / 174 * 4  # ~1379.31ms
    boundaries = (bar_ms, 2 * bar_ms, 3 * bar_ms)
    engine = DurationEngine(seed=42)
    durs = [
        engine.compute(DurationRequest(
            articulation="sustained",
            bar_boundaries_ms=boundaries,
            sustain_bars=2,
        ))
        for _ in range(100)
    ]
    median = statistics.median(durs)
    assert 2700 <= median <= 2760, median


# --- legato ---------------------------------------------------------------

def test_legato_overlap_within_five_to_twentyfive_ms():
    """AC: 'Overlap de legato entre 5 e 25ms quando o elemento for marcado
    como legato'."""
    lo, hi = LEGATO_OVERLAP_MS
    gap = 300.0
    engine = DurationEngine(seed=7)
    durs = [
        engine.compute(DurationRequest(
            articulation="tight",
            gap_ms=gap,
            is_legato=True,
        ))
        for _ in range(400)
    ]
    overlaps = [d - gap for d in durs]
    for o in overlaps:
        assert lo <= o <= hi, o


def test_legato_wins_over_safety_cap():
    """Legato explicito atravessa o proximo onset — o cap de seguranca nao
    deve neutralizar essa intencao."""
    gap = 200.0
    engine = DurationEngine(seed=3)
    for _ in range(200):
        dur = engine.compute(DurationRequest(
            articulation="tight", gap_ms=gap, is_legato=True,
        ))
        assert dur > gap  # sempre passa do onset da proxima


def test_legato_ignored_when_no_next_note():
    """Sem gap_ms (ultima nota), legato nao aplica overlap."""
    engine = DurationEngine(seed=3)
    dur = engine.compute(DurationRequest(
        articulation="tight", gap_ms=None, is_legato=True,
    ))
    assert dur == DEFAULT_LAST_NOTE_MS


# --- reprodutibilidade ----------------------------------------------------

def test_same_seed_produces_identical_sequence():
    requests = [
        DurationRequest(articulation="ghost", gap_ms=200.0),
        DurationRequest(
            articulation="sustained",
            bar_boundaries_ms=(1000.0, 2000.0),
            sustain_bars=2,
        ),
        DurationRequest(articulation="open", gap_ms=333.0, is_legato=True),
        DurationRequest(articulation="tight", gap_ms=180.0),
        DurationRequest(
            articulation="let_ring",
            bar_boundaries_ms=(1500.0,),
            sustain_bars=1,
        ),
    ] * 20
    a = DurationEngine(seed=2026)
    b = DurationEngine(seed=2026)
    assert [a.compute(r) for r in requests] == [b.compute(r) for r in requests]


def test_different_seeds_diverge():
    req = DurationRequest(articulation="tight", gap_ms=250.0)
    seq_a = [DurationEngine(seed=1).compute(req) for _ in range(50)]
    seq_b = [DurationEngine(seed=2).compute(req) for _ in range(50)]
    assert seq_a != seq_b


# --- fallback / bordas ----------------------------------------------------

def test_last_note_without_gap_uses_default_duration():
    engine = DurationEngine(seed=1)
    for articulation in ("ghost", "staccato", "tight", "open"):
        dur = engine.compute(DurationRequest(articulation=articulation))
        assert dur == DEFAULT_LAST_NOTE_MS


def test_duration_clamped_to_minimum_when_gap_smaller_than_safety():
    engine = DurationEngine(seed=1)
    dur = engine.compute(
        DurationRequest(articulation="ghost", gap_ms=5.0)
    )
    assert dur >= MIN_DURATION_MS


# --- validacao ------------------------------------------------------------

def test_unknown_articulation_raises():
    engine = DurationEngine(seed=1)
    with pytest.raises(ValueError, match="unknown articulation"):
        engine.compute(DurationRequest(articulation="not_an_articulation"))


def test_sustain_bars_less_than_one_raises():
    engine = DurationEngine(seed=1)
    with pytest.raises(ValueError, match="sustain_bars"):
        engine.compute(DurationRequest(
            articulation="sustained",
            bar_boundaries_ms=(1000.0,),
            sustain_bars=0,
        ))
