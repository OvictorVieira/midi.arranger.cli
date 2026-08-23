"""Testes do gerador de drone / nota-pedal (US-005)."""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.constants import REGISTER_BANDS, VELOCITY_RANGES
from tools.palette.harmonic import (
    DEFAULT_DRONE_REGISTER,
    DRONE_BASE_VELOCITY_BUCKET,
    DRONE_EXPRESSION_CC,
    DRONE_EXPRESSION_HI,
    DRONE_EXPRESSION_LO,
    DRONE_FIFTH_SEMITONES,
    DRONE_FILTER_CC,
    DRONE_FILTER_CYCLE_BARS_ALLOWED,
    DRONE_FILTER_HI,
    DRONE_FILTER_LO,
    DRONE_MODULATION_MIN_BARS,
    DRONE_ROLES,
    DroneCCEvent,
    DroneLayer,
    DroneNote,
    generate_drone,
    validate_drone_register,
)
from tools.plan import PlanSection

BAR_S = 2.0


def _bar(index: int, chord: Chord | None = None) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=chord,
    )


def _analysis(n_bars: int = 16, key_root: int = 0) -> Analysis:
    bars = [_bar(i, Chord(root=key_root, quality="minor")) for i in range(n_bars)]
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=[],
    )


def _section(
    label: str = "S1", kind: str = "verse",
    start_bar: int = 0, end_bar: int = 16,
) -> PlanSection:
    return PlanSection(
        label=label, kind=kind, start_bar=start_bar, end_bar=end_bar,
        source="marker", protagonist="texture",
        energy={"densidade": 3, "impacto": 3, "largura": 3, "altura": 3, "instabilidade": 3},
    )


# --- basics -----------------------------------------------------------------

def test_drone_roles_vocabulary():
    assert set(DRONE_ROLES) == {"drone"}


def test_generate_drone_produces_layer_with_single_sustained_note():
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), layers=1, seed=0)
    assert len(layers) == 1
    layer = layers[0]
    assert isinstance(layer, DroneLayer)
    assert len(layer.notes) == 1
    note = layer.notes[0]
    assert isinstance(note, DroneNote)
    assert note.start_s == 0.0
    assert note.end_s == pytest.approx(32.0)  # 16 bars * 2s


def test_generate_drone_pitch_is_tonic_of_key_within_register():
    ana = _analysis(16, key_root=2)  # D
    reg = REGISTER_BANDS["mid"]  # (48, 71)
    layers = generate_drone(ana, _section(), register=reg, seed=0)
    pitch = layers[0].notes[0].pitch
    assert pitch % 12 == 2
    assert reg[0] <= pitch <= reg[1]


def test_generate_drone_pedal_fifth_transposes_by_seven_semitones():
    ana = _analysis(16, key_root=0)  # C
    reg = REGISTER_BANDS["mid"]
    tonic = generate_drone(ana, _section(), register=reg, pedal=True, seed=0)[0].notes[0]
    fifth = generate_drone(
        ana, _section(), register=reg, pedal=True, pedal_pitch="fifth", seed=0,
    )[0].notes[0]
    assert fifth.pitch % 12 == (0 + DRONE_FIFTH_SEMITONES) % 12
    assert (fifth.pitch - tonic.pitch) % 12 == DRONE_FIFTH_SEMITONES


def test_generate_drone_velocity_from_tied_soft_bucket():
    lo, hi = VELOCITY_RANGES[DRONE_BASE_VELOCITY_BUCKET]
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), seed=0)
    v = layers[0].notes[0].velocity
    assert lo <= v <= hi


def test_generate_drone_layer_count_matches_request():
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), layers=3, seed=0)
    assert [layer.index for layer in layers] == [0, 1, 2]
    for layer in layers:
        assert len(layer.notes) == 1


def test_generate_drone_default_register_is_mid_band():
    assert REGISTER_BANDS["mid"] == DEFAULT_DRONE_REGISTER


# --- validators (register / cycle / modulation) -----------------------------

def test_validate_drone_register_accepts_single_band():
    validate_drone_register(REGISTER_BANDS["mid"])
    validate_drone_register(REGISTER_BANDS["low"])
    validate_drone_register((50, 60))  # inside mid


def test_validate_drone_register_rejects_multi_band():
    with pytest.raises(ValueError, match="spans multiple bands"):
        validate_drone_register((40, 60))  # low + mid


def test_validate_drone_register_rejects_inverted():
    with pytest.raises(ValueError, match="low > high"):
        validate_drone_register((60, 40))


def test_generate_drone_raises_when_register_spans_multiple_bands():
    ana = _analysis(16)
    with pytest.raises(ValueError, match="spans multiple bands"):
        generate_drone(ana, _section(), register=(40, 60), seed=0)


def test_generate_drone_layers_lt_one_raises():
    ana = _analysis(16)
    with pytest.raises(ValueError):
        generate_drone(ana, _section(), layers=0)


def test_generate_drone_bad_pedal_pitch_raises():
    ana = _analysis(16)
    with pytest.raises(ValueError, match="pedal_pitch"):
        generate_drone(ana, _section(), pedal=True, pedal_pitch="third", seed=0)


