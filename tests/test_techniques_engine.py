"""Testes do registro de tecnicas aplicaveis."""

from __future__ import annotations

import inspect
from io import BytesIO
from pathlib import Path

import mido
import pytest

from tools.registry import (
    Tool,
)
from tools.registry import (
    call as call_tool,
)
from tools.registry import (
    register as register_tool,
)
from tools.registry import (
    restore as restore_tools,
)
from tools.registry import (
    snapshot as snapshot_tools,
)
from tools.techniques import (
    SUPPORTED_TECHNIQUES,
    ExpectedNote,
    NoteSignature,
    Technique,
    TechniqueApplyResult,
    TechniqueContext,
    TechniqueContractError,
    TechniqueIndex,
    TechniquePhysicalError,
    TechniqueRecipeError,
    TechniqueRegistrationError,
    TechniqueRegistry,
    UnknownTechniqueError,
    apply_technique,
    apply_technique_with_warnings,
    build_index,
    derive_note_classification,
    register_technique,
    registered_techniques,
    validate_registry_against_index,
)

MANUALS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "tecnicas"


def test_registered_technique_declares_canonical_level_and_apply_function():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=38,
            velocity=32,
            start_tick=240,
            end_tick=300,
        )
        return mid

    tech = registry.get("drums.ghost_notes")

    assert tech.canonical == "drums.ghost_notes"
    assert tech.level == "technique"
    assert callable(tech.apply)
    assert tech.allow_structural_velocity_change is False
    assert tech.allow_structural_duration_change is False


def test_dispatch_by_canonical_name_calls_registered_function():
    registry = TechniqueRegistry()
    calls = []

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        payload: dict[str, str],
        *,
        context: TechniqueContext,
        intensity: float,
    ) -> dict[str, object]:
        calls.append((payload, context.seed, intensity))
        return {"payload": payload, "intensity": intensity}

    result = registry.apply(
        "drums.ghost_notes",
        {"track": "Drums"},
        seed=123,
        intensity=0.5,
    )

    assert result == {"payload": {"track": "Drums"}, "intensity": 0.5}
    assert calls == [({"track": "Drums"}, 123, 0.5)]


def test_unknown_technique_error_names_available_techniques():
    registry = TechniqueRegistry()
    registry.register("drums.microtiming", "humanize")(
        lambda payload, *, context: payload
    )
    registry.register("drums.ghost_notes", "technique")(
        lambda payload, *, context: payload
    )

    with pytest.raises(UnknownTechniqueError) as exc:
        registry.apply("drums.flanm", object(), seed=1)

    assert exc.value.available == ("drums.ghost_notes", "drums.microtiming")
    assert "drums.ghost_notes" in str(exc.value)
    assert "drums.microtiming" in str(exc.value)


def test_duplicate_or_malformed_registration_fails():
    registry = TechniqueRegistry()
    registry.register("drums.ghost_notes", "technique")(
        lambda payload, *, context: payload
    )

    with pytest.raises(TechniqueRegistrationError, match="duplicada"):
        registry.register("drums.ghost_notes", "technique")(
            lambda payload, *, context: payload
        )
    with pytest.raises(TechniqueRegistrationError, match="<familia>.<nome>"):
        registry.register("ghost_notes", "technique")(
            lambda payload, *, context: payload
        )
    with pytest.raises(TechniqueRegistrationError, match="nivel"):
        registry.register("drums.microtiming", "ornament")(
            lambda payload, *, context: payload
        )
    with pytest.raises(TechniqueRegistrationError, match="chamavel"):
        registry.register("drums.microtiming", "humanize")(None)


def test_registration_requires_explicit_context_parameter():
    registry = TechniqueRegistry()

    with pytest.raises(TechniqueRegistrationError, match="context"):
        registry.register("drums.microtiming", "humanize")(lambda payload: payload)


def test_global_registration_rejects_identity_stub():
    def apply(subject, *, context: TechniqueContext):
        _ = context
        return subject

    with pytest.raises(TechniqueRegistrationError, match="aplicador neutro"):
        register_technique("drums.noop", "technique")(apply)


def test_registration_rejects_context_var_keyword_only():
    registry = TechniqueRegistry()

    def apply(**context):
        return context

    with pytest.raises(TechniqueRegistrationError, match="aceitar keyword"):
        registry.register("drums.microtiming", "humanize")(apply)


def test_technique_context_rejects_non_integer_seed():
    with pytest.raises(TechniqueRegistrationError, match="seed"):
        TechniqueContext(seed="1", canonical="drums.ghost_notes")  # type: ignore[arg-type]


def test_supported_techniques_is_derived_from_the_registry():
    assert tuple(t.canonical for t in registered_techniques()) == SUPPORTED_TECHNIQUES
    assert tuple(sorted(SUPPORTED_TECHNIQUES)) == SUPPORTED_TECHNIQUES
    assert SUPPORTED_TECHNIQUES == (
        "bass.ghost_notes",
        "bass.velocity_contour",
        "drums.ghost_notes",
    )


def test_global_dispatch_rejects_documented_but_unimplemented_technique():
    payload = {"notes": [38]}

    with pytest.raises(UnknownTechniqueError) as exc:
        apply_technique("drums.flam", payload, seed=1)

    assert exc.value.available == (
        "bass.ghost_notes",
        "bass.velocity_contour",
        "drums.ghost_notes",
    )


def test_technique_level_accepts_non_midi_subject_without_snapshot():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        subject,
        *,
        context: TechniqueContext,
    ):
        _ = context
        return {"wrapped": subject}

    payload = object()

    assert registry.apply("drums.ghost_notes", payload, seed=1) == {"wrapped": payload}


def test_humanize_returning_non_midi_still_validates_original_midi():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ):
        _ = (mid, context)
        return "ok"

    assert registry.apply(
        "drums.microtiming",
        _midi_with_two_notes(),
        seed=1,
    ) == "ok"


def test_technique_dispatch_replaces_midi_keyword_argument():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        *,
        midi: mido.MidiFile,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            midi.tracks[1],
            channel=9,
            note=38,
            velocity=32,
            start_tick=240,
            end_tick=300,
        )
        return midi

    source = _midi_with_two_notes()
    result = registry.apply("drums.ghost_notes", midi=source, seed=1)

    assert result is not source
    assert len(_note_tuples(result)) == len(_note_tuples(source)) + 1


def test_registered_techniques_do_not_capture_global_or_nonlocal_state():
    for tech in registered_techniques():
        closure = inspect.getclosurevars(tech.apply)

        assert closure.globals == {}
        assert closure.nonlocals == {}


def test_humanize_allows_only_timing_velocity_and_duration_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        out = _midi_with_two_notes(first_note=60, second_note=64)
        first_on = out.tracks[1][1]
        first_off = out.tracks[1][2]
        out.tracks[1][1] = first_on.copy(time=12, velocity=70)
        out.tracks[1][2] = first_off.copy(time=300)
        return out

    result = registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)

    assert result.tracks[1][1].time == 12
    assert result.tracks[1][1].velocity == 70
    assert result.tracks[1][2].time == 300


