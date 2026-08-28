"""Classifica trechos de bateria como virada (fill) ou groove.

Existe para dar a `drums.accent_hierarchy` a nocao de contexto que a
implementacao original nao tinha — decidir camada de velocity so pela
posicao metrica rebaixava a virada inteira, porque virada e feita de
contratempo. Ver issue #50 e AGENTS.md.

Padrao de agrupamento reusado do `drums.accented_roll`: runs de notas
consecutivas por gap maximo, tamanho minimo. Sobre cada run avaliamos:

- densidade (notas por tempo)
- variedade de pecas (kick/snare/tom/hihat/cymbal)
- presenca de tom (virada quase sempre passa por tom)
- ausencia de padrao de backbeat regular (snare so em 2/4 e groove)

Numeros abaixo sao CONVENCAO — a lacuna esta declarada na secao 11 do
manual (`Limiar quantitativo de virada 'de bom gosto' vs 'atulhada'`).
Estao replicados no bloco `technique` de `accent_hierarchy` como
parametros com `source: CONVENCAO`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

FILL_MAX_GAP_BEATS = 0.25
FILL_MIN_NOTES = 4
FILL_MIN_DENSITY_PER_BEAT = 3.0
FILL_MIN_PIECE_VARIETY = 2

_KICKS = frozenset({35, 36})
_SNARES = frozenset({37, 38, 40})
_TOMS = frozenset({41, 43, 45, 47, 48, 50, 58, 65, 66, 67, 68})
_HI_HATS = frozenset({
    10, 11, 12, 13, 14, 15, 16, 17, 21, 22, 23, 24, 25, 26, 42, 44, 46,
    60, 61, 62, 63, 64,
})
_CYMBALS = frozenset({49, 51, 52, 53, 55, 57, 59})


def piece_family(pitch: int) -> str | None:
    """Familia GM da peca. `None` quando a nota nao tem familia conhecida."""

    if pitch in _KICKS:
        return "kick"
    if pitch in _SNARES:
        return "snare"
    if pitch in _TOMS:
        return "tom"
    if pitch in _HI_HATS:
        return "hihat"
    if pitch in _CYMBALS:
        return "cymbal"
    return None


def _drum_channel_notes(notes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (note for note in notes if int(note.get("channel", 9)) == 9),
        key=lambda note: (int(note["start"]), int(note["pitch"])),
    )


def _fill_carrying_notes(notes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Notas que constroem virada. Hi-hat e textura de groove, nao entra.

    Ostinato de hi-hat em semicolcheia dura o compasso inteiro; se entrar
    no agrupamento por gap, funde groove e virada numa run so e o detector
    vira a coisa toda em virada. Filtramos hi-hat aqui e mantemos snare,
    tom, kick, cymbal — que sao as pecas que caracterizam virada.
    """

    filtered: list[dict[str, Any]] = []
    for note in _drum_channel_notes(notes):
        family = piece_family(int(note["pitch"]))
        if family == "hihat":
            continue
        filtered.append(note)
    return filtered


def _runs(notes: Sequence[dict[str, Any]], *, max_gap_ticks: int) -> list[list[dict[str, Any]]]:
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for note in notes:
        if previous is not None and int(note["start"]) - int(previous["start"]) <= max_gap_ticks:
            current.append(note)
        else:
            if len(current) >= FILL_MIN_NOTES:
                runs.append(current)
            current = [note]
        previous = note
    if len(current) >= FILL_MIN_NOTES:
        runs.append(current)
    return runs


def _has_regular_backbeat(run: Sequence[dict[str, Any]], *, sixteenth: int) -> bool:
    """True quando as snares do trecho caem so em 2/4 e nao ha tom."""

    snare_positions: list[int] = []
    for note in run:
        family = piece_family(int(note["pitch"]))
        if family == "tom":
            return False
        if family == "snare":
            snare_positions.append(round(int(note["start"]) / sixteenth) % 16)
    if not snare_positions:
        return False
    return all(position in (4, 12) for position in snare_positions)


def is_fill_run(run: Sequence[dict[str, Any]], *, ticks_per_beat: int) -> bool:
    """Aplica os quatro criterios da CONVENCAO sobre um run ja agrupado."""

    if len(run) < FILL_MIN_NOTES or ticks_per_beat <= 0:
        return False
    span_ticks = int(run[-1]["start"]) - int(run[0]["start"])
    span_beats = max(span_ticks / ticks_per_beat, 1.0 / max(ticks_per_beat, 1))
    density = len(run) / max(span_beats, 0.25)
    if density < FILL_MIN_DENSITY_PER_BEAT:
        return False
    families = {piece_family(int(note["pitch"])) for note in run}
    families.discard(None)
    if len(families) < FILL_MIN_PIECE_VARIETY:
        return False
    sixteenth = max(1, ticks_per_beat // 4)
    if _has_regular_backbeat(run, sixteenth=sixteenth):
        return False
    return "tom" in families or "snare" in families


def fill_windows(
    notes: Iterable[dict[str, Any]],
    *,
    ticks_per_beat: int,
) -> tuple[tuple[int, int], ...]:
    """Ranges `[start_tick, end_tick]` de trechos classificados como virada.

    `notes` sao dicionarios no formato de `iter_note_dicts`. So o canal 9
    (bateria) entra; qualquer outro canal e ignorado antes do agrupamento.
    """

    if ticks_per_beat <= 0:
        return ()
    max_gap_ticks = max(1, int(round(ticks_per_beat * FILL_MAX_GAP_BEATS)))
    drum_notes = _fill_carrying_notes(notes)
    if not drum_notes:
        return ()
    windows: list[tuple[int, int]] = []
    for run in _runs(drum_notes, max_gap_ticks=max_gap_ticks):
        if is_fill_run(run, ticks_per_beat=ticks_per_beat):
            windows.append((int(run[0]["start"]), int(run[-1]["start"])))
    return tuple(windows)


def classify_window(
    notes: Iterable[dict[str, Any]],
    *,
    ticks_per_beat: int,
) -> str:
    """Classifica um conjunto de notas ja delimitado como 'fill' ou 'groove'.

    Utilitario para testes e consumidores que ja tem o recorte pronto.
    """

    drum_notes = _fill_carrying_notes(notes)
    if not drum_notes:
        return "groove"
    if is_fill_run(drum_notes, ticks_per_beat=ticks_per_beat):
        return "fill"
    return "groove"


__all__ = [
    "FILL_MAX_GAP_BEATS",
    "FILL_MIN_DENSITY_PER_BEAT",
    "FILL_MIN_NOTES",
    "FILL_MIN_PIECE_VARIETY",
    "classify_window",
    "fill_windows",
    "is_fill_run",
    "piece_family",
]
