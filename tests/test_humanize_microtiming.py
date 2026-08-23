"""Testes do motor de microtiming (humanize.py)."""

from __future__ import annotations

import statistics

import pytest

from tools.constants import SYNC_ROLES, TIMING_JITTER_MS
from tools.humanize import (
    ANCHOR_MAX_ABS_MS,
    DEFAULT_DIRECTIONAL_BIAS_MS,
    SYNC_ROLE_BUCKET,
    MicrotimingEngine,
    MicrotimingRequest,
)

# --- tabelas de configuracao ------------------------------------------------

def test_every_sync_role_maps_to_a_known_bucket():
    for role in SYNC_ROLES:
        assert role in SYNC_ROLE_BUCKET, role
        assert SYNC_ROLE_BUCKET[role] in TIMING_JITTER_MS


def test_every_sync_role_has_default_directional_bias():
    for role in SYNC_ROLES:
        assert role in DEFAULT_DIRECTIONAL_BIAS_MS


def test_only_anticipation_and_response_have_nonzero_default_bias():
    for role, bias in DEFAULT_DIRECTIONAL_BIAS_MS.items():
        if role in {"anticipation", "response"}:
            assert bias != 0
        else:
            assert bias == 0
    assert DEFAULT_DIRECTIONAL_BIAS_MS["anticipation"] < 0  # early
    assert DEFAULT_DIRECTIONAL_BIAS_MS["response"] > 0      # late


def test_anchor_max_matches_timing_jitter_bucket():
    assert TIMING_JITTER_MS["anchor"][1] == ANCHOR_MAX_ABS_MS


# --- comportamento de ancora ------------------------------------------------

def test_anchor_note_never_leaves_three_ms_regardless_of_role_or_bias():
    """AC: 'nota de ancora fica dentro de +/- 3ms'.
    Vale ate quando o usuario configura um bias absurdo — ancora ignora."""
    engine = MicrotimingEngine(seed=42)
    for role in SYNC_ROLES:
        for _ in range(200):
            req = MicrotimingRequest(
                sync_role=role,
                is_anchor=True,
                directional_bias_ms=999,
            )
            offset = engine.compute(req)
            assert -ANCHOR_MAX_ABS_MS <= offset <= ANCHOR_MAX_ABS_MS


def test_exact_anchor_role_stays_within_anchor_bucket():
    engine = MicrotimingEngine(seed=7)
    req = MicrotimingRequest(sync_role="exact_anchor")
    for _ in range(500):
        offset = engine.compute(req)
        assert -ANCHOR_MAX_ABS_MS <= offset <= ANCHOR_MAX_ABS_MS


def test_guitar_unison_role_stays_within_anchor_bucket():
    """AC: 'guitar_unison aproximadamente 0ms' — bucket anchor."""
    assert SYNC_ROLE_BUCKET["guitar_unison"] == "anchor"
    engine = MicrotimingEngine(seed=11)
    req = MicrotimingRequest(sync_role="guitar_unison")
    for _ in range(500):
        offset = engine.compute(req)
        assert -ANCHOR_MAX_ABS_MS <= offset <= ANCHOR_MAX_ABS_MS


# --- comportamento por sync_role --------------------------------------------

def test_kick_support_bias_and_spread_track_normal_bucket():
    """kick_support => bucket 'normal' (3, 8): bias ~+3ms, sigma ~8ms."""
    engine = MicrotimingEngine(seed=99)
    req = MicrotimingRequest(sync_role="kick_support")
    offsets = [engine.compute(req) for _ in range(2000)]
    mean = statistics.fmean(offsets)
    stdv = statistics.stdev(offsets)
    assert 1.5 <= mean <= 4.5
    assert 6.5 <= stdv <= 9.5


def test_anticipation_default_biases_early():
    """AC: 'anticipation adiantado'."""
    engine = MicrotimingEngine(seed=13)
    offsets = [
        engine.compute(MicrotimingRequest(sync_role="anticipation"))
        for _ in range(500)
    ]
    assert statistics.fmean(offsets) < 0


def test_response_default_biases_late():
    """AC: 'response atrasado'."""
    engine = MicrotimingEngine(seed=17)
    offsets = [
        engine.compute(MicrotimingRequest(sync_role="response"))
        for _ in range(500)
    ]
    assert statistics.fmean(offsets) > 0


def test_sustain_through_uses_free_fill_bucket():
    """AC: 'sustain_through livre' — mapeado ao bucket 'fill' (0, 15)."""
    assert SYNC_ROLE_BUCKET["sustain_through"] == "fill"
    engine = MicrotimingEngine(seed=19)
    offsets = [
        engine.compute(MicrotimingRequest(sync_role="sustain_through"))
        for _ in range(2000)
    ]
    assert 12.0 <= statistics.stdev(offsets) <= 18.0
    assert abs(statistics.fmean(offsets)) <= 1.5


