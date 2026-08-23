"""Testes das constantes numericas do pacote tools."""

from tools import constants


def test_velocity_ranges_are_tuples_within_midi_range():
    for name, rng in constants.VELOCITY_RANGES.items():
        assert isinstance(rng, tuple) and len(rng) == 2, name
        lo, hi = rng
        assert isinstance(lo, int) and isinstance(hi, int), name
        assert lo <= hi, name
        assert 0 <= lo <= 127, name
        assert 0 <= hi <= 127, name


def test_gate_ratios_are_ordered_pairs_between_zero_and_one():
    for name, rng in constants.GATE_RATIOS.items():
        lo, hi = rng
        assert 0.0 <= lo <= hi <= 1.0, name


def test_timing_jitter_ranges_are_ordered_ms_pairs():
    for name, rng in constants.TIMING_JITTER_MS.items():
        lo, hi = rng
        assert lo <= hi, name
        assert lo >= 0, name


def test_overlap_ranges_are_ordered():
    lo, hi = constants.LEGATO_OVERLAP_MS
    assert lo <= hi
    lo, hi = constants.SLIDE_OVERLAP_MS
    assert lo <= hi


def test_sync_roles_matches_spec():
    assert constants.SYNC_ROLES == (
        "exact_anchor",
        "kick_support",
        "guitar_unison",
        "anticipation",
        "response",
        "sustain_through",
        "ghost_fill",
    )


def test_register_bands_cover_full_midi_range_without_gap():
    order = ["sub", "low", "mid", "high"]
    prev_hi = -1
    for name in order:
        lo, hi = constants.REGISTER_BANDS[name]
        assert lo == prev_hi + 1, f"band {name} does not chain from previous"
        assert lo <= hi
        assert 0 <= lo <= 127 and 0 <= hi <= 127
        prev_hi = hi
    assert prev_hi == 127


def test_specific_velocity_values_match_spec():
    assert constants.VELOCITY_RANGES["ghost"] == (20, 50)
    assert constants.VELOCITY_RANGES["accent"] == (102, 118)
    assert constants.VELOCITY_RANGES["extreme"] == (115, 125)


def test_specific_gate_values_match_spec():
    assert constants.GATE_RATIOS["ghost"] == (0.15, 0.35)
    assert constants.GATE_RATIOS["sustained"] == (0.90, 1.00)


def test_specific_register_bands_match_spec():
    assert constants.REGISTER_BANDS["sub"] == (0, 35)
    assert constants.REGISTER_BANDS["low"] == (36, 47)
    assert constants.REGISTER_BANDS["mid"] == (48, 71)
    assert constants.REGISTER_BANDS["high"] == (72, 127)


def test_trigger_note_exported():
    assert constants.TRIGGER_NOTE == 60
