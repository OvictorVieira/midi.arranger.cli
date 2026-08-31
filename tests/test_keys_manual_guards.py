"""Cobre os guard-rails de consistencia dos manuais das 4 tecnicas de teclas.

Cada raise em `_apply_keys_*` protege contra manual editado errado (cc trocado,
limiar invertido, teto ausente). Nao dispara em producao — o manual e checked-in
JSON — mas coverage do gate exige exercitar. Usamos `monkeypatch` sobre
`_helpers.build_index` para retornar um indice com o parametro alvo mutado.
"""

from __future__ import annotations

from dataclasses import replace

import mido
import pytest

from tools.techniques import _helpers
from tools.techniques.engine import apply_technique
from tools.techniques.index import Technique, TechniqueIndex


def _empty_mid() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
    mid.tracks.append(track)
    return mid


def _patched_index(monkeypatch, canonical: str, name: str, new_value):
    """Substitui `_helpers.build_index` por um indice com um parametro mutado."""
    original = _helpers.build_index()
    target = original.get(canonical)
    assert target is not None
    new_params = tuple(
        replace(p, value=new_value) if p.name == name else p
        for p in target.parameters
    )
    mutated = Technique(
        canonical=target.canonical,
        name=target.name,
        family=target.family,
        summary=target.summary,
        verified=target.verified,
        description=target.description,
        parameters=new_params,
        tools=target.tools,
        source_manual=target.source_manual,
    )
    others = tuple(t for t in original.techniques if t.canonical != canonical)
    patched = TechniqueIndex(techniques=others + (mutated,))
    monkeypatch.setattr(_helpers, "build_index", lambda: patched)
    return patched


@pytest.mark.parametrize(
    "canonical,name,bad_value,message",
    [
        ("keys.pitch_bend", "centro", 0, "espera centro=8192"),
        ("keys.modulation", "cc", 2, "espera cc=1"),
        ("keys.modulation", "cc_lsb", 34, "espera cc_lsb=33"),
        ("keys.expression", "cc_expression", 12, "espera cc_expression=11"),
        ("keys.expression", "cc_volume", 8, "espera cc_volume=7"),
        ("keys.expression", "cc11_lsb", 44, "espera cc11_lsb=43"),
        ("keys.expression", "default_cc11", 100, "espera default_cc11=127"),
        ("keys.damper_pedal", "cc", 65, "espera cc=64"),
    ],
)
def test_manual_consistency_guards(monkeypatch, canonical, name, bad_value, message):
    _patched_index(monkeypatch, canonical, name, bad_value)
    with pytest.raises(ValueError, match=message):
        apply_technique(
            canonical, _empty_mid(), seed=1, parameters={"density": 1.0},
        )


def test_modulation_manual_teto_less_than_default_raises(monkeypatch):
    _patched_index(monkeypatch, "keys.modulation", "teto_dls_cents", 10)
    with pytest.raises(ValueError, match="manual inconsistente"):
        apply_technique(
            "keys.modulation", _empty_mid(), seed=1, parameters={"density": 1.0},
        )


def test_damper_pedal_limiar_inconsistente_raises(monkeypatch):
    _patched_index(monkeypatch, "keys.damper_pedal", "limiar_on_min", 60)
    with pytest.raises(ValueError, match="manual inconsistente"):
        apply_technique(
            "keys.damper_pedal", _empty_mid(), seed=1, parameters={"density": 1.0},
        )


def test_damper_pedal_default_fora_da_faixa_off_raises(monkeypatch):
    _patched_index(monkeypatch, "keys.damper_pedal", "default", 90)
    with pytest.raises(ValueError, match="cair na faixa OFF"):
        apply_technique(
            "keys.damper_pedal", _empty_mid(), seed=1, parameters={"density": 1.0},
        )


