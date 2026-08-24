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
