"""Distribuicao de notas por canal, por SMF track (US-001, issue #35)."""

from __future__ import annotations

import os
import tempfile

import mido

from tools import tuning


def _write_midi(path: str, tracks: list[list[tuple[int, int]]]) -> None:
    """Escreve um MIDI multi-track. Cada track e uma lista de (channel, pitch).

    Uma nota por evento (`note_on vel=100` + `note_off` no tick seguinte),
    canais como no MIDI real (0-15). Nome da track = 'Track {n}'.
    """
    mid = mido.MidiFile(ticks_per_beat=480)
    for i, notes in enumerate(tracks):
        t = mido.MidiTrack()
        t.append(mido.MetaMessage("track_name", name=f"Track {i}", time=0))
        for ch, pitch in notes:
            t.append(mido.Message("note_on", channel=ch, note=pitch, velocity=100, time=0))
            t.append(mido.Message("note_off", channel=ch, note=pitch, velocity=0, time=120))
        mid.tracks.append(t)
    mid.save(path)


def test_five_channels_reports_five():
    """Track com 5 canais reporta 5 canais, um por canal."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "five.mid")
        notes = [
            (0, 40), (0, 42),                    # canal 0 — 2 notas
            (1, 45), (1, 46), (1, 47),           # canal 1 — 3 notas
            (2, 50),                             # canal 2 — 1 nota
            (3, 55), (3, 57),                    # canal 3 — 2 notas
            (4, 60), (4, 62),                    # canal 4 — 2 notas
        ]
        _write_midi(p, [notes])
        dist = tuning.channel_distribution(p)
        assert len(dist) == 1
        track = dist[0]
        assert track.track_name == "Track 0"
        assert len(track.channels) == 5
        assert [c.channel for c in track.channels] == [0, 1, 2, 3, 4]


def test_percentages_sum_to_100():
    """A soma de percentuais dentro de uma track e 100."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "percent.mid")
        notes = [(0, 40)] * 3 + [(1, 50)] * 7  # 30% e 70%
        _write_midi(p, [notes])
        dist = tuning.channel_distribution(p)
        assert len(dist) == 1
        percentages = [c.percentage for c in dist[0].channels]
        assert abs(sum(percentages) - 100.0) < 1e-6
        # E as fracoes conferem.
        assert abs(percentages[0] - 30.0) < 1e-6
        assert abs(percentages[1] - 70.0) < 1e-6


def test_single_channel_reports_one_without_error():
    """Tudo num canal so devolve um unico canal, sem erro."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "one.mid")
        _write_midi(p, [[(0, 40), (0, 44), (0, 48)]])
        dist = tuning.channel_distribution(p)
        assert len(dist) == 1
        assert len(dist[0].channels) == 1
        assert dist[0].channels[0].channel == 0
        assert dist[0].channels[0].note_count == 3
        assert dist[0].channels[0].percentage == 100.0


def test_track_without_notes_is_omitted():
    """Track sem nenhuma nota nao aparece — a secao reporta apenas o que existe."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "empty.mid")
        _write_midi(p, [[], [(0, 40)]])  # a primeira e so meta+track_name
        dist = tuning.channel_distribution(p)
        # A track vazia (indice 0) some; a segunda entra.
        assert [t.track_index for t in dist] == [1]


def test_channel_stats_include_pitch_span_and_count():
    """Cada canal traz pitch_min, pitch_max, span em semitons e contagem."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "stats.mid")
        _write_midi(p, [[(0, 40), (0, 45), (0, 52)]])
        stats = tuning.channel_distribution(p)[0].channels[0]
        assert stats.note_count == 3
        assert stats.pitch_min == 40
        assert stats.pitch_max == 52
        assert stats.span == 12


def test_channels_are_ordered_by_channel_number():
    """Ordem estavel: canais ordenados por numero de canal ascendente."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "order.mid")
        # emite fora de ordem
        _write_midi(p, [[(4, 60), (1, 45), (2, 50), (0, 40), (3, 55)]])
        chans = [c.channel for c in tuning.channel_distribution(p)[0].channels]
        assert chans == [0, 1, 2, 3, 4]


