"""Fixtures compartilhadas pelos testes das tecnicas de guitarra e teclas.

Um MIDI escrito nota a nota, sem nenhum ornamento, e a unica forma honesta de
provar que a tecnica escreveu alguma coisa: tudo que aparecer na saida foi o
motor que colocou.
"""

from __future__ import annotations

import io

import mido


def build_track_midi(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 0,
    tempo_us: int = 500_000,
    name: str = "Line",
) -> mido.MidiFile:
    """events: `(start_tick, duracao_ticks, pitch, velocity)`, tick absoluto."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch, velocity in events:
        absolute.append((
            start,
            order,
            mido.Message(
                "note_on", channel=channel, note=pitch, velocity=velocity,
            ),
        ))
        order += 1
        absolute.append((
            start + duration,
            order,
            mido.Message(
                "note_off", channel=channel, note=pitch, velocity=0,
            ),
        ))
        order += 1
    previous = 0
    for absolute_tick, _order, msg in sorted(
        absolute, key=lambda item: (item[0], item[1]),
    ):
        track.append(msg.copy(time=absolute_tick - previous))
        previous = absolute_tick
    mid.tracks.append(track)
    return mid


def note_events(mid: mido.MidiFile) -> list[tuple[int, str, int, int]]:
    """`(tick, tipo, pitch, velocity)` de todo note_on/note_off do arquivo."""

    out: list[tuple[int, str, int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                out.append((tick, "on", msg.note, msg.velocity))
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                out.append((tick, "off", msg.note, msg.velocity))
    return out


def pitchwheel_events(mid: mido.MidiFile) -> list[tuple[int, int, int]]:
    """`(tick, canal, valor)` de todo pitch bend do arquivo."""

    out: list[tuple[int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if not msg.is_meta and msg.type == "pitchwheel":
                out.append((tick, msg.channel, msg.pitch))
    return out


def control_events(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """`(tick, canal, controlador, valor)` de todo CC do arquivo."""

    out: list[tuple[int, int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if not msg.is_meta and msg.type == "control_change":
                out.append((tick, msg.channel, msg.control, msg.value))
    return out


def midi_bytes(mid: mido.MidiFile) -> bytes:
    buffer = io.BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()
