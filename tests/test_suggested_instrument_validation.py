"""Cobertura das guardas de `_validate_suggested_instrument` em tools/plan.py.

Cada caso exercita uma barreira do validador (tipo, ASCII, `|`, plugin default
por FR-24, Serum fora do escopo do FR-14). O carimbo do arranjador so pode
carregar sugestao que passe por todas.
"""

from __future__ import annotations

import mido
import pytest

from tools.plan import (
    ArrangementPlan,
    BriefRef,
    FamilyStyle,
    PlanEdit,
    PlanValidationError,
    SourceMidi,
    StyleTechnique,
    _style_family_for_role,
    _validate_suggested_instrument,
    to_dict,
)
from tools.render import (
    RenderError,
    _apply_style_techniques_to_tracks,
    _format_stamp,
    _insert_stamp,
    _stamp_edit_tracks,
)
from tools.render import _style_family_for_role as _render_style_family_for_role


def _check(profile, suggested, path="edits[0].suggested_instrument"):
    _validate_suggested_instrument(suggested, profile, path)


def test_non_dict_suggested_instrument_rejected():
    with pytest.raises(PlanValidationError, match="must be object"):
        _check("drums", ["not", "a", "dict"])


def test_unknown_field_rejected():
    with pytest.raises(PlanValidationError, match="unknown fields"):
        _check("drums", {"plugin": "A", "preset": "B", "hue": "green"})


@pytest.mark.parametrize("value", ["", "   ", 42, None])
def test_empty_or_non_string_plugin_rejected(value):
    with pytest.raises(PlanValidationError, match=r"\.plugin"):
        _check("drums", {"plugin": value, "preset": "P"})


@pytest.mark.parametrize("value", ["", "   ", 42, None])
def test_empty_or_non_string_preset_rejected(value):
    with pytest.raises(PlanValidationError, match=r"\.preset"):
        _check("drums", {"plugin": "A", "preset": value})


def test_non_bool_verified_rejected():
    with pytest.raises(PlanValidationError, match="verified.*must be bool"):
        _check("drums", {"plugin": "A", "preset": "B", "verified": "yes"})


def test_non_ascii_plugin_rejected():
    with pytest.raises(PlanValidationError, match="must be ASCII"):
        _check("drums", {"plugin": "Pianoão", "preset": "B"})


def test_non_ascii_preset_rejected():
    with pytest.raises(PlanValidationError, match="must be ASCII"):
        _check("drums", {"plugin": "A", "preset": "Grandão"})


def test_pipe_in_plugin_or_preset_rejected():
    with pytest.raises(PlanValidationError, match="separador reservado do carimbo"):
        _check("drums", {"plugin": "A|B", "preset": "P"})


def test_default_plugin_mismatch_rejected(monkeypatch):
    import tools.tracks as tracks_mod

    monkeypatch.setattr(
        tracks_mod,
        "default_plugin_for_role",
        lambda role: "Superior Drummer" if role == "drums" else None,
    )
    with pytest.raises(PlanValidationError, match="must use 'Superior Drummer'"):
        _check("drums", {"plugin": "SomeSampler", "preset": "P"})


def test_serum_outside_allowed_roles_rejected():
    with pytest.raises(PlanValidationError, match="Serum is not allowed"):
        _check("drums", {"plugin": "Serum", "preset": "Lead"})


@pytest.mark.parametrize("role", ["bass", "drums", "guitar", "keys"])
def test_style_family_for_role_returns_family_role_directly(role):
    assert _style_family_for_role(role) == role
    assert _render_style_family_for_role(role) == role


def test_format_stamp_rejects_non_ascii_field():
    with pytest.raises(RenderError, match="must be ASCII"):
        _format_stamp(
            role="drums", plugin="Pianoão", preset="Metal",
            verified=True,
        )


def test_format_stamp_rejects_pipe_in_field():
    with pytest.raises(RenderError, match="must not contain"):
        _format_stamp(
            role="drums", plugin="A|B", preset="Metal",
            verified=True,
        )


def test_insert_stamp_falls_back_to_track_start_when_no_track_name():
    track = mido.MidiTrack()
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    _insert_stamp(track, "midi-arranger v1|role=drums")
    assert track[0].is_meta and track[0].type == "text"
    assert track[0].text == "midi-arranger v1|role=drums"


def _empty_plan():
    return ArrangementPlan(
        version=1,
        seed=1,
        source_midi=SourceMidi(path="/tmp/x.mid", sha256="0" * 64),
        route="R1",
        sections=[],
        elements=[],
    )