def test_humanize_contract_rejects_added_notes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        mid.tracks[1].append(mido.Message(
            "note_on", channel=9, note=38, velocity=90, time=0
        ))
        mid.tracks[1].append(mido.Message(
            "note_off", channel=9, note=38, velocity=0, time=120
        ))
        return mid

    with pytest.raises(TechniqueContractError, match="contagem de note_on"):
        registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)


def test_humanize_contract_rejects_pitch_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        _mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        return _midi_with_two_notes(first_note=61, second_note=64)

    with pytest.raises(TechniqueContractError, match="multiconjunto de pitches"):
        registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)


def test_humanize_contract_rejects_changed_note_off_pitch():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        first_off = mid.tracks[1][2]
        mid.tracks[1][2] = first_off.copy(note=61)
        return mid

    with pytest.raises(TechniqueContractError, match="note_off orfao"):
        registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)


def test_humanize_contract_rejects_removed_note_off():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        del mid.tracks[1][2]
        return mid

    with pytest.raises(TechniqueContractError, match="note_on sem note_off"):
        registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)


def test_humanize_contract_rejects_orphan_note_off():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        mid.tracks[1].append(mido.Message(
            "note_off", channel=9, note=61, velocity=0, time=0
        ))
        return mid

    with pytest.raises(TechniqueContractError, match="note_off orfao"):
        registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)


def test_humanize_contract_treats_note_on_velocity_zero_as_note_off():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        _mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        return _midi_with_single_note()

    result = registry.apply(
        "drums.microtiming",
        _midi_with_note_on_zero_note_off(),
        seed=1,
    )

    assert result.tracks[1][2].type == "note_off"


def test_humanize_contract_rejects_note_on_order_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        _mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        return _midi_with_two_notes(first_note=64, second_note=60)

    with pytest.raises(TechniqueContractError, match="ordem dos note_on"):
        registry.apply("drums.microtiming", _midi_with_two_notes(), seed=1)


def test_registered_techniques_preserve_structural_notes_by_default():
    for tech in registered_techniques():
        if tech.level != "technique":
            continue

        source = _midi_with_two_notes()
        result = apply_technique(tech.canonical, source, seed=1)

        assert _note_tuples(result) == _note_tuples(_midi_with_two_notes())


def test_technique_allows_ornamental_notes_cc_and_pitch_bend():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        track = mid.tracks[1]
        track.append(mido.Message(
            "control_change", channel=9, control=4, value=48, time=0
        ))
        track.append(mido.Message("pitchwheel", channel=9, pitch=-120, time=0))
        track.append(mido.Message(
            "note_on", channel=9, note=38, velocity=32, time=0
        ))
        track.append(mido.Message(
            "note_off", channel=9, note=38, velocity=0, time=120
        ))
        return mid

    result = registry.apply("drums.ghost_notes", _midi_with_two_notes(), seed=1)

    note_ons = [
        msg for msg in result.tracks[1]
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    control_changes = [
        msg for msg in result.tracks[1]
        if not msg.is_meta and msg.type == "control_change"
    ]
    pitch_bends = [
        msg for msg in result.tracks[1]
        if not msg.is_meta and msg.type == "pitchwheel"
    ]
    assert [(msg.channel, msg.note, msg.velocity) for msg in note_ons] == [
        (9, 60, 96),
        (9, 64, 88),
        (9, 38, 32),
    ]
    assert [(msg.channel, msg.control, msg.value) for msg in control_changes] == [
        (9, 4, 48),
    ]
    assert [(msg.channel, msg.pitch) for msg in pitch_bends] == [(9, -120)]


def test_technique_contract_rejects_structural_pitch_or_position_changes():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def change_pitch(
        _mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        return _midi_with_two_notes(first_note=61, second_note=64)

    with pytest.raises(TechniqueContractError, match="pitch ou posicao"):
        registry.apply("drums.ghost_notes", _midi_with_two_notes(), seed=1)

    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def change_position(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        first_on = mid.tracks[1][1]
        mid.tracks[1][1] = first_on.copy(time=24)
        return mid

    with pytest.raises(TechniqueContractError, match="pitch ou posicao"):
        registry.apply("drums.ghost_notes", _midi_with_two_notes(), seed=1)


def test_technique_contract_rejects_structural_velocity_without_permission():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        first_on = mid.tracks[1][1]
        mid.tracks[1][1] = first_on.copy(velocity=72)
        return mid

    with pytest.raises(TechniqueContractError, match="velocity"):
        registry.apply("drums.ghost_notes", _midi_with_two_notes(), seed=1)


def test_technique_contract_rejects_structural_duration_without_permission():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        first_off = mid.tracks[1][2]
        mid.tracks[1][2] = first_off.copy(time=300)
        return mid

    with pytest.raises(TechniqueContractError, match="duracao"):
        registry.apply("drums.ghost_notes", _midi_with_single_note(), seed=1)


def test_technique_can_declare_structural_velocity_and_duration_changes():
    registry = TechniqueRegistry()

    @registry.register(
        "bass.ghost_notes",
        "technique",
        allow_structural_velocity_change=True,
        allow_structural_duration_change=True,
    )
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        first_on = mid.tracks[1][1]
        first_off = mid.tracks[1][2]
        mid.tracks[1][1] = first_on.copy(velocity=72)
        mid.tracks[1][2] = first_off.copy(time=300)
        return mid

    result = registry.apply("bass.ghost_notes", _midi_with_single_note(), seed=1)

    assert result.tracks[1][1].velocity == 72
    assert result.tracks[1][2].time == 300


def test_technique_application_is_idempotent_in_memory_byte_for_byte():
    registry = _ornament_registry()
    once = _apply_two_ornament_techniques(registry, _midi_with_drums_and_bass())
    once_bytes = _midi_bytes(once)
    once_events = _continuous_event_tuples(once)

    twice = _apply_two_ornament_techniques(registry, once)

    assert _midi_bytes(twice) == once_bytes
    assert _note_tuples(twice) == [
        (1, 9, 38, 120, 180, 32),
        (1, 9, 60, 0, 480, 96),
        (2, 0, 35, 240, 300, 28),
        (2, 0, 40, 0, 480, 96),
    ]
    assert _continuous_event_tuples(twice) == once_events
    assert once_events == [
        (1, "control_change", 9, 120, 4, 48),
        (1, "pitchwheel", 9, 120, 0, -120),
        (2, "control_change", 0, 240, 11, 64),
        (2, "pitchwheel", 0, 240, 0, 80),
    ]


def test_technique_application_is_idempotent_after_saved_round_trip(
    tmp_path: Path,
):
    registry = _ornament_registry()
    once = _apply_two_ornament_techniques(registry, _midi_with_drums_and_bass())
    once_path = tmp_path / "once.mid"
    once.save(once_path)

    reloaded = mido.MidiFile(once_path)
    twice = _apply_two_ornament_techniques(registry, reloaded)

    assert _midi_bytes(twice) == once_path.read_bytes()
    assert _note_tuples(twice) == _note_tuples(once)
    assert _continuous_event_tuples(twice) == _continuous_event_tuples(once)


def test_technique_idempotence_preserves_pre_existing_cc_and_pitch_bend():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_event(
            mid.tracks[1],
            mido.Message("control_change", channel=9, control=4, value=48),
            tick=120,
        )
        _insert_event(
            mid.tracks[1],
            mido.Message("pitchwheel", channel=9, pitch=-120),
            tick=120,
        )
        return mid

    source = _midi_with_two_notes()
    _insert_event(
        source.tracks[1],
        mido.Message("control_change", channel=9, control=4, value=48),
        tick=120,
    )
    _insert_event(
        source.tracks[1],
        mido.Message("pitchwheel", channel=9, pitch=-120),
        tick=120,
    )
    source_events = _continuous_event_tuples(source)

    result = registry.apply("drums.ghost_notes", source, seed=1)

    assert _continuous_event_tuples(result) == source_events


def test_technique_context_makes_seed_effect_explicit_and_deterministic():
    registry = TechniqueRegistry()

    @registry.register("drums.microtiming", "humanize")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        first_on = mid.tracks[1][1]
        mid.tracks[1][1] = first_on.copy(velocity=70 + context.seed % 16)
        return mid

    same_a = registry.apply("drums.microtiming", _midi_with_two_notes(), seed=17)
    same_b = registry.apply("drums.microtiming", _midi_with_two_notes(), seed=17)
    different = registry.apply("drums.microtiming", _midi_with_two_notes(), seed=18)

    assert _midi_bytes(same_a) == _midi_bytes(same_b)
    assert _midi_bytes(same_a) != _midi_bytes(different)


def test_technique_context_derives_local_rng_from_seed_and_name():
    ctx_a = TechniqueContext(seed=7, canonical="drums.microtiming")
    ctx_b = TechniqueContext(seed=7, canonical="drums.microtiming")
    ctx_c = TechniqueContext(seed=8, canonical="drums.microtiming")

    assert ctx_a.rng("offset").randrange(10_000) == ctx_b.rng("offset").randrange(
        10_000
    )
    assert ctx_a.rng("offset").randrange(10_000) != ctx_c.rng("offset").randrange(
        10_000
    )


def _midi_realistic_metal_drums(bars: int = 16) -> mido.MidiFile:
    """Levada de metal chapada em 127 com humanizacao de onset (+/-8 ticks)
    parecida com o MIDI real de producao. Simula: kick em beats 1 e 3, snare
    backbeat em 2 e 4, hi-hat em contratempo (offbeats 8), crash na chegada
    de secao (a cada 8 compassos). Nenhum onset cai em `tick % ticks_per_beat
    == 0` — se a tecnica exigir alinhamento perfeito, tudo desaba."""

    ticks_per_beat = 480
    jitter_seq = (2, -3, 5, -1, 4, -6, 7, -2, 3, -4, 6, -5, 1, -7, 8, -8)
    jitter_iter = iter(jitter_seq * 40)
    def next_jitter() -> int:
        try:
            return next(jitter_iter)
        except StopIteration:  # pragma: no cover - defensive
            return 0

    notes: list[tuple[int, int, int, int]] = []
    gate = 96
    for bar in range(bars):
        bar_tick = bar * ticks_per_beat * 4
        for beat_index in range(4):
            beat_tick = bar_tick + beat_index * ticks_per_beat
            if beat_index in (0, 2):
                start = beat_tick + next_jitter()
                notes.append((start, start + gate, 36, 127))
            if beat_index in (1, 3):
                start = beat_tick + next_jitter()
                notes.append((start, start + gate, 38, 127))
            hat_start = beat_tick + ticks_per_beat // 2 + next_jitter()
            notes.append((hat_start, hat_start + gate, 42, 127))
        if bar % 8 == 0:
            crash_start = bar_tick + next_jitter()
            notes.append((crash_start, crash_start + 240, 49, 127))
    notes.sort(key=lambda item: item[0])
    return _midi_with_notes("Drums", 9, notes)


def test_drums_ghost_notes_adds_candidates_between_backbeats_only():
    source = _midi_with_ghost_note_window()

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=1,
        parameters={"density": 1.0},
    )
    starts = {note[3] for note in _new_note_tuples(source, result)}

    assert starts
    assert all(480 < start < 1440 for start in starts)


def test_drums_ghost_notes_discards_sixteenth_immediately_before_backbeat():
    source = _midi_with_ghost_note_window()

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=2,
        parameters={"density": 1.0},
    )
    starts = {note[3] for note in _new_note_tuples(source, result)}

    assert 1320 not in starts


def test_drums_ghost_notes_discards_consecutive_pair_after_backbeat():
    source = _midi_with_ghost_note_window()

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=3,
        parameters={"density": 1.0},
    )
    starts = {note[3] for note in _new_note_tuples(source, result)}

    assert not {600, 720}.issubset(starts)


