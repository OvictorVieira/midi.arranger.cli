"""Deteccao de corda e afinacao a partir da distribuicao de canais MIDI.

O MIDI nao carrega metadado de afinacao, mas Guitar Pro e Songsterr
exportam **um canal por corda**. Este modulo le a distribuicao de notas
por canal em cada SMF track — o dado bruto de onde qualquer inferencia
de afinacao vai partir em rodadas seguintes.

Le o arquivo com `mido`, porque `pretty_midi.Instrument` funde notas
por (channel, program) e perde a nocao de SMF track. O que importa aqui
e a track fisica exportada pela DAW, e cada canal dentro dela.

Escopo desta rodada: distribuicao por canal (US-001). Inferencia de
afinacao vem nas proximas stories.
"""

from __future__ import annotations

from dataclasses import dataclass

import mido


@dataclass(frozen=True)
class ChannelStats:
    """Distribuicao de uma corda-candidata dentro de uma SMF track.

    - `channel`: 0-15, o numero do canal MIDI.
    - `note_count`: quantas notas foram disparadas nesse canal.
    - `pitch_min` / `pitch_max`: menor e maior nota vista.
    - `span`: `pitch_max - pitch_min`, em semitons.
    - `percentage`: fracao das notas da track que caem nesse canal,
      em porcentagem (0.0-100.0). A soma dentro de uma track e 100.0.
    """
    channel: int
    note_count: int
    pitch_min: int
    pitch_max: int
    span: int
    percentage: float


@dataclass(frozen=True)
class TrackChannelDistribution:
    """Distribuicao de canais de uma SMF track (arquivo bruto)."""
    track_index: int
    track_name: str
    channels: tuple[ChannelStats, ...]


def _track_name(track: mido.MidiTrack, index: int) -> str:
    """Nome estavel para uma SMF track. Cai em `Track {index}` quando o
    arquivo nao declarou meta `track_name` ou o valor veio vazio."""
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            name = (msg.name or "").strip()
            if name:
                return name
            break
    return f"Track {index}"


def _iter_note_ons(track: mido.MidiTrack):
    """Itera apenas note_on de fato (velocity > 0). `note_on vel=0` e o
    `note_off` embutido do MIDI running status e nao inicia nota."""
    for msg in track:
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            yield msg


def channel_distribution(midi_path: str) -> list[TrackChannelDistribution]:
    """Distribuicao de notas por canal, por SMF track, em `midi_path`.

    Ordem estavel: tracks na ordem em que aparecem no arquivo; canais
    dentro de uma track ordenados por numero de canal ascendente. Track
    sem nenhuma nota nao entra no resultado — a secao de distribuicao
    reporta apenas o que existe.

    Track com todas as notas num canal so devolve uma unica entrada de
    canal, sem erro.
    """
    mid = mido.MidiFile(midi_path)

    result: list[TrackChannelDistribution] = []
    for idx, track in enumerate(mid.tracks):
        per_channel: dict[int, list[int]] = {}
        for msg in _iter_note_ons(track):
            per_channel.setdefault(msg.channel, []).append(msg.note)
        if not per_channel:
            continue

        total = sum(len(pitches) for pitches in per_channel.values())
        stats: list[ChannelStats] = []
        for ch in sorted(per_channel):
            pitches = per_channel[ch]
            lo = min(pitches)
            hi = max(pitches)
            stats.append(ChannelStats(
                channel=int(ch),
                note_count=len(pitches),
                pitch_min=int(lo),
                pitch_max=int(hi),
                span=int(hi - lo),
                percentage=100.0 * len(pitches) / total,
            ))
        result.append(TrackChannelDistribution(
            track_index=idx,
            track_name=_track_name(track, idx),
            channels=tuple(stats),
        ))

    return result


__all__ = [
    "ChannelStats",
    "TrackChannelDistribution",
    "channel_distribution",
]