def test_note_on_velocity_zero_does_not_count():
    """`note_on vel=0` e o `note_off` embutido do MIDI — nao inicia nota."""
    mid = mido.MidiFile(ticks_per_beat=480)
    t = mido.MidiTrack()
    t.append(mido.MetaMessage("track_name", name="Guitar", time=0))
    t.append(mido.Message("note_on", channel=0, note=40, velocity=100, time=0))
    # note_on vel=0: nao inicia nota nova, so fecha a anterior
    t.append(mido.Message("note_on", channel=0, note=40, velocity=0, time=120))
    mid.tracks.append(t)
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "runstat.mid")
        mid.save(p)
        dist = tuning.channel_distribution(p)
        assert dist[0].channels[0].note_count == 1


def test_track_name_falls_back_to_index():
    """Track sem meta `track_name` cai em 'Track {index}'."""
    mid = mido.MidiFile(ticks_per_beat=480)
    t = mido.MidiTrack()
    t.append(mido.Message("note_on", channel=0, note=40, velocity=100, time=0))
    t.append(mido.Message("note_off", channel=0, note=40, velocity=0, time=120))
    mid.tracks.append(t)
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "anon.mid")
        mid.save(p)
        dist = tuning.channel_distribution(p)
        assert dist[0].track_name == "Track 0"


def test_analyze_exposes_channel_distribution():
    """A analise principal expoe a distribuicao — a tool `analyze` compoe."""
    from tools import analyze as analysis

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "compose.mid")
        _write_midi(p, [[(0, 40), (1, 45), (1, 47)]])
        a = analysis.analyze(p)
        assert len(a.channel_distribution) == 1
        assert len(a.channel_distribution[0].channels) == 2


# ---------------------------------------------------------------------------
# US-002 — as tres travas contra inferir afinacao onde nao ha corda
# ---------------------------------------------------------------------------


def _write_midi_full(
    path: str,
    tracks: list[dict],
) -> None:
    """Escreve MIDI com controle fino de nome, program e notas por track.

    Cada `tracks[i]` e um dict com chaves:
      - `name`: str (meta track_name); omitido = sem meta
      - `program`: int|None (GM program change no canal 0); None omite
      - `notes`: list[(channel, pitch)]
    """
    mid = mido.MidiFile(ticks_per_beat=480)
    for spec in tracks:
        t = mido.MidiTrack()
        if "name" in spec:
            t.append(mido.MetaMessage("track_name", name=spec["name"], time=0))
        if spec.get("program") is not None:
            t.append(mido.Message(
                "program_change",
                channel=0,
                program=int(spec["program"]),
                time=0,
            ))
        for ch, pitch in spec["notes"]:
            t.append(mido.Message("note_on", channel=ch, note=pitch, velocity=100, time=0))
            t.append(mido.Message("note_off", channel=ch, note=pitch, velocity=0, time=120))
        mid.tracks.append(t)
    mid.save(path)


def _rep(channel: int, pitch: int, count: int) -> list[tuple[int, int]]:
    """`count` disparos da mesma nota no mesmo canal."""
    return [(channel, pitch)] * count


