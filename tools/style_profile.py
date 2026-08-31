"""Perfil de estilo overridable para o motor de humanizacao por profile.

Carrega os tres dicionarios que `tools/humanize.py` e `tools/edits.py` hoje leem
como constantes de modulo de `tools/constants.py`. O default reproduz
EXATAMENTE os numeros de `constants.py`, que continua sendo a fonte declarada.

Este arquivo NAO substitui `constants.py`: apenas oferece um wrapper imutavel
que pode ser trocado por chamador que queira mudar as FAIXAS de entrada da
humanizacao, sem mudar a formula de calculo.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from tools.constants import GATE_RATIOS, TIMING_JITTER_MS, VELOCITY_RANGES

RangeMap = Mapping[str, tuple[float, float]]

# Tetos de sanidade fisica (US-005 / AC-21). Nao sao faixa util nem default; sao
# o limite alem do qual o valor nao representa mais o dominio fisico:
#   - velocity: valor MIDI 7-bit, [0, 127].
#   - gate: fracao da duracao nominal da nota, [0.0, 1.0].
#   - timing jitter: deslocamento em ms; 250 ms cobre uma semicolcheia a 120bpm
#     com folga. Sigma maior que isso nao "humaniza" — dilui a intencao ritmica.
VELOCITY_BOUNDS: tuple[float, float] = (0.0, 127.0)
GATE_BOUNDS: tuple[float, float] = (0.0, 1.0)
TIMING_JITTER_MS_BOUNDS: tuple[float, float] = (0.0, 250.0)


def _freeze(ranges: Mapping[str, tuple[float, float]]) -> Mapping[str, tuple[float, float]]:
    # NAO forca float(): `constants.py` declara varios pares como int
    # (`VELOCITY_RANGES["ghost"] = (20, 50)`), e `default()` promete
    # reproduzir esses tres dicionarios "byte a byte". Forcar float aqui
    # trocava `(20, 50)` por `(20.0, 50.0)` — igual por `==`, mas divergente
    # em tipo para qualquer consumidor sensivel a isso (serializacao, por
    # exemplo). So imutabiliza a tupla; a validacao numerica ja rodou.
    return MappingProxyType({str(k): (v[0], v[1]) for k, v in ranges.items()})


def _validate(
    ranges: Mapping[str, tuple[float, float]],
    *,
    label: str,
    bounds: tuple[float, float],
) -> None:
    if not isinstance(ranges, Mapping):
        raise ValueError(f"{label}: esperado mapping, recebido {type(ranges).__name__}")
    lo_bound, hi_bound = bounds
    for key, value in ranges.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label}: chave invalida {key!r}")
        try:
            lo, hi = value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}[{key!r}]: esperado par (lo, hi)") from exc
        if (
            not isinstance(lo, (int, float)) or isinstance(lo, bool)
            or not isinstance(hi, (int, float)) or isinstance(hi, bool)
        ):
            raise ValueError(f"{label}[{key!r}]: lo/hi devem ser numericos")
        # `nan > x` e `nan < x` sao SEMPRE False — sem isto, `(nan, nan)`
        # atravessava `lo > hi` e a checagem de limites sem disparar erro
        # nenhum, e o valor invalido so estourava tarde, dentro do render.
        if not math.isfinite(lo) or not math.isfinite(hi):
            raise ValueError(f"{label}[{key!r}]: lo/hi devem ser finitos, recebido ({lo}, {hi})")
        if lo > hi:
            raise ValueError(f"{label}[{key!r}]: lo ({lo}) > hi ({hi})")
        if lo < lo_bound or hi > hi_bound:
            raise ValueError(
                f"{label}[{key!r}]: ({lo}, {hi}) fora dos limites fisicos "
                f"[{lo_bound}, {hi_bound}]"
            )


@dataclass(frozen=True)
class StyleProfile:
    """Perfil imutavel de humanizacao por profile.

    `default()` reproduz `constants.py` byte a byte. Validacao estrutural (par
    (lo, hi), lo <= hi, chave string nao vazia) e sanidade fisica (velocity em
    [0,127], gate em [0,1], jitter em [0,250] ms) rodam no construtor.
    """

    velocity_ranges: RangeMap = field(default_factory=lambda: _freeze(VELOCITY_RANGES))
    gate_ratios: RangeMap = field(default_factory=lambda: _freeze(GATE_RATIOS))
    timing_jitter_ms: RangeMap = field(default_factory=lambda: _freeze(TIMING_JITTER_MS))

    def __post_init__(self) -> None:
        _validate(self.velocity_ranges, label="velocity_ranges", bounds=VELOCITY_BOUNDS)
        _validate(self.gate_ratios, label="gate_ratios", bounds=GATE_BOUNDS)
        _validate(self.timing_jitter_ms, label="timing_jitter_ms", bounds=TIMING_JITTER_MS_BOUNDS)
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