def test_stamp_edit_tracks_skips_edit_with_no_matching_track():
    plan = _empty_plan()
    plan.edits = [PlanEdit(track="Ghost", profile="drums", intensity=0.0)]
    plan.style = {
        "drums": FamilyStyle(
            reference="X", researched_at="2026-08-26",
            sources=["https://example.test/x"], confidence="high",
            techniques=[StyleTechnique(name="drums.accent_hierarchy")],
            parameters={},
        ),
    }
    mid = mido.MidiFile(ticks_per_beat=480)
    real = mido.MidiTrack()
    real.append(mido.MetaMessage("track_name", name="Piano", time=0))
    mid.tracks.append(real)
    _stamp_edit_tracks(mid, plan=plan, index=None)
    # nenhuma track ganhou carimbo — Piano nao esta em edits, Ghost nao existe
    for tr in mid.tracks:
        for msg in tr:
            assert not (msg.is_meta and msg.type == "text")


def test_apply_style_techniques_to_edit_tracks_skips_missing_target():
    from tools.render import _apply_style_techniques_to_edit_tracks

    plan = _empty_plan()
    plan.edits = [PlanEdit(track="Ghost", profile="drums", intensity=0.0)]
    plan.style = {
        "drums": FamilyStyle(
            reference="X", researched_at="2026-08-26",
            sources=["https://example.test/x"], confidence="high",
            techniques=[StyleTechnique(name="drums.accent_hierarchy")],
            parameters={},
        ),
    }
    mid = mido.MidiFile(ticks_per_beat=480)
    real = mido.MidiTrack()
    real.append(mido.MetaMessage("track_name", name="Piano", time=0))
    mid.tracks.append(real)
    warnings = _apply_style_techniques_to_edit_tracks(mid, plan=plan, index=None)
    assert warnings == []


def test_accent_hierarchy_rejects_unknown_canonical():
    from tools.techniques.engine import (
        TechniqueContext,
        _apply_drums_accent_hierarchy,
    )

    mid = mido.MidiFile(ticks_per_beat=480)
    mid.tracks.append(mido.MidiTrack())
    ctx = TechniqueContext(
        seed=1, canonical="drums.does_not_exist", tool="generic",
    )
    with pytest.raises(ValueError, match="nao existe no indice"):
        _apply_drums_accent_hierarchy(mid, context=ctx)


def test_ghost_notes_rejects_unknown_canonical():
    from tools.techniques.engine import (
        TechniqueContext,
        _apply_drums_ghost_notes,
    )

    mid = mido.MidiFile(ticks_per_beat=480)
    mid.tracks.append(mido.MidiTrack())
    ctx = TechniqueContext(
        seed=1, canonical="drums.does_not_exist", tool="generic",
    )
    with pytest.raises(ValueError, match="nao existe no indice"):
        _apply_drums_ghost_notes(mid, context=ctx)


def test_returns_first_argument_unchanged_on_context_only_signature():
    from tools.techniques.engine import _returns_first_argument_unchanged

    def only_context(*, context):
        return context
    assert _returns_first_argument_unchanged(only_context) is False


def test_returns_first_argument_unchanged_on_unsignaturable_callable():
    import inspect

    from tools.techniques import engine as engine_mod

    class Boom:
        def __init__(self):
            raise TypeError("no signature")

    original = inspect.signature
    def stub(func):
        if getattr(func, "_boom", False):
            raise TypeError("no signature for test callable")
        return original(func)

    engine_mod.inspect.signature = stub
    try:
        def fn(mid):
            return mid
        fn._boom = True  # type: ignore[attr-defined]
        assert engine_mod._returns_first_argument_unchanged(fn) is False
    finally:
        engine_mod.inspect.signature = original


def test_iter_note_pairs_skips_orphan_note_off():
    from tools.techniques.engine import _iter_note_pairs

    track = mido.MidiTrack()
    track.append(mido.Message("note_off", note=60, velocity=0, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=100))
    pairs = list(_iter_note_pairs(track))
    assert len(pairs) == 1
    channel, pitch, start, end, velocity, _on_idx, _off_idx = pairs[0]
    assert (pitch, start, end, velocity) == (60, 0, 100, 90)


def test_get_technique_returns_registered_entry():
    from tools.techniques.engine import get_technique

    entry = get_technique("drums.accent_hierarchy")
    assert entry is not None
    assert entry.canonical == "drums.accent_hierarchy"


