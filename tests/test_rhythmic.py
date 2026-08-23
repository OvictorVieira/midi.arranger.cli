"""Testes do gerador de arp / rhythmic_machine (US-006)."""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord, GuitarNote
from tools.constants import GATE_RATIOS, REGISTER_BANDS, VELOCITY_RANGES
from tools.palette.rhythmic import (
    DEFAULT_RHYTHMIC_ARTICULATION,
    DEFAULT_RHYTHMIC_REGISTER,
    RHYTHMIC_BASE_VELOCITY_BUCKET,
    RHYTHMIC_FILTER_CC,
    RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED,
    RHYTHMIC_FILTER_HI,
    RHYTHMIC_FILTER_LO,
    RHYTHMIC_GAP_MIN_STEPS,
    RHYTHMIC_INTERLOCK_MAX_SHIFT_STEPS,
    RHYTHMIC_INTERLOCK_WINDOW_S,
    RHYTHMIC_MUTATE_ALLOWED,
    RHYTHMIC_PATTERN_BARS_ALLOWED,
    RHYTHMIC_PATTERNS,
    RHYTHMIC_ROLES,
    STEPS_PER_BAR,
    RhythmicCCEvent,
    RhythmicLayer,
    RhythmicNote,
    _assert_catalog_has_gaps,
    _chord_pitches_in_register,
    _max_gap_run,
    _pick_window_patterns,
    _resolve_interlock_onset,
    _snake_index,
    _validate_custom_steps,
    generate_rhythmic,
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
) -> Analysis:
    bars = [_bar(i, Chord(root=key_root, quality=quality)) for i in range(n_bars)]
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=guitar_notes or [],
    )


