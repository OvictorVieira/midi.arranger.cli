"""Testes de `bass.string_selection` — forca a corda por keyswitch do MODO BASS."""

from __future__ import annotations

import mido

from tools.techniques.engine import SUPPORTED_TECHNIQUES, apply_technique

# Afinacao padrao de 4 cordas (E-A-D-G), grave para agudo — mesma usada por
# `tools.techniques.physical._BASS_DEFAULT_TUNING`.
_STANDARD_4 = (28, 33, 38, 43)

# Keyswitches reais do manual (tecnicas_baixo_midi.md, secao 5.9), so as
# quatro relevantes para um baixo de 4 cordas na convencao E-A-D-G.
_KS_E = 16
_KS_A = 9
_KS_D = 14
_KS_G = 19


def _make_bass_line(
    events: list[tuple[int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 1,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch in events:
        absolute.append((
            start, order,
            mido.Message("note_on", channel=channel, note=pitch, velocity=100, time=0),
        ))
        order += 1
        absolute.append((
            start + duration, order,
            mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0),
        ))
        order += 1
    prev = 0
    for tick, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=tick - prev))
        prev = tick
    mid.tracks.append(track)
    return mid


def _note_on_pitches(mid: mido.MidiFile) -> list[int]:
    return [
        msg.note
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    ]


def _note_on_pitch_channel_pairs(mid: mido.MidiFile) -> list[tuple[int, int]]:
    return [
        (msg.note, msg.channel)
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    ]


def _make_multi_channel_bass_line(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch, channel)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch, channel in events:
        absolute.append((
            start, order,
            mido.Message("note_on", channel=channel, note=pitch, velocity=100, time=0),
        ))
        order += 1
        absolute.append((
            start + duration, order,
            mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0),
        ))
        order += 1
    prev = 0
    for tick, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=tick - prev))
        prev = tick
    mid.tracks.append(track)
    return mid


def test_string_selection_is_supported():
    assert "bass.string_selection" in SUPPORTED_TECHNIQUES


def test_generic_tool_is_a_noop():
    # Sem tool (receita generic): a propria manual diz "declare que a
    # intencao de corda nao pode ser honrada nesta ferramenta" — sem
    # keyswitch nenhum, nunca reescreve a linha.
    source = _make_bass_line([(0, 480, 30), (960, 480, 60)])
    result = apply_technique(
        "bass.string_selection", source, seed=1,
        parameters={"tuning": _STANDARD_4},
    )
    assert _note_on_pitches(result) == [30, 60]


def test_forces_lowest_reachable_string_and_groups_runs():
    # 30 e alcancavel na corda E (28..52) — a MAIS GRAVE das duas que
    # alcancam (E e A) — deve ficar na E. 60 so e alcancavel na D (38..62,
    # pois E vai so ate 52 e A ate 57) — troca de corda real.
    source = _make_bass_line([
        (0, 480, 30), (480, 480, 32), (960, 480, 60),
    ])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    pitches = _note_on_pitches(result)
    # Um keyswitch de E antes das duas primeiras notas (mesmo run), um de D
    # antes da terceira (novo run) — nunca um keyswitch por nota.
    assert pitches == [_KS_E, 30, 32, _KS_D, 60]


def test_string_selection_keyswitches_are_held_not_pulsed():
    source = _make_bass_line([(0, 480, 30), (960, 480, 60)])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    track = result.tracks[0]
    tick = 0
    ks_on_tick = ks_off_tick = None
    for msg in track:
        tick += msg.time
        if msg.is_meta or getattr(msg, "note", None) != _KS_E:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            ks_on_tick = tick
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            ks_off_tick = tick
    assert ks_on_tick is not None and ks_off_tick is not None
    # LATCH vem desligado de fabrica no MODO BASS (manual, 5.9): o
    # keyswitch precisa ficar PRESSIONADO (nota longa), nao ser um blip —
    # aqui ele deve cobrir ate a proxima troca de corda (tick 960 da nota D).
    assert ks_off_tick > ks_on_tick + 1


def test_string_selection_is_idempotent():
    source = _make_bass_line([(0, 480, 30), (960, 480, 60)])
    once = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    twice = apply_technique(
        "bass.string_selection", once, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    assert _note_on_pitches(twice) == _note_on_pitches(once)


def test_falls_back_to_physical_default_tuning_when_undeclared():
    # Sem `tuning` em parameters: cai no default fisico de 4 cordas
    # (mesma convencao do resto do motor) em vez de recusar.
    source = _make_bass_line([(0, 480, 30)])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={},
    )
    assert _KS_E in _note_on_pitches(result)


def test_unmappable_string_count_is_a_noop():
    # 7 cordas nao tem convencao documentada (so 4/5/6) — no-op explicito
    # em vez de adivinhar uma ordem.
    source = _make_bass_line([(0, 480, 30)])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": (20, 25, 30, 35, 40, 45, 50)},
    )
    assert _note_on_pitches(result) == [30]


def test_string_selection_does_not_split_run_when_another_channel_interleaves():
    # Achado do Codex: formar run numa lista global ordenada por tick fazia
    # uma nota de OUTRO canal, intercalada no meio de duas notas do MESMO
    # canal na MESMA corda, quebrar o run do canal 1 em dois — soltando e
    # reacionando o mesmo keyswitch sem necessidade. Canal 1: duas notas na
    # corda E (tick 0 e tick 960). Canal 2: uma nota na corda D (tick 240),
    # cronologicamente ENTRE as duas notas do canal 1.
    source = _make_multi_channel_bass_line([
        (0, 480, 30, 1),     # corda E, canal 1
        (240, 480, 60, 2),   # corda D, canal 2 — intercalada no meio
        (960, 480, 32, 1),   # corda E, canal 1 — mesma corda da primeira
    ])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    pitches = _note_on_pitches(result)
    # Um unico keyswitch de E pro canal 1 inteiro — nao dois.
    assert pitches.count(_KS_E) == 1


