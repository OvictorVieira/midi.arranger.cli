"""Perfil de estilo overridable para o motor de humanizacao por profile.

Carrega os tres dicionarios que `tools/humanize.py` e `tools/edits.py` hoje leem
como constantes de modulo de `tools/constants.py`. O default reproduz
EXATAMENTE os numeros de `constants.py`, que continua sendo a fonte declarada.

Este arquivo NAO substitui `constants.py`: apenas oferece um wrapper imutavel
que pode ser trocado por chamador que queira mudar as FAIXAS de entrada da
humanizacao, sem mudar a formula de calculo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from tools.constants import GATE_RATIOS, TIMING_JITTER_MS, VELOCITY_RANGES

RangeMap = Mapping[str, tuple[float, float]]


def _freeze(ranges: Mapping[str, tuple[float, float]]) -> Mapping[str, tuple[float, float]]:
    return MappingProxyType({str(k): (float(v[0]), float(v[1])) for k, v in ranges.items()})


def _validate(ranges: Mapping[str, tuple[float, float]], *, label: str) -> None:
    if not isinstance(ranges, Mapping):
        raise ValueError(f"{label}: esperado mapping, recebido {type(ranges).__name__}")
    for key, value in ranges.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label}: chave invalida {key!r}")
        try:
            lo, hi = value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}[{key!r}]: esperado par (lo, hi)") from exc
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise ValueError(f"{label}[{key!r}]: lo/hi devem ser numericos")
        if lo > hi:
            raise ValueError(f"{label}[{key!r}]: lo ({lo}) > hi ({hi})")


@dataclass(frozen=True)
class StyleProfile:
    """Perfil imutavel de humanizacao por profile.

    `default()` reproduz `constants.py` byte a byte. Validacao estrutural (par
    (lo, hi), lo <= hi, chave string nao vazia) roda no construtor. Sanidade
    fisica adicional (bounds MIDI, teto de jitter) fica em story posterior.
    """

    velocity_ranges: RangeMap = field(default_factory=lambda: _freeze(VELOCITY_RANGES))
    gate_ratios: RangeMap = field(default_factory=lambda: _freeze(GATE_RATIOS))
    timing_jitter_ms: RangeMap = field(default_factory=lambda: _freeze(TIMING_JITTER_MS))

    def __post_init__(self) -> None:
        _validate(self.velocity_ranges, label="velocity_ranges")
        _validate(self.gate_ratios, label="gate_ratios")
        _validate(self.timing_jitter_ms, label="timing_jitter_ms")
        object.__setattr__(self, "velocity_ranges", _freeze(self.velocity_ranges))
        object.__setattr__(self, "gate_ratios", _freeze(self.gate_ratios))
        object.__setattr__(self, "timing_jitter_ms", _freeze(self.timing_jitter_ms))

    @classmethod
    def default(cls) -> StyleProfile:
        return cls(
            velocity_ranges=_freeze(VELOCITY_RANGES),
            gate_ratios=_freeze(GATE_RATIOS),
            timing_jitter_ms=_freeze(TIMING_JITTER_MS),
        )


__all__ = ["StyleProfile"]