def _section(
    label: str = "S1", kind: str = "verse",
    start_bar: int = 0, end_bar: int = 8,
) -> PlanSection:
    return PlanSection(
        label=label, kind=kind, start_bar=start_bar, end_bar=end_bar,
        source="marker", protagonist="rhythm_machine",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


# --- vocabulario / basicos --------------------------------------------------

def test_rhythmic_roles_vocabulary():
    assert set(RHYTHMIC_ROLES) == {"arp", "rhythmic_machine"}


def test_default_rhythmic_register_is_high_band():
    assert REGISTER_BANDS["high"] == DEFAULT_RHYTHMIC_REGISTER


def test_default_articulation_is_staccato():
    assert DEFAULT_RHYTHMIC_ARTICULATION == "staccato"
    assert DEFAULT_RHYTHMIC_ARTICULATION in GATE_RATIOS


def test_pattern_bars_allowed_is_one_or_two():
    assert set(RHYTHMIC_PATTERN_BARS_ALLOWED) == {1, 2}


def test_mutate_allowed_is_four_or_eight():
    assert set(RHYTHMIC_MUTATE_ALLOWED) == {4, 8}


# --- catalogo de padroes ----------------------------------------------------

def test_catalog_has_gap_of_min_rest_steps():
    """AC: 'padrao de 1 ou 2 compassos com uma lacuna memoravel'."""
    for bars, catalog in RHYTHMIC_PATTERNS.items():
        for i, pattern in enumerate(catalog):
            assert len(pattern) == bars * STEPS_PER_BAR
            gap = _max_gap_run(pattern)
            assert gap >= RHYTHMIC_GAP_MIN_STEPS, (
                f"pattern {bars}bar[{i}] has no lacuna (max gap = {gap})"
            )


def test_assert_catalog_guard_reruns_without_error():
    _assert_catalog_has_gaps()


def test_max_gap_run_basic():
    assert _max_gap_run((1, 0, 0, 0, 1)) == 3
    assert _max_gap_run((1, 1, 1)) == 0
    assert _max_gap_run(()) == 0
    assert _max_gap_run((0, 0, 1, 0, 0, 0, 0)) == 4


# --- helpers puros ----------------------------------------------------------

def test_snake_index_up_and_down():
    # 3 pitches: 0,1,2,1,0,1,2,1,...
    seq = [_snake_index(i, 3) for i in range(8)]
    assert seq == [0, 1, 2, 1, 0, 1, 2, 1]


def test_snake_index_single_pitch_always_zero():
    for i in range(5):
        assert _snake_index(i, 1) == 0
    assert _snake_index(0, 0) == 0


def test_chord_pitches_in_register_covers_multiple_octaves():
    chord = Chord(root=0, quality="minor")  # C, Eb, G
    reg = REGISTER_BANDS["high"]  # (72, 127)
    pitches = _chord_pitches_in_register(chord, reg)
    # C5=72, Eb5=75, G5=79, C6=84, etc.
    assert 72 in pitches
    assert 75 in pitches
    assert 79 in pitches
    assert all(reg[0] <= p <= reg[1] for p in pitches)


def test_chord_pitches_in_register_falls_back_when_empty():
    chord = Chord(root=1, quality="minor")  # C#, E, G#
    reg = (72, 72)  # apenas C5 cabe
    pitches = _chord_pitches_in_register(chord, reg)
    # nenhum grau de C# cabe em [72,72] => fallback [lo]
    assert pitches == [72]


def test_validate_custom_steps_accepts_valid_pattern():
    steps = (1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1)  # gap de 4 (2-5? no, 1-3=3 zeros, 6-8=3 zeros)
    validated = _validate_custom_steps(steps, pattern_bars=1)
    assert len(validated) == STEPS_PER_BAR
    assert all(v in (0, 1) for v in validated)


def test_validate_custom_steps_rejects_wrong_length():
    with pytest.raises(ValueError, match="length"):
        _validate_custom_steps((1, 0, 1), pattern_bars=1)


def test_validate_custom_steps_rejects_no_gap():
    # 16 steps sem gap de 3 consecutivos
    steps = (1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1)  # max gap = 1
    with pytest.raises(ValueError, match="no gap"):
        _validate_custom_steps(steps, pattern_bars=1)


def test_pick_window_patterns_avoids_immediate_repeat():
    import random as _r
    catalog = ((1,), (2,), (3,))
    picks = _pick_window_patterns(10, catalog, _r.Random(0))
    for a, b in zip(picks, picks[1:], strict=False):
        assert a != b


def test_pick_window_patterns_single_catalog_repeats():
    import random as _r
    picks = _pick_window_patterns(4, ((1,),), _r.Random(0))
    assert picks == [0, 0, 0, 0]


def test_pick_window_patterns_empty_catalog_raises():
    import random as _r
    with pytest.raises(ValueError, match="empty pattern catalog"):
        _pick_window_patterns(1, (), _r.Random(0))


# --- gerador — validacao de args --------------------------------------------

def test_generate_rhythmic_layers_lt_one_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="layers"):
        generate_rhythmic(ana, _section(), layers=0)


def test_generate_rhythmic_bad_role_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="role"):
        generate_rhythmic(ana, _section(), role="pad")


def test_generate_rhythmic_bad_pattern_bars_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="pattern_bars"):
        generate_rhythmic(ana, _section(), pattern_bars=3)


def test_generate_rhythmic_bad_mutate_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="mutate_every_bars"):
        generate_rhythmic(ana, _section(), mutate_every_bars=5)


def test_generate_rhythmic_bad_filter_cycle_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="filter_cycle_bars"):
        generate_rhythmic(ana, _section(), filter_cycle_bars=3)


def test_generate_rhythmic_bad_velocity_cycle_raises():
    ana = _analysis(4)
    with pytest.raises(ValueError, match="velocity_cycle_bars"):
        generate_rhythmic(ana, _section(), velocity_cycle_bars=0)


def test_generate_rhythmic_all_allowed_filter_cycles_accepted():
    ana = _analysis(16)
    for cycle in RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED:
        layers = generate_rhythmic(
            ana, _section(end_bar=16), filter_cycle_bars=cycle, seed=0,
        )
        assert layers[0].cc_events, f"cycle {cycle} produced no CC"