def test_ghost_fill_matches_intermediate_bucket():
    """AC: 'ghost_fill 5 +/- 12ms' — bucket 'intermediate' (5, 12)."""
    assert SYNC_ROLE_BUCKET["ghost_fill"] == "intermediate"
    engine = MicrotimingEngine(seed=23)
    offsets = [
        engine.compute(MicrotimingRequest(sync_role="ghost_fill"))
        for _ in range(2000)
    ]
    mean = statistics.fmean(offsets)
    stdv = statistics.stdev(offsets)
    assert 3.5 <= mean <= 6.5
    assert 10.5 <= stdv <= 13.5


# --- calibracao empirica (secao 7 do spec) ---------------------------------

def test_hand_played_track_matches_empirical_range():
    """AC: 'mean(abs(offset)) de uma track normal cai entre 19 e 24ms com
    stddev proximo de 12ms' — numeros da ANCORA fixture (spec secao 7).

    Modelado com sync_role='ghost_fill' (bucket intermediate, sigma 12ms)
    e directional_bias configurado pelo elemento para replicar o vies
    tardio tipico de teclado tocado a mao.
    """
    engine = MicrotimingEngine(seed=1234)
    req = MicrotimingRequest(
        sync_role="ghost_fill",
        is_anchor=False,
        directional_bias_ms=15,
    )
    offsets = [engine.compute(req) for _ in range(2000)]
    mean_abs = statistics.fmean(abs(o) for o in offsets)
    stddev = statistics.stdev(offsets)
    assert 19.0 <= mean_abs <= 24.0
    assert 10.5 <= stddev <= 13.5


# --- reprodutibilidade ------------------------------------------------------

def test_same_seed_produces_identical_sequence():
    """AC: 'duas execucoes com a mesma seed produzem offsets identicos'."""
    requests = [
        MicrotimingRequest(sync_role="kick_support"),
        MicrotimingRequest(sync_role="exact_anchor"),
        MicrotimingRequest(sync_role="ghost_fill", directional_bias_ms=-5),
        MicrotimingRequest(sync_role="response"),
        MicrotimingRequest(sync_role="guitar_unison"),
        MicrotimingRequest(sync_role="sustain_through"),
    ] * 50

    a = MicrotimingEngine(seed=2026)
    b = MicrotimingEngine(seed=2026)
    seq_a = [a.compute(r) for r in requests]
    seq_b = [b.compute(r) for r in requests]
    assert seq_a == seq_b


def test_different_seeds_diverge():
    req = MicrotimingRequest(sync_role="ghost_fill")
    seq_a = [MicrotimingEngine(seed=1).compute(req) for _ in range(80)]
    seq_b = [MicrotimingEngine(seed=2).compute(req) for _ in range(80)]
    assert seq_a != seq_b


# --- vies por elemento, nunca global ---------------------------------------

def test_directional_bias_is_per_element():
    """AC: 'Vies direcional e configuravel por elemento, nunca global'.

    Dois elementos com bias opostos rodando alternadamente no mesmo engine
    (mesma seed): cada um preserva seu vies sem contaminar o outro.
    """
    engine = MicrotimingEngine(seed=77)
    early = MicrotimingRequest(sync_role="ghost_fill", directional_bias_ms=-25)
    late = MicrotimingRequest(sync_role="ghost_fill", directional_bias_ms=+25)

    early_offsets: list[int] = []
    late_offsets: list[int] = []
    for _ in range(400):
        early_offsets.append(engine.compute(early))
        late_offsets.append(engine.compute(late))

    m_early = statistics.fmean(early_offsets)
    m_late = statistics.fmean(late_offsets)
    assert m_early < -10
    assert m_late > 10
    # separacao esperada ~= 2 * 25 = 50ms
    assert m_late - m_early > 30


def test_default_bias_overridden_by_explicit_zero():
    """directional_bias_ms=0 deve zerar o default do sync_role, nao ser
    lido como 'ausente'."""
    req_default = MicrotimingRequest(sync_role="anticipation")
    req_zero = MicrotimingRequest(sync_role="anticipation", directional_bias_ms=0)

    def mean_of(engine_seed: int, req: MicrotimingRequest, n: int = 800) -> float:
        eng = MicrotimingEngine(seed=engine_seed)
        return statistics.fmean(eng.compute(req) for _ in range(n))

    mean_default = mean_of(31, req_default)
    mean_zero = mean_of(31, req_zero)

    # bucket intermediate bias = 5; anticipation default = -8
    # default total: 5 + (-8) = -3
    # explicit zero: 5 + 0 = +5
    assert mean_default < -1
    assert mean_zero > 3


# --- validacao --------------------------------------------------------------

def test_unknown_sync_role_raises():
    engine = MicrotimingEngine(seed=1)
    with pytest.raises(ValueError, match="unknown sync_role"):
        engine.compute(MicrotimingRequest(sync_role="not_a_role"))