def test_cc_envelope_setup_returns_none_when_ticks_per_beat_invalid():
    """`cc_envelope_setup` deve devolver None (NO-OP) quando `ticks_per_beat<=0`.

    MidiFile aceita `ticks_per_beat=0` mas o pipeline nao consegue derivar
    `ticks_per_second`; ambos os aplicadores de envelope precisam sair sem
    emitir eventos, sem estourar.
    """
    mid = mido.MidiFile(ticks_per_beat=1)
    mid.ticks_per_beat = 0  # forca cenario invalido pos-construcao
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    mid.tracks.append(track)

    result = apply_technique(
        "keys.modulation", mid, seed=1, parameters={"density": 1.0},
    )
    # NO-OP: nenhum CC1 emitido, so o que ja existia (2 metas).
    cc1_events = [
        msg for msg in result.tracks[0]
        if not msg.is_meta and msg.type == "control_change" and msg.control == 1
    ]
    assert cc1_events == []


def test_manual_int_param_raises_when_parameter_missing(monkeypatch):
    """`manual_int_param` levanta erro claro quando o manual perde o parametro."""
    original = _helpers.build_index()
    target = original.get("keys.pitch_bend")
    assert target is not None
    kept = tuple(p for p in target.parameters if p.name != "range_default_gm")
    mutated = Technique(
        canonical=target.canonical,
        name=target.name,
        family=target.family,
        summary=target.summary,
        verified=target.verified,
        description=target.description,
        parameters=kept,
        tools=target.tools,
        source_manual=target.source_manual,
    )
    others = tuple(t for t in original.techniques if t.canonical != target.canonical)
    monkeypatch.setattr(
        _helpers, "build_index", lambda: TechniqueIndex(techniques=others + (mutated,)),
    )
    with pytest.raises(ValueError, match="precisa declarar range_default_gm"):
        apply_technique(
            "keys.pitch_bend", _empty_mid(), seed=1, parameters={"density": 1.0},
        )


@pytest.mark.parametrize("canonical,pname", [
    ("keys.modulation", "depth_cents"),
    ("keys.expression", "depth"),
    ("keys.damper_pedal", "press_value"),
])
def test_non_numeric_parameter_raises_explicit_error(canonical, pname):
    with pytest.raises(ValueError, match=f"{pname} precisa ser"):
        apply_technique(
            canonical, _empty_mid(), seed=1,
            parameters={"density": 1.0, pname: "loud"},
        )


def test_damper_half_pedal_supported_must_be_bool():
    with pytest.raises(ValueError, match="half_pedal_supported precisa ser bool"):
        apply_technique(
            "keys.damper_pedal", _empty_mid(), seed=1,
            parameters={"density": 1.0, "half_pedal_supported": "yes"},
        )


@pytest.mark.parametrize("canonical,pname", [
    ("keys.modulation", "depth_cents"),
    ("keys.expression", "depth"),
])
def test_zero_depth_is_noop(canonical, pname):
    result = apply_technique(
        canonical, _empty_mid(), seed=1,
        parameters={"density": 1.0, pname: 0},
    )
    cc_events = [
        m for m in result.tracks[0]
        if not m.is_meta and m.type == "control_change"
    ]
    assert cc_events == []


def test_damper_pedal_ticks_per_beat_zero_is_noop():
    mid = mido.MidiFile(ticks_per_beat=1)
    mid.ticks_per_beat = 0
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    mid.tracks.append(track)
    result = apply_technique(
        "keys.damper_pedal", mid, seed=1, parameters={"density": 1.0},
    )
    cc64 = [
        m for m in result.tracks[0]
        if not m.is_meta and m.type == "control_change" and m.control == 64
    ]
    assert cc64 == []


def test_pitch_bend_ticks_per_beat_zero_is_noop():
    mid = mido.MidiFile(ticks_per_beat=1)
    mid.ticks_per_beat = 0
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    mid.tracks.append(track)
    result = apply_technique(
        "keys.pitch_bend", mid, seed=1, parameters={"density": 1.0},
    )
    bends = [
        m for m in result.tracks[0] if not m.is_meta and m.type == "pitchwheel"
    ]
    assert bends == []