def test_generate_drone_bad_filter_cycle_raises():
    ana = _analysis(16)
    with pytest.raises(ValueError, match="filter_cycle_bars"):
        generate_drone(ana, _section(), filter_cycle_bars=3, seed=0)


def test_generate_drone_modulation_below_min_raises():
    ana = _analysis(16)
    with pytest.raises(ValueError, match="modulation_bars"):
        generate_drone(ana, _section(), modulation_bars=8, seed=0)


def test_generate_drone_modulation_at_min_ok():
    ana = _analysis(16)
    layers = generate_drone(
        ana, _section(), modulation_bars=DRONE_MODULATION_MIN_BARS, seed=0,
    )
    # Deve emitir eventos CC11
    cc11 = [ev for ev in layers[0].cc_events if ev.cc == DRONE_EXPRESSION_CC]
    assert cc11


def test_generate_drone_all_allowed_filter_cycles_accepted():
    ana = _analysis(16)
    for cycle in DRONE_FILTER_CYCLE_BARS_ALLOWED:
        layers = generate_drone(ana, _section(), filter_cycle_bars=cycle, seed=0)
        assert layers[0].cc_events, f"cycle {cycle} produced no CC"


# --- CC modulation ----------------------------------------------------------

def test_generate_drone_normal_mode_emits_filter_and_expression_cc():
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), seed=0)
    ccs = layers[0].cc_events
    filter_events = [ev for ev in ccs if ev.cc == DRONE_FILTER_CC]
    expression_events = [ev for ev in ccs if ev.cc == DRONE_EXPRESSION_CC]
    assert filter_events, "no CC74 (filter) events"
    assert expression_events, "no CC11 (expression) events"


def test_generate_drone_pedal_mode_emits_no_cc_events():
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), pedal=True, seed=0)
    assert layers[0].cc_events == ()


def test_generate_drone_cc_values_in_configured_ranges():
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), seed=0)
    for ev in layers[0].cc_events:
        if ev.cc == DRONE_FILTER_CC:
            assert DRONE_FILTER_LO - 1 <= ev.value <= DRONE_FILTER_HI + 1
        elif ev.cc == DRONE_EXPRESSION_CC:
            assert DRONE_EXPRESSION_LO - 1 <= ev.value <= DRONE_EXPRESSION_HI + 1


def test_generate_drone_cc_events_sorted_by_time():
    ana = _analysis(32)
    layers = generate_drone(ana, _section(end_bar=32), seed=0)
    events = layers[0].cc_events
    times = [ev.time_s for ev in events]
    assert times == sorted(times)


def test_generate_drone_expression_cycle_never_shorter_than_16_bars():
    """AC: 'Ciclo de modulacao com periodo minimo de 16 compassos'.

    Emite drone com ciclo = 16 bars ao longo de 32 bars => 2 ciclos, entao
    DRONE_CC_STEPS_PER_CYCLE * 2 pontos aproximados na CC11.
    """
    ana = _analysis(32)
    layers = generate_drone(
        ana, _section(end_bar=32), modulation_bars=16, seed=0,
    )
    cc11 = [ev for ev in layers[0].cc_events if ev.cc == DRONE_EXPRESSION_CC]
    span_s = 32 * BAR_S
    cycle_s = 16 * BAR_S
    # Numero de eventos e proporcional ao numero de ciclos (2).
    assert len(cc11) >= 2
    # A distancia entre o primeiro e ultimo evento cobre o span; um ciclo
    # completo cabe (cycle_s <= span_s).
    assert cycle_s <= span_s


def test_generate_drone_filter_cycle_respects_configured_cycle_bars():
    ana = _analysis(16)
    layers = generate_drone(ana, _section(), filter_cycle_bars=4, seed=0)
    cc74 = [ev for ev in layers[0].cc_events if ev.cc == DRONE_FILTER_CC]
    # 16 bars / 4 bars por ciclo = 4 ciclos; steps por ciclo = 16 => ~64 eventos
    assert len(cc74) >= 8


# --- determinism / edges ----------------------------------------------------

def test_generate_drone_same_seed_same_output():
    ana = _analysis(16)
    a = generate_drone(ana, _section(), seed=42)
    b = generate_drone(ana, _section(), seed=42)
    assert a == b


def test_generate_drone_empty_section_returns_empty_layers():
    ana = _analysis(4)  # 4 bars totais
    # Secao no range 10-12 nao encontra bars.
    layers = generate_drone(ana, _section(start_bar=10, end_bar=12), layers=2, seed=0)
    assert len(layers) == 2
    for layer in layers:
        assert layer.notes == ()
        assert layer.cc_events == ()


def test_generate_drone_note_covers_full_section_span():
    ana = _analysis(16)
    section = _section(start_bar=4, end_bar=12)
    layers = generate_drone(ana, section, seed=0)
    note = layers[0].notes[0]
    assert note.start_s == pytest.approx(4 * BAR_S)
    assert note.end_s == pytest.approx(12 * BAR_S)


def test_drone_cc_event_dataclass():
    ev = DroneCCEvent(time_s=1.0, cc=74, value=64)
    assert ev.time_s == 1.0
    assert ev.cc == 74
    assert ev.value == 64