# --- comportamento basico --------------------------------------------------

def test_generate_rhythmic_returns_layer_per_request():
    ana = _analysis(8)
    layers = generate_rhythmic(ana, _section(), layers=3, seed=0)
    assert len(layers) == 3
    assert [layer.index for layer in layers] == [0, 1, 2]
    for layer in layers:
        assert isinstance(layer, RhythmicLayer)


def test_generate_rhythmic_emits_notes_in_register():
    ana = _analysis(4)
    reg = (72, 96)
    layers = generate_rhythmic(ana, _section(end_bar=4), register=reg, seed=0)
    assert layers[0].notes
    for note in layers[0].notes:
        assert isinstance(note, RhythmicNote)
        assert reg[0] <= note.pitch <= reg[1]
        vlo, vhi = VELOCITY_RANGES[RHYTHMIC_BASE_VELOCITY_BUCKET]
        # Ciclo de velocity pode extrapolar bucket por amplitude; garantimos MIDI-valido
        assert 1 <= note.velocity <= 127


def test_generate_rhythmic_notes_sorted_by_time():
    ana = _analysis(4)
    layers = generate_rhythmic(ana, _section(end_bar=4), seed=0)
    starts = [n.start_s for n in layers[0].notes]
    assert starts == sorted(starts)


def test_generate_rhythmic_note_duration_positive():
    ana = _analysis(4)
    layers = generate_rhythmic(ana, _section(end_bar=4), seed=0)
    for n in layers[0].notes:
        assert n.end_s > n.start_s


def test_generate_rhythmic_bar_without_chord_emits_nothing_that_bar():
    ana = _analysis(4)
    ana.bars[1] = _bar(1, chord=None)  # segundo bar sem acorde
    layers = generate_rhythmic(ana, _section(end_bar=4), seed=0)
    # Nenhuma nota deve cair no segundo bar (start no [BAR_S, 2*BAR_S)).
    for n in layers[0].notes:
        assert not (BAR_S <= n.start_s < 2 * BAR_S)


def test_generate_rhythmic_empty_section_returns_empty_layers():
    ana = _analysis(4)
    layers = generate_rhythmic(
        ana, _section(start_bar=10, end_bar=12), layers=2, seed=0,
    )
    assert len(layers) == 2
    for layer in layers:
        assert layer.notes == ()
        assert layer.cc_events == ()


# --- padrao com lacuna ------------------------------------------------------

def test_generated_pattern_has_gap_in_output():
    """AC: 'Teste verifica que o padrao tem lacuna'.

    Emite arp com pattern_bars=1 sem mutacao e verifica que existe janela
    de RHYTHMIC_GAP_MIN_STEPS steps consecutivos sem nota."""
    ana = _analysis(1)  # 1 bar exato
    layers = generate_rhythmic(
        ana, _section(end_bar=1),
        pattern_bars=1, seed=0,
    )
    notes = layers[0].notes
    step_dur = BAR_S / STEPS_PER_BAR
    # Marca cada step onde ha nota (com tolerancia igual a metade do step).
    hits = [False] * STEPS_PER_BAR
    for n in notes:
        idx = int(round(n.start_s / step_dur))
        if 0 <= idx < STEPS_PER_BAR:
            hits[idx] = True
    # Maior sequencia de "step sem hit" deve atingir o piso do AC.
    max_gap = 0
    run = 0
    for hit in hits:
        if not hit:
            run += 1
            max_gap = max(max_gap, run)
        else:
            run = 0
    assert max_gap >= RHYTHMIC_GAP_MIN_STEPS


def test_custom_steps_without_gap_rejected():
    ana = _analysis(1)
    steps = tuple([1] * STEPS_PER_BAR)  # zero gap
    with pytest.raises(ValueError, match="no gap"):
        generate_rhythmic(
            ana, _section(end_bar=1),
            pattern_bars=1, custom_steps=steps, seed=0,
        )


