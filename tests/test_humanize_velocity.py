"""Testes do motor de velocity (humanize.py)."""

from __future__ import annotations

import random

import pytest

from tools.constants import VELOCITY_RANGES
from tools.humanize import (
    ARTICULATION_ADJUST,
    KICK_ALIGN_BONUS,
    MAX_JITTER_AMPLITUDE,
    MAX_VELOCITY,
    MIN_VELOCITY,
    SNARE_ALIGN_BONUS,
    VelocityComponents,
    VelocityEngine,
    VelocityRequest,
    articulation_adjust,
    base_velocity,
    drum_alignment,
    jitter,
    metric_accent,
    phrase_shape,
    stroke_adjust,
)

# --- componentes puros ------------------------------------------------------

def test_base_velocity_is_midpoint_of_range():
    for role, (lo, hi) in VELOCITY_RANGES.items():
        assert base_velocity(role) == (lo + hi) // 2


def test_base_velocity_unknown_role_raises():
    with pytest.raises(ValueError):
        base_velocity("nonexistent")


def test_metric_accent_downbeat_greater_than_weak_greater_than_sub():
    assert metric_accent("downbeat") > metric_accent("strong") > 0
    assert metric_accent("weak") < 0
    assert metric_accent("sub") < metric_accent("weak")


def test_metric_accent_unknown_position_raises():
    with pytest.raises(ValueError):
        metric_accent("swing")


def test_drum_alignment_kick_and_snare_stack():
    assert drum_alignment(aligned_with_kick=False, aligned_with_snare=False) == 0
    assert drum_alignment(aligned_with_kick=True, aligned_with_snare=False) == KICK_ALIGN_BONUS
    assert drum_alignment(aligned_with_kick=False, aligned_with_snare=True) == SNARE_ALIGN_BONUS
    assert drum_alignment(aligned_with_kick=True, aligned_with_snare=True) == KICK_ALIGN_BONUS + SNARE_ALIGN_BONUS


def test_phrase_shape_peaks_in_the_middle_and_dips_at_the_edges():
    assert phrase_shape(0.5) > phrase_shape(0.0)
    assert phrase_shape(0.5) > phrase_shape(1.0)
    # Sinusoide: pontas simetricas.
    assert phrase_shape(0.0) == phrase_shape(1.0)


def test_phrase_shape_out_of_range_raises():
    with pytest.raises(ValueError):
        phrase_shape(-0.1)
    with pytest.raises(ValueError):
        phrase_shape(1.1)


def test_articulation_ghost_negative_accent_positive_normal_zero():
    assert articulation_adjust("ghost") < 0
    assert articulation_adjust("accent") > 0
    assert articulation_adjust("normal") == 0


def test_articulation_unknown_raises():
    with pytest.raises(ValueError):
        articulation_adjust("bogus")


def test_stroke_down_positive_up_negative_none_zero():
    assert stroke_adjust("down") > 0
    assert stroke_adjust("up") < 0
    assert stroke_adjust("none") == 0


def test_jitter_zero_amplitude_returns_zero():
    rng = random.Random(0)
    assert jitter(rng, 0) == 0


def test_jitter_bounded_by_amplitude():
    rng = random.Random(42)
    for _ in range(500):
        v = jitter(rng, 5)
        assert -5 <= v <= 5


def test_jitter_negative_amplitude_raises():
    with pytest.raises(ValueError):
        jitter(random.Random(0), -1)


# --- VelocityComponents -----------------------------------------------------

def test_components_total_and_intent():
    c = VelocityComponents(
        base=90,
        metric_accent=+6,
        drum_align=+5,
        phrase_shape=+4,
        articulation=-3,
        stroke=+3,
        jitter=-2,
    )
    assert c.total == 90 + 6 + 5 + 4 - 3 + 3 - 2
    # Intent exclui base e jitter.
    assert c.deterministic_intent == 6 + 5 + 4 + 3 + 3


# --- comportamento musical --------------------------------------------------

