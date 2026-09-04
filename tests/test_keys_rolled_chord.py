"""Testes de `keys.rolled_chord` — espalhamento ACELERADO, topo no tempo.

O achado que da nome ao bloco (`tecnicas_teclas_midi.md` §6.5) e que a taxa de
rolagem NAO e constante: os intervalos entre notas sucessivas diminuem do grave
para o agudo. Espalhar um valor fixo entre cada nota e o erro classico, e e
justamente isso que os testes abaixo proibem.
"""

from __future__ import annotations

import mido

from tests._guitar_keys_fixtures import (
    build_track_midi,
    copy_midi,
    midi_bytes,
    note_events,
    reapplied,
)
from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

CANONICAL = "keys.rolled_chord"
TICKS_PER_MS = 480 * 1000 / 500_000  # 0.96 tick/ms a 120 BPM, 480 ppq
BEAT = 960


def _chord() -> mido.MidiFile:
    """Acorde de quatro vozes no tempo 3, com o compasso inteiro livre antes."""

    return build_track_midi(
        [
            (BEAT, 960, 60, 90),
            (BEAT, 960, 64, 90),
            (BEAT, 960, 67, 90),
            (BEAT, 960, 72, 90),
        ],
        name="Keys",
    )


def _apply(mid: mido.MidiFile, **parameters) -> mido.MidiFile:
    payload = {"density": 1.0}
    payload.update(parameters)
    return apply_technique(CANONICAL, mid, seed=13, parameters=payload, tool="generic")


def _onsets(mid: mido.MidiFile) -> list[tuple[int, int]]:
    return [
        (tick, pitch)
        for tick, kind, pitch, _velocity in note_events(mid)
        if kind == "on"
    ]


def test_rolled_chord_is_registered_as_humanize_level():
    assert CANONICAL in SUPPORTED_TECHNIQUES
    assert get_technique(CANONICAL).level == "humanize"


def test_without_density_the_file_comes_out_byte_identical():
    untouched = midi_bytes(_chord())
    for parameters in ({}, {"density": 0.0}):
        result = apply_technique(
            CANONICAL, _chord(), seed=13, parameters=parameters, tool="generic",
        )
        assert midi_bytes(result) == untouched


def test_chord_stops_being_simultaneous_and_rolls_from_the_bottom_up():
    onsets = _onsets(_apply(_chord()))

    assert len({tick for tick, _pitch in onsets}) == 4
    assert onsets == sorted(onsets), "o rolo sobe: grave primeiro, agudo por ultimo"
    assert [pitch for _tick, pitch in onsets] == [60, 64, 67, 72]


def test_the_top_note_lands_on_the_beat():
    onsets = _onsets(_apply(_chord()))
    assert onsets[-1] == (BEAT, 72)


def test_intervals_decrease_and_total_spread_respects_the_manual_window():
    ticks = [tick for tick, _pitch in _onsets(_apply(_chord()))]
    gaps = [second - first for first, second in zip(ticks, ticks[1:], strict=False)]

    assert gaps == sorted(gaps, reverse=True)
    assert len(set(gaps)) > 1, "espalhamento uniforme e o erro que a fonte aponta"

    total_ms = (ticks[-1] - ticks[0]) / TICKS_PER_MS
    assert 30 <= total_ms <= 120


def test_declared_spread_commands_the_total():
    result = _apply(_chord(), espalhamento_total_ms=120)
    ticks = [tick for tick, _pitch in _onsets(result)]
    total_ms = (ticks[-1] - ticks[0]) / TICKS_PER_MS
    assert 115 <= total_ms <= 120


def test_durations_are_preserved_note_by_note():
    result = _apply(_chord())
    starts: dict[int, int] = {}
    durations: dict[int, int] = {}
    for tick, kind, pitch, _velocity in note_events(result):
        if kind == "on":
            starts[pitch] = tick
        else:
            durations[pitch] = tick - starts[pitch]
    assert set(durations.values()) == {960}


