"""Reconstroi uma `mido.MidiTrack` a partir de eventos com tick absoluto.

Usado pelos aplicadores de tecnica em `engine.py` que inserem CC, keyswitch ou
ornamentos e precisam reordenar antes de gravar delta times. Importado
localmente dentro do aplicador para nao virar captura de global — o teste
`test_registered_techniques_do_not_capture_global_or_nonlocal_state` continua
verde.
"""

from __future__ import annotations


def collect_absolute(track) -> list:
    """Percorre `track` acumulando `(tick_absoluto, bias=0, order, msg)`.

    Retorno serve de base para o aplicador inserir novos eventos e chamar
    `sort_and_flush` para reconstruir a track com delta times corretos. O
    proximo `order` a usar em insercoes e `len(absolute)`.
    """

    absolute: list = []
    tick = 0
    for order, msg in enumerate(track):
        tick += msg.time
        absolute.append((tick, 0, order, msg))
    return absolute


def sort_and_flush(absolute, track) -> None:
    """Ordena `absolute` por `(tick, bias, order)` e sobrescreve `track` in-place.

    `absolute` e uma lista de tuplas `(tick, bias, order, msg)` com tick
    absoluto e `msg` como `mido.Message`/`mido.MetaMessage`. `bias` desempata
    eventos no mesmo tick (por exemplo CC on antes de note_on); `order`
    preserva ordem de insercao entre eventos com mesmo tick e bias.
    """

    import mido

    absolute.sort(key=lambda item: (item[0], item[1], item[2]))
    rebuilt = mido.MidiTrack()
    previous_tick = 0
    for absolute_tick, _bias, _order, msg in absolute:
        rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
        previous_tick = absolute_tick
    track[:] = rebuilt