def test_drums_ghost_notes_never_writes_three_consecutive_sixteenths():
    source = _midi_with_ghost_note_window()

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=4,
        parameters={"density": 1.0},
    )
    starts = {note[3] for note in _new_note_tuples(source, result)}

    assert not any(
        {start, start + 120, start + 240}.issubset(starts)
        for start in starts
    )


def test_drums_ghost_notes_uses_requested_tool_recipe_for_note_and_velocity():
    source = _midi_with_ghost_note_window()

    applied = apply_technique_with_warnings(
        "drums.ghost_notes",
        source,
        seed=5,
        parameters={"density": 1.0},
        tool="superior_drummer",
        index=_technique_index(
            "drums.ghost_notes",
            {
                "generic": {"notes": [38], "velocity": [20, 45]},
                "superior_drummer": {"notes": [40], "velocity": [30, 31]},
            },
        ),
    )
    ghosts = _new_note_tuples(source, applied.result)

    assert applied.warnings == ()
    assert ghosts
    assert {note[2] for note in ghosts} == {40}
    assert {note[5] for note in ghosts} <= {30, 31}


def test_drums_ghost_notes_falls_back_to_generic_recipe_with_warning():
    source = _midi_with_ghost_note_window()

    applied = apply_technique_with_warnings(
        "drums.ghost_notes",
        source,
        seed=6,
        parameters={"density": 1.0},
        tool="maschine",
        index=_technique_index(
            "drums.ghost_notes",
            {"generic": {"notes": [38], "velocity": [20, 45]}},
        ),
    )

    assert applied.warnings[0]["code"] == "W_NO_TOOL_RECIPE"
    assert {note[2] for note in _new_note_tuples(source, applied.result)} == {38}


def test_drums_ghost_notes_marks_added_notes_as_ornamental_by_derivation():
    source = _midi_with_ghost_note_window()
    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=7,
        parameters={"density": 1.0},
    )
    ornaments = tuple(
        ExpectedNote(
            signature=NoteSignature(
                track_index=track_index,
                channel=channel,
                pitch=pitch,
                start_tick=start,
                end_tick=end,
            ),
            origin="technique",
            velocity=velocity,
            track_name="Drums",
        )
        for track_index, channel, pitch, start, end, velocity
        in _new_note_tuples(source, result)
    )

    classified = derive_note_classification(
        result,
        source_mid=source,
        technique_notes=ornaments,
    )

    assert ornaments
    assert [
        (note.origin, note.role)
        for note in classified
        if note.origin == "technique"
    ] == [("technique", "ornamental")] * len(ornaments)


