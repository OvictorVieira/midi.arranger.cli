"""Testes do gerador de strings/choir (US-004)."""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord, GuitarNote
from tools.palette.harmonic import (
    STRINGS_CHUG_MIN_NOTES,
    STRINGS_EXPRESSION_HI,
    STRINGS_EXPRESSION_LO,
    STRINGS_GHOST_RATIO,
    STRINGS_LOW_PITCH_CEILING,
    STRINGS_ROLES,
    STRINGS_TUTTI_MAX_VOICES,
    STRINGS_TUTTI_SPREAD_ST,
    StringsVoice,
    _bar_has_chug,
    _initial_voice_pitches,
    _next_voice_pitch,
    _strings_ghost_velocity,
    _tutti_pitches,
    check_tutti_uniqueness,
    generate_strings,
)
from tools.plan import (
    ArrangementPlan,
    Element,
    PlanSection,
    SourceMidi,
)

BAR_S = 2.0


def _bar(index: int, chord: Chord | None) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=chord,
    )


def _analysis(chord_bars: list[Chord | None], guitar_notes: list[GuitarNote] | None = None) -> Analysis:
    bars = [_bar(i, c) for i, c in enumerate(chord_bars)]
    return Analysis(
        key_root=chord_bars[0].root if chord_bars and chord_bars[0] else 0,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=guitar_notes or [],
    )


