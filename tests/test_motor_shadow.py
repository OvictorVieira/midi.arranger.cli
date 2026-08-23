"""Testes dos geradores motor e shadow (US-007)."""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord, GuitarNote
from tools.constants import GATE_RATIOS, REGISTER_BANDS, VELOCITY_RANGES
from tools.palette.rhythmic import (
    DEFAULT_MOTOR_REGISTER,
    DEFAULT_SHADOW_REGISTER,
    MOTOR_DEFAULT_ARTICULATION,
    MOTOR_DEFAULT_SUBDIVISION,
    MOTOR_FILTER_CYCLE_BARS_ALLOWED,
    MOTOR_GAP_MIN_STEPS,
    MOTOR_PATTERNS,
    MOTOR_ROLES,
    MOTOR_SUBDIVISION_ALLOWED,
    RHYTHMIC_FILTER_CC,
    RHYTHMIC_FILTER_HI,
    RHYTHMIC_FILTER_LO,
    SHADOW_DEFAULT_ARTICULATION,
    SHADOW_DEFAULT_OCTAVE_SHIFT,
    SHADOW_DEFAULT_TAIL_NOTES,
    SHADOW_MAX_VELOCITY,
    SHADOW_MIN_VELOCITY,
    SHADOW_OCTAVE_SHIFT_ALLOWED,
    SHADOW_PHRASE_END_GAP_S,
    SHADOW_ROLES,
    STEPS_PER_BAR,
    RhythmicCCEvent,
    RhythmicLayer,
    RhythmicNote,
    _assert_motor_catalog_has_gaps,
    _clamp_pitch_to_register,
    _max_gap_run,
    _phrases_by_silence,
    _phrases_by_unisons,
    _pick_motor_pattern,
    _validate_motor_custom_steps,
    generate_motor,
    generate_shadow,
)
from tools.plan import PlanSection

BAR_S = 2.0


def _bar(index: int, chord: Chord | None = None) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=chord,
    )


def _analysis(
    n_bars: int = 8,
    key_root: int = 0,
    quality: str = "minor",
    guitar_notes: list[GuitarNote] | None = None,
    guitar_unison_positions: list[float] | None = None,
) -> Analysis:
    bars = [_bar(i, Chord(root=key_root, quality=quality)) for i in range(n_bars)]
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=guitar_unison_positions or [],
        track_names=[],
        guitar_notes=guitar_notes or [],
    )