def test_trava1_voice_track_with_wind_patch_does_not_infer():
    """TRAVA 1 — track de voz com patch de sopro (flute, GM=73)
    nao passa. Mesmo com 4 canais distintos, o resultado marca a track
    como `is_stringed=False` com motivo `not_stringed` e nenhum canal
    candidato."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "voice.mid")
        notes = (
            _rep(0, 60, 20)
            + _rep(1, 65, 20)
            + _rep(2, 70, 20)
            + _rep(3, 74, 20)
        )
        _write_midi_full(p, [{"name": "Vocals", "program": 73, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert len(out) == 1
        ti = out[0]
        assert ti.is_stringed is False
        assert ti.stringed_source is None
        assert ti.discard_reason == tuning.NOT_STRINGED
        assert ti.candidate_channels == ()
        assert ti.discarded_channels == ()
        assert 73 in ti.gm_programs


def test_trava2_low_note_count_channel_is_excluded_with_reason():
    """TRAVA 2 — canais com poucas notas caem, motivo `low_note_count`.
    Track de guitarra (GM=30) com um canal cheio e outro com 3 notas."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lownote.mid")
        notes = _rep(0, 40, 12) + _rep(1, 55, 3)
        _write_midi_full(p, [{"name": "Guitar", "program": 30, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert len(out) == 1
        ti = out[0]
        assert ti.is_stringed is True
        assert {c.channel for c in ti.candidate_channels} == {0}
        assert len(ti.discarded_channels) == 1
        d = ti.discarded_channels[0]
        assert d.channel == 1
        assert d.reason == tuning.DISCARD_LOW_NOTE_COUNT
        assert d.note_count == 3


def test_trava3_channel_with_wide_span_is_discarded():
    """TRAVA 3 — canal com span de 40 semitons e descartado, motivo
    `span_too_wide`. Notas o suficiente para passar na TRAVA 2."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "widespan.mid")
        # canal 0: min=40, max=80 -> span 40 semitons, 10 notas
        wide = [(0, 40), (0, 44), (0, 50), (0, 55), (0, 60),
                (0, 65), (0, 70), (0, 72), (0, 76), (0, 80)]
        # canal 1: corda solta candidata, span 0, 10 notas
        tight = _rep(1, 45, 10)
        _write_midi_full(p, [{"name": "Bass", "program": 33, "notes": wide + tight}])
        out = tuning.tuning_inference(p)
        assert len(out) == 1
        ti = out[0]
        assert {c.channel for c in ti.candidate_channels} == {1}
        assert len(ti.discarded_channels) == 1
        d = ti.discarded_channels[0]
        assert d.channel == 0
        assert d.reason == tuning.DISCARD_SPAN_TOO_WIDE
        assert d.span == 40


def test_declared_stringed_track_passes_trava1_without_patch_or_name():
    """Declaracao explicita do usuario dispara a TRAVA 1 mesmo sem patch
    GM de corda e sem nome sugestivo. `stringed_source` = `declared`."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "declared.mid")
        notes = _rep(0, 40, 12) + _rep(1, 45, 12)
        _write_midi_full(p, [{"name": "Custom", "program": None, "notes": notes}])
        out = tuning.tuning_inference(p, declared_stringed_tracks=["Custom"])
        assert len(out) == 1
        ti = out[0]
        assert ti.is_stringed is True
        assert ti.stringed_source == tuning.STRINGED_SOURCE_DECLARED
        assert len(ti.candidate_channels) == 2


def test_stringed_source_precedence_declared_over_program_over_name():
    """Precedencia: `declared` > `gm_program` > `track_name`."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "prec.mid")
        # nome sugere corda, patch e de corda, e declarado tambem —
        # source deve ser `declared`.
        notes = _rep(0, 40, 12)
        _write_midi_full(p, [{"name": "Guitar", "program": 30, "notes": notes}])
        out = tuning.tuning_inference(p, declared_stringed_tracks=["Guitar"])
        assert out[0].stringed_source == tuning.STRINGED_SOURCE_DECLARED

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "prec2.mid")
        # nome nao sugere corda, patch e de corda -> `gm_program`.
        notes = _rep(0, 40, 12)
        _write_midi_full(p, [{"name": "Rhythm", "program": 30, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert out[0].stringed_source == tuning.STRINGED_SOURCE_GM_PROGRAM

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "prec3.mid")
        # sem patch, so nome hint -> `track_name`.
        notes = _rep(0, 40, 12)
        _write_midi_full(p, [{"name": "Baixo", "program": None, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert out[0].stringed_source == tuning.STRINGED_SOURCE_NAME


def test_gm_program_ranges_cover_guitar_and_bass():
    """Vocabulario GM de corda dedilhada: 24-31 (guitarra) + 32-39 (baixo)."""
    assert frozenset(range(24, 32)) == tuning.GM_GUITAR_PROGRAMS
    assert frozenset(range(32, 40)) == tuning.GM_BASS_PROGRAMS
    assert frozenset(range(24, 40)) == tuning.GM_STRINGED_PROGRAMS


def test_analyze_exposes_tuning_inference():
    """A analise principal expoe a inferencia — a tool `analyze` compoe."""
    from tools import analyze as analysis

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "compose_ti.mid")
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _rep(0, 40, 12) + _rep(1, 45, 3),
        }])
        a = analysis.analyze(p)
        assert len(a.tuning_inference) == 1
        ti = a.tuning_inference[0]
        assert ti.is_stringed is True
        assert {c.channel for c in ti.candidate_channels} == {0}
        assert ti.discarded_channels[0].reason == tuning.DISCARD_LOW_NOTE_COUNT


def test_analyze_forwards_declared_stringed_tracks():
    """`analyze()` propaga `declared_stringed_tracks` para a inferencia."""
    from tools import analyze as analysis

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "compose_decl.mid")
        _write_midi_full(p, [{
            "name": "MyThing", "program": None,
            "notes": _rep(0, 40, 12),
        }])
        a = analysis.analyze(p, declared_stringed_tracks=["MyThing"])
        assert a.tuning_inference[0].stringed_source == tuning.STRINGED_SOURCE_DECLARED


def test_boundary_threshold_note_count_included():
    """Canal com exatamente `MIN_NOTES_PER_CHANNEL_FOR_INFERENCE` notas passa."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "boundary.mid")
        notes = _rep(0, 40, tuning.MIN_NOTES_PER_CHANNEL_FOR_INFERENCE)
        _write_midi_full(p, [{"name": "Guitar", "program": 30, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert len(out[0].candidate_channels) == 1
        assert out[0].discarded_channels == ()


def test_boundary_span_max_included():
    """Canal com span == `MAX_STRING_SPAN_SEMITONES` passa; span+1 e descartado."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "span_ok.mid")
        # span 24 = max, 10 notas
        max_span = tuning.MAX_STRING_SPAN_SEMITONES
        notes = [(0, 40)] * 9 + [(0, 40 + max_span)]
        _write_midi_full(p, [{"name": "Guitar", "program": 30, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert len(out[0].candidate_channels) == 1

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "span_over.mid")
        notes = [(0, 40)] * 9 + [(0, 40 + tuning.MAX_STRING_SPAN_SEMITONES + 1)]
        _write_midi_full(p, [{"name": "Guitar", "program": 30, "notes": notes}])
        out = tuning.tuning_inference(p)
        assert out[0].candidate_channels == ()
        assert out[0].discarded_channels[0].reason == tuning.DISCARD_SPAN_TOO_WIDE


def test_empty_track_is_omitted_from_tuning_inference():
    """Track sem nenhuma nota nao entra no resultado — espelha
    `channel_distribution`."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "empty_ti.mid")
        _write_midi_full(p, [
            {"name": "Guitar", "program": 30, "notes": []},
            {"name": "Guitar 2", "program": 30, "notes": _rep(0, 40, 12)},
        ])
        out = tuning.tuning_inference(p)
        assert [t.track_index for t in out] == [1]


# ---------------------------------------------------------------------------
# US-003 — classificacao da afinacao a partir dos intervalos entre canais
# ---------------------------------------------------------------------------


def _six_channels(minimos: list[int]) -> list[tuple[int, int]]:
    """Um canal por corda solta: canal `i` cheio da nota `minimos[i]`.
    Cada canal recebe notas o suficiente para passar na TRAVA 2 e span 0
    para nao esbarrar na TRAVA 3."""
    notes: list[tuple[int, int]] = []
    for ch, pitch in enumerate(minimos):
        notes.extend([(ch, pitch)] * tuning.MIN_NOTES_PER_CHANNEL_FOR_INFERENCE)
    return notes


def test_drop_pattern_75545_classifies_drop_and_names_from_lowest():
    """`[7,5,5,4,5]` sobre corda grave 38 => Drop D."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "drop_d.mid")
        # Drop D: 38 45 50 55 59 64
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([38, 45, 50, 55, 59, 64]),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.tuning_intervals == (7, 5, 5, 4, 5)
        assert ti.tuning_class == tuning.TUNING_CLASS_DROP
        assert ti.tuning_name == "Drop D"
        assert ti.lowest_string_pitch == 38


def test_standard_pattern_55545_classifies_standard_and_names_from_lowest():
    """`[5,5,5,4,5]` sobre corda grave 40 => Standard E."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "standard_e.mid")
        # E padrao: 40 45 50 55 59 64
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([40, 45, 50, 55, 59, 64]),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.tuning_intervals == (5, 5, 5, 4, 5)
        assert ti.tuning_class == tuning.TUNING_CLASS_STANDARD
        assert ti.tuning_name == "Standard E"
        assert ti.lowest_string_pitch == 40


def test_intervals_without_known_pattern_classify_unknown_and_do_not_name():
    """`[5,3,14,2,5,9]` nao bate com nenhum padrao — nao classifica e
    NUNCA gera nome de afinacao."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "gibberish.mid")
        # 40 45 48 62 64 69 78 -> intervalos [5,3,14,2,5,9]
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([40, 45, 48, 62, 64, 69, 78]),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.tuning_intervals == (5, 3, 14, 2, 5, 9)
        assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
        assert ti.tuning_name is None


def test_drop_prefix_classifies_drop_even_without_the_high_strings():
    """Prefixo `[7,5]` das 3 cordas graves ja classifica drop — o riff
    pode nao usar as agudas."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "drop_prefix.mid")
        # 3 cordas graves de Drop D: 38, 45, 50 -> intervalos [7,5]
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([38, 45, 50]),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.tuning_intervals == (7, 5)
        assert ti.tuning_class == tuning.TUNING_CLASS_DROP
        assert ti.tuning_name == "Drop D"


def test_tuning_name_derives_from_pitch_class_of_lowest_string():
    """A nota da corda mais grave determina o nome — MIDI 32 (G#1) com
    padrao drop => Drop G#."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "drop_gsharp.mid")
        # padrao drop hipotetico deslocado: 32 39 44 49 53 58
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([32, 39, 44, 49, 53, 58]),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.tuning_class == tuning.TUNING_CLASS_DROP
        assert ti.tuning_name == "Drop G#"
        assert ti.lowest_string_pitch == 32


def test_single_candidate_channel_yields_no_intervals_and_unknown():
    """Um unico canal candidato nao gera intervalo — classe fica unknown
    e nao ha nome de afinacao. `lowest_string_pitch` ainda vem preenchido."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "one_candidate.mid")
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _rep(0, 40, 12),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.tuning_intervals == ()
        assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
        assert ti.tuning_name is None
        assert ti.lowest_string_pitch == 40


# ---------------------------------------------------------------------------
# US-004 — confianca declarada, nunca maquiada
# ---------------------------------------------------------------------------


def test_confidence_high_when_pattern_and_many_candidates():
    """6 canais de Drop D exercitados => classe drop + confianca `high`."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "drop_d_full.mid")
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([38, 45, 50, 55, 59, 64]),
        }])
        ti = tuning.tuning_inference(p)[0]
        assert ti.tuning_class == tuning.TUNING_CLASS_DROP
        assert ti.confidence == tuning.TUNING_CONFIDENCE_HIGH
        assert len(ti.candidate_channels) >= tuning.MIN_CANDIDATES_FOR_HIGH_CONFIDENCE


def test_confidence_low_when_pattern_but_few_candidates():
    """Prefixo `[7,5]` (2 canais candidatos) classifica drop, mas com poucos
    canais a confianca cai para `low`. Nome de afinacao ainda vem."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "drop_prefix_conf.mid")
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([38, 45, 50]),
        }])
        ti = tuning.tuning_inference(p)[0]
        assert len(ti.candidate_channels) < tuning.MIN_CANDIDATES_FOR_HIGH_CONFIDENCE
        assert ti.tuning_class == tuning.TUNING_CLASS_DROP
        assert ti.confidence == tuning.TUNING_CONFIDENCE_LOW
        assert ti.tuning_name == "Drop D"


def test_confidence_unknown_when_intervals_dont_match_and_no_name():
    """Intervalos sem padrao => classe unknown + confianca `unknown`;
    afinacao desconhecida NUNCA vem com nome."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "gibberish_conf.mid")
        _write_midi_full(p, [{
            "name": "Guitar", "program": 30,
            "notes": _six_channels([40, 45, 48, 62, 64, 69, 78]),
        }])
        ti = tuning.tuning_inference(p)[0]
        assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
        assert ti.confidence == tuning.TUNING_CONFIDENCE_UNKNOWN
        assert ti.tuning_name is None


def test_confidence_unknown_when_not_stringed():
    """Track que nao passa TRAVA 1 => confianca `unknown` e sem nome."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "vox_conf.mid")
        _write_midi_full(p, [{
            "name": "Vocals", "program": 73,
            "notes": _six_channels([60, 65, 70, 74]),
        }])
        ti = tuning.tuning_inference(p)[0]
        assert ti.is_stringed is False
        assert ti.confidence == tuning.TUNING_CONFIDENCE_UNKNOWN
        assert ti.tuning_name is None


def test_confidence_vocabulary_is_closed():
    """Vocabulario fechado: `high`, `low`, `unknown`."""
    assert tuning.TUNING_CONFIDENCE_HIGH == "high"
    assert tuning.TUNING_CONFIDENCE_LOW == "low"
    assert tuning.TUNING_CONFIDENCE_UNKNOWN == "unknown"


def test_not_stringed_track_has_unknown_class_and_no_name():
    """Track que nao passa TRAVA 1 nao classifica afinacao nem gera nome."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "vox_ti.mid")
        _write_midi_full(p, [{
            "name": "Vocals", "program": 73,
            "notes": _six_channels([60, 65, 70, 74]),
        }])
        out = tuning.tuning_inference(p)
        ti = out[0]
        assert ti.is_stringed is False
        assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
        assert ti.tuning_name is None
        assert ti.lowest_string_pitch is None
        assert ti.tuning_intervals == ()