def test_drums_ghost_notes_preserves_structural_pitch_and_position():
    source = _midi_with_ghost_note_window()
    before = _note_tuples(source)

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=8,
        parameters={"density": 1.0},
    )

    assert all(note in _note_tuples(result) for note in before)
    assert len(_new_note_tuples(source, result)) > 0


def test_drums_ghost_notes_placement_depends_on_seed_deterministically():
    source = _midi_with_ghost_note_window()

    same_a = apply_technique(
        "drums.ghost_notes",
        source,
        seed=9,
        parameters={"density": 0.5},
    )
    same_b = apply_technique(
        "drums.ghost_notes",
        source,
        seed=9,
        parameters={"density": 0.5},
    )
    different = apply_technique(
        "drums.ghost_notes",
        source,
        seed=10,
        parameters={"density": 0.5},
    )

    assert _midi_bytes(same_a) == _midi_bytes(same_b)
    assert {note[3] for note in _new_note_tuples(source, same_a)} != {
        note[3] for note in _new_note_tuples(source, different)
    }


def test_drums_ghost_notes_is_idempotent():
    source = _midi_with_ghost_note_window()
    once = apply_technique(
        "drums.ghost_notes",
        source,
        seed=11,
        parameters={"density": 1.0},
    )
    once_bytes = _midi_bytes(once)

    twice = apply_technique(
        "drums.ghost_notes",
        once,
        seed=11,
        parameters={"density": 1.0},
    )

    assert _midi_bytes(twice) == once_bytes


def test_drums_ghost_notes_skips_physically_impossible_third_hand():
    source = _midi_with_ghost_note_window(extra_notes=[
        (720, 840, 42, 90),
        (720, 840, 48, 90),
    ])

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=12,
        parameters={"density": 1.0},
    )

    assert 720 not in {note[3] for note in _new_note_tuples(source, result)}


def test_drums_ghost_notes_survives_irregular_structural_durations():
    """Regressao US-006: duracoes irregulares (curtissimas, ultrapassando o
    proximo tempo, sobrepostas na mesma altura) nao podem quebrar o contrato
    do nivel technique. `ghost_notes` tem que rodar sem levantar
    TechniqueContractError e preservar a duracao de toda nota estrutural,
    byte a byte."""

    source = _midi_with_notes(
        "Drums",
        9,
        [
            (0, 47, 36, 100),        # kick nota curtissima
            (480, 1500, 38, 108),    # backbeat 1: sustenta alem do backbeat 2
            (720, 900, 38, 90),      # snare sobreposta na mesma altura
            (1440, 1560, 38, 108),   # backbeat 2 regular
        ],
    )

    structural = {
        (track, channel, pitch, start, end)
        for track, channel, pitch, start, end, _ in _note_tuples(source)
    }

    result = apply_technique(
        "drums.ghost_notes",
        source,
        seed=13,
        parameters={"density": 1.0},
    )

    after = {
        (track, channel, pitch, start, end)
        for track, channel, pitch, start, end, _ in _note_tuples(result)
    }
    assert structural <= after


def test_drums_ghost_notes_reaches_metal_density_on_realistic_backbeats():
    """Regressao US-008: sem `density` explicita, o motor ornamentava a levada
    inteira com apenas duas ghosts (o alvo global `min(2, size)` capava o set
    inteiro em duas). Com bateria realista de 16 compassos e backbeats de
    verdade, a densidade tem que ser compativel com metal — nao duas — e as
    quatro regras de posicao continuam valendo, e nenhuma nota estrutural
    muda de pitch, posicao ou duracao."""

    source = _midi_realistic_metal_drums(bars=16)

    result = apply_technique("drums.ghost_notes", source, seed=21)

    ghosts = _new_note_tuples(source, result)
    assert len(ghosts) >= 16, (
        "sobre 16 compassos com backbeats reais, densidade metal tem que "
        f"passar de uma ghost por compasso; veio {len(ghosts)}"
    )
    assert all(pitch == 38 for _, _, pitch, _, _, _ in ghosts)
    assert all(20 <= vel <= 45 for _, _, _, _, _, vel in ghosts)

    structural_before = {
        (track, channel, pitch, start, end)
        for track, channel, pitch, start, end, _ in _note_tuples(source)
    }
    structural_after = {
        (track, channel, pitch, start, end)
        for track, channel, pitch, start, end, _ in _note_tuples(result)
    }
    assert structural_before <= structural_after, (
        "toda nota estrutural tem que sobreviver byte a byte"
    )

    ticks_per_beat = source.ticks_per_beat
    sixteenth = ticks_per_beat // 4
    ghost_starts = sorted({start for _, _, _, start, _, _ in ghosts})
    ghost_set = set(ghost_starts)

    source_snares = [
        (start, end)
        for _, ch, pitch, start, end, vel in _note_tuples(source)
        if ch == 9 and pitch == 38 and vel > 45
    ]
    backbeat_ticks = sorted({start for start, _ in source_snares})
    for tick in backbeat_ticks:
        assert tick - sixteenth not in ghost_set, (
            "regra 1: 16a imediatamente antes do backbeat nao pode virar ghost"
        )

    for start in ghost_starts:
        assert not (
            start + sixteenth in ghost_set
            and start + 2 * sixteenth in ghost_set
        ), "regra 4: nao pode haver tres 16as consecutivas de ghost"

    intervals = list(zip(backbeat_ticks, backbeat_ticks[1:], strict=False))
    for current, following in intervals:
        window = [s for s in ghost_starts if current < s < following]
        assert len(window) <= 2, (
            f"regra 3: no maximo duas ghosts por intervalo entre backbeats "
            f"({current}->{following}); veio {len(window)}"
        )
        if current + sixteenth in ghost_set and current + 2 * sixteenth in ghost_set:
            raise AssertionError(
                "regra 2: nao pode haver par consecutivo logo depois do backbeat"
            )


def test_apply_uses_requested_tool_recipe_without_warning():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        payload: dict[str, object],
        *,
        context: TechniqueContext,
    ) -> dict[str, object]:
        return {
            **payload,
            "tool": context.tool,
            "requested_tool": context.requested_tool,
            "recipe": dict(context.recipe),
        }

    result = registry.apply_with_warnings(
        "drums.ghost_notes",
        {"payload": True},
        seed=1,
        tool="superior_drummer",
        index=_technique_index(
            "drums.ghost_notes",
            {
                "generic": {"notes": [38]},
                "superior_drummer": {"notes": [40]},
            },
        ),
    )

    assert isinstance(result, TechniqueApplyResult)
    assert result.warnings == ()
    assert result.result == {
        "payload": True,
        "tool": "superior_drummer",
        "requested_tool": "superior_drummer",
        "recipe": {"notes": [40]},
    }


def test_apply_falls_back_to_generic_recipe_with_structured_warning():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        _payload: object,
        *,
        context: TechniqueContext,
    ) -> dict[str, object]:
        return {
            "tool": context.tool,
            "requested_tool": context.requested_tool,
            "recipe": dict(context.recipe),
        }

    result = registry.apply_with_warnings(
        "drums.ghost_notes",
        object(),
        seed=1,
        tool="maschine",
        index=_technique_index(
            "drums.ghost_notes",
            {"generic": {"notes": [38]}},
        ),
    )

    assert result.result == {
        "tool": "generic",
        "requested_tool": "maschine",
        "recipe": {"notes": [38]},
    }
    assert result.warnings == ({
        "code": "W_NO_TOOL_RECIPE",
        "message": (
            "tecnica 'drums.ghost_notes' nao tem receita para tool='maschine'; "
            "usando fallback generico. Disponiveis: ['generic']"
        ),
        "path": "tool",
    },)