def _section(
    label: str = "S1", kind: str = "verse",
    start_bar: int = 0, end_bar: int = 8,
) -> PlanSection:
    return PlanSection(
        label=label, kind=kind, start_bar=start_bar, end_bar=end_bar,
        source="marker", protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


# ===========================================================================
# MOTOR
# ===========================================================================

def test_motor_role_vocabulary():
    assert set(MOTOR_ROLES) == {"motor"}


def test_motor_subdivision_vocabulary():
    assert set(MOTOR_SUBDIVISION_ALLOWED) == {"eighth", "sixteenth"}


def test_motor_default_subdivision_is_sixteenth():
    assert MOTOR_DEFAULT_SUBDIVISION == "sixteenth"


def test_motor_default_articulation_in_gate_ratios():
    assert MOTOR_DEFAULT_ARTICULATION in GATE_RATIOS


def test_default_motor_register_is_mid_band():
    assert REGISTER_BANDS["mid"] == DEFAULT_MOTOR_REGISTER


# --- catalogo -------------------------------------------------------------

def test_motor_catalog_has_gap_per_ac():
    for subdivision, catalog in MOTOR_PATTERNS.items():
        for i, pattern in enumerate(catalog):
            assert len(pattern) == STEPS_PER_BAR, (
                f"pattern {subdivision}[{i}] wrong length"
            )
            gap = _max_gap_run(pattern)
            assert gap >= MOTOR_GAP_MIN_STEPS, (
                f"pattern {subdivision}[{i}] has no lacuna (max gap {gap})"
            )


def test_motor_eighth_patterns_only_activate_even_steps():
    for i, pattern in enumerate(MOTOR_PATTERNS["eighth"]):
        for step_idx, active in enumerate(pattern):
            if active:
                assert step_idx % 2 == 0, (
                    f"eighth pattern[{i}] activates odd step {step_idx}"
                )


def test_assert_motor_catalog_guard_reruns_without_error():
    _assert_motor_catalog_has_gaps()


def test_assert_motor_catalog_rejects_wrong_length(monkeypatch):
    import tools.palette.rhythmic as R
    monkeypatch.setattr(R, "MOTOR_PATTERNS", {"eighth": ((1, 0),)})
    with pytest.raises(ValueError, match="steps"):
        R._assert_motor_catalog_has_gaps()


def test_assert_motor_catalog_rejects_no_gap(monkeypatch):
    import tools.palette.rhythmic as R
    monkeypatch.setattr(
        R, "MOTOR_PATTERNS",
        {"sixteenth": (tuple([1] * STEPS_PER_BAR),)},
    )
    with pytest.raises(ValueError, match="no gap"):
        R._assert_motor_catalog_has_gaps()


def test_assert_motor_catalog_rejects_odd_step_in_eighth(monkeypatch):
    import tools.palette.rhythmic as R
    # padrao com step 1 (odd) ativo — invalido para eighth
    bad = (0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    monkeypatch.setattr(R, "MOTOR_PATTERNS", {"eighth": (bad,)})
    with pytest.raises(ValueError, match="odd step"):
        R._assert_motor_catalog_has_gaps()


# --- helpers puros --------------------------------------------------------

def test_pick_motor_pattern_returns_from_catalog():
    import random as _r
    catalog = ((1, 0, 1), (0, 1, 0))
    pattern = _pick_motor_pattern(catalog, _r.Random(0))
    assert pattern in catalog


def test_pick_motor_pattern_empty_raises():
    import random as _r
    with pytest.raises(ValueError, match="empty motor pattern catalog"):
        _pick_motor_pattern((), _r.Random(0))


def test_validate_motor_custom_steps_accepts_valid_sixteenth():
    steps = (1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1)  # gap of 3
    v = _validate_motor_custom_steps(steps, "sixteenth")
    assert len(v) == STEPS_PER_BAR


def test_validate_motor_custom_steps_rejects_wrong_length():
    with pytest.raises(ValueError, match="length"):
        _validate_motor_custom_steps((1, 0), "sixteenth")


def test_validate_motor_custom_steps_rejects_no_gap():
    steps = tuple([1] * STEPS_PER_BAR)
    with pytest.raises(ValueError, match="no gap"):
        _validate_motor_custom_steps(steps, "sixteenth")


def test_validate_motor_custom_steps_rejects_odd_step_in_eighth():
    # gap ok, mas step 1 ativo => invalido para eighth
    steps = (1, 1, 1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0)
    with pytest.raises(ValueError, match="odd step"):
        _validate_motor_custom_steps(steps, "eighth")


# --- gerador — validacao de args -----------------------------------------

def test_generate_motor_layers_lt_one_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="layers"):
        generate_motor(ana, _section(), layers=0)


def test_generate_motor_bad_role_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="role"):
        generate_motor(ana, _section(), role="pad")


def test_generate_motor_bad_subdivision_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="subdivision"):
        generate_motor(ana, _section(), subdivision="quarter")


def test_generate_motor_bad_filter_cycle_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="filter_cycle_bars"):
        generate_motor(ana, _section(), filter_cycle_bars=3)


def test_generate_motor_bad_velocity_cycle_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="velocity_cycle_bars"):
        generate_motor(ana, _section(), velocity_cycle_bars=0)


def test_generate_motor_all_allowed_filter_cycles_accepted():
    ana = _analysis(16)
    for cycle in MOTOR_FILTER_CYCLE_BARS_ALLOWED:
        layers = generate_motor(
            ana, _section(end_bar=16), filter_cycle_bars=cycle, seed=0,
        )
        assert layers[0].cc_events, f"cycle {cycle} produced no CC"


# --- comportamento basico ------------------------------------------------

def test_generate_motor_returns_layer_per_request():
    ana = _analysis(4)
    layers = generate_motor(ana, _section(end_bar=4), layers=3, seed=0)
    assert len(layers) == 3
    assert [layer.index for layer in layers] == [0, 1, 2]
    for layer in layers:
        assert isinstance(layer, RhythmicLayer)


def test_generate_motor_emits_notes_in_register():
    ana = _analysis(4)
    reg = REGISTER_BANDS["mid"]
    layers = generate_motor(ana, _section(end_bar=4), register=reg, seed=0)
    assert layers[0].notes
    for note in layers[0].notes:
        assert isinstance(note, RhythmicNote)
        assert reg[0] <= note.pitch <= reg[1]
        assert 1 <= note.velocity <= 127


