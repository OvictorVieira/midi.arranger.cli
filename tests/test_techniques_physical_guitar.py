"""Testes de `guitar_voicing_frets`/`guitar_voicing_is_playable` (issue #19).

Checagem de span no braco: `tools/palette/guitar.py` reusa exatamente esta
funcao antes de escrever qualquer voicing, entao os testes aqui cobrem a
regra fisica isolada da geracao (arXiv 2510.10619: uma altura por corda,
casas pisadas dentro de uma janela de 6 casas).
"""

from __future__ import annotations

import mido
import pytest

from tools.techniques.physical import (
    _GUITAR_TUNINGS,
    GUITAR_CHORD_VOICING_WINDOW_FRETS,
    TechniquePhysicalError,
    guitar_voicing_frets,
    guitar_voicing_is_playable,
    validate_physical_plausibility,
)

E_STANDARD = _GUITAR_TUNINGS["e_padrao"]  # (40, 45, 50, 55, 59, 64)


def test_guitar_chord_voicing_window_is_six_frets():
    assert GUITAR_CHORD_VOICING_WINDOW_FRETS == 6


def test_single_note_is_always_playable():
    assert guitar_voicing_is_playable((40,), E_STANDARD)
    assert guitar_voicing_is_playable((64,), E_STANDARD)


def test_open_power_chord_is_playable():
    # E2 (corda solta) + B2 (quinta, corda A casa 2) — power chord classico.
    assert guitar_voicing_is_playable((40, 47), E_STANDARD)


def test_power_chord_with_octave_within_window_is_playable():
    # Raiz na corda D (casa 2), quinta na corda G (casa 2), oitava na
    # corda B (casa 3) — tres notas, tres cordas distintas, janela minima.
    assert guitar_voicing_is_playable((52, 57, 62), E_STANDARD)


def test_voicing_needing_two_notes_on_the_same_string_is_rejected():
    # 40 e 41 so cabem na corda Mi grave (40-64 cobre a corda inteira, mas
    # 41 tambem cabe na regiao inicial da mesma corda so) — forcar as duas
    # alturas na MESMA corda com max_fret=1 nao deixa corda sobrando.
    assert guitar_voicing_frets((40, 41), (40,), max_fret=24) is None


def test_voicing_wider_than_window_is_rejected():
    # 43 (casa 3 da corda Mi grave) e 88 (casa 24 da corda Mi aguda) — as
    # DUAS pisadas, nao cordas soltas, ficam a 21 casas de distancia:
    # viola a janela de 6 casas mesmo cabendo cada uma numa corda distinta.
    assert not guitar_voicing_is_playable((43, 88), E_STANDARD, max_fret=24)


def test_open_string_does_not_count_toward_the_fret_window():
    # Corda solta (fret 0) fica de fora da janela por definicao (arXiv
    # 2510.10619): raiz solta na Mi grave (40) + quinta na casa 24 da Mi
    # aguda (88) tem so UMA altura pisada de verdade, entao a janela nao
    # se aplica.
    assert guitar_voicing_is_playable((40, 88), E_STANDARD, max_fret=24)


def test_voicing_below_tuning_floor_has_no_assignment():
    # Nenhuma corda de E padrao alcanca abaixo de 40 (Mi grave solta).
    assert guitar_voicing_frets((39,), E_STANDARD) is None


def test_guitar_voicing_frets_minimizes_fret_span():
    # Duas alturas que podem, cada uma, ser tocadas em mais de uma corda:
    # a atribuicao escolhida tem que ser a de MENOR span entre as casas
    # pisadas, nao uma atribuicao arbitraria que produza span maior.
    frets = guitar_voicing_frets((50, 55), E_STANDARD, max_fret=24)
    assert frets is not None
    fretted = [f for f in frets if f > 0]
    assert (max(fretted) - min(fretted)) <= 1 if fretted else True