def test_string_selection_resolves_max_fret_parameter_pair():
    # Achado do Codex: max_fret como par [min, max] (formato aceito por
    # qualquer style.parameters) caia no default 24 em silencio. Com
    # max_fret=[12, 12] (ponto medio 12) e afinacao E-A-D-G, pitch 45 nao
    # alcanca mais a corda E (28+12=40 < 45) e forca a corda A (33+12=45).
    source = _make_bass_line([(0, 480, 45)])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4, "max_fret": [12, 12]},
    )
    pitches = _note_on_pitches(result)
    assert _KS_A in pitches
    assert _KS_E not in pitches


def _ticks_for_channel_pitch(mid: mido.MidiFile, *, channel: int, pitch: int) -> tuple[int, int]:
    """(on_tick, off_tick) do primeiro par note_on/off daquele canal/pitch."""
    tick = 0
    on_tick = None
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta or msg.channel != channel or msg.note != pitch:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                if on_tick is None:
                    on_tick = tick
            elif on_tick is not None:
                return on_tick, tick
    raise AssertionError(f"no note found for channel={channel} pitch={pitch}")


def test_string_selection_holds_keyswitch_per_channel_when_channels_interleave():
    # Achado do Codex: o off_tick usava o inicio do PROXIMO run na ordem
    # GLOBAL (que pode ser de outro canal), soltando o keyswitch de um
    # canal cedo demais quando runs de canais diferentes se intercalam no
    # tempo. Canal 1: nota longa (corda E) tick 0-1000. Canal 2: nota
    # (corda D) comecando tick 480, ANTES do fim da nota do canal 1.
    source = _make_multi_channel_bass_line([
        (0, 1000, 30, 1),   # corda E, canal 1, tick 0-1000
        (480, 480, 60, 2),  # corda D, canal 2, tick 480-960
    ])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    ks_e_on, ks_e_off = _ticks_for_channel_pitch(result, channel=1, pitch=_KS_E)
    # O keyswitch de E (canal 1) tem que ficar segurado ate pelo menos o
    # fim da nota estrutural dele (tick 1000) — nao pode ser cortado pelo
    # inicio da nota do canal 2 (tick 480).
    assert ks_e_off >= 1000, (
        f"keyswitch E do canal 1 foi solto no tick {ks_e_off}, antes do fim "
        "da propria nota estrutural (1000) — cortado pelo run de outro canal"
    )


def test_string_selection_honors_numeric_max_fret():
    # Achado do Codex: max_fret so aceitava int; um float integral valido
    # (12.0, formato JSON comum para numero escalar de style.parameters)
    # caia no default 24 em silencio. Com max_fret=12 e afinacao padrao
    # E-A-D-G, pitch 45 nao alcanca mais a corda E (28+12=40 < 45) e tem
    # que forcar a corda A (33+12=45).
    source = _make_bass_line([(0, 480, 45)])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4, "max_fret": 12.0},
    )
    pitches = _note_on_pitches(result)
    assert _KS_A in pitches
    assert _KS_E not in pitches


def test_string_selection_emits_keyswitch_on_each_notes_own_channel():
    # Achado do Codex: track fisica com notas em mais de um canal usava o
    # canal da PRIMEIRA nota estrutural pra todo keyswitch, inclusive runs
    # vindos de outro canal — essas notas ficavam sem o keyswitch delas.
    source = _make_multi_channel_bass_line([
        (0, 480, 30, 1), (480, 480, 32, 1),   # corda E, canal 1
        (960, 480, 60, 2),                     # corda D, canal 2
    ])
    result = apply_technique(
        "bass.string_selection", source, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    pairs = _note_on_pitch_channel_pairs(result)
    assert pairs == [
        (_KS_E, 1), (30, 1), (32, 1),
        (_KS_D, 2), (60, 2),
    ]


def test_string_selection_recomputes_runs_when_partial_keyswitch_exists():
    # Achado do Codex: pular a track inteira so por achar QUALQUER keyswitch
    # deixa o resto da track sem corda forcada quando so um trecho ja tinha
    # keyswitch previo (ex.: autor manual parcial cobrindo so a primeira
    # nota). A reaplicacao tem que recalcular o conjunto completo de runs
    # em vez de tratar qualquer pitch de keyswitch como prova de que a
    # track inteira ja foi processada.
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    # Keyswitch de E pre-existente (autor manual), so cobrindo a primeira nota.
    track.append(mido.Message("note_on", channel=1, note=_KS_E, velocity=127, time=0))
    track.append(mido.Message("note_off", channel=1, note=_KS_E, velocity=0, time=479))
    track.append(mido.Message("note_on", channel=1, note=30, velocity=100, time=1))
    track.append(mido.Message("note_off", channel=1, note=30, velocity=0, time=479))
    # Segunda nota, corda G (so alcancavel acima do alcance de E/A/D com
    # max_fret=24) — a track NUNCA recebeu keyswitch de G.
    track.append(mido.Message("note_on", channel=1, note=65, velocity=100, time=1))
    track.append(mido.Message("note_off", channel=1, note=65, velocity=0, time=479))
    mid.tracks.append(track)

    result = apply_technique(
        "bass.string_selection", mid, seed=1, tool="modo_bass",
        parameters={"tuning": _STANDARD_4},
    )
    pitches = _note_on_pitches(result)
    assert _KS_G in pitches