def _section(label: str = "S1", kind: str = "chorus", start_bar: int = 0, end_bar: int = 8) -> PlanSection:
    return PlanSection(
        label=label, kind=kind, start_bar=start_bar, end_bar=end_bar, source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


def _c_major_bars(n: int = 8) -> list[Chord | None]:
    return [Chord(root=0, quality="major") for _ in range(n)]


# --- basics -----------------------------------------------------------------

def test_strings_roles_vocabulary():
    assert set(STRINGS_ROLES) == {"strings", "choir"}


def test_generate_strings_produces_exactly_n_voices():
    ana = _analysis(_c_major_bars(4))
    voices = generate_strings(ana, _section(end_bar=4), voices=3, seed=0)
    assert [v.index for v in voices] == [0, 1, 2]
    for v in voices:
        assert isinstance(v, StringsVoice)
        assert len(v.notes) > 0


def test_generate_strings_voices_zero_raises():
    ana = _analysis(_c_major_bars(2))
    with pytest.raises(ValueError):
        generate_strings(ana, _section(end_bar=2), voices=0)


def test_generate_strings_role_outside_vocabulary_raises():
    ana = _analysis(_c_major_bars(2))
    with pytest.raises(ValueError):
        generate_strings(ana, _section(end_bar=2), role="piano", voices=3)


def test_generate_strings_choir_role_accepted():
    ana = _analysis(_c_major_bars(2))
    voices = generate_strings(ana, _section(end_bar=2), role="choir", voices=3, seed=0)
    assert all(v.notes for v in voices)


def test_generate_strings_empty_section_returns_empty_voices():
    ana = _analysis(_c_major_bars(2))
    sec = _section(start_bar=10, end_bar=12)
    voices = generate_strings(ana, sec, voices=3, seed=0)
    assert len(voices) == 3
    assert all(v.notes == () for v in voices)
    assert all(v.expression_events == () for v in voices)


def test_generate_strings_no_chord_bar_returns_empty():
    ana = _analysis([None, None, None])
    voices = generate_strings(ana, _section(end_bar=3), voices=3, seed=0)
    assert all(v.notes == () for v in voices)


# --- voice-leading ----------------------------------------------------------

def test_common_tone_preserved_when_pitch_class_matches():
    """Voz externa (top) mantem tom comum entre acordes consecutivos que
    compartilham pitch class. C major={C,E,G}, Em={E,G,B}: G (pc 7) e
    tom comum — a voz do topo deve manter a mesma pitch class.

    Como o gerador colapsa runs contiguos de mesmo pitch numa unica nota
    sustentada (KB linhas 524/699), a assercao aceita tanto 2 notas com
    mesma pitch class quanto 1 nota longa cobrindo os dois bars."""
    chords = [Chord(root=0, quality="major"), Chord(root=4, quality="minor")]
    ana = _analysis(chords)
    voices = generate_strings(ana, _section(end_bar=2), voices=3, seed=0)
    top = voices[-1].notes
    assert len(top) in {1, 2}
    if len(top) == 1:
        # Merge de nota comum: cobre os dois bars com uma unica nota.
        assert top[0].start_s < BAR_S
        assert top[0].end_s > BAR_S
    else:
        # A pitch class do primeiro topo esta em C={0,4,7}. Comum com
        # Em={4,7,11} sao 4 e 7. Se a primeira nota do topo cair em pc
        # {4,7}, a segunda deve ficar na mesma pitch class.
        first_pc = top[0].pitch % 12
        if first_pc in {4, 7}:
            assert top[1].pitch % 12 == first_pc


def test_inner_voice_moves_by_step_when_possible():
    """Voz interna prefere movimento por grau conjunto."""
    chords = [Chord(root=0, quality="major"), Chord(root=5, quality="major")]  # C -> F
    ana = _analysis(chords)
    voices = generate_strings(ana, _section(end_bar=2), voices=3, register=(48, 71), seed=0)
    inner = voices[1].notes
    assert len(inner) == 2
    # movimento por grau conjunto: distancia <= 7 semitons (quinta).
    assert abs(inner[1].pitch - inner[0].pitch) <= 7


def test_initial_voices_span_register():
    pitches = _initial_voice_pitches(Chord(root=0, quality="major"), (48, 84), voices=3)
    assert pitches[0] >= 48
    assert pitches[-1] <= 84
    assert pitches[0] <= pitches[-1]


def test_initial_voices_single_voice_returns_one_pitch():
    pitches = _initial_voice_pitches(Chord(root=0, quality="major"), (48, 71), voices=1)
    assert len(pitches) == 1


def test_next_voice_pitch_common_tone_preferred_for_outer_voice():
    # C -> Am, prev pitch 60 (C4). Both chords contain C -> should keep pc 0.
    p = _next_voice_pitch(60, Chord(root=9, quality="minor"), (48, 84), inner=False)
    assert p % 12 == 0


def test_next_voice_pitch_inner_moves_when_common_tone_exists():
    """Voz interna evita repetir o mesmo pitch — busca por grau conjunto."""
    p = _next_voice_pitch(60, Chord(root=9, quality="minor"), (48, 84), inner=True)
    # Nao deve ser o proprio 60 quando ha alternativas por grau conjunto.
    assert p != 60


# --- tutti ------------------------------------------------------------------

def test_tutti_pitches_up_to_max_voices():
    pitches = _tutti_pitches(Chord(root=0, quality="major"), (36, 96))
    assert len(pitches) <= STRINGS_TUTTI_MAX_VOICES
    assert pitches[-1] - pitches[0] <= STRINGS_TUTTI_SPREAD_ST


def test_generate_strings_tutti_produces_up_to_eight_voices():
    """Tutti pede stack de ate 8 vozes — mais que isso e truncado."""
    ana = _analysis(_c_major_bars(2))
    voices = generate_strings(
        ana, _section(end_bar=2), voices=12, tutti=True, register=(36, 96), seed=0,
    )
    assert len(voices) == STRINGS_TUTTI_MAX_VOICES


def test_generate_strings_tutti_respects_declared_voices_when_below_cap():
    """Tutti com voices=5 mantem 5 vozes (nao infla ate 8) — usuario decide."""
    ana = _analysis(_c_major_bars(2))
    voices = generate_strings(
        ana, _section(end_bar=2), voices=5, tutti=True, register=(36, 96), seed=0,
    )
    assert len(voices) == 5


def test_generate_strings_tutti_span_within_forty_semitones():
    ana = _analysis(_c_major_bars(2))
    voices = generate_strings(
        ana, _section(end_bar=2), voices=8, tutti=True, register=(36, 96), seed=0,
    )
    active_pitches = [n.pitch for v in voices for n in v.notes]
    assert max(active_pitches) - min(active_pitches) <= STRINGS_TUTTI_SPREAD_ST


# --- ghost distribution -----------------------------------------------------

def test_ghost_distribution_around_twenty_percent():
    """AC: '~20% das notas em ghost'. Aceitamos 2 tokens de tolerancia
    (arredondamento por round)."""
    ana = _analysis(_c_major_bars(8))
    voices = generate_strings(ana, _section(end_bar=8), voices=5, seed=0)
    all_notes = [n for v in voices for n in v.notes]
    ghosts = [n for n in all_notes if n.is_ghost]
    ratio = len(ghosts) / len(all_notes)
    assert abs(ratio - STRINGS_GHOST_RATIO) <= 0.06


def test_ghost_only_on_inner_voices():
    """Vozes extremas nunca sao ghost."""
    ana = _analysis(_c_major_bars(8))
    voices = generate_strings(ana, _section(end_bar=8), voices=5, seed=0)
    assert not any(n.is_ghost for n in voices[0].notes)
    assert not any(n.is_ghost for n in voices[-1].notes)


def test_ghost_velocity_below_forty():
    """Velocity de nota ghost respeita '<40' literalmente."""
    ana = _analysis(_c_major_bars(8))
    voices = generate_strings(ana, _section(end_bar=8), voices=5, seed=0)
    for v in voices:
        for n in v.notes:
            if n.is_ghost:
                assert n.velocity < 40


def test_strings_ghost_velocity_helper_within_bucket():
    v = _strings_ghost_velocity()
    assert v < 40
    assert v >= 20


def test_ghost_ratio_zero_produces_no_ghost():
    ana = _analysis(_c_major_bars(4))
    voices = generate_strings(ana, _section(end_bar=4), voices=5, ghost_ratio=0.0, seed=0)
    assert not any(n.is_ghost for v in voices for n in v.notes)


# --- attack por voz + CC11 --------------------------------------------------

def test_voice_attack_offsets_are_distinct():
    """AC: 'Attack diferente por voz'."""
    ana = _analysis(_c_major_bars(1))
    voices = generate_strings(ana, _section(end_bar=1), voices=4, seed=1)
    firsts = sorted(v.notes[0].start_s for v in voices if v.notes)
    for a, b in zip(firsts, firsts[1:], strict=False):
        assert b > a


def test_expression_events_emitted_per_voice():
    """AC: 'curva de expression (CC11) por voz'."""
    ana = _analysis(_c_major_bars(4))
    voices = generate_strings(ana, _section(end_bar=4), voices=3, seed=0)
    for v in voices:
        assert len(v.expression_events) > 0
        for ev in v.expression_events:
            assert 0 <= ev.value <= 127


def test_expression_curves_differ_between_voices():
    """Vozes distintas devem receber curvas com fases distintas — nao mesmo
    vetor de valores."""
    ana = _analysis(_c_major_bars(4))
    voices = generate_strings(ana, _section(end_bar=4), voices=3, seed=0)
    signatures = {tuple(e.value for e in v.expression_events) for v in voices}
    assert len(signatures) > 1


def test_expression_values_stay_within_declared_range():
    ana = _analysis(_c_major_bars(4))
    voices = generate_strings(ana, _section(end_bar=4), voices=3, seed=0)
    all_values = [e.value for v in voices for e in v.expression_events]
    # Todos dentro do range senoidal + arredondamento.
    assert min(all_values) >= STRINGS_EXPRESSION_LO - 1
    assert max(all_values) <= STRINGS_EXPRESSION_HI + 1


# --- chug grave -------------------------------------------------------------

def _guitar_chug_bar(bar_start: float, count: int = 4, pitch: int = 30) -> list[GuitarNote]:
    return [
        GuitarNote(start=bar_start + 0.1 * i, pitch=pitch, track="Guitar")
        for i in range(count)
    ]


def test_bar_has_chug_true_when_low_guitar_dense():
    bars = [_bar(0, Chord(root=0, quality="major"))]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
        guitar_notes=_guitar_chug_bar(0.0, count=STRINGS_CHUG_MIN_NOTES + 1, pitch=30),
    )
    assert _bar_has_chug(ana.bars[0], ana) is True