# --- mutacao ---------------------------------------------------------------

def test_mutate_every_bars_changes_pattern_at_boundary():
    """AC: 'Teste verifica que muta no compasso certo'.

    Com mutate_every_bars=4 e catalogo com >=2 padroes, o padrao usado nos
    primeiros 4 bars deve diferir do padrao usado nos 4 seguintes.
    """
    ana = _analysis(8)
    layers = generate_rhythmic(
        ana, _section(end_bar=8),
        pattern_bars=1, mutate_every_bars=4, seed=0,
    )
    notes = layers[0].notes
    step_dur = BAR_S / STEPS_PER_BAR

    def bar_pattern_signature(bar_idx: int) -> tuple[int, ...]:
        hits = [0] * STEPS_PER_BAR
        for n in notes:
            if bar_idx * BAR_S <= n.start_s < (bar_idx + 1) * BAR_S:
                s = int(round((n.start_s - bar_idx * BAR_S) / step_dur))
                if 0 <= s < STEPS_PER_BAR:
                    hits[s] = 1
        return tuple(hits)

    first_window = bar_pattern_signature(0)
    second_window = bar_pattern_signature(4)
    assert first_window != second_window


def test_mutate_every_bars_none_uses_single_pattern():
    ana = _analysis(8)
    layers = generate_rhythmic(
        ana, _section(end_bar=8),
        pattern_bars=1, mutate_every_bars=None, seed=0,
    )
    step_dur = BAR_S / STEPS_PER_BAR

    def sig(bar_idx: int) -> tuple[int, ...]:
        hits = [0] * STEPS_PER_BAR
        for n in layers[0].notes:
            if bar_idx * BAR_S <= n.start_s < (bar_idx + 1) * BAR_S:
                s = int(round((n.start_s - bar_idx * BAR_S) / step_dur))
                if 0 <= s < STEPS_PER_BAR:
                    hits[s] = 1
        return tuple(hits)

    for i in range(1, 8):
        assert sig(i) == sig(0)


def test_mutate_every_bars_eight_holds_pattern_within_window():
    ana = _analysis(8)
    layers = generate_rhythmic(
        ana, _section(end_bar=8),
        pattern_bars=1, mutate_every_bars=8, seed=0,
    )
    step_dur = BAR_S / STEPS_PER_BAR

    def sig(bar_idx: int) -> tuple[int, ...]:
        hits = [0] * STEPS_PER_BAR
        for n in layers[0].notes:
            if bar_idx * BAR_S <= n.start_s < (bar_idx + 1) * BAR_S:
                s = int(round((n.start_s - bar_idx * BAR_S) / step_dur))
                if 0 <= s < STEPS_PER_BAR:
                    hits[s] = 1
        return tuple(hits)

    for i in range(1, 8):
        assert sig(i) == sig(0)


# --- interlock -------------------------------------------------------------

def test_interlocking_avoids_guitar_attack_positions():
    """AC: 'Teste verifica que interlocking nao coloca onset em posicao
    ocupada por ataque de guitarra'.

    Constroi ataques de guitarra em CADA step da grade — o unico jeito de
    respeitar interlock e nao emitir onset nenhum nesses instantes.
    """
    step_dur = BAR_S / STEPS_PER_BAR
    # Guitar em cada step do bar 0
    guitar = [
        GuitarNote(start=i * step_dur, pitch=40, track="Guitar 1")
        for i in range(STEPS_PER_BAR)
    ]
    ana = _analysis(1, guitar_notes=guitar)
    layers = generate_rhythmic(
        ana, _section(end_bar=1),
        pattern_bars=1, interlock=True, seed=0,
    )
    for n in layers[0].notes:
        # Nenhum onset da arp cai dentro da janela de conflito com ataque de guitarra.
        for gt in guitar:
            assert abs(n.start_s - gt.start) >= RHYTHMIC_INTERLOCK_WINDOW_S, (
                f"note at {n.start_s} conflicts with guitar {gt.start}"
            )


