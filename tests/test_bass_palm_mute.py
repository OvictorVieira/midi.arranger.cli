"""Testes de `bass.palm_mute` — palma sem virar volume geral."""

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
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    beat = ticks_per_beat
    dur = beat if duration is None else duration
    for i, vel in enumerate(velocities):
        track.append(mido.Message(
            "note_on", channel=1, note=40, velocity=vel,
            time=beat if i > 0 else 0,
        ))
        track.append(mido.Message(
            "note_off", channel=1, note=40, velocity=0,
            time=dur,
        ))
    mid.tracks.append(track)
    return mid


def _note_pairs(mid: mido.MidiFile) -> list[tuple[int, int, int]]:
    """Returns (velocity, duration_ticks, start_tick) per note in order."""

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


def test_bass_palm_mute_is_registered_as_supported():
    assert "bass.palm_mute" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.palm_mute")
    assert entry.canonical == "bass.palm_mute"
    assert entry.level == "humanize"


def test_bass_palm_mute_without_density_is_no_op():
    # Aplica so onde o plano pedir por parametro. Sem `density`, nao abafa.
    source = _make_midi([120, 118, 115, 110, 112, 116])
    before = _note_pairs(source)

    out = apply_technique(
        "bass.palm_mute", source, seed=1, tool="generic",
    )

    assert _note_pairs(out) == before


def test_bass_palm_mute_density_zero_is_no_op():
    # `density=0.0` DESLIGA a tecnica. Parametro mentiroso ja foi rejeitado
    # nesta base.
    source = _make_midi([120, 118, 115, 110, 112, 116])
    before = _note_pairs(source)

    out = apply_technique(
        "bass.palm_mute", source, seed=1, tool="generic",
        parameters={"density": 0.0},
    )

    assert _note_pairs(out) == before


def test_bass_palm_mute_shortens_duration_by_gate_pct():
    # Encurta pelo `gate_pct` do manual — nunca mais longo que o original.
    source = _make_midi([90, 90, 90, 90], duration=480)
    out = apply_technique(
        "bass.palm_mute", source, seed=2, tool="generic",
        parameters={"density": 1.0},
    )
    after = _note_pairs(out)

    assert all(dur < 480 for _, dur, _ in after), (
        f"palm mute com density=1 devia encurtar todas as notas: {after}"
    )
    # gate_pct do manual e [25, 50], entao duracao <= 50% + margem de arredondamento.
    assert all(dur <= 480 * 0.5 + 1 for _, dur, _ in after)


def test_bass_palm_mute_preserves_top_note_pressure():
    # INVARIANTE DE PRESSAO: nota do topo da origem (>= P75) nao pode cair na
    # faixa mais baixa (< P25) da origem, mesmo depois de abafada.
    velocities = [60, 65, 70, 75, 80, 100, 115, 120, 125, 127]
    source = _make_midi(velocities)
    out = apply_technique(
        "bass.palm_mute", source, seed=3, tool="generic",
        parameters={"density": 1.0},
    )
    after = [v for v, _, _ in _note_pairs(out)]

    sorted_orig = sorted(velocities)
    p25 = sorted_orig[len(sorted_orig) // 4]
    top_indices = [i for i, v in enumerate(velocities) if v >= 115]
    assert top_indices
    for idx in top_indices:
        assert after[idx] >= p25, (
            f"nota do topo da origem (idx={idx}, v={velocities[idx]}) "
            f"caiu na faixa mais baixa: saiu com {after[idx]}, P25={p25}"
        )


def test_bass_palm_mute_preserves_note_content():
    # Contrato `humanize`: mesma contagem, mesmos pitches, mesma ordem.
    source = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    original_notes = [
        (msg.channel, msg.note)
        for track in source.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    out = apply_technique(
        "bass.palm_mute", source, seed=5, tool="generic",
        parameters={"density": 0.5},
    )
    out_notes = [
        (msg.channel, msg.note)
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]

    assert out_notes == original_notes


def test_bass_palm_mute_is_deterministic_for_same_seed():
    src_a = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    src_b = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    out_a = apply_technique(
        "bass.palm_mute", src_a, seed=7, tool="generic",
        parameters={"density": 0.75},
    )
    out_b = apply_technique(
        "bass.palm_mute", src_b, seed=7, tool="generic",
        parameters={"density": 0.75},
    )

    assert _note_pairs(out_a) == _note_pairs(out_b)


def test_bass_palm_mute_reads_gate_range_from_context_parameters():
    # `context.parameters` COMANDA a receita. `gate_pct` fixo em 25 deve dar
    # duracao em torno de 25% da original.
    source = _make_midi([90, 90, 90, 90], duration=480)
    out = apply_technique(
        "bass.palm_mute", source, seed=9, tool="generic",
        parameters={"density": 1.0, "gate_pct": [25, 25]},
    )
    after = _note_pairs(out)

    assert all(115 <= dur <= 125 for _, dur, _ in after), (
        f"gate_pct=25 devia dar duracao ~120 ticks: {after}"
    )