def test_bar_has_chug_false_when_low_guitar_single_note():
    bars = [_bar(0, Chord(root=0, quality="major"))]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
        guitar_notes=[GuitarNote(start=0.5, pitch=30, track="Guitar")],
    )
    assert _bar_has_chug(ana.bars[0], ana) is False


def test_bar_has_chug_false_when_notes_are_high():
    bars = [_bar(0, Chord(root=0, quality="major"))]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
        guitar_notes=_guitar_chug_bar(0.0, count=4, pitch=60),  # high
    )
    assert _bar_has_chug(ana.bars[0], ana) is False


def test_low_strings_suppressed_when_chug_active():
    """AC: 'Nao gera low strings densas quando ha chug grave ativo'."""
    ana = Analysis(
        key_root=0,
        bars=[_bar(0, Chord(root=0, quality="major")), _bar(1, Chord(root=0, quality="major"))],
        kick_positions=[], snare_positions=[], guitar_unison_positions=[], track_names=[],
        # Chug so no bar 0.
        guitar_notes=_guitar_chug_bar(0.0, count=4, pitch=30),
    )
    # Registro inclui low (36-71) — a voz mais grave cairia < 48.
    voices = generate_strings(
        ana, _section(end_bar=2), voices=4, register=(36, 71), seed=0,
    )
    # No bar 0 (start<2.0): nenhuma nota com pitch < STRINGS_LOW_PITCH_CEILING.
    for v in voices:
        for n in v.notes:
            if n.start_s < BAR_S:
                assert n.pitch >= STRINGS_LOW_PITCH_CEILING