def _midi_with_one_note(pitch: int, start: int, end: int) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.Message("note_on", channel=1, note=pitch, velocity=90, time=start))
    track.append(mido.Message("note_off", channel=1, note=pitch, velocity=0, time=end - start))
    mid.tracks.append(track)
    return mid


def _midi_with_two_notes(
    pitch_a: int, end_a: int, pitch_b: int, start_b: int, end_b: int,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.Message("note_on", channel=1, note=pitch_a, velocity=90, time=0))
    track.append(mido.Message("note_on", channel=1, note=pitch_b, velocity=90, time=start_b))
    track.append(mido.Message("note_off", channel=1, note=pitch_a, velocity=0, time=end_a - start_b))
    track.append(mido.Message("note_off", channel=1, note=pitch_b, velocity=0, time=end_b - end_a))
    mid.tracks.append(track)
    return mid


def test_hammer_pull_legato_overlap_on_a_shared_single_string_is_valid():
    """Regressao: `guitar.hammer_pull` (e `bass.hammer_pull`, mesmo
    mecanismo) estende a primeira nota por cima do ataque da segunda para
    disparar legato no instrumento sampleado — a sobreposicao e uma
    transicao na MESMA corda, nao duas cordas tocadas ao mesmo tempo. Sem
    a excecao dedicada a `*.hammer_pull` em `_validate_strings`, o
    validador generico (correto para ornamentos independentes, ver
    `test_physical_guitar_rejects_overlap_on_same_string` em
    `tests/test_techniques_engine.py`) rejeitava esse caso quando so uma
    corda alcancava as duas alturas (ex.: 40 e 42, que so cabem na corda
    Mi grave em E padrao)."""
    before = _midi_with_one_note(40, 0, 200)
    after = _midi_with_two_notes(pitch_a=40, end_a=220, pitch_b=42, start_b=200, end_b=400)

    # Nao deve levantar TechniquePhysicalError.
    validate_physical_plausibility(
        "guitar.hammer_pull", before, after, {"tuning": E_STANDARD, "max_fret": 24},
    )


def test_hammer_pull_legato_chain_of_three_notes_on_one_string_is_valid():
    """A mesma excecao cobre uma cadeia de 3 notas em legato (hammer-on
    seguido de outro hammer-on) quando todas cabem numa unica corda —
    nao so o caso de 2 notas."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.Message("note_on", channel=1, note=40, velocity=90, time=0))
    track.append(mido.Message("note_on", channel=1, note=42, velocity=85, time=200))
    track.append(mido.Message("note_off", channel=1, note=40, velocity=0, time=8))
    track.append(mido.Message("note_on", channel=1, note=43, velocity=85, time=192))
    track.append(mido.Message("note_off", channel=1, note=42, velocity=0, time=8))
    track.append(mido.Message("note_off", channel=1, note=43, velocity=0, time=200))
    mid.tracks.append(track)

    validate_physical_plausibility(
        "guitar.hammer_pull",
        _midi_with_one_note(40, 0, 200),
        mid,
        {"tuning": E_STANDARD, "max_fret": 24},
    )


def test_ghost_note_overlap_on_the_same_string_still_rejected():
    """A excecao e SO de `*.hammer_pull`: um ornamento generico (ex.
    `guitar.dead_notes`) continua proibido de exigir a mesma corda de uma
    nota estrutural simultanea — nao e legato, e duas notas independentes
    nao podem soar juntas de uma corda so."""
    before = _midi_with_one_note(40, 0, 200)
    after = _midi_with_two_notes(pitch_a=40, end_a=200, pitch_b=42, start_b=120, end_b=250)

    with pytest.raises(TechniquePhysicalError, match="mesma corda"):
        validate_physical_plausibility(
            "guitar.dead_notes", before, after, {"tuning": E_STANDARD, "max_fret": 24},
        )


def test_drop_tuning_lowers_the_playable_floor():
    drop_c = _GUITAR_TUNINGS["drop_c"]
    assert guitar_voicing_frets((36,), drop_c) is not None
    assert guitar_voicing_frets((36,), E_STANDARD) is None
