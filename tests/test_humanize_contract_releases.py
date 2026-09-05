"""Contrato `humanize` com releases sobrepostos (issue #125).

Bateria real de biblioteca re-ataca hi-hat com a peca anterior ainda soando.
Deslocar essas notas alguns milissegundos -- que e exatamente o que
`drums.microtiming` existe para fazer -- troca a ORDEM GLOBAL dos `note_off`
da track. O `AGENTS.md` exige que a nota seja par FECHADO por
track/canal/altura, nao que o entrelacamento de releases entre alturas
DIFERENTES fique congelado.

Este modulo cobre os dois lados da mesma moeda:

1. o material legitimo com release sobreposto passa;
2. cada violacao real que o pareamento tinha que pegar CONTINUA sendo pega,
   inclusive num MIDI cujos releases se sobrepoem -- caso contrario o
   afrouxamento seria teatro.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import mido
import pytest

from tools.techniques import (
    TechniqueContext,
    TechniqueContractError,
    TechniqueRegistry,
    apply_technique,
)

SEEDS = (0, 1, 3, 42)


def _midi_com_releases_sobrepostos() -> mido.MidiFile:
    """Oito re-ataques de 42 com o 46 anterior ainda soando.

    Os dois `note_off` ficam a 2 ticks um do outro: qualquer microtiming
    inverte a ordem global dos releases sem tocar em pitch, contagem ou
    ordem de `note_on`.
    """

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))

    eventos: list[tuple[int, int, mido.Message]] = []
    for compasso in range(8):
        base = compasso * 480
        eventos.append((base, 0, mido.Message(
            "note_on", note=46, velocity=100, channel=9, time=0)))
        eventos.append((base + 240, 1, mido.Message(
            "note_on", note=42, velocity=90, channel=9, time=0)))
        eventos.append((base + 300, 2, mido.Message(
            "note_off", note=46, velocity=0, channel=9, time=0)))
        eventos.append((base + 302, 3, mido.Message(
            "note_off", note=42, velocity=0, channel=9, time=0)))

    eventos.sort(key=lambda item: (item[0], item[1]))
    anterior = 0
    for tick, _ordem, msg in eventos:
        msg.time = tick - anterior
        anterior = tick
        track.append(msg)
    track.append(mido.MetaMessage("end_of_track", time=0))
    return mid


def _midi_sem_sobreposicao() -> mido.MidiFile:
    """Mesma bateria, releases sequenciais -- o material que ja passava."""

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    for _ in range(8):
        track.append(mido.Message(
            "note_on", note=46, velocity=100, channel=9, time=0))
        track.append(mido.Message(
            "note_off", note=46, velocity=0, channel=9, time=120))
        track.append(mido.Message(
            "note_on", note=42, velocity=90, channel=9, time=120))
        track.append(mido.Message(
            "note_off", note=42, velocity=0, channel=9, time=120))
    track.append(mido.MetaMessage("end_of_track", time=0))
    return mid


def _bytes(mid: mido.MidiFile) -> bytes:
    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


# --- o material legitimo passa --------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_microtiming_aceita_bateria_com_releases_sobrepostos(seed: int) -> None:
    resultado = apply_technique(
        "drums.microtiming", _midi_com_releases_sobrepostos(), seed=seed
    )

    def notas(mid: mido.MidiFile) -> list[tuple[int, int]]:
        return [
            (msg.channel, msg.note)
            for msg in mid.tracks[0]
            if msg.type == "note_on" and msg.velocity > 0
        ]

    assert notas(resultado) == notas(_midi_com_releases_sobrepostos())


def test_troca_de_ordem_de_release_entre_alturas_diferentes_e_permitida() -> None:
    """Trocar a ordem de dois `note_off` de alturas diferentes so muda duracao.

    Duracao e eixo LIVRE no nivel `humanize`; o pareamento nao pode reprovar
    isso.
    """

    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        track = mid.tracks[0]
        indices = [
            i for i, msg in enumerate(track)
            if msg.type == "note_off"
        ]
        primeiro, segundo = indices[0], indices[1]
        a, b = track[primeiro], track[segundo]
        track[primeiro] = b.copy(time=a.time)
        track[segundo] = a.copy(time=b.time)
        return mid

    resultado = registry.apply(
        "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
    )

    offs = [msg.note for msg in resultado.tracks[0] if msg.type == "note_off"]
    assert offs[:2] == [42, 46]


# --- cada violacao CONTINUA sendo pega, mesmo com releases sobrepostos -----

def _registry_que(mutacao) -> TechniqueRegistry:
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        mutacao(mid)
        return mid

    return registry


def test_ainda_pega_mudanca_de_contagem_com_releases_sobrepostos() -> None:
    def mutacao(mid: mido.MidiFile) -> None:
        mid.tracks[0].append(mido.Message(
            "note_on", channel=9, note=38, velocity=90, time=0))
        mid.tracks[0].append(mido.Message(
            "note_off", channel=9, note=38, velocity=0, time=120))

    with pytest.raises(TechniqueContractError, match="contagem de note_on"):
        _registry_que(mutacao).apply(
            "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
        )


def test_ainda_pega_mudanca_de_pitch_com_releases_sobrepostos() -> None:
    def mutacao(mid: mido.MidiFile) -> None:
        track = mid.tracks[0]
        for i, msg in enumerate(track):
            if msg.type in ("note_on", "note_off") and msg.note == 42:
                track[i] = msg.copy(note=38)

    with pytest.raises(TechniqueContractError, match="multiconjunto de pitches"):
        _registry_que(mutacao).apply(
            "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
        )


def test_ainda_pega_note_off_orfao_com_releases_sobrepostos() -> None:
    def mutacao(mid: mido.MidiFile) -> None:
        mid.tracks[0].append(mido.Message(
            "note_off", channel=9, note=46, velocity=0, time=0))

    with pytest.raises(TechniqueContractError, match="note_off orfao"):
        _registry_que(mutacao).apply(
            "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
        )


def test_ainda_pega_note_on_sem_fechamento_com_releases_sobrepostos() -> None:
    def mutacao(mid: mido.MidiFile) -> None:
        track = mid.tracks[0]
        for i, msg in enumerate(track):
            if msg.type == "note_off" and msg.note == 42:
                del track[i]
                track[i] = track[i].copy(time=track[i].time + msg.time)
                return

    with pytest.raises(TechniqueContractError, match="note_on sem note_off"):
        _registry_que(mutacao).apply(
            "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
        )


def test_ainda_pega_troca_de_ordem_de_note_on_com_releases_sobrepostos() -> None:
    """Reordenar `note_on` de alturas diferentes e violacao explicita."""

    def mutacao(mid: mido.MidiFile) -> None:
        track = mid.tracks[0]
        indices = [
            i for i, msg in enumerate(track)
            if msg.type == "note_on" and msg.velocity > 0
        ]
        primeiro, segundo = indices[0], indices[1]
        a, b = track[primeiro], track[segundo]
        track[primeiro] = b.copy(time=a.time)
        track[segundo] = a.copy(time=b.time)

    with pytest.raises(TechniqueContractError, match="ordem dos note_on"):
        _registry_que(mutacao).apply(
            "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
        )


def test_ainda_pega_note_off_movido_para_outra_altura() -> None:
    """Reatribuir um `note_off` a outra altura deixa par aberto E orfao."""

    def mutacao(mid: mido.MidiFile) -> None:
        track = mid.tracks[0]
        for i, msg in enumerate(track):
            if msg.type == "note_off" and msg.note == 46:
                track[i] = msg.copy(note=42)
                return

    with pytest.raises(TechniqueContractError, match="note_off orfao"):
        _registry_que(mutacao).apply(
            "drums.microtiming", _midi_com_releases_sobrepostos(), seed=1
        )


# --- compatibilidade: o que passava continua byte-identico ----------------

# Congelado com a implementacao ANTERIOR a issue #125 (pareamento por ordem
# global). Se o afrouxamento mudasse a saida de um MIDI que ja passava, este
# digest quebraria.
DIGEST_SEM_SOBREPOSICAO = {
    0: "65fd1100d3e20bffde9b29a4bb7860786d289550a97aa974745d8c6320e7ee04",
    1: "10ef56bf901038dafb460a2c0cd1d41d95d07731206615786218051cac681e2a",
    3: "a4ac86ba1f765dd43ec200d9e7786a55d48b7779d89a32ca58f99023a75a80ed",
    42: "57e7fca4460fa62e46dc02ea69803c55d97079ebd8f10a2ef4fa74dd92eb371e",
}


@pytest.mark.parametrize("seed", SEEDS)
def test_midi_sem_sobreposicao_continua_byte_identico(seed: int) -> None:
    resultado = apply_technique(
        "drums.microtiming", _midi_sem_sobreposicao(), seed=seed
    )
    digest = hashlib.sha256(_bytes(resultado)).hexdigest()

    assert digest == DIGEST_SEM_SOBREPOSICAO[seed]


@pytest.mark.parametrize("seed", SEEDS)
def test_microtiming_com_sobreposicao_e_deterministico(seed: int) -> None:
    a = apply_technique(
        "drums.microtiming", _midi_com_releases_sobrepostos(), seed=seed
    )
    b = apply_technique(
        "drums.microtiming", _midi_com_releases_sobrepostos(), seed=seed
    )

    assert _bytes(a) == _bytes(b)
