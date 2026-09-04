"""Testes de `guitar.double_tracking` — segunda track real, nunca copia."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

MARKER_PREFIX = "guitar_double_tracking_of="


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
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
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


def _collect_notes(track: mido.MidiTrack) -> list[dict]:
    notes: list[dict] = []
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


_EVENTS = [
    (0, 200, 40, 90), (240, 200, 40, 92),
    (480, 200, 45, 95), (720, 200, 47, 88),
]


def test_guitar_double_tracking_is_registered_as_supported():
    assert "guitar.double_tracking" in SUPPORTED_TECHNIQUES
    entry = get_technique("guitar.double_tracking")
    assert entry.canonical == "guitar.double_tracking"
    assert entry.level == "technique"


def test_guitar_double_tracking_without_density_is_no_op():
    source = _make_guitar_line(_EVENTS)
    out = apply_technique("guitar.double_tracking", source, seed=1, tool="generic")

    assert len(out.tracks) == 1


def test_guitar_double_tracking_adds_a_second_track():
    source = _make_guitar_line(_EVENTS)
    out = apply_technique(
        "guitar.double_tracking", source, seed=2, tool="generic",
        parameters={"density": 1.0},
    )

    assert len(out.tracks) == 2
    marker_found = any(
        msg.is_meta and msg.type == "text" and msg.text.startswith(MARKER_PREFIX)
        for msg in out.tracks[1]
    )
    assert marker_found


def test_guitar_double_tracking_preserves_original_track_untouched():
    source = _make_guitar_line(_EVENTS)
    before = _collect_notes(source.tracks[0])

    out = apply_technique(
        "guitar.double_tracking", source, seed=3, tool="generic",
        parameters={"density": 1.0},
    )

    after = _collect_notes(out.tracks[0])
    assert after == before


def test_guitar_double_tracking_second_track_is_not_an_identical_copy():
    source = _make_guitar_line(_EVENTS)
    out = apply_technique(
        "guitar.double_tracking", source, seed=4, tool="generic",
        parameters={"density": 1.0},
    )

    original = _collect_notes(out.tracks[0])
    doubled = _collect_notes(out.tracks[1])

    assert len(doubled) == len(original)
    # Nunca a mesma sequencia de start/velocity/canal — offsets reais, nao
    # uma copia coerente em fase (manual §13).
    same = all(
        o["start"] == d["start"]
        and o["velocity"] == d["velocity"]
        and o["channel"] == d["channel"]
        for o, d in zip(original, doubled, strict=True)
    )
    assert not same
    assert doubled[0]["channel"] != original[0]["channel"]


def test_guitar_double_tracking_pitch_is_unchanged():
    source = _make_guitar_line(_EVENTS)
    out = apply_technique(
        "guitar.double_tracking", source, seed=5, tool="generic",
        parameters={"density": 1.0},
    )
    original_pitches = [n["pitch"] for n in _collect_notes(out.tracks[0])]
    doubled_pitches = [n["pitch"] for n in _collect_notes(out.tracks[1])]

    assert doubled_pitches == original_pitches


def test_guitar_double_tracking_emits_a_constant_detune_pitch_bend():
    source = _make_guitar_line(_EVENTS)
    out = apply_technique(
        "guitar.double_tracking", source, seed=6, tool="generic",
        parameters={"density": 1.0},
    )
    bends = [msg.pitch for msg in out.tracks[1] if msg.type == "pitchwheel"]

    assert len(bends) == 1
    assert bends[0] != 0
    # Detune CONVENCAO e bem abaixo de +-2 semitons (range default).
    assert -2048 < bends[0] < 2048


def test_guitar_double_tracking_is_idempotent():
    source = _make_guitar_line(_EVENTS)
    once = apply_technique(
        "guitar.double_tracking", source, seed=7, tool="generic",
        parameters={"density": 1.0},
    )
    twice = apply_technique(
        "guitar.double_tracking", once, seed=7, tool="generic",
        parameters={"density": 1.0},
    )

    assert len(twice.tracks) == len(once.tracks) == 2


def test_guitar_double_tracking_is_deterministic_for_same_seed():
    out_a = apply_technique(
        "guitar.double_tracking", _make_guitar_line(_EVENTS), seed=9, tool="generic",
        parameters={"density": 1.0},
    )
    out_b = apply_technique(
        "guitar.double_tracking", _make_guitar_line(_EVENTS), seed=9, tool="generic",
        parameters={"density": 1.0},
    )

    assert _collect_notes(out_a.tracks[1]) == _collect_notes(out_b.tracks[1])