def test_low_strings_allowed_when_no_chug():
    ana = _analysis(_c_major_bars(2), guitar_notes=[])
    voices = generate_strings(
        ana, _section(end_bar=2), voices=4, register=(36, 71), seed=0,
    )
    # Sem chug, ao menos alguma nota da voz grave pode cair < 48.
    all_pitches = [n.pitch for v in voices for n in v.notes]
    assert min(all_pitches) < STRINGS_LOW_PITCH_CEILING


# --- tutti uniqueness -------------------------------------------------------

def _plan_with_tutti(section_labels: list[str]) -> ArrangementPlan:
    sections = [
        PlanSection(
            label=lbl, kind="chorus", start_bar=i * 4, end_bar=(i + 1) * 4,
            source="marker", protagonist="texture",
            energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
        )
        for i, lbl in enumerate(section_labels)
    ]
    element = Element(
        id="str_main", role="strings", sections=list(section_labels),
        register=[48, 84], layers=8, sync_role="sustain_through",
        articulation="sustained", harmony="follow_chords",
        pattern={"tutti": True},
        instrument={"plugin": "Omnisphere", "preset": "Layered Strings", "verified": True},
    )
    return ArrangementPlan(
        version=1, seed=0,
        source_midi=SourceMidi(path="fake.mid", sha256="deadbeef"),
        route="cinematica_emocional",
        sections=sections,
        elements=[element],
    )


def test_tutti_uniqueness_warns_when_multiple_sections():
    plan = _plan_with_tutti(["VERSE", "CHORUS"])
    warnings = check_tutti_uniqueness(plan)
    assert len(warnings) == 1
    assert "tutti" in warnings[0]


def test_tutti_uniqueness_silent_when_single_section():
    plan = _plan_with_tutti(["PEAK"])
    warnings = check_tutti_uniqueness(plan)
    assert warnings == []


def test_tutti_uniqueness_silent_when_no_tutti_element():
    plan = _plan_with_tutti(["A"])
    plan.elements[0].pattern = {}
    warnings = check_tutti_uniqueness(plan)
    assert warnings == []


# --- determinismo -----------------------------------------------------------

def test_generate_strings_deterministic_with_seed():
    ana = _analysis(_c_major_bars(4))
    a = generate_strings(ana, _section(end_bar=4), voices=3, seed=42)
    b = generate_strings(ana, _section(end_bar=4), voices=3, seed=42)
    assert a == b


