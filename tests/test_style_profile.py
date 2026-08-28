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


def test_rejects_nan_and_inf_in_ranges():
    """Achado 1 do review com o Codex no PR #60.

    `nan > x` e `nan < x` sao SEMPRE False, entao `(nan, nan)` atravessava
    tanto a checagem `lo > hi` quanto os limites fisicos sem disparar erro
    nenhum — o valor invalido so estourava tarde, dentro do render.
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finitos"):
            StyleProfile(
                velocity_ranges={"x": (bad, bad)},
                gate_ratios={}, timing_jitter_ms={},
            )


def test_default_preserves_int_type_from_constants():
    """Achado 3: `default()` convertia (20, 50) em (20.0, 50.0).

    Igualdade numerica (`20 == 20.0`) escondia isso, mas a promessa e
    reproduzir `constants.py` byte a byte — inclusive o tipo, para
    qualquer consumidor sensivel a isso (serializacao, por exemplo).
    """
    from tools.constants import GATE_RATIOS, TIMING_JITTER_MS, VELOCITY_RANGES

    default = StyleProfile.default()
    for key, value in VELOCITY_RANGES.items():
        got = default.velocity_ranges[key]
        assert type(got[0]) is type(value[0])
        assert type(got[1]) is type(value[1])
    for key, value in GATE_RATIOS.items():
        got = default.gate_ratios[key]
        assert type(got[0]) is type(value[0])
    for key, value in TIMING_JITTER_MS.items():
        got = default.timing_jitter_ms[key]
        assert type(got[0]) is type(value[0])
