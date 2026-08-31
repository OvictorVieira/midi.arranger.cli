"""US-002 — StyleProfile plumbing em tools/humanize.py.

Cobre:
- AC-05: perfil customizado muda a FAIXA lida por `base_velocity` e pelos
  engines.
- Regressao: chamada antiga (sem `profile`) e chamada com
  `StyleProfile.default()` produzem sequencias IDENTICAS — assinatura
  antiga preservada, comportamento inalterado quando nao ha override.
"""

from __future__ import annotations

from tools.constants import GATE_RATIOS, TIMING_JITTER_MS, VELOCITY_RANGES
from tools.humanize import (
    DurationEngine,
    DurationRequest,
    MicrotimingEngine,
    MicrotimingRequest,
    VelocityEngine,
    VelocityRequest,
    base_velocity,
)
from tools.style_profile import StyleProfile

# --- AC-05: perfil muda faixa -----------------------------------------------

def test_base_velocity_reads_from_profile_ranges():
    profile = StyleProfile(
        velocity_ranges={**dict(VELOCITY_RANGES), "ghost": (5, 10)},
    )
    assert base_velocity("ghost", profile=profile) == 7
    assert base_velocity("ghost") == (20 + 50) // 2


def test_velocity_engine_uses_profile_base():
    profile = StyleProfile(
        velocity_ranges={**dict(VELOCITY_RANGES), "ghost": (5, 10)},
    )
    engine = VelocityEngine(seed=1, profile=profile)
    req = VelocityRequest(role="ghost", metric_position="downbeat", articulation="ghost")
    v_profiled = engine.compute(req, track="t")
    v_default = VelocityEngine(seed=1).compute(req, track="t")
    assert v_profiled != v_default
    assert 1 <= v_profiled <= 127


def test_microtiming_engine_uses_profile_timing():
    tighter = StyleProfile(
        timing_jitter_ms={**dict(TIMING_JITTER_MS), "normal": (0, 1)},
    )
    req = MicrotimingRequest(sync_role="kick_support")
    tight_engine = MicrotimingEngine(seed=42, profile=tighter)
    default_engine = MicrotimingEngine(seed=42)
    tight_offsets = [tight_engine.compute(req) for _ in range(200)]
    default_offsets = [default_engine.compute(req) for _ in range(200)]
    assert max(abs(x) for x in tight_offsets) < max(abs(x) for x in default_offsets)


def test_duration_engine_uses_profile_gate_ratios():
    fat = StyleProfile(
        gate_ratios={**dict(GATE_RATIOS), "tight": (0.95, 0.99)},
    )
    req = DurationRequest(articulation="tight", gap_ms=1000.0)
    fat_dur = DurationEngine(seed=1, profile=fat).compute(req)
    default_dur = DurationEngine(seed=1).compute(req)
    assert fat_dur > default_dur


# --- Regressao: chamada antiga == chamada com StyleProfile.default() ---------
#
# Nenhum chamador existente passa `profile`. A regra e simples: `default()`
# reproduz `constants.py` byte a byte, entao as duas chamadas tem que
# produzir a mesma sequencia. Se algum dia StyleProfile.default() divergir de
# constants.py, este teste quebra.

_VELOCITY_INPUTS = [
    VelocityRequest(role="normal", metric_position="downbeat", aligned_with_kick=True,
                    phrase_position=0.5, articulation="accent", stroke="down"),
    VelocityRequest(role="ghost", metric_position="weak", articulation="ghost"),
    VelocityRequest(role="mid", metric_position="strong", aligned_with_snare=True,
                    phrase_position=0.25, articulation="tight", stroke="up"),
    VelocityRequest(role="accent", metric_position="sub", phrase_position=0.9,
                    articulation="open"),
    VelocityRequest(role="extreme", metric_position="downbeat", phrase_position=0.1,
                    articulation="sustained", stroke="down"),
    VelocityRequest(role="tied_soft", metric_position="strong", phrase_position=0.75,
                    articulation="staccato", stroke="none"),
]


_VELOCITY_BASELINE = [118, 20, 87, 109, 127, 67]


def test_velocity_engine_no_profile_matches_baseline():
    without = VelocityEngine(seed=2026)
    with_default = VelocityEngine(seed=2026, profile=StyleProfile.default())
    got_no = [without.compute(r, track=f"t{i%2}") for i, r in enumerate(_VELOCITY_INPUTS)]
    got_default = [with_default.compute(r, track=f"t{i%2}") for i, r in enumerate(_VELOCITY_INPUTS)]
    assert got_no == _VELOCITY_BASELINE
    assert got_default == _VELOCITY_BASELINE


_MICROTIMING_INPUTS = [
    MicrotimingRequest(sync_role="kick_support"),
    MicrotimingRequest(sync_role="anticipation"),
    MicrotimingRequest(sync_role="response"),
    MicrotimingRequest(sync_role="exact_anchor"),
    MicrotimingRequest(sync_role="sustain_through"),
    MicrotimingRequest(sync_role="ghost_fill", directional_bias_ms=-2),
    MicrotimingRequest(sync_role="kick_support", is_anchor=True),
]


_MICROTIMING_BASELINE = [10, 7, -11, -2, -2, 6, 0]


def test_microtiming_engine_no_profile_matches_baseline():
    without = MicrotimingEngine(seed=2026)
    with_default = MicrotimingEngine(seed=2026, profile=StyleProfile.default())
    assert [without.compute(r) for r in _MICROTIMING_INPUTS] == _MICROTIMING_BASELINE
    assert [with_default.compute(r) for r in _MICROTIMING_INPUTS] == _MICROTIMING_BASELINE


_DURATION_INPUTS = [
    DurationRequest(articulation="ghost", gap_ms=500.0),
    DurationRequest(articulation="tight", gap_ms=250.0),
    DurationRequest(articulation="open", gap_ms=100.0),
    DurationRequest(articulation="sustained", gap_ms=800.0, sustain_bars=2,
                    bar_boundaries_ms=(400.0, 900.0)),
    DurationRequest(articulation="let_ring", gap_ms=1200.0, sustain_bars=1,
                    bar_boundaries_ms=(600.0,)),
    DurationRequest(articulation="staccato", gap_ms=200.0, is_legato=True),
]


_DURATION_BASELINE = [
    86.9119884963963,
    177.63836653771878,
    85.0,
    785.0,
    585.0,
    207.0527370101392,
]


def test_duration_engine_no_profile_matches_baseline():
    without = DurationEngine(seed=2026)
    with_default = DurationEngine(seed=2026, profile=StyleProfile.default())
    assert [without.compute(r) for r in _DURATION_INPUTS] == _DURATION_BASELINE
    assert [with_default.compute(r) for r in _DURATION_INPUTS] == _DURATION_BASELINE
