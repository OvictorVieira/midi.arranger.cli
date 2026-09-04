"""Testes de `guitar.palm_mute` — chug com profundidade por velocity."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)


def _make_midi(
    velocities: list[int],
    *,
    ticks_per_beat: int = 480,
    duration: int | None = None,
    pitch: int = 40,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Guitar", time=0))
    beat = ticks_per_beat
    dur = beat if duration is None else duration
    for i, vel in enumerate(velocities):
        track.append(mido.Message(
            "note_on", channel=1, note=pitch, velocity=vel,
            time=beat if i > 0 else 0,
        ))
        track.append(mido.Message(
            "note_off", channel=1, note=pitch, velocity=0,
            time=dur,
        ))
    mid.tracks.append(track)
    return mid


def _note_pairs(mid: mido.MidiFile) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault((msg.channel, msg.note), []).append((msg.velocity, tick))
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if not stack:
                    continue
                vel, start = stack.pop(0)
                out.append((vel, tick - start, start))
    return out


def test_guitar_palm_mute_is_registered_as_supported():
    assert "guitar.palm_mute" in SUPPORTED_TECHNIQUES
    entry = get_technique("guitar.palm_mute")
    assert entry.canonical == "guitar.palm_mute"
    assert entry.level == "technique"


def test_guitar_palm_mute_without_density_is_no_op():
    source = _make_midi([120, 118, 115, 110, 112, 116])
    before = _note_pairs(source)

    out = apply_technique("guitar.palm_mute", source, seed=1, tool="generic")

    assert _note_pairs(out) == before


def test_guitar_palm_mute_density_zero_is_no_op():
    source = _make_midi([120, 118, 115, 110, 112, 116])
    before = _note_pairs(source)

    out = apply_technique(
        "guitar.palm_mute", source, seed=1, tool="generic",
        parameters={"density": 0.0},
    )

    assert _note_pairs(out) == before


def test_guitar_palm_mute_shortens_duration_by_gate_pct():
    source = _make_midi([90, 90, 90, 90], duration=480)
    out = apply_technique(
        "guitar.palm_mute", source, seed=2, tool="generic",
        parameters={"density": 1.0},
    )
    after = _note_pairs(out)

    assert all(dur < 480 for _, dur, _ in after)
    # gate_pct do manual e [25, 50] -> duracao <= 50% + margem.
    assert all(dur <= 480 * 0.5 + 1 for _, dur, _ in after)


def test_guitar_palm_mute_lowers_velocity_into_manual_range():
    source = _make_midi([120, 118, 115, 110])
    out = apply_technique(
        "guitar.palm_mute", source, seed=3, tool="generic",
        parameters={"density": 1.0},
    )
    after = [v for v, _, _ in _note_pairs(out)]

    # velocity CONVENCAO do manual e [30, 70]: mute deveria sair mais fraco.
    assert all(30 <= v <= 70 for v in after)
    assert all(v < orig for v, orig in zip(after, [120, 118, 115, 110], strict=True))


def test_guitar_palm_mute_preserves_note_content():
    source = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    original_notes = [
        (msg.channel, msg.note)
        for track in source.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    out = apply_technique(
        "guitar.palm_mute", source, seed=5, tool="generic",
        parameters={"density": 0.5},
    )
    out_notes = [
        (msg.channel, msg.note)
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]

    assert out_notes == original_notes


def test_guitar_palm_mute_is_deterministic_for_same_seed():
    src_a = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    src_b = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    out_a = apply_technique(
        "guitar.palm_mute", src_a, seed=7, tool="generic",
        parameters={"density": 0.75},
    )
    out_b = apply_technique(
        "guitar.palm_mute", src_b, seed=7, tool="generic",
        parameters={"density": 0.75},
    )

    assert _note_pairs(out_a) == _note_pairs(out_b)


def test_guitar_palm_mute_ample_holds_keyswitch_per_note():
    # Ample declara keyswitch=26 (D0) para a articulacao Mute (manual §1).
    source = _make_midi([100, 100], duration=480, pitch=52)
    out = apply_technique(
        "guitar.palm_mute", source, seed=11, tool="ample",
        parameters={"density": 1.0},
    )
    ks_notes = [
        msg.note
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
        and msg.note == 26
    ]
    assert len(ks_notes) == 2
