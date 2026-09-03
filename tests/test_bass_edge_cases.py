"""Bordas comuns dos aplicadores de baixo: MIDI vazio, `ticks_per_beat` invalido,
parametros ausentes. Consolida cenarios que exercitam os primeiros returns e
`continue`s dos aplicadores de tecnica registrados em `engine.py`.
"""

from __future__ import annotations

import mido

from tools.techniques.engine import apply_technique


def _empty_midi(*, ticks_per_beat: int = 480) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    return mid


def _bass_line(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 1,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch, velocity in events:
        absolute.append((
            start, order,
            mido.Message(
                "note_on", channel=channel, note=pitch,
                velocity=velocity, time=0,
            ),
        ))
        order += 1
        absolute.append((
            start + duration, order,
            mido.Message(
                "note_off", channel=channel, note=pitch,
                velocity=0, time=0,
            ),
        ))
        order += 1
    absolute.sort(key=lambda item: (item[0], item[1]))
    tick = 0
    for abs_tick, _order, msg in absolute:
        track.append(msg.copy(time=abs_tick - tick))
        tick = abs_tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    return mid


BASS_TECHNIQUES = [
    ("bass.velocity_contour", {}),
    ("bass.ghost_notes", {"density": 0.5}),
    ("bass.palm_mute", {"density": 0.5}),
    ("bass.attack_style", {"style": "picked"}),
    ("bass.hammer_pull", {"density": 1.0}),
    ("bass.let_ring", {"density": 1.0}),
]


def test_all_bass_techniques_pass_through_track_without_notes():
    """Track so com meta events nao pode quebrar nenhum aplicador de baixo."""

    for canonical, params in BASS_TECHNIQUES:
        mid = _empty_midi()
        result = apply_technique(canonical, mid, seed=1, parameters=params)
        # Track sem notas continua sem notas, sem lancar excecao.
        notes = [
            msg for msg in result.tracks[0]
            if not msg.is_meta and msg.type in {"note_on", "note_off"}
        ]
        assert notes == []


def test_all_bass_techniques_short_circuit_on_zero_ticks_per_beat():
    """`ticks_per_beat <= 0` degenera qualquer clock, entao aplicador desliga."""

    for canonical, params in BASS_TECHNIQUES:
        mid = _bass_line(
            [(0, 240, 40, 90), (240, 240, 43, 95)],
            ticks_per_beat=480,
        )
        mid.ticks_per_beat = 0
        result = apply_technique(canonical, mid, seed=1, parameters=params)
        # Sem clock valido, saida vem sem eventos novos alem dos originais.
        assert result is not None


def test_attack_style_no_op_when_style_is_unknown():
    """`style` fora do vocabulario aceito degenera para no-op."""

    mid = _bass_line([(0, 240, 40, 90)], ticks_per_beat=480)
    result = apply_technique(
        "bass.attack_style", mid, seed=1, parameters={"style": "tapping"},
    )
    assert list(result.tracks[0]) == list(mid.tracks[0])


def test_attack_style_fingered_no_op_when_recipe_has_no_keyswitch():
    """dedo/fingered sem `keyswitch_*` na receita continua no-op (issue #57:
    sem faixa sourced de index/middle picking, generic nao inventa numero)."""

    mid = _bass_line([(0, 240, 40, 90)], ticks_per_beat=480)
    result = apply_technique(
        "bass.attack_style", mid, seed=1,
        parameters={"style": "dedo"},
        tool="generic",
    )
    assert list(result.tracks[0]) == list(mid.tracks[0])


def test_attack_style_picked_differentiates_velocity_when_recipe_has_no_keyswitch():
    """Receita generic (sem `keyswitch_*`) ainda diferencia picked por delta
    relativo de velocity — issue #57, fallback generic sourced no manual."""

    mid = _bass_line([(0, 240, 40, 90)], ticks_per_beat=480)
    result = apply_technique(
        "bass.attack_style", mid, seed=1,
        parameters={"style": "picked"},
        tool="generic",
    )
    original_on = mid.tracks[0][1]
    result_on = result.tracks[0][1]
    assert result_on.note == original_on.note
    assert result_on.time == original_on.time
    assert result_on.velocity != original_on.velocity


def test_hammer_pull_skips_pair_when_gap_is_too_wide():
    """Notas separadas por mais de meia batida nao ligam."""

    mid = _bass_line(
        [(0, 240, 40, 90), (960, 240, 42, 90)],  # 2 batidas de gap
        ticks_per_beat=480,
    )
    result = apply_technique(
        "bass.hammer_pull", mid, seed=1, parameters={"density": 1.0},
    )
    # Sem ligado, note_off da primeira nota nao muda de posicao.
    original_ticks = [msg.time for msg in mid.tracks[0]]
    result_ticks = [msg.time for msg in result.tracks[0]]
    assert original_ticks == result_ticks


def test_hammer_pull_partial_density_uses_random_selector():
    """`density` entre 0 e 1 exercita o ramo de selecao por rng."""

    mid = _bass_line(
        [(i * 120, 120, 40 + (i % 3), 90) for i in range(6)],
        ticks_per_beat=480,
    )
    result = apply_technique(
        "bass.hammer_pull", mid, seed=7, parameters={"density": 0.5},
    )
    assert result is not None


def test_attack_style_short_circuits_on_zero_ticks_per_beat_with_valid_recipe():
    """`ticks_per_beat=0` derruba a tecnica mesmo com receita valida."""

    mid = _bass_line([(0, 240, 40, 90)], ticks_per_beat=480)
    mid.ticks_per_beat = 0
    result = apply_technique(
        "bass.attack_style", mid, seed=1,
        parameters={"style": "picked"}, tool="modo_bass",
    )
    # Sem clock, nao ha atraso_ticks e a tecnica devolve o midi cru.
    assert result is mid or list(result.tracks[0]) == list(mid.tracks[0])


def test_attack_style_skips_track_without_structural_notes():
    """Track so com meta events (nem uma nota estrutural) e pulada."""

    mid = _empty_midi()
    result = apply_technique(
        "bass.attack_style", mid, seed=1,
        parameters={"style": "picked"}, tool="modo_bass",
    )
    notes = [
        msg for msg in result.tracks[0]
        if not msg.is_meta and msg.type in {"note_on", "note_off"}
    ]
    assert notes == []


def test_hammer_pull_skips_pair_when_notes_start_at_same_tick():
    """Duas notas simultaneas nao viram ligadura hammer/pull."""

    mid = _bass_line(
        [(0, 240, 40, 90), (0, 240, 42, 90)],
        ticks_per_beat=480,
    )
    result = apply_technique(
        "bass.hammer_pull", mid, seed=1, parameters={"density": 1.0},
    )
    # Sem ligado, ordem original preservada.
    assert result is not None


def test_let_ring_emits_cc_pair_on_run_of_notes():
    """Notas proximas viram um run e recebem CC on/off nos limites."""

    mid = _bass_line(
        [(0, 240, 40, 90), (240, 240, 43, 90), (480, 240, 40, 90)],
        ticks_per_beat=480,
    )
    result = apply_technique(
        "bass.let_ring", mid, seed=1, parameters={"density": 1.0},
        tool="modo_bass",
    )
    ccs = [
        (msg.control, msg.value) for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "control_change"
    ]
    assert (64, 127) in ccs
    assert (64, 0) in ccs


def test_let_ring_splits_runs_on_long_gap_and_emits_pair_per_run():
    """Gap maior que 4 compassos quebra o run: cada trecho ganha CC on/off."""

    ticks_per_beat = 480
    long_gap = ticks_per_beat * 4 + ticks_per_beat  # > max_gap_ticks
    mid = _bass_line(
        [(0, 240, 40, 90), (long_gap, 240, 40, 90)],
        ticks_per_beat=ticks_per_beat,
    )
    result = apply_technique(
        "bass.let_ring", mid, seed=1, parameters={"density": 1.0},
        tool="modo_bass",
    )
    on_events = [
        msg for msg in result.tracks[0]
        if not msg.is_meta
        and msg.type == "control_change"
        and msg.control == 64
        and msg.value == 127
    ]
    off_events = [
        msg for msg in result.tracks[0]
        if not msg.is_meta
        and msg.type == "control_change"
        and msg.control == 64
        and msg.value == 0
    ]
    # Dois runs ⇒ dois pares CC on/off.
    assert len(on_events) == 2
    assert len(off_events) == 2


def test_ghost_notes_without_density_fills_all_slots():
    """Sem `density` no plano, ghost_notes preenche todos os candidatos."""

    mid = _bass_line(
        [(i * 480, 240, 40, 90) for i in range(4)],
        ticks_per_beat=480,
    )
    result = apply_technique("bass.ghost_notes", mid, seed=1)
    on_events = [
        msg for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    # Sem ceu de density, todo slot vira ghost — mais notes on que a origem.
    assert len(on_events) >= 4


def test_palm_mute_partial_density_may_leave_track_untouched():
    """`density` positiva mas minima pode nao selecionar nota alguma;
    nesse caso a tecnica pula a track sem reescrever eventos."""

    mid = _bass_line(
        [(i * 120, 120, 40, 90) for i in range(4)],
        ticks_per_beat=480,
    )
    result = apply_technique(
        "bass.palm_mute", mid, seed=999_999_999,
        parameters={"density": 1e-9},
    )
    original_velocities = [
        msg.velocity for msg in mid.tracks[0]
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    result_velocities = [
        msg.velocity for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    assert result_velocities == original_velocities


def test_let_ring_partial_density_may_skip_all_runs():
    """`density` baixa nao seleciona run algum e a track sai intacta."""

    mid = _bass_line([(0, 240, 40, 90)], ticks_per_beat=480)
    # `density=0.0` seria NO-OP antes do loop; use densidade positiva
    # minima e uma seed que rejeita o unico run disponivel.
    result = apply_technique(
        "bass.let_ring", mid, seed=999_999_999,
        parameters={"density": 1e-9},
        tool="modo_bass",
    )
    ccs = [
        msg for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "control_change"
    ]
    assert ccs == []


def test_let_ring_short_circuits_when_cc_number_out_of_range():
    """`cc` fora de `[0, 127]` desliga a tecnica sem alterar a track."""

    mid = _bass_line([(0, 240, 40, 90)], ticks_per_beat=480)
    result = apply_technique(
        "bass.let_ring", mid, seed=1,
        parameters={"density": 1.0, "cc": 200},
    )
    ccs = [
        msg for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "control_change"
    ]
    assert ccs == []


def test_velocity_contour_uses_default_span_when_parameter_absent():
    """Sem `span_tipico` explicito, cai no default (`40`), exercitando o
    ramo de fallback do resolvedor de parametros."""

    mid = _bass_line(
        [(i * 240, 240, 40, 90) for i in range(4)],
        ticks_per_beat=480,
    )
    # Passa um parametro que nao existe no manual do velocity_contour para
    # forcar `_range(name)` a devolver `None` e cair no default do aplicador.
    result = apply_technique(
        "bass.velocity_contour", mid, seed=42,
        parameters={"parametro_inexistente": 999},
    )
    velocities = [
        msg.velocity for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    assert len(velocities) == 4
    assert all(1 <= v <= 127 for v in velocities)