def test_downbeat_kick_beats_weak_offbeat_for_same_role_and_seed():
    """Nota em downbeat alinhada com kick tem que ganhar de nota em
    subdivisao fraca sem alinhamento — a diferenca deterministica
    (13 pontos) excede a amplitude maxima de dois jitters (6 pontos)."""
    eng = VelocityEngine(seed=123, jitter_amplitude=3)
    hi = eng.compute(
        VelocityRequest(
            role="normal",
            metric_position="downbeat",
            aligned_with_kick=True,
        ),
        track="lead",
    )
    lo = eng.compute(
        VelocityRequest(
            role="normal",
            metric_position="weak",
            aligned_with_kick=False,
        ),
        track="lead",
    )
    assert hi > lo


def test_same_seed_produces_same_sequence():
    reqs = [
        VelocityRequest(role="normal", metric_position="downbeat", aligned_with_kick=True),
        VelocityRequest(role="mid", metric_position="weak", articulation="staccato"),
        VelocityRequest(role="accent", metric_position="strong", stroke="down"),
        VelocityRequest(role="ghost", metric_position="sub", articulation="ghost"),
    ]
    a = VelocityEngine(seed=7, jitter_amplitude=3)
    b = VelocityEngine(seed=7, jitter_amplitude=3)
    out_a = [a.compute(r, track="t") for r in reqs]
    out_b = [b.compute(r, track="t") for r in reqs]
    assert out_a == out_b


def test_different_seeds_diverge():
    req = VelocityRequest(
        role="normal", metric_position="downbeat", aligned_with_kick=True
    )
    a = VelocityEngine(seed=1, jitter_amplitude=3)
    b = VelocityEngine(seed=2, jitter_amplitude=3)
    out_a = [a.compute(req, track="t") for _ in range(50)]
    out_b = [b.compute(req, track="t") for _ in range(50)]
    assert out_a != out_b


# --- regra de ferro: jitter < intencao --------------------------------------

def test_engine_rejects_amplitude_above_sanity_ceiling_at_construction():
    """Amplitude absurda e erro de programacao e tem que falhar na
    construcao — cedo e uma vez so, nao a cada nota."""
    with pytest.raises(ValueError, match="sanity ceiling"):
        VelocityEngine(seed=0, jitter_amplitude=MAX_JITTER_AMPLITUDE + 1)
    with pytest.raises(ValueError, match="sanity ceiling"):
        VelocityEngine(seed=0, jitter_amplitude=100)
    # No teto exato ainda e aceito.
    VelocityEngine(seed=0, jitter_amplitude=MAX_JITTER_AMPLITUDE)


def test_jitter_is_clamped_strictly_below_intent():
    """Regra de ferro por nota: o jitter efetivo nunca alcanca a intencao.

    Nota de intencao alta (6+5+6+8+3 = 28) com amplitude 20 mantem os 20;
    a mesma amplitude numa nota de intencao 6 e clampada para 5.
    """
    hi_intent = VelocityRequest(
        role="normal",
        metric_position="downbeat",
        aligned_with_kick=True,
        phrase_position=0.5,
        articulation="accent",
        stroke="down",
    )
    lo_intent = VelocityRequest(
        role="normal",
        metric_position="weak",     # -2
        phrase_position=0.0,        # -4
        articulation="normal",      # 0
        stroke="none",              # 0
    )
    eng = VelocityEngine(seed=0, jitter_amplitude=MAX_JITTER_AMPLITUDE)
    assert eng._deterministic_components(hi_intent).deterministic_intent == 28
    assert eng._deterministic_components(lo_intent).deterministic_intent == 6

    # Amostra o desvio real do componente base+deterministico para cada nota.
    for req, intent in ((hi_intent, 28), (lo_intent, 6)):
        det = eng._deterministic_components(req)
        floor = det.total - det.jitter          # soma sem jitter
        seen = set()
        e = VelocityEngine(seed=7, jitter_amplitude=MAX_JITTER_AMPLITUDE)
        for _ in range(400):
            seen.add(e.compute(req, track="t") - floor)
        bound = min(MAX_JITTER_AMPLITUDE, intent - 1)
        assert max(abs(d) for d in seen) <= bound, (
            f"jitter excedeu {bound} numa nota de intencao {intent}"
        )