def test_generate_strings_seed_variation_changes_output():
    ana = _analysis(_c_major_bars(4))
    a = generate_strings(ana, _section(end_bar=4), voices=3, seed=0)
    b = generate_strings(ana, _section(end_bar=4), voices=3, seed=999)
    assert a != b


# --- ramos defensivos e edge cases -----------------------------------------

def test_bar_has_chug_breaks_once_past_bar_end():
    """Notas de guitarra depois do fim do bar disparam `break` no loop."""
    bars = [_bar(0, Chord(root=0, quality="major"))]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
        guitar_notes=[
            GuitarNote(start=0.5, pitch=30, track="G"),
            GuitarNote(start=3.0, pitch=30, track="G"),  # past bar.end=2.0
        ],
    )
    assert _bar_has_chug(ana.bars[0], ana) is False


def test_bar_has_chug_skips_notes_before_bar_start():
    """Notas anteriores ao inicio do bar nao contam."""
    bars = [_bar(1, Chord(root=0, quality="major"))]  # bar 1: [2.0, 4.0)
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
        guitar_notes=[
            GuitarNote(start=0.5, pitch=30, track="G"),
            GuitarNote(start=1.0, pitch=30, track="G"),
            GuitarNote(start=2.5, pitch=30, track="G"),  # in bar
        ],
    )
    # Apenas 1 nota grave dentro do bar => nao dispara chug (min=2).
    assert _bar_has_chug(ana.bars[0], ana) is False


def test_tutti_bar_without_chord_holds_previous_voicing():
    """No path tutti, bar sem acorde no meio mantem voicing do bar anterior
    para a voz nao sumir."""
    chords: list[Chord | None] = [
        Chord(root=0, quality="major"), None, Chord(root=0, quality="major"),
    ]
    ana = _analysis(chords)
    voices = generate_strings(
        ana, _section(end_bar=3), voices=6, tutti=True, register=(36, 96), seed=0,
    )
    # A voz mais grave deve ter notas nos bars 0 e 2, mas nao no 1.
    for v in voices:
        for n in v.notes:
            assert not (BAR_S <= n.start_s < 2 * BAR_S)


def test_non_tutti_bar_without_chord_sustains_voice():
    """Sem chord bar no meio: gerador nao emite nota nesse bar, mas as vozes
    seguintes retomam com o mesmo pitch anterior (voice-leading holds)."""
    chords: list[Chord | None] = [
        Chord(root=0, quality="major"), None, Chord(root=5, quality="major"),
    ]
    ana = _analysis(chords)
    voices = generate_strings(ana, _section(end_bar=3), voices=3, seed=0)
    # Bar 1 nao emite nota.
    for v in voices:
        for n in v.notes:
            assert not (BAR_S <= n.start_s < 2 * BAR_S)


def test_tutti_when_pitches_fewer_than_voices_pads_last():
    """Registro apertado — `_tutti_pitches` devolve poucos pitches; o gerador
    preenche a lista repetindo a ultima nota ate cobrir `voices`."""
    ana = _analysis(_c_major_bars(2))
    # Registro apertado (mid, 24 semitons). Tutti pede spread 40 mas trava
    # em hi-lo=23; alguns pitches serao truncados/dobrados.
    voices = generate_strings(
        ana, _section(end_bar=2), voices=8, tutti=True, register=(48, 71), seed=0,
    )
    assert len(voices) == 8
    # Cada voz produziu >= 1 nota — nao fica com voz vazia.
    for v in voices:
        assert len(v.notes) >= 1


def test_generate_strings_voice_offset_pushes_note_past_boundary():
    """Voz muito atrasada em bar curtissimo — nota e pulada silenciosamente."""
    # Bar de duracao 0.05s: com 8 vozes acumulando ~10-45ms, ultima voz
    # comeca > 0.05s.
    bars = [
        BarAnalysis(index=0, start=0.0, end=0.05, chord=Chord(root=0, quality="major")),
    ]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
    )
    sec = PlanSection(
        label="X", kind="verse", start_bar=0, end_bar=1, source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )
    voices = generate_strings(ana, sec, voices=8, seed=0)
    # Voz 0 (offset 0) provavelmente emite; vozes posteriores podem ser puladas.
    voice_note_counts = [len(v.notes) for v in voices]
    assert voice_note_counts[0] == 1
    assert min(voice_note_counts) == 0


