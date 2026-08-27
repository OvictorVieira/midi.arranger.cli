"""Regressoes dos achados do review conjunto com o Codex no PR #55.

Cada teste foi verificado por mutacao: falha quando o fix correspondente
e removido.
"""

from __future__ import annotations

import mido

from tools.techniques.engine import apply_technique


def _drums_track(events) -> mido.MidiFile:
    """MIDI de uma track de bateria. `events` sao `(start, dur, pitch, vel)`."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    absolute = []
    for start, dur, pitch, vel in events:
        absolute.append((start, 0, mido.Message(
            "note_on", note=pitch, velocity=vel, channel=9,
        )))
        absolute.append((start + dur, 1, mido.Message(
            "note_off", note=pitch, velocity=0, channel=9,
        )))
    absolute.sort(key=lambda item: (item[0], item[1]))
    previous = 0
    for tick, _bias, msg in absolute:
        track.append(msg.copy(time=tick - previous))
        previous = tick
    mid.tracks.append(track)
    return mid


def _drum_notes(mid: mido.MidiFile):
    out = []
    for track in mid.tracks:
        tick = 0
        pending: dict[int, list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault(msg.note, []).append((tick, msg.velocity))
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get(msg.note)
                if stack:
                    start, vel = stack.pop(0)
                    out.append((start, tick - start, msg.note, vel))
    return sorted(out)


def test_accented_roll_nao_esmaga_nota_forte_abaixo_do_piso_hardcoded():
    """Achado 1: o gate de pressao comparava contra 105, nao contra o teto
    da faixa suave (79). Nota de origem 104 passava direto pelo `>= 105`
    e saia esmagada a 55 — abaixo do que a origem escreveu.
    """
    events = [(0, 120, 38, 104), (120, 120, 38, 104), (240, 120, 38, 104),
              (360, 120, 38, 104)]
    out = apply_technique("drums.accented_roll", _drums_track(events), seed=123)
    velocities = [v for _s, _d, _p, v in _drum_notes(out)]
    assert all(v > 79 for v in velocities), (
        f"nota de origem 104 nao pode sair na faixa suave: {velocities}"
    )


def test_buzz_roll_nao_quebra_com_caixa_proxima():
    """Achado 2: gerar ornamento sobrepondo nota estrutural da mesma pitch
    reembaralhava o pareamento FIFO e o contrato explodia com
    'duracao de nota estrutural mudou'.
    """
    events = [(360, 90, 38, 100), (480, 120, 38, 110)]
    out = apply_technique(
        "drums.buzz_roll", _drums_track(events), seed=123,
        tool="superior_drummer",
    )
    structural = {(s, d, p, v) for s, d, p, v in events}
    survived = {(s, d, p, v) for s, d, p, v in _drum_notes(out) if (s, d, p, v) in structural}
    assert survived == structural


def test_buzz_roll_nao_semeia_em_silencio():
    """Achado 3: uma caixa isolada apos pausa longa gerava rufo do nada,
    sem nenhuma atividade de bateria antes dela.
    """
    events = [(3840, 120, 38, 110)]
    out = apply_technique(
        "drums.buzz_roll", _drums_track(events), seed=123,
        tool="superior_drummer",
    )
    antes = [item for item in _drum_notes(out) if item[0] < 3840]
    assert antes == [], f"ornamento semeado no silencio: {antes}"


def test_buzz_roll_ainda_nasce_com_atividade_recente():
    """Contraprova do achado 3: com groove ativo antes, o rufo continua
    nascendo normalmente."""
    events = [(3600, 60, 42, 90), (3840, 120, 38, 110)]
    out = apply_technique(
        "drums.buzz_roll", _drums_track(events), seed=123,
        tool="superior_drummer",
    )
    antes = [item for item in _drum_notes(out) if item[0] < 3840]
    assert antes, "rufo deveria nascer quando ha atividade recente"


def test_accented_roll_density_seleciona_quais_rulos_recebem_contorno():
    """Achado 5: `density` era aceito e ignorado — 0.1 e 1.0 produziam
    exatamente a mesma saida. A selecao e por SEQUENCIA de rulo inteira,
    nunca nota a nota dentro do rulo (isso embaralharia a posicao que da
    sentido a mao dominante e ao lift pre-acento).
    """
    seq1 = [(120 * i, 60, 38, 60) for i in range(4)]
    seq2 = [(2000 + 120 * i, 60, 38, 60) for i in range(4)]
    events = seq1 + seq2
    partial = apply_technique(
        "drums.accented_roll", _drums_track(events), seed=123,
        parameters={"density": 0.3},
    )
    full = apply_technique(
        "drums.accented_roll", _drums_track(events), seed=123,
        parameters={"density": 1.0},
    )
    v_partial = [v for _s, _d, _p, v in _drum_notes(partial)]
    v_full = [v for _s, _d, _p, v in _drum_notes(full)]
    assert v_partial != v_full


def test_articulation_diff_density_seleciona_quais_batidas_trocam():
    """Achado 5: mesma classe de bug em `articulation_diff` — density
    aceito pelo schema e ignorado na aplicacao."""
    events = [(0, 60, 42, 80), (240, 60, 42, 81), (480, 60, 42, 82),
              (720, 60, 42, 83)]
    partial = apply_technique(
        "drums.articulation_diff", _drums_track(events), seed=1,
        tool="superior_drummer", parameters={"density": 0.1},
    )
    full = apply_technique(
        "drums.articulation_diff", _drums_track(events), seed=1,
        tool="superior_drummer", parameters={"density": 1.0},
    )
    p_partial = [p for _s, _d, p, _v in _drum_notes(partial)]
    p_full = [p for _s, _d, p, _v in _drum_notes(full)]
    assert p_partial != p_full