def test_low_intent_note_does_not_raise_regression():
    """REGRESSAO: nota legitima de intencao baixa derrubava o motor.

    `metric_accent` tem minimo em modulo de 2 (`weak`) e os outros quatro
    componentes podem zerar — `phrase_shape` cruza zero em 70 das 1001
    posicoes de frase. Com `DEFAULT_JITTER_AMPLITUDE = 3`, a checagem antiga
    (`amplitude >= intent`, ou seja `3 >= 2`) levantava `ValueError` numa nota
    perfeitamente normal. Arpejo toca majoritariamente em subdivisao fraca,
    entao isso derrubaria a rodada 2 na primeira execucao.
    """
    eng = VelocityEngine(seed=42)          # usa DEFAULT_JITTER_AMPLITUDE = 3
    req = VelocityRequest(
        role="normal",
        metric_position="weak",
        aligned_with_kick=False,
        aligned_with_snare=False,
        phrase_position=0.114,             # phrase_shape == 0
        articulation="normal",
        stroke="none",
    )
    assert eng._deterministic_components(req).deterministic_intent == 2
    v = eng.compute(req, track="arp")      # nao pode levantar
    assert MIN_VELOCITY <= v <= MAX_VELOCITY


def test_every_zero_crossing_of_phrase_shape_survives_default_engine():
    """Varre as 1001 posicoes de frase com a configuracao default e a nota
    mais fraca possivel. Nenhuma pode derrubar o motor."""
    eng = VelocityEngine(seed=1)
    for i in range(1001):
        req = VelocityRequest(
            role="normal",
            metric_position="weak",
            phrase_position=i / 1000,
            articulation="normal",
            stroke="none",
        )
        v = eng.compute(req, track="sweep")
        assert MIN_VELOCITY <= v <= MAX_VELOCITY


# --- clamp e regra do 127 unico ---------------------------------------------

def test_result_always_between_1_and_127():
    eng = VelocityEngine(seed=99, jitter_amplitude=3)
    combos = [
        VelocityRequest(role=r, metric_position=p, articulation=a)
        for r in VELOCITY_RANGES
        for p in ("downbeat", "strong", "weak", "sub")
        for a in ARTICULATION_ADJUST
    ]
    for req in combos:
        v = eng.compute(req, track=f"t-{req.role}-{req.metric_position}-{req.articulation}")
        assert MIN_VELOCITY <= v <= MAX_VELOCITY


def test_never_emits_127_more_than_once_per_track():
    """Dois picos consecutivos no mesmo track: o segundo tem que ser
    rebaixado para 126."""
    # Setup que garante estouro do teto: extreme + downbeat + kick + accent
    # + down + phrase pico = 120 + 6 + 5 + 6 + 8 + 3 = 148 -> clamp 127.
    req = VelocityRequest(
        role="extreme",
        metric_position="downbeat",
        aligned_with_kick=True,
        phrase_position=0.5,
        articulation="accent",
        stroke="down",
    )
    eng = VelocityEngine(seed=0, jitter_amplitude=3)
    first = eng.compute(req, track="lead")
    second = eng.compute(req, track="lead")
    assert first == MAX_VELOCITY
    assert second == MAX_VELOCITY - 1


def test_different_tracks_can_both_hit_127():
    req = VelocityRequest(
        role="extreme",
        metric_position="downbeat",
        aligned_with_kick=True,
        phrase_position=0.5,
        articulation="accent",
        stroke="down",
    )
    eng = VelocityEngine(seed=0, jitter_amplitude=3)
    assert eng.compute(req, track="lead") == MAX_VELOCITY
    assert eng.compute(req, track="rhythm") == MAX_VELOCITY


def test_engine_negative_jitter_amplitude_rejected_at_construction():
    with pytest.raises(ValueError):
        VelocityEngine(seed=0, jitter_amplitude=-1)