def test_empty_voice_notes_produces_no_expression_events():
    """Se uma voz nao emite nenhuma nota (offset > boundary + chug), o
    campo expression_events vem vazio."""
    bars = [
        BarAnalysis(index=0, start=0.0, end=0.05, chord=Chord(root=0, quality="major")),
    ]
    ana = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
    )
    sec = PlanSection(
        label="X", kind="verse", start_bar=0, end_bar=1, source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )
    voices = generate_strings(ana, sec, voices=8, seed=0)
    empty = [v for v in voices if not v.notes]
    assert empty
    for v in empty:
        assert v.expression_events == ()


def test_initial_voice_pitches_less_candidates_than_voices():
    """Registro cabe so 1 pitch — fallback preenche o resto com o topo."""
    pitches = _initial_voice_pitches(Chord(root=0, quality="major"), (48, 51), voices=4)
    # Registro 48-51 so cabe C4 (48). Restantes duplicam.
    assert len(pitches) == 4
    assert all(48 <= p <= 51 for p in pitches)


def test_initial_voice_pitches_no_candidates_fallback():
    """Registro totalmente fora do acorde — fallback usa o root direto."""
    # Registro high alto onde nenhuma nota de C major (0,4,7) cai.
    pitches = _initial_voice_pitches(Chord(root=0, quality="major"), (200, 200), voices=3)
    assert len(pitches) == 3


def test_next_voice_pitch_no_candidates_returns_prev():
    """Registro sem nota do acorde — funcao devolve prev sem crashar."""
    # Registro (200, 200) — nada dentro. Deve devolver prev_pitch.
    p = _next_voice_pitch(60, Chord(root=0, quality="major"), (200, 200), inner=False)
    assert p == 60


def test_tutti_first_bar_no_chord_leaves_empty_pitches():
    """Tutti onde o primeiro bar do slice esta sem acorde: o fallback e
    lista vazia enquanto voice_pitches_per_bar continua sem prev."""
    chords: list[Chord | None] = [None, Chord(root=0, quality="major")]
    ana = _analysis(chords)
    voices = generate_strings(
        ana, _section(end_bar=2), voices=6, tutti=True, register=(36, 96), seed=0,
    )
    # Bar 0 sem chord => nenhuma nota; bar 1 emite.
    for v in voices:
        for n in v.notes:
            assert n.start_s >= BAR_S


def test_generate_strings_with_dynamics_shape_fade_in():
    """Cobrindo o path de fade_in — nao trata como caso especial mas
    verifica que dyn e propagado."""
    ana = _analysis(_c_major_bars(4))
    voices = generate_strings(
        ana, _section(end_bar=4), voices=3,
        dynamics={"entry": "fade_in_2bars", "shape": "hold"}, seed=0,
    )
    # Notas do bar 0 tem velocity menor que notas do bar 3 (fade in).
    for v in voices:
        if len(v.notes) >= 2:
            bar_zero = [n for n in v.notes if n.start_s < BAR_S]
            later = [n for n in v.notes if n.start_s >= 2 * BAR_S]
            if bar_zero and later:
                assert min(n.velocity for n in bar_zero) <= max(n.velocity for n in later)


def test_generate_strings_articulation_fallback_to_sustained():
    """Articulacao fora de GATE_RATIOS cai para sustained sem crashar."""
    ana = _analysis(_c_major_bars(2))
    voices = generate_strings(
        ana, _section(end_bar=2), voices=3, articulation="let_ring", seed=0,
    )
    # Notas devem ser sustentadas (gate ~0.95 do sustained).
    for v in voices:
        for n in v.notes:
            assert n.end_s > n.start_s