def test_accent_hierarchy_short_circuits_zero_ticks_per_beat():
    from tools.techniques.engine import apply_technique

    mid = mido.MidiFile(ticks_per_beat=1)
    mid.ticks_per_beat = 0
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    mid.tracks.append(track)
    out = apply_technique(
        "drums.accent_hierarchy", mid, seed=1, tool="generic",
    )
    assert out.ticks_per_beat == 0


def test_ghost_notes_short_circuits_zero_ticks_per_beat():
    from tools.techniques.engine import apply_technique

    mid = mido.MidiFile(ticks_per_beat=1)
    mid.ticks_per_beat = 0
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    mid.tracks.append(track)
    out = apply_technique(
        "drums.ghost_notes", mid, seed=1, tool="generic",
    )
    assert out.ticks_per_beat == 0


def test_accent_hierarchy_layer_for_default_case_covers_tom():
    """Toms (nota 50) nao caem em kicks/snares/hi_hats/crashes — camada default."""
    from tools.techniques.engine import apply_technique

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    # nota 50 (High Tom) NAO esta em kicks/snares/hi_hats/crashes — cai no default
    # off-beat (16-avo 1) -> "soft"
    track.append(mido.Message("note_on", note=50, velocity=127, channel=9, time=120))
    track.append(mido.Message("note_off", note=50, velocity=0, channel=9, time=120))
    mid.tracks.append(track)
    out = apply_technique(
        "drums.accent_hierarchy", mid, seed=1, tool="generic",
    )
    velocities = [
        m.velocity for m in out.tracks[0]
        if m.type == "note_on" and m.velocity > 0
    ]
    # 127 chapado clampeado para faixa "soft" (55-79)
    assert velocities and all(55 <= v <= 79 for v in velocities)


def test_style_technique_density_survives_roundtrip():
    plan = _empty_plan()
    plan.brief_ref = BriefRef(path="brief.json", sha256="0" * 64)
    plan.style = {
        "drums": FamilyStyle(
            reference="X", researched_at="2026-08-26",
            sources=["https://example.test/x"], confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes", density=0.4)],
            parameters={},
        ),
    }
    data = to_dict(plan)
    tech = data["style"]["drums"]["techniques"][0]
    assert tech["density"] == pytest.approx(0.4)


def _drums_style_plan_with_edit(track_name_value: str):
    plan = _empty_plan()
    plan.edits = [PlanEdit(track=track_name_value, profile="drums", intensity=0.0)]
    plan.style = {
        "drums": FamilyStyle(
            reference="X", researched_at="2026-08-26",
            sources=["https://example.test/x"], confidence="high",
            techniques=[StyleTechnique(name="drums.accent_hierarchy")],
            parameters={},
        ),
    }
    return plan


def _midi_with_named_track(name: str) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.Message("note_on", note=38, velocity=90, channel=9, time=0))
    track.append(mido.Message("note_off", note=38, velocity=0, channel=9, time=240))
    mid.tracks.append(track)
    return mid


def test_style_render_without_index_raises_render_error():
    """`render()` so passa `index=None` quando nenhuma familia tem tecnica.

    Chamar o helper com tecnica declarada e indice ausente quebra esse
    invariante; tem que virar `RenderError` explicito, nao `AttributeError`
    la dentro do motor. NAO trocar por `assert`: `python -O` remove assert e
    o erro tratado vira crash cru.
    """
    plan = _drums_style_plan_with_edit("Drums")
    track = _midi_with_named_track("Drums").tracks[0]
    with pytest.raises(RenderError, match="missing techniques index"):
        _apply_style_techniques_to_tracks(
            [track],
            plan=plan,
            family="drums",
            tool_target=None,
            ticks_per_beat=480,
            midi_type=1,
            index=None,
        )


def test_style_on_edit_tracks_without_index_raises_render_error():
    from tools.render import _apply_style_techniques_to_edit_tracks

    plan = _drums_style_plan_with_edit("Drums")
    mid = _midi_with_named_track("Drums")
    with pytest.raises(RenderError, match="missing techniques index"):
        _apply_style_techniques_to_edit_tracks(mid, plan=plan, index=None)


def test_stamp_edit_tracks_without_index_raises_render_error():
    plan = _drums_style_plan_with_edit("Drums")
    mid = _midi_with_named_track("Drums")
    with pytest.raises(RenderError, match="missing techniques index"):
        _stamp_edit_tracks(mid, plan=plan, index=None)
