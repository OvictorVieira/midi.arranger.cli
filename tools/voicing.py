"""Motor de voicing (US-008).

Traduz um conjunto de pitches em `VoicedNote`s carregando:
- `velocity_delta` — ajuste a somar a base velocity, segundo a regra de
  spread da secao 7 do spec (narrow -> topo canta, wide -> base sustenta,
  topo sussurra).
- `onset_offset_ms` — desencontro de ataque entre notas do mesmo acorde.
  Piano/Rhodes espalha a mao ("acorde nao e simultaneo"). Pad NAO espalha
  dentro do voicing — desencontro de pad e entre camadas, e vive no
  render, nao aqui.
- `is_ghost` — vozes internas de strings tutti (~20% do total) marcadas
  como ghost. A velocidade final absoluta (<40) e responsabilidade do
  passo de velocity com o bucket 'ghost'.

Regra de registro: abaixo de C2, so nota unica ou quinta. Nunca acorde
denso. Um voicing cuja nota mais aguda esta abaixo de C2 e reduzido a no
maximo 2 notas.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# --- regras de spread (secao 7 do spec) -------------------------------------

OCTAVE_SPREAD = 12
CLOSED_TRIAD_MIN_SPREAD = 7
CLOSED_TRIAD_MAX_SPREAD = 11
WIDE_STACK_MIN_SPREAD = 19
WIDE_STACK_MAX_SPREAD = 24

# Grave "acentuado" — delta positivo aplicado a nota mais grave em oitava
# e stack largo. Mantido pequeno (mesma ordem de grandeza do downbeat)
# porque a assinatura auditiva ja vem do topo diminuido em 14.
OCTAVE_BASS_ACCENT_DELTA = +6
OCTAVE_TOP_DELTA = -14

CLOSED_TRIAD_TOP_DELTA = +20

WIDE_STACK_BASS_ACCENT_DELTA = +6
WIDE_STACK_TOP_DELTA = -14


# --- strings tutti ----------------------------------------------------------

STRINGS_GHOST_RATIO = 0.20
STRINGS_GHOST_MAX_ABS_VELOCITY = 39  # "<40"


# --- espalhamento de ataque -------------------------------------------------

# Piano/Rhodes: mao humana nao ataca tudo junto. Cada nota subsequente
# entra alguns milissegundos depois da anterior.
PIANO_ONSET_SPREAD_MS: tuple[float, float] = (2.0, 8.0)

# Papeis cujo acorde tem ataques desencontrados DENTRO do voicing.
KEYBOARD_SPREAD_ROLES: frozenset[str] = frozenset({"piano", "rhodes"})


# --- restricao de registro --------------------------------------------------

C2_PITCH = 36
PERFECT_FIFTH_SEMITONES = 7


@dataclass(frozen=True)
class VoicedNote:
    """Nota individual do voicing final.

    - `pitch`: pitch MIDI.
    - `velocity_delta`: valor a somar a base velocity do papel. Ghost
      ignora esse delta e usa o bucket 'ghost' na hora de renderizar.
    - `onset_offset_ms`: atraso (positivo) em ms em relacao ao onset do
      acorde. 0.0 quer dizer "junto com o acorde".
    - `is_ghost`: quando True, essa voz e ghost (aplicavel a vozes
      internas de strings tutti).
    """
    pitch: int
    velocity_delta: int
    onset_offset_ms: float = 0.0
    is_ghost: bool = False


@dataclass(frozen=True)
class VoicingRequest:
    """Descreve um acorde candidato ao motor de voicing.

    - `pitches`: pitches MIDI do acorde ja nas oitavas desejadas. A ordem
      do input nao importa; o motor ordena antes de aplicar regras.
    - `role`: papel do instrumento (`piano`, `rhodes`, `pad`, `strings`,
      etc.). Papel afeta espalhamento de ataque e ghost tagging.
    """
    pitches: tuple[int, ...]
    role: str


class VoicingEngine:
    """Motor deterministico de voicing.

    - Mesma seed + mesma sequencia de requests => mesma saida.
    - Componentes RNG-dependentes: escolha das vozes ghost em strings e
      valores exatos do espalhamento de ataque de piano/rhodes.
    """

    def __init__(self, *, seed: int) -> None:
        self._rng = random.Random(seed)

    def voice(self, req: VoicingRequest) -> list[VoicedNote]:
        if not req.pitches:
            return []

        pitches = sorted(set(req.pitches))
        pitches = _apply_register_constraint(pitches)

        if len(pitches) == 1:
            return [VoicedNote(pitch=pitches[0], velocity_delta=0)]

        if req.role == "strings":
            return self._voice_strings(pitches)

        spread = pitches[-1] - pitches[0]
        deltas = _spread_deltas(spread, len(pitches))
        offsets = self._onset_offsets(req.role, len(pitches))
        return [
            VoicedNote(pitch=p, velocity_delta=d, onset_offset_ms=o)
            for p, d, o in zip(pitches, deltas, offsets, strict=True)
        ]

    # -- strings tutti -------------------------------------------------------

    def _voice_strings(self, pitches: list[int]) -> list[VoicedNote]:
        n = len(pitches)
        internal_indices = list(range(1, n - 1))
        target_ghost = round(STRINGS_GHOST_RATIO * n)
        ghost_count = min(target_ghost, len(internal_indices))
        chosen = internal_indices.copy()
        self._rng.shuffle(chosen)
        ghost_set = set(chosen[:ghost_count])

        return [
            VoicedNote(
                pitch=p,
                velocity_delta=0,
                onset_offset_ms=0.0,
                is_ghost=(i in ghost_set),
            )
            for i, p in enumerate(pitches)
        ]

    # -- ataque desencontrado ------------------------------------------------

    def _onset_offsets(self, role: str, n: int) -> list[float]:
        if role in KEYBOARD_SPREAD_ROLES:
            lo, hi = PIANO_ONSET_SPREAD_MS
            offsets: list[float] = [0.0]
            for _ in range(n - 1):
                offsets.append(offsets[-1] + self._rng.uniform(lo, hi))
            return offsets
        # Pad e demais papeis: acorde ataca junto. Desencontro de pad
        # e entre camadas (renderer), nao aqui.
        return [0.0] * n


# --- funcoes puras (testaveis isoladamente) ---------------------------------

def _spread_deltas(spread: int, n: int) -> list[int]:
    """Deltas de velocity por posicao dentro do voicing (grave -> agudo)."""
    deltas = [0] * n
    if n < 2:
        return deltas
    if spread == OCTAVE_SPREAD:
        deltas[0] = OCTAVE_BASS_ACCENT_DELTA
        deltas[-1] = OCTAVE_TOP_DELTA
    elif CLOSED_TRIAD_MIN_SPREAD <= spread <= CLOSED_TRIAD_MAX_SPREAD:
        deltas[-1] = CLOSED_TRIAD_TOP_DELTA
    elif WIDE_STACK_MIN_SPREAD <= spread <= WIDE_STACK_MAX_SPREAD:
        deltas[0] = WIDE_STACK_BASS_ACCENT_DELTA
        deltas[-1] = WIDE_STACK_TOP_DELTA
    return deltas


def _apply_register_constraint(pitches: list[int]) -> list[int]:
    """Abaixo de C2, apenas nota unica ou quinta.

    Se a nota mais aguda ja esta em C2 ou acima, o voicing nao vive no
    registro sub e passa inalterado.
    """
    if not pitches or pitches[-1] >= C2_PITCH:
        return pitches
    root = pitches[0]
    for p in pitches[1:]:
        if p - root == PERFECT_FIFTH_SEMITONES:
            return [root, p]
    return [root]