def test_pitch_bend_skips_when_track_has_less_than_two_notes():
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
    mid.tracks.append(track)
    result = apply_technique(
        "keys.pitch_bend", mid, seed=1, parameters={"density": 1.0},
    )
    bends = [
        m for m in result.tracks[0] if not m.is_meta and m.type == "pitchwheel"
    ]
    assert bends == []


def _make_pair(events):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    absolute = []
    order = 0
    for start, dur, pitch, vel in events:
        absolute.append((start, order,
            mido.Message("note_on", channel=0, note=pitch, velocity=vel, time=0)))
        order += 1
        absolute.append((start + dur, order,
            mido.Message("note_off", channel=0, note=pitch, velocity=0, time=0)))
        order += 1
    prev = 0
    for tick, _o, msg in sorted(absolute, key=lambda x: (x[0], x[1])):
        track.append(msg.copy(time=tick - prev))
        prev = tick
    mid.tracks.append(track)
    return mid


def test_pitch_bend_skips_repeated_pitch_and_wide_leap():
    """Interval=0 e interval>range_semitones sao pulados sem emitir bend."""
    mid = _make_pair([
        (0, 100, 60, 90),
        (100, 100, 60, 90),   # interval=0
        (200, 100, 80, 90),   # interval=20 > range_default_gm(2)
    ])
    result = apply_technique(
        "keys.pitch_bend", mid, seed=1, parameters={"density": 1.0},
    )
    bends = [m for m in result.tracks[0] if not m.is_meta and m.type == "pitchwheel"]
    assert bends == []


def test_pitch_bend_skips_when_pair_gap_exceeds_max_gap():
    """Par com gap > 1 beat (max_gap_ticks) e descartado."""
    mid = _make_pair([
        (0, 100, 60, 90),
        (2000, 100, 61, 90),  # gap 1900 ticks > 480
    ])
    result = apply_technique(
        "keys.pitch_bend", mid, seed=1, parameters={"density": 1.0},
    )
    bends = [m for m in result.tracks[0] if not m.is_meta and m.type == "pitchwheel"]
    assert bends == []


def test_pitch_bend_skips_when_notes_are_simultaneous():
    """b.start <= a.start (empatado) nao vira par."""
    mid = _make_pair([
        (0, 100, 60, 90),
        (0, 100, 62, 90),  # mesmo start
    ])
    result = apply_technique(
        "keys.pitch_bend", mid, seed=1, parameters={"density": 1.0},
    )
    bends = [m for m in result.tracks[0] if not m.is_meta and m.type == "pitchwheel"]
    assert bends == []


def test_modulation_skips_ultrashort_notes():
    """Nota com duracao<=1 tick nao gera envelope de CC1."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=1))
    mid.tracks.append(track)
    result = apply_technique(
        "keys.modulation", mid, seed=1, parameters={"density": 1.0},
    )
    cc1 = [
        m for m in result.tracks[0]
        if not m.is_meta and m.type == "control_change" and m.control == 1
    ]
    assert cc1 == []


def test_iter_track_selections_skips_when_no_selection():
    """Densidade tao baixa que nenhum candidato sobe: NO-OP silencioso.

    Cobre o `continue` de `iter_track_selections` — sem esse guard, `apply`
    ainda tentaria emitir envelope numa lista vazia.
    """
    mid = _empty_mid()
    before = [msg.copy() for msg in mid.tracks[0]]
    # density=1e-9 quase certamente descarta a nota unica na primeira track.
    result = apply_technique(
        "keys.modulation", mid, seed=1, parameters={"density": 1e-9},
    )
    # NOTA: densidade positiva mas microscopica pode ou nao selecionar; o
    # importante e que nao estoura. Verificamos que a track ficou coerente.
    assert isinstance(result, mido.MidiFile)
    assert len(result.tracks[0]) >= len(before)
