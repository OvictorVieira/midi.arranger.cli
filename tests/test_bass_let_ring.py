"""Testes de `bass.let_ring` — sustentacao via CC 64."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)


def _make_bass_line(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 1,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch, velocity)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch, velocity in events:
        absolute.append((
            start,
            order,
            mido.Message(
                "note_on", channel=channel, note=pitch, velocity=velocity, time=0,
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
        absolute, key=lambda item: (item[0], item[1]),
    ):
        track.append(msg.copy(time=absolute_tick - prev))
        prev = absolute_tick
    mid.tracks.append(track)
    return mid


def _collect_notes(mid: mido.MidiFile) -> list[dict]:
    notes: list[dict] = []
    for track in mid.tracks:
        tick = 0
        pending: dict[tuple[int, int], list[dict]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                entry = {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start": tick,
                    "velocity": msg.velocity,
                    "end": None,
                }
                pending.setdefault((msg.channel, msg.note), []).append(entry)
                notes.append(entry)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if stack:
                    stack.pop(0)["end"] = tick
    return [n for n in notes if n["end"] is not None]


def _collect_cc(mid: mido.MidiFile, cc: int) -> list[tuple[int, int, int, int]]:
    """Devolve (track_index, tick, channel, value) para todos os CC de `cc`."""

    events: list[tuple[int, int, int, int]] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "control_change" and msg.control == cc:
                events.append((track_index, tick, msg.channel, msg.value))
    return events


def test_bass_let_ring_is_registered_as_supported():
    assert "bass.let_ring" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.let_ring")
    assert entry.canonical == "bass.let_ring"
    assert entry.level == "technique"
    assert entry.allow_structural_velocity_change is False
    assert entry.allow_structural_duration_change is False


def test_bass_let_ring_default_parameters_is_noop():
    source = _make_bass_line([
        (0, 240, 40, 96),
        (480, 240, 42, 96),
    ])
    before_notes = _collect_notes(source)
    out = apply_technique(
        "bass.let_ring", source, seed=1, tool="modo_bass",
    )
    assert _collect_notes(out) == before_notes
    assert _collect_cc(out, 64) == []


def test_bass_let_ring_zero_density_is_noop():
    source = _make_bass_line([
        (0, 240, 40, 96),
        (480, 240, 42, 96),
    ])
    out = apply_technique(
        "bass.let_ring", source, seed=1, tool="modo_bass",
        parameters={"density": 0.0},
    )
    assert _collect_cc(out, 64) == []


def test_bass_let_ring_emits_paired_on_off_around_run():
    source = _make_bass_line([
        (0, 240, 40, 96),
        (480, 240, 42, 96),
        (960, 240, 43, 96),
    ])
    out = apply_technique(
        "bass.let_ring", source, seed=1, tool="modo_bass",
        parameters={"density": 1.0},
    )
    events = _collect_cc(out, 64)
    # Deve ter pelo menos um par de eventos.
    assert len(events) >= 2
    values = [value for _t, _tick, _ch, value in events]
    assert 127 in values
    assert 0 in values
    # Numero de "on" (>= 64) igual ao numero de "off" (0): nunca pendurado.
    ons = sum(1 for v in values if v >= 64)
    offs = sum(1 for v in values if v == 0)
    assert ons == offs


def test_bass_let_ring_does_not_alter_structural_notes():
    events = [
        (0, 240, 40, 96),
        (480, 240, 42, 96),
        (960, 240, 43, 96),
    ]
    source = _make_bass_line(events)
    before = _collect_notes(source)
    out = apply_technique(
        "bass.let_ring", source, seed=3, tool="modo_bass",
        parameters={"density": 1.0},
    )
    after = _collect_notes(out)
    assert before == after


def test_bass_let_ring_off_falls_within_track_bounds():
    events = [
        (0, 240, 40, 96),
        (480, 240, 42, 96),
    ]
    source = _make_bass_line(events)
    out = apply_technique(
        "bass.let_ring", source, seed=5, tool="modo_bass",
        parameters={"density": 1.0},
    )
    cc = _collect_cc(out, 64)
    # Ultimo evento CC64 nao passa do fim da ultima nota estrutural (720).
    assert cc[-1][1] <= 720


def test_bass_let_ring_is_deterministic_for_same_seed():
    events = [
        (0, 240, 40, 96),
        (480, 240, 42, 96),
        (960, 240, 43, 96),
    ]
    a = _make_bass_line(events)
    b = _make_bass_line(events)
    out_a = apply_technique(
        "bass.let_ring", a, seed=7, tool="modo_bass",
        parameters={"density": 0.5},
    )
    out_b = apply_technique(
        "bass.let_ring", b, seed=7, tool="modo_bass",
        parameters={"density": 0.5},
    )
    assert _collect_cc(out_a, 64) == _collect_cc(out_b, 64)


def test_bass_let_ring_is_idempotent_on_reapply_same_seed():
    events = [
        (0, 240, 40, 96),
        (480, 240, 42, 96),
        (960, 240, 43, 96),
    ]
    source = _make_bass_line(events)
    once = apply_technique(
        "bass.let_ring", source, seed=11, tool="modo_bass",
        parameters={"density": 1.0},
    )
    once_cc = _collect_cc(once, 64)
    twice = apply_technique(
        "bass.let_ring", once, seed=11, tool="modo_bass",
        parameters={"density": 1.0},
    )
    twice_cc = _collect_cc(twice, 64)
    assert once_cc == twice_cc
