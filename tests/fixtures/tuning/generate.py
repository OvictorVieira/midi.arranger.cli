"""Gerador determinístico das fixtures de deteccao de afinacao (issue #35).

O MIDI multi-instrumento real do usuario nao vive no repositorio. Este script
constroi seis pequenos MIDIs sinteticos que reproduzem, cada um, uma
situacao concreta que o detector precisa lidar:

  fixture_a_rhythm_guitar.mid   — guitarra ritmica com 5 canais e cerca de
                                  28/37/29/3/3 % das notas (~94% nas 3 graves)
  fixture_b_bass_riff.mid       — baixo com ~91,5% em uma corda unica (MIDI 21)
  fixture_c_voice_wind_patch.mid — voz com 4 canais [5,5,4] mas patch GM 73
                                   (flauta): TRAVA 1 recusa a inferencia
  fixture_d_lead_guitar_low_count.mid — canais com 2 e 4 notas: TRAVA 2 os
                                        elimina
  fixture_e_standard_tuning.mid — 6 canais com intervalos [5,5,5,4,5]
  fixture_f_single_channel_guitar.mid — track unica de corda sem separacao
                                        por canal

Rode `python tests/fixtures/tuning/generate.py` para reescrever as seis
fixtures. A saida e deterministica: as mesmas entradas produzem os mesmos
bytes de arquivo, o que permite versionar os `.mid` e o `README.md` juntos
sem drift.
"""

from __future__ import annotations

import os
from typing import NamedTuple

import mido

TICKS_PER_BEAT = 480
NOTE_TICK_LENGTH = 120


class TrackSpec(NamedTuple):
    name: str
    program: int | None
    notes: list[tuple[int, int]]


def _rep(channel: int, pitch: int, count: int) -> list[tuple[int, int]]:
    return [(channel, pitch)] * count


def _write_midi(path: str, tracks: list[TrackSpec]) -> None:
    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    for spec in tracks:
        t = mido.MidiTrack()
        t.append(mido.MetaMessage("track_name", name=spec.name, time=0))
        if spec.program is not None:
            t.append(mido.Message(
                "program_change",
                channel=0,
                program=int(spec.program),
                time=0,
            ))
        for ch, pitch in spec.notes:
            t.append(mido.Message(
                "note_on", channel=ch, note=pitch, velocity=100, time=0,
            ))
            t.append(mido.Message(
                "note_off", channel=ch, note=pitch, velocity=0,
                time=NOTE_TICK_LENGTH,
            ))
        mid.tracks.append(t)
    mid.save(path)


def build_fixture_a() -> list[TrackSpec]:
    """Guitarra ritmica exportada por Songsterr — um canal por corda, com
    a distribuicao documentada na issue #35: os minimos dos canais sao
    32, 39, 44, 52, 55 (intervalos 7,5,8,3 entre eles) e as notas se
    concentram nas tres cordas graves (~28% + ~37% + ~29% = ~94%). Os
    canais 3 e 4 tem 3 notas cada e caem pela TRAVA 2."""
    notes = (
        _rep(0, 32, 28)
        + _rep(1, 39, 37)
        + _rep(2, 44, 29)
        + _rep(3, 52, 3)
        + _rep(4, 55, 3)
    )
    return [TrackSpec(name="Guitar", program=30, notes=notes)]


def build_fixture_b() -> list[TrackSpec]:
    """Baixo com ~91,5% das notas concentradas na corda mais grave aberta
    (MIDI 21, A0). Total 200 notas: 183 no canal 0 (91,5%), 10 no canal 1
    (5%), 7 no canal 2 (3,5% — cai pela TRAVA 2)."""
    notes = _rep(0, 21, 183) + _rep(1, 28, 10) + _rep(2, 33, 7)
    return [TrackSpec(name="Bass", program=33, notes=notes)]


def build_fixture_c() -> list[TrackSpec]:
    """Voz com 4 canais e intervalos [5,5,4] entre minimos, mas o patch GM
    e 73 (Flute). Serve para provar que a TRAVA 1 impede a inferencia
    mesmo quando a distribuicao imita um instrumento de corda."""
    notes = (
        _rep(0, 60, 20)
        + _rep(1, 65, 20)
        + _rep(2, 70, 20)
        + _rep(3, 74, 20)
    )
    return [TrackSpec(name="Vocals", program=73, notes=notes)]


def build_fixture_d() -> list[TrackSpec]:
    """Lead guitar com canais dedilhados brevemente (2 e 4 notas) cujos
    minimos NAO representam corda solta. Instrumento e de corda (GM 30),
    passa TRAVA 1; a TRAVA 2 elimina ambos os canais."""
    notes = (
        _rep(0, 68, 2)
        + _rep(1, 75, 4)
    )
    return [TrackSpec(name="Lead Guitar", program=30, notes=notes)]


def build_fixture_e() -> list[TrackSpec]:
    """Afinacao padrao: 6 canais com minimos 40,45,50,55,59,64
    (intervalos [5,5,5,4,5]). Cada canal com contagem suficiente para
    passar TRAVA 2. Serve para provar que padrao nao e classificado
    como drop."""
    minimos = [40, 45, 50, 55, 59, 64]
    notes: list[tuple[int, int]] = []
    for ch, pitch in enumerate(minimos):
        notes.extend(_rep(ch, pitch, 8))
    return [TrackSpec(name="Guitar", program=30, notes=notes)]


def build_fixture_f() -> list[TrackSpec]:
    """Track de corda unica, sem separacao por canal — tudo cai no canal 0.
    Passa TRAVA 1 pelo patch e pelo nome; a inferencia devolve um unico
    canal candidato, sem intervalos e sem afinacao nomeada (ausencia de
    informacao de corda), mas nao gera erro."""
    return [TrackSpec(
        name="Guitar",
        program=30,
        notes=_rep(0, 40, 24),
    )]


FIXTURES: dict[str, list[TrackSpec]] = {
    "fixture_a_rhythm_guitar.mid": build_fixture_a(),
    "fixture_b_bass_riff.mid": build_fixture_b(),
    "fixture_c_voice_wind_patch.mid": build_fixture_c(),
    "fixture_d_lead_guitar_low_count.mid": build_fixture_d(),
    "fixture_e_standard_tuning.mid": build_fixture_e(),
    "fixture_f_single_channel_guitar.mid": build_fixture_f(),
}


def build_all(dest_dir: str) -> list[str]:
    """Gera as seis fixtures em `dest_dir` e devolve os caminhos escritos."""
    written: list[str] = []
    for filename, tracks in FIXTURES.items():
        path = os.path.join(dest_dir, filename)
        _write_midi(path, tracks)
        written.append(path)
    return written


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for path in build_all(here):
        print(os.path.relpath(path, os.getcwd()))