def test_apply_fails_without_target_or_generic_recipe_before_calling_function():
    registry = TechniqueRegistry()
    calls = []

    @registry.register("drums.roll", "technique")
    def apply(
        payload: object,
        *,
        context: TechniqueContext,
    ) -> object:
        _ = context
        calls.append(payload)
        return payload

    with pytest.raises(TechniqueRecipeError, match="nem fallback generic"):
        registry.apply_with_warnings(
            "drums.roll",
            object(),
            seed=1,
            tool="maschine",
            index=_technique_index(
                "drums.roll",
                {"superior_drummer": {"notes": [38]}},
            ),
        )

    assert calls == []


def test_apply_technique_with_warnings_rejects_unimplemented_technique():
    with pytest.raises(UnknownTechniqueError):
        apply_technique_with_warnings(
            "drums.flam",
            {"ok": True},
            seed=1,
            tool="maschine",
            index=_technique_index(
                "drums.flam",
                {"generic": {"notes": [38]}},
            ),
        )


def test_registry_apply_with_warnings_exposes_engine_warnings():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        payload: dict[str, bool],
        *,
        context: TechniqueContext,
    ) -> dict[str, bool]:
        _ = context
        return payload

    result = registry.apply_with_warnings(
        "drums.ghost_notes",
        {"ok": True},
        seed=1,
        tool="maschine",
        index=_technique_index(
            "drums.ghost_notes",
            {"generic": {"notes": [38]}},
        ),
    )

    assert result.result == {"ok": True}
    assert result.warnings[0]["code"] == "W_NO_TOOL_RECIPE"


def test_engine_warnings_fit_the_tool_envelope_shape():
    snap = snapshot_tools()
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        _payload: object,
        *,
        context: TechniqueContext,
    ) -> dict[str, str]:
        return {"tool": context.tool}

    def tool_impl(payload: dict[str, object]):
        applied = registry.apply_with_warnings(
            "drums.ghost_notes",
            object(),
            seed=1,
            tool=str(payload["tool"]),
            index=_technique_index(
                "drums.ghost_notes",
                {"generic": {"notes": [38]}},
            ),
        )
        return applied.result, list(applied.warnings)

    try:
        register_tool(Tool(
            name="test.technique_apply",
            description=(
                "Use em teste para confirmar que os warnings do motor de "
                "tecnicas entram no envelope JSON padrao das tools."
            ),
            input_schema={
                "type": "object",
                "properties": {"tool": {"type": "string"}},
                "required": ["tool"],
            },
            output_schema={
                "type": "object",
                "properties": {"tool": {"type": "string"}},
                "required": ["tool"],
            },
            func=tool_impl,
        ))
        env = call_tool("test.technique_apply", {"tool": "maschine"})
    finally:
        restore_tools(snap)

    assert env["ok"] is True
    assert env["data"] == {"tool": "generic"}
    assert env["warnings"][0]["code"] == "W_NO_TOOL_RECIPE"


@pytest.mark.parametrize("ornament_note", [48, 59])
def test_physical_drums_rejects_third_simultaneous_hand_and_keeps_source_clean(
    ornament_note: int,
):
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=ornament_note,
            velocity=90,
            start_tick=0,
            end_tick=120,
        )
        return mid

    source = _midi_with_notes("Drums", 9, [
        (0, 480, 38, 96),
        (0, 480, 42, 88),
    ])
    before_bytes = _midi_bytes(source)

    with pytest.raises(TechniquePhysicalError, match="3 maos"):
        registry.apply("drums.ghost_notes", source, seed=1)

    assert _midi_bytes(source) == before_bytes


def test_physical_drums_accepts_two_hands_and_kick_at_same_tick():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=36,
            velocity=100,
            start_tick=0,
            end_tick=120,
        )
        return mid

    result = registry.apply(
        "drums.ghost_notes",
        _midi_with_notes("Drums", 9, [
            (0, 480, 38, 96),
            (0, 480, 42, 88),
        ]),
        seed=1,
    )

    assert sorted(_note_tuples(result)) == [
        (1, 9, 36, 0, 120, 100),
        (1, 9, 38, 0, 480, 96),
        (1, 9, 42, 0, 480, 88),
    ]


def test_physical_drums_accepts_kick_and_pedal_hihat_as_two_feet():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=44,
            velocity=70,
            start_tick=0,
            end_tick=120,
        )
        return mid

    result = registry.apply(
        "drums.ghost_notes",
        _midi_with_notes("Drums", 9, [
            (0, 240, 36, 100),
        ]),
        seed=1,
    )

    assert sorted(_note_tuples(result)) == [
        (1, 9, 36, 0, 240, 100),
        (1, 9, 44, 0, 120, 70),
    ]


def test_physical_drums_rejects_third_simultaneous_foot():
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=44,
            velocity=70,
            start_tick=0,
            end_tick=120,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="3 pes"):
        registry.apply(
            "drums.ghost_notes",
            _midi_with_notes("Drums", 9, [
                (0, 240, 35, 100),
                (0, 240, 36, 100),
            ]),
            seed=1,
        )


def test_physical_bass_rejects_overlap_on_same_string():
    registry = TechniqueRegistry()

    @registry.register("bass.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=29,
            velocity=32,
            start_tick=120,
            end_tick=180,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="mesma corda"):
        registry.apply(
            "bass.ghost_notes",
            _midi_with_notes("Bass", 0, [(0, 480, 28, 96)]),
            seed=1,
        )


def test_physical_bass_rejects_note_below_lowest_open_string():
    registry = TechniqueRegistry()

    @registry.register("bass.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=27,
            velocity=32,
            start_tick=0,
            end_tick=120,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="corda solta mais grave"):
        registry.apply("bass.ghost_notes", _midi_with_notes("Bass", 0, []), seed=1)


def test_physical_bass_accepts_distinct_open_strings():
    registry = TechniqueRegistry()

    @registry.register("bass.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=33,
            velocity=32,
            start_tick=120,
            end_tick=180,
        )
        return mid

    result = registry.apply(
        "bass.ghost_notes",
        _midi_with_notes("Bass", 0, [(0, 480, 28, 96)]),
        seed=1,
    )

    assert sorted(_note_tuples(result)) == [
        (1, 0, 28, 0, 480, 96),
        (1, 0, 33, 120, 180, 32),
    ]


def test_physical_bass_rejects_unknown_tuning_name():
    registry = TechniqueRegistry()

    @registry.register("bass.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=40,
            velocity=32,
            start_tick=0,
            end_tick=120,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="nao e conhecida"):
        registry.apply(
            "bass.ghost_notes",
            _midi_with_notes("Bass", 0, []),
            seed=1,
            parameters={"tuning": "misteriosa"},
        )