def test_generate_motor_notes_sorted_by_time():
    ana = _analysis(4)
    layers = generate_motor(ana, _section(end_bar=4), seed=0)
    starts = [n.start_s for n in layers[0].notes]
    assert starts == sorted(starts)


def test_generate_motor_empty_section_returns_empty_layers():
    ana = _analysis(4)
    layers = generate_motor(
        ana, _section(start_bar=10, end_bar=12), layers=2, seed=0,
    )
    assert len(layers) == 2
    for layer in layers:
        assert layer.notes == ()
        assert layer.cc_events == ()


def test_generate_motor_bar_without_chord_emits_nothing_that_bar():
    ana = _analysis(4)
    ana.bars[1] = _bar(1, chord=None)
    layers = generate_motor(ana, _section(end_bar=4), seed=0)
    for n in layers[0].notes:
        assert not (BAR_S <= n.start_s < 2 * BAR_S)


def test_generate_motor_pattern_has_gap_in_output():
    """AC: 'Teste verifica que motor tem lacuna e nao e 100% preenchido'."""
    ana = _analysis(1)
    layers = generate_motor(
        ana, _section(end_bar=1), subdivision="sixteenth", seed=0,
    )
    step_dur = BAR_S / STEPS_PER_BAR
    hits = [False] * STEPS_PER_BAR
    for n in layers[0].notes:
        idx = int(round(n.start_s / step_dur))
        if 0 <= idx < STEPS_PER_BAR:
            hits[idx] = True
    max_gap = 0
    run = 0
    for hit in hits:
        if not hit:
            run += 1
            max_gap = max(max_gap, run)
        else:
            run = 0
    assert max_gap >= MOTOR_GAP_MIN_STEPS


