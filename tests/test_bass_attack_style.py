"""Testes de `bass.attack_style` — keyswitches do MODO BASS."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

STYLE_KS = {"dedo": 13, "palheta": 15, "slap": 18}
FORCAR_DOWN = 1
FORCAR_UP = 3


def _make_midi(
    velocities: list[int],
    *,
    ticks_per_beat: int = 480,
    duration: int | None = None,
    pitch: int = 40,
    channel: int = 1,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    beat = ticks_per_beat
    dur = beat if duration is None else duration
    for i, vel in enumerate(velocities):
        track.append(mido.Message(
            "note_on", channel=channel, note=pitch, velocity=vel,
            time=beat if i > 0 else beat,
        ))
        track.append(mido.Message(
            "note_off", channel=channel, note=pitch, velocity=0, time=dur,
        ))
    mid.tracks.append(track)
    return mid


def _iter_notes(mid: mido.MidiFile):
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            yield tick, msg


def _structural_note_ons(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """(tick, channel, pitch, velocity) para note_on estruturais."""

    out: list[tuple[int, int, int, int]] = []
    for tick, msg in _iter_notes(mid):
        if msg.type == "note_on" and msg.velocity > 0 and msg.note >= 20:
            out.append((tick, msg.channel, msg.note, msg.velocity))
    return out


def _keyswitch_events(mid: mido.MidiFile, pitch: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for tick, msg in _iter_notes(mid):
        if msg.type == "note_on" and msg.velocity > 0 and msg.note == pitch:
            out.append((tick, msg.channel))
    return out


def test_bass_attack_style_is_registered_as_supported():
    assert "bass.attack_style" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.attack_style")
    assert entry.canonical == "bass.attack_style"
    assert entry.level == "technique"


def test_no_style_declared_is_no_op():
    source = _make_midi([80, 90, 100, 110])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
    )

    assert _serialize(out) == original_bytes


def test_generic_tool_without_keyswitch_is_no_op():
    # Receita generic nao tem keyswitch — nao ha o que inserir.
    source = _make_midi([80, 90, 100, 110])
    original = [
        (t, m.channel, m.note, m.velocity)
        for t, m in _iter_notes(source)
        if m.type == "note_on" and m.velocity > 0
    ]

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "dedo"},
    )

    after = [
        (t, m.channel, m.note, m.velocity)
        for t, m in _iter_notes(out)
        if m.type == "note_on" and m.velocity > 0
    ]
    assert after == original


def test_fingered_style_inserts_ks13_and_does_not_change_velocity():
    source = _make_midi([80, 90, 100, 110])
    before = _structural_note_ons(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "dedo"},
    )

    ks = _keyswitch_events(out, STYLE_KS["dedo"])
    assert len(ks) == 1, f"esperava 1 keyswitch de estilo, veio {ks}"
    assert _structural_note_ons(out) == before, (
        "dedo nao deve alterar velocity das notas estruturais"
    )


def test_slap_style_inserts_ks18_and_does_not_change_velocity():
    source = _make_midi([80, 90, 100, 110])
    before = _structural_note_ons(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "slap"},
    )

    ks = _keyswitch_events(out, STYLE_KS["slap"])
    assert len(ks) == 1
    assert _structural_note_ons(out) == before


def test_picked_style_inserts_ks15_and_alternates_downstroke_upstroke():
    # Alternancia deterministica por posicao: par=down, impar=up.
    source = _make_midi([80, 80, 80, 80, 80, 80])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    style_ks = _keyswitch_events(out, STYLE_KS["palheta"])
    assert len(style_ks) == 1

    down_ks = _keyswitch_events(out, FORCAR_DOWN)
    up_ks = _keyswitch_events(out, FORCAR_UP)
    # Seis notas → 3 downstrokes (0, 2, 4) e 3 upstrokes (1, 3, 5).
    assert len(down_ks) == 3
    assert len(up_ks) == 3

    # Velocities alternam picked_downstroke_velocity vs picked_upstroke_velocity.
    structural = _structural_note_ons(out)
    downs = [v for i, (_t, _c, _p, v) in enumerate(structural) if i % 2 == 0]
    ups = [v for i, (_t, _c, _p, v) in enumerate(structural) if i % 2 == 1]
    assert len(set(downs)) == 1
    assert len(set(ups)) == 1
    # Manual: picked_downstroke [85,120] mid=102; picked_upstroke [70,100] mid=85.
    assert downs[0] > ups[0], "downstroke deveria bater mais forte que upstroke"


def test_picked_style_does_not_alter_note_position_or_pitch():
    source = _make_midi([70, 75, 80, 85, 90])
    before = [(t, m.channel, m.note) for t, m in _iter_notes(source)
              if m.type == "note_on" and m.velocity > 0]

    out = apply_technique(
        "bass.attack_style", source, seed=3, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    after = [(t, m.channel, m.note) for t, m in _iter_notes(out)
             if m.type == "note_on" and m.velocity > 0 and m.note >= 20]
    assert after == before


def test_keyswitches_do_not_collide_with_structural_notes():
    # Keyswitch fica em pitches 1, 3, 13, 15, 18 — bem abaixo do baixo (>= 28).
    source = _make_midi([80, 90, 100, 110])
    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    for _t, msg in _iter_notes(out):
        if msg.type == "note_on" and msg.velocity > 0 and msg.note >= 20:
            # nenhuma estrutural mexeu de altura
            assert msg.note == 40


def test_is_deterministic_for_same_seed():
    src_a = _make_midi([80, 90, 100, 110])
    src_b = _make_midi([80, 90, 100, 110])
    out_a = apply_technique(
        "bass.attack_style", src_a, seed=42, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    out_b = apply_technique(
        "bass.attack_style", src_b, seed=42, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    assert _serialize(out_a) == _serialize(out_b)


def test_reapplying_is_idempotent():
    source = _make_midi([80, 90, 100, 110])
    once = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    once_bytes = _serialize(once)
    twice = apply_technique(
        "bass.attack_style", once, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    assert _serialize(twice) == once_bytes


def test_generic_picked_alternates_downstroke_upstroke_by_relative_delta():
    # Sem keyswitch (tool=generic): picked ainda diferencia downstroke/upstroke,
    # mas so por delta relativo direto na velocity — sem nota de keyswitch.
    source = _make_midi([80, 80, 80, 80, 80, 80])
    before = _structural_note_ons(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    # Nenhum keyswitch inserido: sem receita para eles no generic.
    for _t, msg in _iter_notes(out):
        assert msg.note not in {1, 3, 13, 15, 18}

    after = _structural_note_ons(out)
    assert len(after) == len(before)
    downs = [v for i, (_t, _c, _p, v) in enumerate(after) if i % 2 == 0]
    ups = [v for i, (_t, _c, _p, v) in enumerate(after) if i % 2 == 1]
    assert len(set(downs)) == 1
    assert len(set(ups)) == 1
    assert downs[0] > ups[0], "downstroke deveria bater mais forte que upstroke"
    # Delta e relativo: origem em 80 vira 80+half_delta / 80-half_delta.
    assert downs[0] > 80 > ups[0]


def test_generic_picked_does_not_alter_note_count_pitch_or_position():
    source = _make_midi([70, 75, 80, 85, 90])
    before = [(t, m.channel, m.note) for t, m in _iter_notes(source)
              if m.type == "note_on" and m.velocity > 0]

    out = apply_technique(
        "bass.attack_style", source, seed=3, tool="generic",
        parameters={"style": "picked"},
    )

    after = [(t, m.channel, m.note) for t, m in _iter_notes(out)
             if m.type == "note_on" and m.velocity > 0]
    assert after == before


def test_generic_picked_preserves_pressure_invariant_never_inverts_origin():
    # Nota que a origem escreveu no topo da faixa nao pode virar a mais fraca
    # da linha so por cair numa posicao de upstroke.
    source = _make_midi([40, 127, 40, 127])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    # Ordem relativa preservada: as notas originalmente mais fortes (127)
    # continuam mais fortes que as originalmente mais fracas (40).
    assert velocities[1] > velocities[0]
    assert velocities[3] > velocities[2]
    assert velocities[1] > velocities[2]
    assert velocities[3] > velocities[0]


def test_generic_picked_reapplication_is_byte_identical():
    source = _make_midi([80, 90, 100, 110, 120])
    once = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )
    once_bytes = _serialize(once)

    twice = apply_technique(
        "bass.attack_style", once, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    assert _serialize(twice) == once_bytes
    # A segunda aplicacao nao deve ter dobrado o deslocamento de velocity.
    assert _structural_note_ons(twice) == _structural_note_ons(once)


def test_generic_picked_saturated_velocity_is_not_reported_as_applied():
    # Origem ja saturada no clamp [1, 127] (127 alternando com 1): o shift
    # relativo nao produz nenhuma mudanca audivel de velocity. Achado do
    # Codex na PR #104 — gravar o marcador de idempotencia mesmo sem
    # nenhuma nota mudar fazia `_midi_bytes` enxergar bytes diferentes (o
    # meta text em si) e o pipeline reportar a tecnica como aplicada ao
    # usuario sem nada ter de fato mudado.
    source = _make_midi([127, 1, 127, 1])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    assert _serialize(out) == original_bytes


def test_generic_fingered_style_remains_no_op():
    source = _make_midi([80, 90, 100, 110])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "fingered"},
    )

    assert _serialize(out) == original_bytes


def test_generic_slap_style_remains_no_op():
    source = _make_midi([80, 90, 100, 110])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "slap"},
    )

    assert _serialize(out) == original_bytes


def _serialize(mid: mido.MidiFile) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()
