"""Testes de `keys.pitch_bend` — RPN 0, LSB+MSB completos, curva monotonica."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)


def _make_line(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 0,
    tempo_us: int = 500_000,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch, velocity)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
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


def _collect_pitchwheel(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """Devolve (track_index, tick, channel, pitch) para todos os pitchwheel."""

    events: list[tuple[int, int, int, int]] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "pitchwheel":
                events.append((track_index, tick, msg.channel, msg.pitch))
    return events


def _collect_cc(
    mid: mido.MidiFile, cc: int
) -> list[tuple[int, int, int, int]]:
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


def test_keys_pitch_bend_is_registered_as_supported():
    assert "keys.pitch_bend" in SUPPORTED_TECHNIQUES
    entry = get_technique("keys.pitch_bend")
    assert entry.canonical == "keys.pitch_bend"
    assert entry.level == "technique"


def test_keys_pitch_bend_without_density_is_noop():
    line = _make_line([(0, 240, 60, 96), (240, 240, 62, 96)])
    out = apply_technique("keys.pitch_bend", line, seed=1)
    assert _collect_pitchwheel(out) == []
    assert _collect_cc(out, 101) == []
    assert _collect_cc(out, 100) == []
    assert _collect_cc(out, 6) == []


def test_keys_pitch_bend_zero_density_is_noop():
    line = _make_line([(0, 240, 60, 96), (240, 240, 62, 96)])
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 0.0},
    )
    assert _collect_pitchwheel(out) == []
    assert _collect_cc(out, 101) == []


def test_keys_pitch_bend_emits_rpn_0_and_monotonic_curve_up():
    line = _make_line([(0, 240, 60, 96), (240, 240, 62, 96)])
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 1.0},
    )

    rpn = _collect_cc(out, 101) + _collect_cc(out, 100) + _collect_cc(out, 6) + _collect_cc(out, 38)
    assert rpn, "RPN 0 e RPN Null tem que aparecer quando ha bend"
    # RPN 0 sequence: CC101=0, CC100=0, CC6=<semitons>, CC38=0.
    cc6 = _collect_cc(out, 6)
    assert cc6, "CC6 (data entry MSB) exigido pelo RPN 0"
    assert cc6[0][3] == 2  # range_default_gm

    # RPN Null (CC101=127, CC100=127) fecha o bloco.
    cc101 = _collect_cc(out, 101)
    cc100 = _collect_cc(out, 100)
    assert 127 in {value for _, _, _, value in cc101}
    assert 127 in {value for _, _, _, value in cc100}

    bends = _collect_pitchwheel(out)
    assert bends, "bend precisa sair no MIDI"
    # Curva subindo de +0 ate positivo, monotona, seguida de reset a 0.
    non_reset = [b for b in bends if b[1] < 240]
    reset = [b for b in bends if b[1] == 240]
    assert reset and reset[-1][3] == 0
    values = [b[3] for b in non_reset]
    assert all(v >= 0 for v in values), "bend para cima nao pode ter valor negativo"
    assert values == sorted(values), "curva subindo tem que ser monotonica"
    assert max(values) > 0


def test_keys_pitch_bend_monotonic_curve_down():
    line = _make_line([(0, 240, 64, 96), (240, 240, 62, 96)])
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 1.0},
    )
    bends = _collect_pitchwheel(out)
    non_reset = [b for b in bends if b[1] < 240]
    values = [b[3] for b in non_reset]
    assert all(v <= 0 for v in values), "bend para baixo nao pode ter valor positivo"
    assert values == sorted(values, reverse=True), "curva descendo tem que ser monotona"


def test_keys_pitch_bend_uses_14_bit_resolution():
    # range=2 semitons, interval=2 semitons → target maximo (passos_para_cima=8191).
    line = _make_line([(0, 240, 60, 96), (240, 240, 62, 96)])
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 1.0},
    )
    bends = _collect_pitchwheel(out)
    non_reset = [b for b in bends if b[1] < 240]
    assert max(b[3] for b in non_reset) == 8191, (
        "interval == range tem que atingir passos_para_cima (14 bits, nao 7)"
    )


def test_keys_pitch_bend_respects_range_from_plan():
    # range=12: bend semitons 5 → 5/12 * 8191 = 3413 (aproximadamente).
    line = _make_line([(0, 240, 60, 96), (240, 240, 65, 96)])
    out = apply_technique(
        "keys.pitch_bend",
        line,
        seed=1,
        parameters={"density": 1.0, "range": 12},
    )
    bends = _collect_pitchwheel(out)
    non_reset = [b for b in bends if b[1] < 240]
    # CC6 tem que refletir o range declarado.
    cc6 = _collect_cc(out, 6)
    assert cc6[0][3] == 12
    peak = max(b[3] for b in non_reset)
    expected = round(8191 * 5 / 12)
    assert abs(peak - expected) <= 1


def test_keys_pitch_bend_event_rate_below_manual_ceiling():
    line = _make_line(
        [(0, 240, 60, 96), (240, 240, 62, 96)],
        ticks_per_beat=480,
        tempo_us=500_000,
    )
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 1.0},
    )
    bends = _collect_pitchwheel(out)
    # Duracao total do bend em segundos: eventos entre inicio da cauda e reset.
    non_reset = [b for b in bends if b[1] < 240]
    if len(non_reset) >= 2:
        first_tick = non_reset[0][1]
        last_tick = non_reset[-1][1]
        seconds = (last_tick - first_tick) * 500_000 / (480 * 1_000_000)
        if seconds > 0:
            rate = len(non_reset) / seconds
            assert rate <= 1042, f"taxa {rate:.1f} eventos/s excede teto do manual"


def test_keys_pitch_bend_is_deterministic_for_same_seed():
    def render() -> list[tuple[int, int, int, int]]:
        line = _make_line(
            [(0, 240, 60, 96), (240, 240, 62, 96), (480, 240, 60, 96)],
        )
        out = apply_technique(
            "keys.pitch_bend",
            line,
            seed=7,
            parameters={"density": 0.5},
        )
        return _collect_pitchwheel(out)

    assert render() == render()


def test_keys_pitch_bend_is_idempotent_on_reapply_same_seed():
    line = _make_line([(0, 240, 60, 96), (240, 240, 62, 96)])
    first = apply_technique(
        "keys.pitch_bend", line, seed=3, parameters={"density": 1.0},
    )
    first_bends = _collect_pitchwheel(first)
    first_cc6 = _collect_cc(first, 6)

    second = apply_technique(
        "keys.pitch_bend", first, seed=3, parameters={"density": 1.0},
    )
    assert _collect_pitchwheel(second) == first_bends
    assert _collect_cc(second, 6) == first_cc6


def test_keys_pitch_bend_does_not_touch_structural_notes():
    events = [(0, 240, 60, 96), (240, 240, 62, 96)]
    line = _make_line(events)
    before = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
              for n in _collect_notes(line)]
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 1.0},
    )
    after = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
             for n in _collect_notes(out)]
    assert before == after


def test_keys_pitch_bend_skips_intervals_beyond_range():
    # Interval 5 > default range 2 → nenhum bend.
    line = _make_line([(0, 240, 60, 96), (240, 240, 65, 96)])
    out = apply_technique(
        "keys.pitch_bend", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_pitchwheel(out) == []