def test_generate_motor_not_100_percent_filled():
    """AC: 'Teste verifica que motor... nao e 100% preenchido'."""
    ana = _analysis(4)
    layers = generate_motor(ana, _section(end_bar=4), seed=0)
    notes = layers[0].notes
    # Total possivel para 4 bars 1/16 seria 64 notas
    assert len(notes) < 4 * STEPS_PER_BAR
    # Tambem para 1/8 (max 8 por bar)
    layers8 = generate_motor(
        ana, _section(end_bar=4), subdivision="eighth", seed=0,
    )
    assert len(layers8[0].notes) < 4 * (STEPS_PER_BAR // 2)


def test_generate_motor_eighth_emits_only_on_downbeats():
    ana = _analysis(2)
    layers = generate_motor(
        ana, _section(end_bar=2), subdivision="eighth", seed=0,
    )
    step_dur = BAR_S / STEPS_PER_BAR
    for n in layers[0].notes:
        bar_pos_s = n.start_s % BAR_S
        step_idx = int(round(bar_pos_s / step_dur))
        assert step_idx % 2 == 0, (
            f"eighth motor emitted odd step at {n.start_s}"
        )


def test_generate_motor_sixteenth_denser_than_eighth():
    ana = _analysis(4)
    n16 = generate_motor(
        ana, _section(end_bar=4), subdivision="sixteenth", seed=0,
    )[0].notes
    n8 = generate_motor(
        ana, _section(end_bar=4), subdivision="eighth", seed=0,
    )[0].notes
    assert len(n16) > len(n8)


def test_generate_motor_emits_cc74():
    ana = _analysis(8)
    layers = generate_motor(ana, _section(end_bar=8), seed=0)
    events = layers[0].cc_events
    assert events
    for ev in events:
        assert isinstance(ev, RhythmicCCEvent)
        assert ev.cc == RHYTHMIC_FILTER_CC
        assert RHYTHMIC_FILTER_LO - 1 <= ev.value <= RHYTHMIC_FILTER_HI + 1


def test_generate_motor_no_notes_means_no_cc():
    ana = _analysis(4)
    layers = generate_motor(
        ana, _section(start_bar=10, end_bar=12), seed=0,
    )
    for layer in layers:
        assert layer.notes == ()
        assert layer.cc_events == ()


def test_generate_motor_articulation_ghost_shortens_gate():
    ana = _analysis(4)
    staccato = generate_motor(
        ana, _section(end_bar=4), articulation="staccato", seed=0,
    )[0].notes
    ghost = generate_motor(
        ana, _section(end_bar=4), articulation="ghost", seed=0,
    )[0].notes

    def avg_dur(ns: list[RhythmicNote]) -> float:
        return sum(n.end_s - n.start_s for n in ns) / max(1, len(ns))

    assert avg_dur(list(ghost)) < avg_dur(list(staccato))


def test_generate_motor_unknown_articulation_falls_back_to_staccato():
    ana = _analysis(4)
    unknown = generate_motor(
        ana, _section(end_bar=4), articulation="legato", seed=0,
    )[0].notes
    default = generate_motor(
        ana, _section(end_bar=4), articulation=MOTOR_DEFAULT_ARTICULATION, seed=0,
    )[0].notes
    assert [round(n.end_s - n.start_s, 6) for n in unknown] == [
        round(n.end_s - n.start_s, 6) for n in default
    ]


def test_generate_motor_velocity_bucket_softer_than_arp():
    """Motor senta ABAIXO do arp — bucket tied_soft (50-75) e menor que
    normal (82-105) do arp. AC: 'da movimento sem competir com o riff'."""
    ana = _analysis(4)
    notes = generate_motor(ana, _section(end_bar=4), seed=0)[0].notes
    avg_v = sum(n.velocity for n in notes) / len(notes)
    normal_lo, normal_hi = VELOCITY_RANGES["normal"]
    normal_mid = (normal_lo + normal_hi) // 2
    assert avg_v < normal_mid


def test_generate_motor_custom_steps_used():
    ana = _analysis(1)
    steps = (1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0)  # gap 3+ steps
    layers = generate_motor(
        ana, _section(end_bar=1),
        subdivision="sixteenth", custom_steps=steps, seed=0,
    )
    assert layers[0].notes
    step_dur = BAR_S / STEPS_PER_BAR
    onset_indices = sorted(
        {int(round(n.start_s / step_dur)) for n in layers[0].notes}
    )
    assert onset_indices == [0, 6, 12]


def test_generate_motor_custom_steps_reject_no_gap():
    ana = _analysis(1)
    with pytest.raises(ValueError, match="no gap"):
        generate_motor(
            ana, _section(end_bar=1),
            custom_steps=tuple([1] * STEPS_PER_BAR), seed=0,
        )


def test_generate_motor_same_seed_same_output():
    ana = _analysis(8)
    a = generate_motor(ana, _section(end_bar=8), seed=123)
    b = generate_motor(ana, _section(end_bar=8), seed=123)
    assert a == b


def test_generate_motor_different_seed_may_differ():
    ana = _analysis(8)
    a = generate_motor(ana, _section(end_bar=8), layers=2, seed=1)
    b = generate_motor(ana, _section(end_bar=8), layers=2, seed=2)
    # layer 1 stagger deve diferir entre seeds
    assert a[1].notes[0].start_s != b[1].notes[0].start_s


def test_generate_motor_note_duration_floors_at_min():
    from tools.palette.rhythmic import MIN_NOTE_DURATION_S
    # bar minusculo => note_dur pode ficar < MIN
    bars = [BarAnalysis(
        index=i, start=i * 0.01, end=(i + 1) * 0.01,
        chord=Chord(root=0, quality="minor"),
    ) for i in range(2)]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[], guitar_notes=[],
    )
    section = PlanSection(
        label="S", kind="verse", start_bar=0, end_bar=2,
        source="marker", protagonist="texture",
        energy={"densidade": 3, "impacto": 3, "largura": 3, "altura": 3, "instabilidade": 3},
    )
    layers = generate_motor(ana, section, articulation="ghost", seed=0)
    for n in layers[0].notes:
        assert n.end_s - n.start_s == pytest.approx(MIN_NOTE_DURATION_S)


def test_generate_motor_skips_note_past_bar_end():
    """Bar minusculo faz o jitter + stagger empurrar o candidate depois
    de bar.end. O gerador nao emite nota — nao ha ramo de erro."""
    bars = [BarAnalysis(
        index=0, start=0.0, end=0.001,
        chord=Chord(root=0, quality="minor"),
    )]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[], guitar_notes=[],
    )
    section = PlanSection(
        label="S", kind="verse", start_bar=0, end_bar=1,
        source="marker", protagonist="texture",
        energy={"densidade": 3, "impacto": 3, "largura": 3, "altura": 3, "instabilidade": 3},
    )
    layers = generate_motor(ana, section, seed=0)
    # Aceitavel emitir zero ou uma nota; nenhuma pode passar do bar_end.
    for n in layers[0].notes:
        assert n.start_s < bars[0].end