def test_interlocking_marks_shifted_notes():
    """Quando o interlock consegue reposicionar (shift <= MAX), a nota tem
    is_interlocked=True."""
    # Bloqueia apenas o step 0 do bar 0 — o pattern[0] deve ser reposicionado.
    guitar = [GuitarNote(start=0.0, pitch=40, track="Guitar 1")]
    ana = _analysis(1, guitar_notes=guitar)
    layers = generate_rhythmic(
        ana, _section(end_bar=1),
        pattern_bars=1, interlock=True, seed=0,
    )
    # Se o padrao emite no step 0 e conseguiu deslocar, deve haver ao menos
    # uma nota marcada; se descartou, nao ha marcada. Ao menos alguma nota
    # e emitida (o padrao tem varios steps ativos).
    assert layers[0].notes
    for n in layers[0].notes:
        if n.is_interlocked:
            assert n.start_s > 0.0


def test_interlock_disabled_does_not_shift():
    guitar = [GuitarNote(start=0.0, pitch=40, track="Guitar 1")]
    ana = _analysis(1, guitar_notes=guitar)
    layers = generate_rhythmic(
        ana, _section(end_bar=1),
        pattern_bars=1, interlock=False, seed=0,
    )
    for n in layers[0].notes:
        assert n.is_interlocked is False


def test_resolve_interlock_onset_returns_none_after_max_shift():
    """Se todas as posicoes ate MAX_SHIFT estao ocupadas, devolve None."""
    step_dur = BAR_S / STEPS_PER_BAR
    bar = _bar(0, chord=Chord(root=0, quality="minor"))
    # Bloqueia todos os steps candidatos.
    guitar_times = [
        i * step_dur for i in range(RHYTHMIC_INTERLOCK_MAX_SHIFT_STEPS + 1)
    ]
    resolved = _resolve_interlock_onset(0.0, step_dur, bar, guitar_times)
    assert resolved is None


def test_resolve_interlock_onset_returns_none_when_past_bar_end():
    bar = _bar(0, chord=Chord(root=0, quality="minor"))
    step_dur = BAR_S / STEPS_PER_BAR
    # candidato ja em bar.end
    resolved = _resolve_interlock_onset(bar.end, step_dur, bar, [])
    assert resolved is None


# --- CC / velocity dynamics -------------------------------------------------

def test_generate_rhythmic_emits_filter_cc74():
    ana = _analysis(8)
    layers = generate_rhythmic(ana, _section(end_bar=8), seed=0)
    events = layers[0].cc_events
    assert events, "no CC events emitted"
    for ev in events:
        assert isinstance(ev, RhythmicCCEvent)
        assert ev.cc == RHYTHMIC_FILTER_CC
        assert RHYTHMIC_FILTER_LO - 1 <= ev.value <= RHYTHMIC_FILTER_HI + 1


def test_generate_rhythmic_cc_events_sorted_by_time():
    ana = _analysis(16)
    layers = generate_rhythmic(
        ana, _section(end_bar=16), seed=0,
    )
    events = layers[0].cc_events
    assert events == tuple(
        sorted(events, key=lambda e: e.time_s)
    )


def test_generate_rhythmic_no_notes_means_no_cc():
    """Sem notas geradas nao emite CC — evita CC solto em secao vazia."""
    ana = _analysis(4)
    layers = generate_rhythmic(
        ana, _section(start_bar=10, end_bar=12), seed=0,
    )
    for layer in layers:
        assert layer.notes == ()
        assert layer.cc_events == ()


