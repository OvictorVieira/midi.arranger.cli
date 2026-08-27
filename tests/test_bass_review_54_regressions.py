"""Regressoes dos achados do review conjunto com o Codex no PR #54.

Cada teste aqui foi verificado por mutacao: falha quando o fix
correspondente e removido. Teste que passa com o codigo quebrado nao vale
nada, e esta base ja foi mordida por isso.
"""

from __future__ import annotations

import mido
import pytest

from tools.techniques.engine import apply_technique


def _bass_track(events, *, pre=None, with_eot=False) -> mido.MidiFile:
    """MIDI de uma track de baixo. `events` sao `(start, dur, pitch, vel)`."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    for msg in pre or ():
        track.append(msg)
    previous = 0
    for start, dur, pitch, vel in events:
        track.append(mido.Message(
            "note_on", note=pitch, velocity=vel, time=start - previous,
        ))
        track.append(mido.Message(
            "note_off", note=pitch, velocity=0, time=dur,
        ))
        previous = start + dur
    if with_eot:
        track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    return mid


def _structural_velocities(mid: mido.MidiFile, floor_pitch: int = 40):
    return [
        msg.velocity
        for msg in mid.tracks[0]
        if msg.type == "note_on"
        and msg.velocity > 0
        and msg.note >= floor_pitch
    ]


def _snapshot(mid: mido.MidiFile):
    return [
        (m.type, getattr(m, "note", None), getattr(m, "velocity", None), m.time)
        for m in mid.tracks[0]
    ]


_LIGABLE = [(0, 480, 40, 100), (480, 240, 42, 100)]


def test_hammer_pull_nao_quebra_com_control_change_na_track():
    """Achado 4: `msg.note` era lido antes de checar o tipo.

    MIDI real de baixo carrega CC no meio das notas (let ring, expressao).
    Qualquer `control_change` derrubava a tecnica com AttributeError — ou
    seja, a tecnica funcionava so em fixture sintetica.
    """
    source = _bass_track(
        _LIGABLE,
        pre=[mido.Message("control_change", control=64, value=127, time=0)],
    )
    out = apply_technique(
        "bass.hammer_pull", source, seed=13, tool="modo_bass",
        parameters={"density": 1.0},
    )
    assert out is not None


def test_let_ring_fecha_o_pedal_antes_do_end_of_track():
    """Achado 3: o pedal-off caia DEPOIS do `end_of_track`.

    `end_of_track` e o ultimo evento da track por definicao do SMF. Leitor
    que o respeita descartava o CC de desliga e o sustain ficava preso.
    """
    source = _bass_track([(0, 480, 40, 100)], with_eot=True)
    out = apply_technique(
        "bass.let_ring", source, seed=5, tool="modo_bass",
        parameters={"density": 1.0},
    )
    types = [msg.type for msg in out.tracks[0]]
    assert "end_of_track" in types
    depois = types[types.index("end_of_track") + 1:]
    assert depois == [], f"eventos depois do end_of_track: {depois}"


def test_attack_style_preserva_a_pressao_da_origem():
    """Achado 2: a alternancia sobrescrevia velocity com valor absoluto.

    Uma nota escrita em 127 saia em 85 — abaixo do PISO da propria origem.
    E o mesmo defeito que tirou `drums.accent_hierarchy` do motor: tecnica
    nao pode inverter a intencao de quem escreveu. Os numeros do manual
    definem a DIFERENCA entre golpe para baixo e para cima, nao o valor.
    """
    events = [
        (0, 240, 40, 100),
        (240, 240, 41, 100),
        (480, 240, 42, 100),
        (720, 240, 43, 127),
    ]
    out = apply_technique(
        "bass.attack_style", _bass_track(events), seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    saida = _structural_velocities(out)
    origem = [e[3] for e in events]
    assert len(saida) == len(origem)

    piso_origem = min(origem)
    topo = max(range(len(origem)), key=lambda i: origem[i])
    assert saida[topo] >= piso_origem, (
        f"a nota mais forte da origem ({origem[topo]}) saiu em "
        f"{saida[topo]}, abaixo do piso da origem ({piso_origem})"
    )
    assert len(set(saida)) > 1, "a alternancia tem que continuar existindo"


@pytest.mark.parametrize("tool", ["generic", "modo_bass"])
def test_hammer_pull_e_idempotente_nos_dois_caminhos(tool):
    """Achado 5: o caminho `generic` reaplicava `velocity_relativa`.

    Sem keyswitch para reconhecer o que ja fez, cada render afundava a
    segunda nota: 100 -> 71 -> 42. A sobreposicao e a assinatura do ligado.
    """
    uma = apply_technique(
        "bass.hammer_pull", _bass_track(_LIGABLE), seed=13, tool=tool,
        parameters={"density": 1.0},
    )
    duas = apply_technique(
        "bass.hammer_pull", uma, seed=13, tool=tool,
        parameters={"density": 1.0},
    )
    assert _snapshot(uma) == _snapshot(duas)


@pytest.mark.parametrize("tool", ["generic", "modo_bass"])
def test_attack_style_e_idempotente_nos_dois_caminhos(tool):
    """A alternancia relativa so e segura se nao acumular.

    O valor absoluto era idempotente por acidente; ao trocar para delta
    relativo (fix do achado 2), a idempotencia passou a depender de
    reconhecer o keyswitch de estilo ja presente.
    """
    events = [(0, 240, 40, 100), (240, 240, 41, 110), (480, 240, 42, 90)]
    uma = apply_technique(
        "bass.attack_style", _bass_track(events), seed=1, tool=tool,
        parameters={"style": "palheta"},
    )
    duas = apply_technique(
        "bass.attack_style", uma, seed=1, tool=tool,
        parameters={"style": "palheta"},
    )
    assert _snapshot(uma) == _snapshot(duas)