# ===========================================================================
# SHADOW
# ===========================================================================

def test_shadow_role_vocabulary():
    assert set(SHADOW_ROLES) == {"shadow"}


def test_shadow_octave_shift_vocabulary():
    assert set(SHADOW_OCTAVE_SHIFT_ALLOWED) == {-12, 12}


def test_shadow_default_octave_shift_is_up():
    assert SHADOW_DEFAULT_OCTAVE_SHIFT == 12


def test_default_shadow_register_is_mid_band():
    assert REGISTER_BANDS["mid"] == DEFAULT_SHADOW_REGISTER


def test_shadow_default_articulation_in_gate_ratios():
    assert SHADOW_DEFAULT_ARTICULATION in GATE_RATIOS


# --- helpers puros --------------------------------------------------------

def test_clamp_pitch_to_register_transposes_up():
    assert _clamp_pitch_to_register(30, (48, 71)) == 30 + 12 * 2  # 54


def test_clamp_pitch_to_register_transposes_down():
    assert _clamp_pitch_to_register(120, (48, 71)) == 120 - 12 * 5  # 60


def test_clamp_pitch_to_register_inside_returns_input():
    assert _clamp_pitch_to_register(60, (48, 71)) == 60


def test_clamp_pitch_to_register_narrow_range_returns_none():
    # register de 1 semitom que nao acomoda nenhuma oitava de 60
    # register (61, 61) — 60+12=72 nao cabe; 60-12=48 nao cabe; None
    assert _clamp_pitch_to_register(60, (61, 61)) is None


def test_phrases_by_unisons_empty_guitar_notes():
    assert _phrases_by_unisons([], [], 10.0) == []


def test_phrases_by_unisons_no_unisons_returns_empty():
    guitar = [GuitarNote(start=0.0, pitch=40, track="G")]
    assert _phrases_by_unisons(guitar, [], 10.0) == []


def test_phrases_by_unisons_splits_by_unison_positions():
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G"),
        GuitarNote(start=1.0, pitch=41, track="G"),  # frase 1
        GuitarNote(start=2.0, pitch=42, track="G"),  # inicia frase 2 (unisono em 2.0)
        GuitarNote(start=3.0, pitch=43, track="G"),
    ]
    phrases = _phrases_by_unisons(guitar, [2.0], 4.0)
    assert len(phrases) == 2
    assert [gn.pitch for gn in phrases[0]] == [40, 41]
    assert [gn.pitch for gn in phrases[1]] == [42, 43]


def test_phrases_by_unisons_pre_phrase_when_first_unison_late():
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G"),
        GuitarNote(start=1.0, pitch=41, track="G"),
        GuitarNote(start=2.0, pitch=42, track="G"),
    ]
    # Se o unisono comeca em 1.5, notas antes formam pre-frase.
    phrases = _phrases_by_unisons(guitar, [1.5], 4.0)
    assert len(phrases) == 2
    assert [gn.pitch for gn in phrases[0]] == [40, 41]
    assert [gn.pitch for gn in phrases[1]] == [42]


def test_phrases_by_silence_splits_by_gap():
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G"),
        GuitarNote(start=0.2, pitch=41, track="G"),   # gap 0.2s < min => same phrase
        GuitarNote(start=1.5, pitch=42, track="G"),   # gap 1.3s >= min => nova frase
        GuitarNote(start=1.6, pitch=43, track="G"),
    ]
    phrases = _phrases_by_silence(guitar, min_gap_s=0.5)
    assert len(phrases) == 2
    assert [gn.pitch for gn in phrases[0]] == [40, 41]
    assert [gn.pitch for gn in phrases[1]] == [42, 43]


def test_phrases_by_silence_empty_returns_empty():
    assert _phrases_by_silence([], 0.5) == []


def test_phrases_by_silence_single_note_returns_single_phrase():
    guitar = [GuitarNote(start=0.0, pitch=40, track="G")]
    phrases = _phrases_by_silence(guitar, 0.5)
    assert len(phrases) == 1
    assert phrases[0] == guitar


# --- gerador — validacao de args -----------------------------------------

def test_generate_shadow_layers_lt_one_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="layers"):
        generate_shadow(ana, _section(), layers=0)


def test_generate_shadow_bad_role_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="role"):
        generate_shadow(ana, _section(), role="motor")