def test_chord_without_room_before_the_beat_is_skipped():
    """O rolo comeca ANTES do tempo: sem folga, nao ha rolo honesto."""

    glued = build_track_midi(
        [(0, 960, 60, 90), (0, 960, 64, 90), (0, 960, 67, 90)], name="Keys",
    )
    assert midi_bytes(_apply(glued)) == midi_bytes(
        build_track_midi(
            [(0, 960, 60, 90), (0, 960, 64, 90), (0, 960, 67, 90)], name="Keys",
        )
    )


def test_chord_written_out_of_ascending_order_is_skipped():
    """Rolar exigiria reordenar os note_on — o contrato `humanize` proibe."""

    descending = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    track.append(mido.Message("note_on", note=72, velocity=90, time=BEAT))
    track.append(mido.Message("note_on", note=67, velocity=90, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=72, velocity=0, time=960))
    track.append(mido.Message("note_off", note=67, velocity=0, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=0))
    descending.tracks.append(track)

    before = midi_bytes(descending)
    assert midi_bytes(_apply(descending)) == before


def test_note_count_and_pitches_are_untouched():
    source = _chord()
    before = sorted(pitch for _t, kind, pitch, _v in note_events(source) if kind == "on")
    result = _apply(_chord())
    after = sorted(pitch for _t, kind, pitch, _v in note_events(result) if kind == "on")
    assert after == before


def test_same_seed_is_deterministic_and_reapplying_is_stable():
    once = _apply(_chord())
    assert midi_bytes(once) == midi_bytes(_apply(_chord()))
    before, after = reapplied(once, _apply)
    assert after == before


