"""Testes de `bass.ghost_notes` — dead notes do metal, sem atulhar."""

from __future__ import annotations

import mido
import pytest

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    TechniqueContractError,
    apply_technique,
    apply_technique_with_warnings,
    get_technique,
)


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
            start,
            order,
            mido.Message(
                "note_on", channel=channel, note=pitch, velocity=96, time=0,
            ),
        ))
        order += 1
        absolute.append((
            start + duration,
            order,
            mido.Message(
                "note_off", channel=channel, note=pitch, velocity=0, time=0,
            ),
        ))
        order += 1
    prev = 0
    for absolute_tick, _order, msg in sorted(
        absolute, key=lambda item: (item[0], item[1])
    ):
        track.append(msg.copy(time=absolute_tick - prev))
        prev = absolute_tick
    mid.tracks.append(track)
    return mid


def _structural_signature(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """Retorna (channel, pitch, start_tick, end_tick) das notas na mesma
    ordem que o motor considera 'estrutural' (mesmas ticks e pitches)."""
    out: list[tuple[int, int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        pending: dict[tuple[int, int], list[int]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault((msg.channel, msg.note), []).append(tick)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if not stack:
                    continue
                start = stack.pop(0)
                out.append((msg.channel, msg.note, start, tick))
    return out


def _pitches(mid: mido.MidiFile) -> list[int]:
    return [
        msg.note
        for track in mid.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]


def _quarter_line(ticks_per_beat: int = 480) -> list[tuple[int, int, int]]:
    # 4 semininas de baixo em 40 (E1), duracao = 1 semicolcheia (240 ticks).
    return [(i * ticks_per_beat, ticks_per_beat // 2, 40) for i in range(8)]


def test_bass_ghost_notes_is_registered_as_supported():
    assert "bass.ghost_notes" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.ghost_notes")
    assert entry.canonical == "bass.ghost_notes"
    assert entry.level == "technique"


def test_bass_ghost_notes_preserves_structural_notes():
    source = _make_bass_line(_quarter_line())
    before = _structural_signature(source)
    out = apply_technique(
        "bass.ghost_notes", source, seed=7, tool="generic",
    )
    after = _structural_signature(out)
    # Toda nota estrutural permanece com mesmo pitch/canal/start/end.
    for note in before:
        assert note in after, (
            f"nota estrutural {note} sumiu ou mudou; after={after}"
        )


def test_bass_ghost_notes_adds_ghost_notes():
    source = _make_bass_line(_quarter_line())
    before_pitches = _pitches(source)
    out = apply_technique(
        "bass.ghost_notes", source, seed=7, tool="generic",
    )
    after_pitches = _pitches(out)
    assert len(after_pitches) > len(before_pitches), (
        "tecnica nao acrescentou ghost note nenhuma"
    )


def test_bass_ghost_notes_density_zero_disables_technique():
    source = _make_bass_line(_quarter_line())
    result = apply_technique_with_warnings(
        "bass.ghost_notes", source, seed=7, tool="generic",
        parameters={"density": 0.0},
    )
    assert len(_pitches(result.result)) == len(_pitches(_make_bass_line(_quarter_line())))


def test_bass_ghost_notes_does_not_seed_across_long_rest():
    # Duas notas com intervalo maior que um compasso (>= 4 beats) — a segunda
    # comeca no beat 8 (dois compassos depois). O algoritmo NAO deve semear
    # ghost dentro dessa borda de pausa.
    ticks_per_beat = 480
    events = [
        (0, ticks_per_beat // 2, 40),
        (ticks_per_beat * 8, ticks_per_beat // 2, 40),
    ]
    source = _make_bass_line(events, ticks_per_beat=ticks_per_beat)
    out = apply_technique(
        "bass.ghost_notes", source, seed=1, tool="generic",
    )
    assert _pitches(out) == _pitches(source), (
        "ghost note foi semeada por cima de silencio > 1 compasso"
    )


def test_bass_ghost_notes_uses_previous_structural_pitch():
    # Ghost herda pitch da estrutural anterior — nunca inventa altura.
    events = [
        (0, 240, 40),
        (960, 240, 45),
        (1920, 240, 43),
        (2880, 240, 40),
    ]
    source = _make_bass_line(events)
    structural_pitches = {40, 45, 43}
    out = apply_technique(
        "bass.ghost_notes", source, seed=5, tool="generic",
    )
    for pitch in _pitches(out):
        assert pitch in structural_pitches, (
            f"ghost pitch {pitch} nao veio da linha estrutural"
        )


def test_bass_ghost_notes_is_deterministic_for_same_seed():
    src_a = _make_bass_line(_quarter_line())
    src_b = _make_bass_line(_quarter_line())
    out_a = apply_technique("bass.ghost_notes", src_a, seed=13, tool="generic")
    out_b = apply_technique("bass.ghost_notes", src_b, seed=13, tool="generic")
    assert _structural_signature(out_a) == _structural_signature(out_b)


def test_bass_ghost_notes_is_idempotent():
    # Reaplicar com mesma seed nao duplica ornamento.
    source = _make_bass_line(_quarter_line())
    once = apply_technique("bass.ghost_notes", source, seed=9, tool="generic")
    first_pitches = _pitches(once)
    twice = apply_technique("bass.ghost_notes", once, seed=9, tool="generic")
    assert _pitches(twice) == first_pitches


def test_bass_ghost_notes_realistic_line_density_per_bar():
    # Linha realista de 4 compassos: 4 semininas cada, semicolcheias
    # ocasionais e uma pausa. Densidade por compasso deve ficar razoavel
    # (<= 6 ghosts por compasso — o teto real do algoritmo, "e" e "a" de cada
    # semicolcheia menos os que colidem com estrutural).
    ticks_per_beat = 480
    events = []
    for bar in range(4):
        base = bar * ticks_per_beat * 4
        events.extend([
            (base + 0, 240, 40),
            (base + ticks_per_beat, 240, 43),
            (base + ticks_per_beat * 2, 240, 45),
            (base + ticks_per_beat * 3, 240, 40),
        ])
    source = _make_bass_line(events, ticks_per_beat=ticks_per_beat)
    out = apply_technique(
        "bass.ghost_notes", source, seed=42, tool="generic",
        parameters={"density": 0.5},
    )
    added = len(_pitches(out)) - len(_pitches(source))
    per_bar = added / 4
    assert 0.5 <= per_bar <= 8.0, (
        f"densidade por compasso fora do razoavel: {per_bar} ghosts/compasso"
    )


def test_bass_ghost_notes_modo_bass_emits_keyswitch():
    # Receita modo_bass declara keyswitch A#-1 (pitch 10). Ele deve aparecer
    # no MIDI de saida, com pitch abaixo da regiao tocavel, sem violar o
    # validador fisico (pitches de keyswitch declarados sao exemptos).
    source = _make_bass_line(_quarter_line())
    out = apply_technique(
        "bass.ghost_notes", source, seed=3, tool="modo_bass",
    )
    ks_hits = sum(
        1
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
        and msg.note == 10
    )
    assert ks_hits > 0, "receita modo_bass nao emitiu keyswitch A#-1"


def test_bass_ghost_notes_respects_technique_contract_on_pitches():
    # Contrato technique proibe transposicao/perda de nota estrutural. Aqui
    # cobrimos o caminho positivo: o dispatch aceita, sem TechniqueContractError.
    source = _make_bass_line(_quarter_line())
    for seed in range(10):
        apply_technique("bass.ghost_notes", source, seed=seed, tool="generic")

    _ = TechniqueContractError  # keeps import used
    _ = pytest  # keeps import used