def test_generate_shadow_bad_octave_shift_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="octave_shift"):
        generate_shadow(ana, _section(), octave_shift=7)


def test_generate_shadow_bad_tail_notes_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="tail_notes"):
        generate_shadow(ana, _section(), tail_notes=0)


def test_generate_shadow_bad_phrase_end_gap_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="phrase_end_gap_s"):
        generate_shadow(ana, _section(), phrase_end_gap_s=0.0)


def test_generate_shadow_empty_section_returns_empty():
    ana = _analysis(4)
    layers = generate_shadow(
        ana, _section(start_bar=10, end_bar=12), layers=2, seed=0,
    )
    assert len(layers) == 2
    for layer in layers:
        assert layer.notes == ()
        assert layer.cc_events == ()


def test_generate_shadow_no_guitar_notes_returns_empty():
    ana = _analysis(4, guitar_notes=[], guitar_unison_positions=[])
    layers = generate_shadow(ana, _section(end_bar=4), seed=0)
    assert layers[0].notes == ()


# --- comportamento nucleo ------------------------------------------------

def test_generate_shadow_emits_only_at_phrase_tails():
    """AC: 'shadow so emite nota nos ultimos eventos de cada frase de
    guitarra'.

    Constroi duas frases via unisons; verifica que shadow so aparece
    nos ultimos N eventos de cada frase.
    """
    # Frase A (bar 0): notas em 0.0, 0.5, 1.0, 1.5
    # Frase B (bar 1..): unisono em 2.0, notas em 2.0, 2.5, 3.0, 3.5
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G1"),
        GuitarNote(start=0.5, pitch=41, track="G1"),
        GuitarNote(start=1.0, pitch=42, track="G1"),
        GuitarNote(start=1.5, pitch=43, track="G1"),  # ultima da frase A
        GuitarNote(start=2.0, pitch=44, track="G1"),
        GuitarNote(start=2.5, pitch=45, track="G1"),
        GuitarNote(start=3.0, pitch=46, track="G1"),
        GuitarNote(start=3.5, pitch=47, track="G1"),  # ultima da frase B
    ]
    ana = _analysis(
        4, guitar_notes=guitar, guitar_unison_positions=[2.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=4),
        tail_notes=2,
        register=(20, 100),
        seed=0,
    )
    onsets = sorted({n.start_s for n in layers[0].notes})
    # Deve conter os ultimos 2 onsets de cada frase: {1.0, 1.5, 3.0, 3.5}
    assert onsets == [1.0, 1.5, 3.0, 3.5]


def test_generate_shadow_shift_up_transposes_pitch():
    guitar = [
        GuitarNote(start=1.0, pitch=40, track="G1"),
        GuitarNote(start=1.5, pitch=42, track="G1"),
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        octave_shift=12, tail_notes=2,
        register=(0, 127), seed=0,
    )
    onset_to_pitch = {n.start_s: n.pitch for n in layers[0].notes}
    assert onset_to_pitch[1.0] == 40 + 12
    assert onset_to_pitch[1.5] == 42 + 12


def test_generate_shadow_shift_down_transposes_pitch():
    guitar = [
        GuitarNote(start=1.0, pitch=60, track="G1"),
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        octave_shift=-12, tail_notes=1,
        register=(0, 127), seed=0,
    )
    assert layers[0].notes[0].pitch == 60 - 12


def test_generate_shadow_pitch_clamped_to_register():
    # Guitarra em 60; +12 = 72; register (30, 71) => 72 nao cabe, tenta -12
    guitar = [GuitarNote(start=1.0, pitch=60, track="G1")]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        octave_shift=12, tail_notes=1,
        register=(30, 71), seed=0,
    )
    assert layers[0].notes[0].pitch == 60


def test_generate_shadow_narrow_register_drops_note():
    # register (61, 61) => 60+12=72 nao cabe; -12=48 nao cabe; None => descarta
    guitar = [GuitarNote(start=1.0, pitch=60, track="G1")]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        octave_shift=12, tail_notes=1,
        register=(61, 61), seed=0,
    )
    assert layers[0].notes == ()