def test_physical_bass_accepts_explicit_open_strings():
    registry = TechniqueRegistry()

    @registry.register("bass.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=35,
            velocity=32,
            start_tick=0,
            end_tick=120,
        )
        return mid

    result = registry.apply(
        "bass.ghost_notes",
        _midi_with_notes("Bass", 0, []),
        seed=1,
        parameters={"open_strings": [30, 35, 40, 45], "max_fret": 12},
    )

    assert _note_tuples(result) == [(1, 0, 35, 0, 120, 32)]


def test_physical_bass_rejects_invalid_max_fret_parameter():
    registry = TechniqueRegistry()

    @registry.register("bass.ghost_notes", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=33,
            velocity=32,
            start_tick=0,
            end_tick=120,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="max_fret"):
        registry.apply(
            "bass.ghost_notes",
            _midi_with_notes("Bass", 0, []),
            seed=1,
            parameters={"max_fret": 0},
        )


def test_physical_guitar_rejects_overlap_on_same_string():
    registry = TechniqueRegistry()

    @registry.register("guitar.chord_voicing", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=41,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="mesma corda"):
        registry.apply(
            "guitar.chord_voicing",
            _midi_with_notes("Guitar", 0, [(0, 480, 40, 96)]),
            seed=1,
        )


def test_physical_guitar_rejects_note_below_lowest_open_string():
    registry = TechniqueRegistry()

    @registry.register("guitar.drop_tuning", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=39,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="corda solta mais grave"):
        registry.apply("guitar.drop_tuning", _midi_with_notes("Guitar", 0, []), seed=1)


