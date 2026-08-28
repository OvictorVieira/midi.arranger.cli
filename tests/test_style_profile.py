"""Testes de `tools.style_profile.StyleProfile` (US-001)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tools import constants
from tools.style_profile import StyleProfile


def test_default_reproduces_constants_exactamente() -> None:
    profile = StyleProfile.default()
    assert dict(profile.velocity_ranges) == constants.VELOCITY_RANGES
    assert dict(profile.gate_ratios) == constants.GATE_RATIOS
    assert dict(profile.timing_jitter_ms) == constants.TIMING_JITTER_MS


def test_default_preserves_every_key_from_constants() -> None:
    profile = StyleProfile.default()
    assert set(profile.velocity_ranges) == set(constants.VELOCITY_RANGES)
    assert set(profile.gate_ratios) == set(constants.GATE_RATIOS)
    assert set(profile.timing_jitter_ms) == set(constants.TIMING_JITTER_MS)


def test_dataclass_is_frozen() -> None:
    profile = StyleProfile.default()
    with pytest.raises(FrozenInstanceError):
        profile.velocity_ranges = {}  # type: ignore[misc]


def test_mappings_are_read_only() -> None:
    profile = StyleProfile.default()
    with pytest.raises(TypeError):
        profile.velocity_ranges["ghost"] = (0.0, 1.0)  # type: ignore[index]


def test_rejects_range_with_lo_greater_than_hi() -> None:
    with pytest.raises(ValueError, match="lo"):
        StyleProfile(velocity_ranges={"ghost": (50, 10)})


def test_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="chave invalida"):
        StyleProfile(gate_ratios={"": (0.1, 0.2)})


def test_rejects_non_pair_range() -> None:
    with pytest.raises(ValueError, match="par"):
        StyleProfile(timing_jitter_ms={"anchor": (1, 2, 3)})  # type: ignore[arg-type]


def test_accepts_custom_ranges_and_freezes_them() -> None:
    profile = StyleProfile(velocity_ranges={"ghost": (5, 10)})
    assert profile.velocity_ranges["ghost"] == (5.0, 10.0)
    with pytest.raises(TypeError):
        profile.velocity_ranges["ghost"] = (0, 0)  # type: ignore[index]


def test_rejects_velocity_out_of_midi_bounds() -> None:
    with pytest.raises(ValueError, match="fora dos limites fisicos"):
        StyleProfile(velocity_ranges={"ghost": (0, 200)})
    with pytest.raises(ValueError, match="fora dos limites fisicos"):
        StyleProfile(velocity_ranges={"ghost": (-1, 100)})


def test_rejects_gate_ratio_above_one() -> None:
    with pytest.raises(ValueError, match="fora dos limites fisicos"):
        StyleProfile(gate_ratios={"open": (0.5, 1.5)})


def test_rejects_absurd_timing_jitter() -> None:
    with pytest.raises(ValueError, match="fora dos limites fisicos"):
        StyleProfile(timing_jitter_ms={"fill": (0, 5_000)})


def test_rejects_non_mapping_argument() -> None:
    with pytest.raises(ValueError, match="esperado mapping"):
        StyleProfile(velocity_ranges=[("ghost", (5, 10))])  # type: ignore[arg-type]


def test_rejects_non_numeric_range_bounds() -> None:
    with pytest.raises(ValueError, match="numericos"):
        StyleProfile(gate_ratios={"open": ("0.5", 0.8)})  # type: ignore[dict-item]
