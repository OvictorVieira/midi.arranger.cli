"""Testes de `guitar.hammer_pull` — ligado sem reataque, MESMA corda."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)


def _make_guitar_line(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 1,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch, velocity)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Guitar", time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch, velocity in events:
        absolute.append((
            start, order,
            mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=0),
        ))
        order += 1
        absolute.append((
            start + duration, order,
            mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0),
        ))
        order += 1
    prev = 0
    for absolute_tick, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
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
                    "channel": msg.channel, "pitch": msg.note,
                    "start": tick, "velocity": msg.velocity, "end": None,
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


def test_guitar_hammer_pull_is_registered_as_supported():
    assert "guitar.hammer_pull" in SUPPORTED_TECHNIQUES
    entry = get_technique("guitar.hammer_pull")
    assert entry.canonical == "guitar.hammer_pull"
    assert entry.level == "technique"


def test_guitar_hammer_pull_without_density_is_no_op():
    source = _make_guitar_line([
        (0, 200, 40, 100), (200, 200, 42, 100),
    ])
    before = _collect_notes(source)

    out = apply_technique("guitar.hammer_pull", source, seed=1, tool="generic")

    after = _collect_notes(out)
    assert [(n["pitch"], n["velocity"]) for n in after] == [
        (n["pitch"], n["velocity"]) for n in before
    ]


def test_guitar_hammer_pull_reduces_velocity_of_second_note():
    source = _make_guitar_line([
        (0, 200, 40, 100), (200, 200, 42, 100),
    ])
    out = apply_technique(
        "guitar.hammer_pull", source, seed=2, tool="generic",
        parameters={"density": 1.0},
    )
    after = _collect_notes(out)
    velocities = [n["velocity"] for n in after]

    assert velocities[0] == 100
    assert velocities[1] < 100


def test_guitar_hammer_pull_overlaps_first_note_over_the_second():
    source = _make_guitar_line([
        (0, 200, 40, 100), (200, 200, 42, 100),
    ])
    out = apply_technique(
        "guitar.hammer_pull", source, seed=3, tool="generic",
        parameters={"density": 1.0},
    )
    after = _collect_notes(out)

    assert after[0]["end"] > 200  # sobrepoe o inicio da segunda


def test_guitar_hammer_pull_rejects_pair_that_needs_different_strings():
    # Afinacao E padrao, max_fret=2: pitch 42 so alcanca a corda Mi grave
    # (40-42); pitch 45 so alcanca a corda La (45-47). Nenhuma corda em
    # comum -> restricao fisica do manual bloqueia a ligadura mesmo com
    # intervalo (3 semitons) e gap dentro dos limites normais.
    source = _make_guitar_line([
        (0, 200, 42, 100), (200, 200, 45, 100),
    ])
    out = apply_technique(
        "guitar.hammer_pull", source, seed=4, tool="generic",
        parameters={"density": 1.0, "max_fret": 2},
    )
    after = _collect_notes(out)

    assert [n["velocity"] for n in after] == [100, 100]
    assert after[0]["end"] == 200


def test_guitar_hammer_pull_allows_pair_reachable_on_the_same_string():
    # 40 (Mi solta) -> 41 (casa 1): interseccao de corda existe (Mi grave).
    source = _make_guitar_line([
        (0, 200, 40, 100), (200, 200, 41, 100),
    ])
    out = apply_technique(
        "guitar.hammer_pull", source, seed=5, tool="generic",
        parameters={"density": 1.0, "max_fret": 2},
    )
    after = _collect_notes(out)

    assert after[1]["velocity"] < 100


def test_guitar_hammer_pull_preserves_pitch_and_position():
    source = _make_guitar_line([
        (0, 200, 40, 100), (200, 200, 42, 100), (400, 200, 43, 100),
    ])
    original = [(n["pitch"], n["start"]) for n in _collect_notes(source)]
    out = apply_technique(
        "guitar.hammer_pull", source, seed=6, tool="generic",
        parameters={"density": 1.0},
    )
    after = [(n["pitch"], n["start"]) for n in _collect_notes(out)]

    assert after == original


def test_guitar_hammer_pull_is_deterministic_for_same_seed():
    events = [(0, 200, 40, 100), (200, 200, 42, 100)]
    out_a = apply_technique(
        "guitar.hammer_pull", _make_guitar_line(events), seed=7, tool="generic",
        parameters={"density": 1.0},
    )
    out_b = apply_technique(
        "guitar.hammer_pull", _make_guitar_line(events), seed=7, tool="generic",
        parameters={"density": 1.0},
    )

    assert [n["velocity"] for n in _collect_notes(out_a)] == [
        n["velocity"] for n in _collect_notes(out_b)
    ]