def test_velocity_shape_open_at_chorus_lifts_when_chorus_kind():
    ana = _analysis(4)
    v_verse = generate_rhythmic(
        ana, _section(kind="verse", end_bar=4),
        dynamics={"shape": "open_at_chorus"}, seed=0,
    )[0].notes[0].velocity
    v_chorus = generate_rhythmic(
        ana, _section(kind="chorus", end_bar=4),
        dynamics={"shape": "open_at_chorus"}, seed=0,
    )[0].notes[0].velocity
    assert v_chorus > v_verse


def test_velocity_fade_in_ramps_up_over_bars():
    ana = _analysis(8)
    layers = generate_rhythmic(
        ana, _section(end_bar=8),
        dynamics={"entry": "fade_in_2bars"}, seed=0,
    )
    notes = layers[0].notes
    first_bar = [n for n in notes if n.start_s < BAR_S]
    later_bar = [n for n in notes if n.start_s >= 4 * BAR_S]
    assert first_bar and later_bar
    assert min(n.velocity for n in first_bar) < max(n.velocity for n in later_bar)


# --- articulacao ------------------------------------------------------------

def test_articulation_ghost_shortens_gate():
    ana = _analysis(4)
    staccato = generate_rhythmic(
        ana, _section(end_bar=4), articulation="staccato", seed=0,
    )[0].notes
    ghost = generate_rhythmic(
        ana, _section(end_bar=4), articulation="ghost", seed=0,
    )[0].notes
    # ghost tem gate menor => notas mais curtas (na media).
    def avg_dur(ns: list[RhythmicNote]) -> float:
        return sum(n.end_s - n.start_s for n in ns) / max(1, len(ns))

    assert avg_dur(list(ghost)) < avg_dur(list(staccato))


def test_unknown_articulation_falls_back_to_default_staccato():
    ana = _analysis(4)
    unknown = generate_rhythmic(
        ana, _section(end_bar=4), articulation="legato", seed=0,
    )[0].notes
    default = generate_rhythmic(
        ana, _section(end_bar=4), articulation=DEFAULT_RHYTHMIC_ARTICULATION, seed=0,
    )[0].notes
    # Durations should match — same gate fallback.
    assert [round(n.end_s - n.start_s, 6) for n in unknown] == [
        round(n.end_s - n.start_s, 6) for n in default
    ]


# --- determinismo -----------------------------------------------------------

def test_generate_rhythmic_same_seed_same_output():
    ana = _analysis(8)
    a = generate_rhythmic(ana, _section(end_bar=8), seed=123)
    b = generate_rhythmic(ana, _section(end_bar=8), seed=123)
    assert a == b


def test_generate_rhythmic_different_seed_different_stagger():
    ana = _analysis(8)
    a = generate_rhythmic(ana, _section(end_bar=8), layers=2, seed=1)
    b = generate_rhythmic(ana, _section(end_bar=8), layers=2, seed=2)
    # Layer 1 (index 1) tem stagger — diferentes seeds produzem starts diferentes.
    assert a[1].notes[0].start_s != b[1].notes[0].start_s


# --- dataclasses ------------------------------------------------------------

def test_rhythmic_note_dataclass_fields():
    n = RhythmicNote(pitch=72, velocity=90, start_s=0.0, end_s=0.1)
    assert n.pitch == 72
    assert n.is_interlocked is False
    n2 = RhythmicNote(
        pitch=72, velocity=90, start_s=0.0, end_s=0.1, is_interlocked=True,
    )
    assert n2.is_interlocked is True


def test_rhythmic_cc_event_dataclass_fields():
    ev = RhythmicCCEvent(time_s=0.5, cc=74, value=80)
    assert (ev.time_s, ev.cc, ev.value) == (0.5, 74, 80)


# --- guardas defensivas / branches raras -----------------------------------

def test_assert_catalog_wrong_length_raises(monkeypatch):
    import tools.palette.rhythmic as R
    monkeypatch.setattr(R, "RHYTHMIC_PATTERNS", {1: ((1, 0),)})
    with pytest.raises(ValueError, match="steps"):
        R._assert_catalog_has_gaps()