def _long_line_of_chords() -> mido.MidiFile:
    """Doze acordes de quatro vozes, um por compasso — pool grande o bastante
    para que uma densidade fracionaria escolha um subconjunto de verdade."""

    return build_track_midi(
        [
            (bar * 4 * BEAT // 2, 900, pitch, 90)
            for bar in range(12)
            for pitch in (60, 64, 67, 72)
        ],
        name="Keys",
    )


def test_chord_is_skipped_when_the_note_before_it_ends_almost_on_the_beat():
    """Regressao do achado 1: a folga se mede ate o FIM da nota anterior.

    A guarda antiga media a distancia ate o ONSET da nota anterior, e a guarda
    de sobreposicao so olhava o intervalo `[tempo, fim_do_acorde)`. Um C4
    soando de 960 a 1915 passava nas duas, e o rolo punha o `note_on` da
    fundamental em 1851 — ANTES do `note_off` de 1915 da nota anterior de mesma
    altura. Em qualquer sintetizador esse `note_off` corta o segundo C4: a
    fundamental do acorde soava 64 ticks em vez de 960 e sumia.
    """

    source = [(960, 955, 60, 90)] + [(1920, 960, pitch, 90) for pitch in (60, 64, 67, 72)]
    untouched = midi_bytes(build_track_midi(source, name="Keys"))
    result = _apply(build_track_midi(source, name="Keys"))

    assert midi_bytes(result) == untouched


def test_no_note_on_is_written_before_the_note_off_of_the_same_pitch():
    """A mesma folga, agora medida no resultado: nenhuma altura soa duas vezes.

    Com folga real antes do tempo (a nota anterior termina em 1660) o rolo
    acontece, e a fundamental do acorde precisa nascer DEPOIS que a nota
    anterior de mesma altura fechou.
    """

    source = [(960, 700, 60, 90)] + [(1920, 960, pitch, 90) for pitch in (60, 64, 67, 72)]
    result = _apply(build_track_midi(source, name="Keys"))

    open_by_pitch: dict[int, int] = {}
    for _tick, kind, pitch, _velocity in note_events(result):
        if kind == "on":
            assert open_by_pitch.get(pitch, 0) == 0, (
                f"altura {pitch} atacada com a anterior ainda soando"
            )
            open_by_pitch[pitch] = open_by_pitch.get(pitch, 0) + 1
        else:
            open_by_pitch[pitch] = open_by_pitch.get(pitch, 0) - 1
    assert len({tick for tick, kind, _p, _v in note_events(result) if kind == "on"}) > 1


def test_reapplying_with_fractional_density_is_stable():
    """Regressao do achado 3: densidade fracionaria convergia para 1.0.

    Acorde rolado deixa de ser simultaneo e sai do pool de candidatos; com
    `select_by_density` cada passada resorteava o RESTO INTOCADO, entao
    reaplicar rolava mais acordes (6, depois 12...). A selecao agora e decidida
    por candidato, a partir da seed e da identidade dele, e nao do tamanho do
    pool.
    """

    for density in (0.3, 0.5, 0.9):
        mid = _long_line_of_chords()
        passes = []
        for _ in range(3):
            mid = _apply(copy_midi(mid), density=density)
            passes.append(midi_bytes(mid))

        rolled = {tick for tick, kind, _p, _v in note_events(mid) if kind == "on"}
        assert len(rolled) > 12, "a densidade precisa rolar algo para o teste valer"
        assert len(rolled) < 48, "e precisa deixar acorde intocado tambem"
        assert passes[1] == passes[0]
        assert passes[2] == passes[0]


def test_chord_whose_note_off_would_cross_another_note_off_is_skipped():
    """O contrato `humanize` congela a ORDEM dos `note_off` da track inteira.

    Achado extra da varredura aleatoria (nao estava na lista da revisao, e ja
    quebrava antes desta correcao): o rolo move `note_on` e `note_off` juntos,
    e a checagem antiga so comparava os `note_off` DENTRO do acorde. Um
    `note_off` de fora caindo no meio do deslocamento fazia
    `_MidiContentSnapshot.note_pairs` mudar e a tecnica estourar
    `TechniqueContractError` no despacho central — a fixture abaixo era um
    desses casos.
    """

    source = [
        (0, 240, 59, 90),
        (0, 280, 78, 90),
        (720, 480, 57, 90),
        (720, 1040, 61, 90),
        (720, 700, 71, 90),
        (1440, 300, 60, 90),
    ]
    before = note_events(build_track_midi(source, name="Keys"))
    result = _apply(build_track_midi(source, name="Keys"))

    after = note_events(result)
    assert [
        (kind, pitch) for _tick, kind, pitch, _v in after
    ] == [(kind, pitch) for _tick, kind, pitch, _v in before]


def test_a_chord_freed_by_the_neighbours_roll_only_settles_on_the_next_pass():
    """LIMITE CONHECIDO da idempotencia, medido e documentado — nao promessa.

    A folga se mede ate o FIM da nota anterior, e rolar um acorde move os
    `note_off` das vozes de baixo para TRAS. Um acorde bloqueado por 20 ticks
    de cauda do acorde anterior passa a ter folga depois que esse anterior
    rola, e so entra na passada seguinte. Nenhuma escolha de seed conserta
    isso: a grandeza medida mudou de verdade.

    O que o motor garante e que a coisa CONVERGE e nao volta a andar — e o que
    este teste trava. Rodar `render` duas vezes sobre a mesma origem continua
    dando o mesmo arquivo, porque a origem e a mesma; o cenario aqui e
    reaplicar a tecnica sobre a SAIDA dela.
    """

    source = [
        (5760, 700, 64, 106),
        (5760, 280, 72, 57),
        (6480, 960, 56, 117),
        (6480, 1000, 64, 107),
        (6480, 480, 74, 117),
    ]
    passes = []
    mid = build_track_midi(source, name="Keys")
    for _ in range(4):
        mid = _apply(copy_midi(mid))
        passes.append(midi_bytes(mid))

    assert passes[1] != passes[0], (
        "o acorde de 6480 so ganha folga depois que o de 5760 rola"
    )
    assert passes[2] == passes[1]
    assert passes[3] == passes[1]