def test_generate_shadow_envelope_differs_from_guitar():
    """AC: 'shadow muda envelope, oitava ou timbre em relacao a guitarra —
    nunca dobra identico'.

    Verifica que velocity do shadow < SHADOW_MAX_VELOCITY (envelope
    diferente) e que dura mais que uma piscada (release mais longo)."""
    guitar = [
        GuitarNote(start=1.0, pitch=60, track="G1"),
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        tail_notes=1, register=(30, 100), seed=0,
    )
    n = layers[0].notes[0]
    assert SHADOW_MIN_VELOCITY <= n.velocity <= SHADOW_MAX_VELOCITY
    assert n.velocity < SHADOW_MAX_VELOCITY  # envelope reduzido (default offset -25)
    assert n.end_s - n.start_s > 0.05  # release sensivel


def test_generate_shadow_fallback_uses_silence_gap_when_no_unison():
    """Quando a secao nao tem unisono, o gerador usa `phrase_end_gap_s`
    como criterio de fim de frase."""
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G1"),
        GuitarNote(start=0.1, pitch=41, track="G1"),
        # gap 1.0s >= 0.5s = fim de frase A
        GuitarNote(start=1.1, pitch=42, track="G1"),
        GuitarNote(start=1.2, pitch=43, track="G1"),
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        tail_notes=1, register=(0, 127), seed=0,
    )
    onsets = sorted({n.start_s for n in layers[0].notes})
    # Ultima nota de cada frase (A: 0.1, B: 1.2)
    assert onsets == [0.1, 1.2]


def test_generate_shadow_multiple_layers_stagger_differs():
    guitar = [GuitarNote(start=1.0, pitch=60, track="G1")]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        layers=2, register=(30, 100), seed=0,
    )
    assert layers[0].notes[0].start_s != layers[1].notes[0].start_s


def test_generate_shadow_same_seed_same_output():
    guitar = [GuitarNote(start=1.0, pitch=60, track="G1")]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    a = generate_shadow(ana, _section(end_bar=2), seed=7)
    b = generate_shadow(ana, _section(end_bar=2), seed=7)
    assert a == b


def test_generate_shadow_end_not_past_next_guitar():
    """Se a proxima nota de guitarra chega antes do fim natural da nota
    do shadow, a nota e cortada."""
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G1"),  # tail
        GuitarNote(start=0.05, pitch=41, track="G1"),  # tail 2
        GuitarNote(start=1.0, pitch=42, track="G1"),  # start of next phrase
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.9],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        tail_notes=1, note_duration_s=5.0,
        register=(30, 100), seed=0,
    )
    # A ultima nota da frase A (t=0.05) tem shadow que nao pode passar
    # de 1.0 (proxima guitarra)
    for n in layers[0].notes:
        if n.start_s < 1.0:
            assert n.end_s <= 1.0


def test_generate_shadow_default_articulation_is_sustained():
    # AC "envelope diferente" — shadow default sustained tem release longo
    assert SHADOW_DEFAULT_ARTICULATION == "sustained"


def test_generate_shadow_unknown_articulation_falls_back():
    guitar = [GuitarNote(start=1.0, pitch=60, track="G1")]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[0.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        articulation="legato",
        tail_notes=1, register=(30, 100), seed=0,
    )
    assert layers[0].notes  # nao explode; usa fallback sustained


def test_generate_shadow_phrase_end_gap_default_is_positive():
    assert SHADOW_PHRASE_END_GAP_S > 0


def test_generate_shadow_default_tail_notes_is_two():
    assert SHADOW_DEFAULT_TAIL_NOTES == 2


def test_generate_shadow_notes_sorted_by_time():
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G1"),
        GuitarNote(start=0.5, pitch=41, track="G1"),
        GuitarNote(start=1.5, pitch=42, track="G1"),
        GuitarNote(start=1.6, pitch=43, track="G1"),
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[1.0],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        register=(30, 100), seed=0,
    )
    starts = [n.start_s for n in layers[0].notes]
    assert starts == sorted(starts)


def test_generate_shadow_pre_phrase_included_when_unisons_late():
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G1"),
        GuitarNote(start=0.5, pitch=41, track="G1"),  # pre-phrase tail
        GuitarNote(start=1.5, pitch=42, track="G1"),
        GuitarNote(start=1.9, pitch=43, track="G1"),  # phrase-B tail
    ]
    ana = _analysis(
        2, guitar_notes=guitar, guitar_unison_positions=[1.5],
    )
    layers = generate_shadow(
        ana, _section(end_bar=2),
        tail_notes=1, register=(0, 127), seed=0,
    )
    onsets = sorted({n.start_s for n in layers[0].notes})
    assert onsets == [0.5, 1.9]