def test_assert_catalog_no_gap_raises(monkeypatch):
    import tools.palette.rhythmic as R
    # 16 steps sem gap de 3 zeros consecutivos.
    monkeypatch.setattr(R, "RHYTHMIC_PATTERNS", {1: (tuple([1] * STEPS_PER_BAR),)})
    with pytest.raises(ValueError, match="no gap"):
        R._assert_catalog_has_gaps()


def test_guitar_attack_times_skips_notes_outside_bar():
    from tools.palette.rhythmic import _guitar_attack_times_in_bar
    guitar = [
        GuitarNote(start=0.5, pitch=40, track="G"),   # antes do bar 1
        GuitarNote(start=2.5, pitch=41, track="G"),   # dentro do bar 1
        GuitarNote(start=4.5, pitch=42, track="G"),   # depois do bar 1
    ]
    ana = _analysis(4, guitar_notes=guitar)
    times = _guitar_attack_times_in_bar(ana, ana.bars[1])
    assert times == [2.5]


def test_filter_cc_curve_returns_empty_for_non_positive_span():
    from tools.palette.rhythmic import _filter_cc_curve
    assert _filter_cc_curve(1.0, 1.0, 4, 2.0, 0.0) == ()
    assert _filter_cc_curve(1.0, 0.5, 4, 2.0, 0.0) == ()


def test_note_duration_floors_at_min_when_gate_would_underflow():
    """`note_dur = step_dur * gate` pode ficar abaixo de MIN_NOTE_DURATION_S
    quando gate=ghost e bar_dur muito curto — o piso garante note_off>note_on."""
    from tools.palette.rhythmic import MIN_NOTE_DURATION_S
    # bar_dur = 0.01s => step_dur = 0.01/16 ≈ 6e-4s; gate ghost midpoint 0.25
    # note_dur ≈ 1.5e-4 < MIN_NOTE_DURATION_S=1e-3.
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
        source="marker", protagonist="rhythm",
        energy={"densidade": 3, "impacto": 3, "largura": 3, "altura": 3, "instabilidade": 3},
    )
    layers = generate_rhythmic(ana, section, articulation="ghost", seed=0)
    for n in layers[0].notes:
        assert n.end_s - n.start_s == pytest.approx(MIN_NOTE_DURATION_S)


def test_interlock_shift_past_bar_end_drops_note():
    """Interlock com shift disponivel apos bar.end faz o resolver devolver
    None; a nota e descartada. Bar minusculo + guitar bloqueando step 0 =
    unico candidato (step+1) cai fora do bar."""
    # bar_dur = 0.02s; step_dur = 0.02/16 = 1.25e-3s.
    # Guitar bloqueia step 0 e step 1; shift=1 vai para step 1 (bloqueado);
    # nao ha step 2 candidato (MAX_SHIFT_STEPS=1) => resolved=None.
    bars = [BarAnalysis(
        index=0, start=0.0, end=0.02,
        chord=Chord(root=0, quality="minor"),
    )]
    step_dur = 0.02 / STEPS_PER_BAR
    guitar = [
        GuitarNote(start=0.0, pitch=40, track="G"),
        GuitarNote(start=step_dur, pitch=41, track="G"),
    ]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[], guitar_notes=guitar,
    )
    section = PlanSection(
        label="S", kind="verse", start_bar=0, end_bar=1,
        source="marker", protagonist="rhythm",
        energy={"densidade": 3, "impacto": 3, "largura": 3, "altura": 3, "instabilidade": 3},
    )
    layers = generate_rhythmic(
        ana, section, pattern_bars=1, interlock=True, seed=0,
    )
    # step 0 do padrao (1,1,...) tenta shift+1 (bloqueado) => descartado.
    for n in layers[0].notes:
        for gt in guitar:
            assert abs(n.start_s - gt.start) >= RHYTHMIC_INTERLOCK_WINDOW_S