def test_physical_guitar_accepts_one_note_per_open_string():
    registry = TechniqueRegistry()

    @registry.register("guitar.chord_voicing", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=64,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    result = registry.apply(
        "guitar.chord_voicing",
        _midi_with_notes("Guitar", 0, [
            (0, 240, 40, 96),
            (0, 240, 45, 92),
            (0, 240, 50, 88),
            (0, 240, 55, 84),
            (0, 240, 59, 80),
        ]),
        seed=1,
    )

    assert [note[2] for note in _note_tuples(result)] == [40, 45, 50, 55, 59, 64]


def test_physical_keys_rejects_single_hand_span_above_limit():
    registry = TechniqueRegistry()

    @registry.register("keys.hand_asynchrony", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=74,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="extensao de mao"):
        registry.apply(
            "keys.hand_asynchrony",
            _midi_with_notes("Piano", 0, [(0, 240, 60, 96)]),
            seed=1,
            parameters={"hand": "right"},
        )


def test_physical_keys_accepts_single_hand_span_at_limit():
    registry = TechniqueRegistry()

    @registry.register("keys.hand_asynchrony", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=73,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    result = registry.apply(
        "keys.hand_asynchrony",
        _midi_with_notes("Piano", 0, [(0, 240, 60, 96)]),
        seed=1,
        parameters={"hand": "right"},
    )

    assert [note[2] for note in _note_tuples(result)] == [60, 73]


def test_physical_keys_accepts_ornament_without_active_chord():
    registry = TechniqueRegistry()

    @registry.register("keys.hand_asynchrony", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=72,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    result = registry.apply(
        "keys.hand_asynchrony",
        _midi_with_notes("Piano", 0, []),
        seed=1,
        parameters={"hand": "right"},
    )

    assert [note[2] for note in _note_tuples(result)] == [72]


def test_physical_keys_rejects_voicing_that_does_not_fit_two_hands():
    registry = TechniqueRegistry()

    @registry.register("keys.hand_asynchrony", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=90,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    with pytest.raises(TechniquePhysicalError, match="duas maos"):
        registry.apply(
            "keys.hand_asynchrony",
            _midi_with_notes("Piano", 0, [
                (0, 240, 40, 96),
                (0, 240, 60, 88),
            ]),
            seed=1,
            parameters={"max_hand_span": 12},
        )


def test_physical_keys_accepts_voicing_split_between_two_hands():
    registry = TechniqueRegistry()

    @registry.register("keys.hand_asynchrony", "technique")
    def apply(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=0,
            note=76,
            velocity=72,
            start_tick=0,
            end_tick=240,
        )
        return mid

    result = registry.apply(
        "keys.hand_asynchrony",
        _midi_with_notes("Piano", 0, [
            (0, 240, 40, 96),
            (0, 240, 52, 88),
        ]),
        seed=1,
        parameters={"max_hand_span": 12},
    )

    assert [note[2] for note in _note_tuples(result)] == [40, 52, 76]


def test_every_registered_technique_exists_in_manual_index():
    idx = build_index(MANUALS_DIR)

    validate_registry_against_index(idx)
    for canonical in SUPPORTED_TECHNIQUES:
        assert idx.get(canonical) is not None


def _technique_index(
    canonical: str,
    tools: dict[str, dict[str, object]],
) -> TechniqueIndex:
    family, name = canonical.split(".", 1)
    return TechniqueIndex((
        Technique(
            canonical=canonical,
            name=name,
            family=family,
            summary="Tecnica de teste.",
            verified=True,
            tools=tools,
            source_manual="manual_teste.md",
        ),
    ))


def _midi_with_ghost_note_window(
    *,
    extra_notes: list[tuple[int, int, int, int]] | None = None,
) -> mido.MidiFile:
    notes = [
        (480, 600, 38, 108),
        (1440, 1560, 38, 108),
    ]
    if extra_notes:
        notes.extend(extra_notes)
    return _midi_with_notes("Drums", 9, notes)


def _new_note_tuples(
    before: mido.MidiFile,
    after: mido.MidiFile,
) -> list[tuple[int, int, int, int, int, int]]:
    remaining = _note_tuples(before)
    new_notes: list[tuple[int, int, int, int, int, int]] = []
    for note in _note_tuples(after):
        if note in remaining:
            remaining.remove(note)
            continue
        new_notes.append(note)
    return new_notes


def _note_tuples(mid: mido.MidiFile) -> list[tuple[int, int, int, int, int, int]]:
    notes: list[tuple[int, int, int, int, int, int]] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault((msg.channel, msg.note), []).append(
                    (tick, msg.velocity)
                )
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = open_notes.get((msg.channel, msg.note))
                if not stack:
                    continue
                start_tick, velocity = stack.pop(0)
                notes.append((
                    track_index,
                    msg.channel,
                    msg.note,
                    start_tick,
                    tick,
                    velocity,
                ))
    return notes


def _continuous_event_tuples(
    mid: mido.MidiFile,
) -> list[tuple[int, str, int, int, int, int]]:
    events: list[tuple[int, str, int, int, int, int]] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "control_change":
                events.append((
                    track_index,
                    msg.type,
                    msg.channel,
                    tick,
                    msg.control,
                    msg.value,
                ))
            elif msg.type == "pitchwheel":
                events.append((
                    track_index,
                    msg.type,
                    msg.channel,
                    tick,
                    0,
                    msg.pitch,
                ))
    return events


def _ornament_registry() -> TechniqueRegistry:
    registry = TechniqueRegistry()

    @registry.register("drums.ghost_notes", "technique")
    def apply_drums(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[1],
            channel=9,
            note=38,
            velocity=32,
            start_tick=120,
            end_tick=180,
        )
        _insert_event(
            mid.tracks[1],
            mido.Message("control_change", channel=9, control=4, value=48),
            tick=120,
        )
        _insert_event(
            mid.tracks[1],
            mido.Message("pitchwheel", channel=9, pitch=-120),
            tick=120,
        )
        return mid

    @registry.register("bass.ghost_notes", "technique")
    def apply_bass(
        mid: mido.MidiFile,
        *,
        context: TechniqueContext,
    ) -> mido.MidiFile:
        _ = context
        _insert_note(
            mid.tracks[2],
            channel=0,
            note=35,
            velocity=28,
            start_tick=240,
            end_tick=300,
        )
        _insert_event(
            mid.tracks[2],
            mido.Message("control_change", channel=0, control=11, value=64),
            tick=240,
        )
        _insert_event(
            mid.tracks[2],
            mido.Message("pitchwheel", channel=0, pitch=80),
            tick=240,
        )
        return mid

    return registry


def _apply_two_ornament_techniques(
    registry: TechniqueRegistry,
    mid: mido.MidiFile,
) -> mido.MidiFile:
    mid = registry.apply("drums.ghost_notes", mid, seed=1)
    return registry.apply("bass.ghost_notes", mid, seed=1)


def _insert_note(
    track: mido.MidiTrack,
    *,
    channel: int,
    note: int,
    velocity: int,
    start_tick: int,
    end_tick: int,
) -> None:
    absolute: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    tick = 0
    for msg in track:
        tick += msg.time
        absolute.append((tick, msg))
    absolute.append((
        start_tick,
        mido.Message("note_on", channel=channel, note=note, velocity=velocity),
    ))
    absolute.append((
        end_tick,
        mido.Message("note_off", channel=channel, note=note, velocity=0),
    ))

    rebuilt = mido.MidiTrack()
    previous_tick = 0
    for absolute_tick, msg in sorted(absolute, key=lambda item: item[0]):
        rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
        previous_tick = absolute_tick
    track[:] = rebuilt


def _insert_event(
    track: mido.MidiTrack,
    msg: mido.Message,
    *,
    tick: int,
) -> None:
    absolute: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    current_tick = 0
    for existing in track:
        current_tick += existing.time
        absolute.append((current_tick, existing))
    absolute.append((tick, msg))

    rebuilt = mido.MidiTrack()
    previous_tick = 0
    for absolute_tick, event in sorted(absolute, key=lambda item: item[0]):
        rebuilt.append(event.copy(time=absolute_tick - previous_tick))
        previous_tick = absolute_tick
    track[:] = rebuilt


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


def _midi_with_notes(
    track_name: str,
    channel: int,
    notes: list[tuple[int, int, int, int]],
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    for start_tick, end_tick, pitch, velocity in notes:
        _insert_note(
            track,
            channel=channel,
            note=pitch,
            velocity=velocity,
            start_tick=start_tick,
            end_tick=end_tick,
        )
    mid.tracks.extend([meta, track])
    return mid


def _midi_with_single_note() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.Message(
        "note_on", channel=0, note=40, velocity=96, time=0
    ))
    track.append(mido.Message(
        "note_off", channel=0, note=40, velocity=0, time=480
    ))
    mid.tracks.extend([meta, track])
    return mid


def _midi_with_note_on_zero_note_off() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.Message(
        "note_on", channel=0, note=40, velocity=96, time=0
    ))
    track.append(mido.Message(
        "note_on", channel=0, note=40, velocity=0, time=480
    ))
    mid.tracks.extend([meta, track])
    return mid


def _midi_with_drums_and_bass() -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))

    drums = mido.MidiTrack()
    drums.append(mido.MetaMessage("track_name", name="Drums", time=0))
    drums.append(mido.Message(
        "note_on", channel=9, note=60, velocity=96, time=0
    ))
    drums.append(mido.Message(
        "note_off", channel=9, note=60, velocity=0, time=480
    ))

    bass = mido.MidiTrack()
    bass.append(mido.MetaMessage("track_name", name="Bass", time=0))
    bass.append(mido.Message(
        "note_on", channel=0, note=40, velocity=96, time=0
    ))
    bass.append(mido.Message(
        "note_off", channel=0, note=40, velocity=0, time=480
    ))

    mid.tracks.extend([meta, drums, bass])
    return mid


def _midi_with_two_notes(
    *,
    first_note: int = 60,
    second_note: int = 64,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    track.append(mido.Message(
        "note_on", channel=9, note=first_note, velocity=96, time=0
    ))
    track.append(mido.Message(
        "note_off", channel=9, note=first_note, velocity=0, time=480
    ))
    track.append(mido.Message(
        "note_on", channel=9, note=second_note, velocity=88, time=0
    ))
    track.append(mido.Message(
        "note_off", channel=9, note=second_note, velocity=0, time=480
    ))
    mid.tracks.extend([meta, track])
    return mid


# ---------------------------------------------------------------------------
# Achados do review conjunto com o Codex no PR #48.
# ---------------------------------------------------------------------------


def _midi_four_backbeats() -> mido.MidiFile:
    """Caixa alta em quatro backbeats — o minimo para haver intervalo."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    prev = 0
    for tick in (480, 1440, 2400, 3360):
        track.append(mido.Message(
            "note_on", note=38, velocity=100, channel=9, time=tick - prev,
        ))
        track.append(mido.Message(
            "note_off", note=38, velocity=0, channel=9, time=60,
        ))
        prev = tick + 60
    mid.tracks.append(track)
    return mid


def _note_on_count(mid: mido.MidiFile) -> int:
    return sum(
        1 for m in mid.tracks[0] if m.type == "note_on" and m.velocity > 0
    )


def test_ghost_notes_density_zero_nao_acrescenta_nota():
    """`density=0.0` desliga a tecnica.

    O loop de selecao acrescentava a candidata e SO ENTAO checava o teto,
    entao `wanted == 0` ainda deixava uma ghost passar. Densidade zero que
    escreve nota torna o parametro mentiroso.
    """
    source = _midi_four_backbeats()
    out = apply_technique(
        "drums.ghost_notes", _midi_four_backbeats(), seed=1, tool="generic",
        parameters={"density": 0.0},
    )
    assert _note_on_count(out) == _note_on_count(source)


def test_ghost_notes_density_cresce_de_forma_monotonica():
    """Densidade maior nunca produz menos ghost que densidade menor."""
    counts = []
    for density in (0.0, 0.25, 0.5, 1.0):
        out = apply_technique(
            "drums.ghost_notes", _midi_four_backbeats(), seed=1,
            tool="generic", parameters={"density": density},
        )
        counts.append(_note_on_count(out))
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_ghost_notes_respeita_velocity_declarada_no_plano():
    """`style.<familia>.parameters.velocity` COMANDA a velocity da ghost.

    Antes, a receita do manual vencia e o parametro do plano era aceito pelo
    schema, validado contra a faixa do manual e depois ignorado na aplicacao.
    """
    out = apply_technique(
        "drums.ghost_notes", _midi_four_backbeats(), seed=1, tool="generic",
        parameters={"velocity": [30, 30]},
    )
    ghosts = [
        m.velocity for m in out.tracks[0]
        if m.type == "note_on" and 0 < m.velocity < 50
    ]
    assert ghosts, "a fixture precisa gerar ghost para o teste valer"
    assert all(v == 30 for v in ghosts)


def test_ghost_notes_sem_velocity_no_plano_usa_a_faixa_do_manual():
    """Contraprova: sem parametro no plano, a receita do manual continua valendo."""
    out = apply_technique(
        "drums.ghost_notes", _midi_four_backbeats(), seed=1, tool="generic",
    )
    ghosts = [
        m.velocity for m in out.tracks[0]
        if m.type == "note_on" and 0 < m.velocity < 50
    ]
    assert ghosts
    assert all(20 <= v <= 45 for v in ghosts)
    assert len(set(ghosts)) > 1, "faixa do manual varia, nao e valor fixo"


def _midi_two_grooves_with_break() -> mido.MidiFile:
    """Duas levadas separadas por quatro compassos de silencio.

    Reproduz a forma real de DEIXE IR: a musica para, fica um vao, e volta.
    O ultimo backbeat antes do vao e o primeiro depois sao consecutivos na
    lista de backbeats, mas nao formam intervalo de groove nenhum.
    """
    tpb = 480
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    ticks = []
    # levada 1: backbeats em 2 e 4 de quatro compassos
    for compasso in range(4):
        for beat in (1, 3):
            ticks.append(compasso * 4 * tpb + beat * tpb)
    # quatro compassos de silencio, depois levada 2
    offset = 8 * 4 * tpb
    for compasso in range(4):
        for beat in (1, 3):
            ticks.append(offset + compasso * 4 * tpb + beat * tpb)
    prev = 0
    for tick in ticks:
        track.append(mido.Message(
            "note_on", note=38, velocity=100, channel=9, time=tick - prev,
        ))
        track.append(mido.Message(
            "note_off", note=38, velocity=0, channel=9, time=60,
        ))
        prev = tick + 60
    mid.tracks.append(track)
    return mid, ticks, tpb


def test_ghost_notes_nao_semeia_no_vao_entre_levadas():
    """Nenhuma ghost pode cair no silencio entre duas levadas.

    `zip(backbeats, backbeats[1:])` emparelhava o ultimo backbeat antes do
    break com o primeiro depois dele, e o loop varria semicolcheias por cima
    do vao inteiro. Em DEIXE IR isso semeava caixa com a nota estrutural mais
    proxima a 18 tempos de distancia.
    """
    source, structural_ticks, tpb = _midi_two_grooves_with_break()
    out = apply_technique(
        "drums.ghost_notes", source, seed=7, tool="generic",
    )

    structural = set(structural_ticks)
    added = []
    tick = 0
    for msg in out.tracks[0]:
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0 and tick not in structural:
            added.append(tick)

    assert added, "a fixture precisa gerar ghost nas levadas para o teste valer"
    gap_start = max(t for t in structural_ticks if t < 8 * 4 * tpb)
    gap_end = min(t for t in structural_ticks if t >= 8 * 4 * tpb)
    no_vao = [t for t in added if gap_start < t < gap_end]
    assert no_vao == [], f"ghosts semeadas no silencio: {no_vao}"


def test_ghost_notes_toda_ghost_fica_perto_de_nota_estrutural():
    """Toda ghost tem vizinho estrutural a no maximo um tempo de distancia.

    Ghost note e ornamento de levada; ghost isolada no vazio nao existe em
    performance real. Mede sobre a bateria real do corpus, nao sobre fixture
    sintetica — fixture regular nao tem break e esconde o bug.
    """
    import bisect

    source = mido.MidiFile("tests/fixtures/corpus_drums/DEIXE IR.mid")
    beat = source.ticks_per_beat

    def drum_onsets(mid):
        out = []
        for tr in mid.tracks:
            tick = 0
            for msg in tr:
                tick += msg.time
                if (
                    msg.type == "note_on"
                    and msg.velocity > 0
                    and getattr(msg, "channel", -1) == 9
                ):
                    out.append((tick, msg.note))
        return out

    base = drum_onsets(source)
    base_set = set(base)
    base_ticks = sorted({t for t, _ in base})

    out = apply_technique(
        "drums.ghost_notes",
        mido.MidiFile("tests/fixtures/corpus_drums/DEIXE IR.mid"),
        seed=7,
        tool="superior_drummer",
        parameters={"density": 0.10},
    )
    added = [item for item in drum_onsets(out) if item not in base_set]
    assert added, "a fixture real precisa gerar ghost para o teste valer"

    orfas = []
    for tick, _pitch in added:
        i = bisect.bisect_left(base_ticks, tick)
        antes = tick - base_ticks[i - 1] if i > 0 else 10**9
        depois = base_ticks[i] - tick if i < len(base_ticks) else 10**9
        if min(antes, depois) > beat:
            orfas.append(tick)
    assert orfas == [], f"ghosts isoladas no vazio: {orfas}"


def test_accent_hierarchy_esta_documentada_mas_fora_do_motor():
    """`drums.accent_hierarchy` sai do motor enquanto a issue #50 nao fecha.

    A tecnica destruia virada: sobre `DEIXE IR.mid` rebaixava 63 das 65
    caixas em contratempo de velocity >= 110 para <= 45, e a mediana dos toms
    de 127 para 67. Decidia a camada so pela posicao metrica, sem nocao de
    virada — e o limiar de virada e lacuna declarada no manual.

    Este teste trava as duas pontas: a tecnica continua no MANUAL (nao
    apagamos conhecimento) e continua FORA do motor (nao aplicamos o defeito).
    Re-registrar sem fechar a #50 quebra aqui.
    """
    from tools.techniques import SUPPORTED_TECHNIQUES, build_index

    assert build_index().get("drums.accent_hierarchy") is not None, (
        "a tecnica tem que continuar documentada no manual"
    )
    assert "drums.accent_hierarchy" not in SUPPORTED_TECHNIQUES, (
        "nao re-registrar enquanto a issue #50 nao fechar"
    )


def test_plano_que_declara_accent_hierarchy_recebe_erro_explicito(tmp_path):
    """Declarar a tecnica parada tem que dar erro, nunca no-op silencioso.

    Tecnica documentada que o motor aceita e ignora e o vicio que esta base
    ja rejeitou duas vezes (`_identity_apply` e o gerador de bateria de
    andaime). O usuario precisa saber que pediu algo que nao vai acontecer.
    """
    import json as _json

    from tools.brief_ref import brief_sha256
    from tools.plan import (
        ArrangementPlan,
        BriefRef,
        FamilyStyle,
        PlanValidationError,
        SourceMidi,
        StyleTechnique,
        validate,
    )

    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        _json.dumps(
            {"style": {"drums": {"authorized_techniques": ["drums.accent_hierarchy"]}}},
        ),
        encoding="utf-8",
    )

    plan = ArrangementPlan(
        version=1,
        seed=1,
        source_midi=SourceMidi(path="/tmp/x.mid", sha256="0" * 64),
        route="cinematica_emocional",
        sections=[],
        elements=[],
        brief_ref=BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path)),
    )
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.accent_hierarchy")],
            parameters={},
        ),
    }

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].name"
    assert "not implemented by the engine" in exc.value.message
