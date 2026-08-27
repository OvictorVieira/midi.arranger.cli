"""Testes de `bass.hammer_pull` — ligado sem reataque."""

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


def test_bass_hammer_pull_is_registered_as_supported():
    assert "bass.hammer_pull" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.hammer_pull")
    assert entry.canonical == "bass.hammer_pull"
    assert entry.level == "technique"


def test_bass_hammer_pull_default_parameters_is_noop():
    # Sem `density`, a tecnica nao muda a linha.
    source = _make_bass_line([
        (0, 480, 40, 96),
        (480, 480, 42, 96),
    ])
    before = _collect_notes(source)
    out = apply_technique(
        "bass.hammer_pull", source, seed=1, tool="generic",
    )
    after = _collect_notes(out)
    assert [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"]) for n in before] == \
           [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"]) for n in after]


def test_bass_hammer_pull_applies_only_between_ligable_pairs():
    # Par 1 (0-480) e (480-720): 40 -> 42, intervalo 2 semitons, ligavel.
    # Par 2 (720-960) e (960-1200): 42 -> 60, salto de 18 semitons, NAO ligavel.
    events = [
        (0, 480, 40, 100),
        (480, 240, 42, 100),
        (720, 240, 42, 100),
        (960, 240, 60, 100),
    ]
    source = _make_bass_line(events)
    out = apply_technique(
        "bass.hammer_pull", source, seed=3, tool="generic",
        parameters={"density": 1.0},
    )
    notes = _collect_notes(out)
    by_start = {(n["channel"], n["start"], n["pitch"]): n for n in notes}

    # Segunda nota do par ligavel (start=480, pitch=42) recebeu velocity_relativa
    # (delta negativo entre -30 e -15 do manual): saiu < 100.
    ligated = by_start[(1, 480, 42)]
    assert ligated["velocity"] < 100, (
        f"nota ligada nao caiu abaixo do ataque (100): {ligated['velocity']}"
    )
    assert ligated["velocity"] >= 100 - 30

    # Segunda nota do par NAO ligavel (start=960, pitch=60): velocity preservada.
    unligated = by_start[(1, 960, 60)]
    assert unligated["velocity"] == 100, (
        "nota nao ligavel recebeu velocity_relativa; o gate por intervalo falhou"
    )


def test_bass_hammer_pull_extends_first_note_to_overlap_second():
    events = [
        (0, 480, 40, 100),
        (480, 240, 42, 100),
    ]
    source = _make_bass_line(events)
    out = apply_technique(
        "bass.hammer_pull", source, seed=5, tool="generic",
        parameters={"density": 1.0},
    )
    notes = _collect_notes(out)
    first = next(n for n in notes if n["start"] == 0 and n["pitch"] == 40)
    second = next(n for n in notes if n["start"] == 480 and n["pitch"] == 42)
    # Sobreposicao real — first.end > second.start.
    assert first["end"] > second["start"], (
        "hammer_pull nao produziu sobreposicao entre as notas"
    )


def test_bass_hammer_pull_does_not_alter_structural_pitch_or_position():
    events = [
        (0, 480, 40, 100),
        (480, 240, 42, 100),
        (960, 240, 60, 100),
    ]
    source = _make_bass_line(events)
    before = _collect_notes(source)
    out = apply_technique(
        "bass.hammer_pull", source, seed=7, tool="generic",
        parameters={"density": 1.0},
    )
    after = _collect_notes(out)
    before_positions = sorted(
        (n["channel"], n["pitch"], n["start"]) for n in before
    )
    after_positions = sorted(
        (n["channel"], n["pitch"], n["start"])
        for n in after
        if n["pitch"] >= 20
    )
    assert before_positions == after_positions


def test_bass_hammer_pull_modo_bass_emits_keyswitch_c0():
    events = [
        (0, 480, 40, 100),
        (480, 240, 42, 100),
    ]
    source = _make_bass_line(events)
    out = apply_technique(
        "bass.hammer_pull", source, seed=2, tool="modo_bass",
        parameters={"density": 1.0},
    )
    ks_hits = sum(
        1
        for track in out.tracks
        for msg in track
        if not msg.is_meta
        and msg.type == "note_on"
        and msg.velocity > 0
        and msg.note == 12
    )
    assert ks_hits > 0, "receita modo_bass nao emitiu keyswitch C0 (12)"


def test_bass_hammer_pull_is_deterministic_for_same_seed():
    events = [
        (0, 480, 40, 100),
        (480, 240, 42, 100),
        (720, 240, 43, 100),
    ]
    a = _make_bass_line(events)
    b = _make_bass_line(events)
    out_a = apply_technique(
        "bass.hammer_pull", a, seed=11, tool="generic",
        parameters={"density": 1.0},
    )
    out_b = apply_technique(
        "bass.hammer_pull", b, seed=11, tool="generic",
        parameters={"density": 1.0},
    )
    assert _collect_notes(out_a) == _collect_notes(out_b)


def test_bass_hammer_pull_is_idempotent_with_modo_bass_keyswitch():
    events = [
        (0, 480, 40, 100),
        (480, 240, 42, 100),
    ]
    source = _make_bass_line(events)
    once = apply_technique(
        "bass.hammer_pull", source, seed=13, tool="modo_bass",
        parameters={"density": 1.0},
    )
    once_notes = _collect_notes(once)
    twice = apply_technique(
        "bass.hammer_pull", once, seed=13, tool="modo_bass",
        parameters={"density": 1.0},
    )
    twice_notes = _collect_notes(twice)
    assert once_notes == twice_notes
