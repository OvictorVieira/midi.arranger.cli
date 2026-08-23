"""Testes do validador de artificialidade (FR-19, US-008)."""

from __future__ import annotations

import os
from pathlib import Path

import pretty_midi
import pytest

from tools.analyze import (
    Analysis,
    BarAnalysis,
    Chord,
    GuitarNote,
)
from tools.plan import ArrangementPlan, Element, PlanSection, SourceMidi
from tools.render import render
from tools.validators.artifice import (
    ALL_PATTERNS,
    GRID_SUBDIVISIONS,
    MIN_NOTES_FOR_CHECK,
    PATTERN_DUPLICATES_SOURCE,
    PATTERN_DURATION_UNIFORM,
    PATTERN_END_CHAIN,
    PATTERN_GRID,
    PATTERN_REPEATED_NOTES,
    PATTERN_VELOCITY_GROUPED,
    PATTERN_VELOCITY_RANDOM,
    ArtificeIssue,
    format_issues,
    has_errors,
    validate_artifice,
)
from tools.validators.harmony import (
    SEVERITY_ERROR,
    RenderedNote,
    RenderedTrack,
)

# --- fixtures ---------------------------------------------------------------

BAR_S = 2.0


def _bar(index: int) -> BarAnalysis:
    return BarAnalysis(
        index=index,
        start=index * BAR_S,
        end=(index + 1) * BAR_S,
        chord=Chord(root=9, quality="minor"),
    )


def _analysis(
    bars: int = 4,
    *,
    kicks: list[float] | None = None,
    guitar: list[GuitarNote] | None = None,
) -> Analysis:
    return Analysis(
        key_root=9,
        bars=[_bar(i) for i in range(bars)],
        kick_positions=kicks or [],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=guitar or [],
    )


def _element(
    id: str = "arp_main",
    *,
    articulation: str = "staccato",
) -> Element:
    return Element(
        id=id, role="arp", sections=["MAIN"],
        register=[48, 71], layers=1,
        sync_role="cycles", articulation=articulation,
        harmony="follow_chords",
    )


def _plan(elements: list[Element]) -> ArrangementPlan:
    section = PlanSection(
        label="MAIN", kind="chorus", start_bar=0, end_bar=4,
        source="marker", protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5,
                "altura": 5, "instabilidade": 3},
    )
    return ArrangementPlan(
        version=1, seed=0,
        source_midi=SourceMidi(path="/dev/null", sha256="0" * 64),
        route="cinematica_emocional",
        sections=[section],
        elements=elements,
    )


def _track(
    notes: list[RenderedNote],
    element_id: str = "arp_main",
    name: str = "Arp",
) -> RenderedTrack:
    return RenderedTrack(element_id=element_id, track_name=name, notes=tuple(notes))


def _note(
    pitch: int, start_s: float,
    end_s: float | None = None,
    velocity: int = 80,
) -> RenderedNote:
    return RenderedNote(
        pitch=pitch, start_s=start_s,
        end_s=end_s if end_s is not None else start_s + 0.1,
        velocity=velocity,
    )


# --- vocabulario e utilidades ----------------------------------------------

def test_all_patterns_vocabulary_is_closed():
    assert PATTERN_GRID in ALL_PATTERNS
    assert PATTERN_END_CHAIN in ALL_PATTERNS
    assert PATTERN_DURATION_UNIFORM in ALL_PATTERNS
    assert PATTERN_VELOCITY_GROUPED in ALL_PATTERNS
    assert PATTERN_VELOCITY_RANDOM in ALL_PATTERNS
    assert PATTERN_REPEATED_NOTES in ALL_PATTERNS
    assert PATTERN_DUPLICATES_SOURCE in ALL_PATTERNS
    assert len(ALL_PATTERNS) == 7


def test_has_errors_true_only_when_error_present():
    err = ArtificeIssue(
        severity=SEVERITY_ERROR, element_id="x", track="X",
        bar=1, pattern=PATTERN_GRID, message="",
    )
    assert has_errors([err])
    assert not has_errors([])


def test_format_issues_ok_when_empty():
    assert format_issues([]) == "Artifice: OK"


def test_format_issues_lists_errors():
    err = ArtificeIssue(
        severity=SEVERITY_ERROR, element_id="a", track="A",
        bar=1, pattern=PATTERN_GRID, message="oops",
    )
    out = format_issues([err])
    assert "1 error(s)" in out
    assert "oops" in out


# --- grid perfeito ---------------------------------------------------------

def test_grid_perfect_flagged_when_all_onsets_snap_to_grid():
    """AC1: todos os onsets exatamente no grid de 16 avos."""
    ana = _analysis(4)
    step = BAR_S / GRID_SUBDIVISIONS  # 0.125s a 120bpm
    # 16 notas, todas em multiplos exatos do passo do grid.
    notes = [
        _note(pitch=60, start_s=b * BAR_S + i * step, velocity=70 + (i % 5))
        for b in range(2) for i in range(8)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    grid_issues = [i for i in issues if i.pattern == PATTERN_GRID]
    assert len(grid_issues) == 1
    assert "grid" in grid_issues[0].message.lower()


def test_grid_check_skipped_when_any_note_off_grid():
    """Uma unica nota fora do grid ja quebra o padrao — nao dispara."""
    ana = _analysis(4)
    step = BAR_S / GRID_SUBDIVISIONS
    notes = [
        _note(pitch=60, start_s=b * BAR_S + i * step, velocity=70 + (i % 5))
        for b in range(2) for i in range(8)
    ]
    # Sujeita uma nota com jitter de 15ms.
    notes[3] = _note(pitch=60, start_s=notes[3].start_s + 0.015, velocity=72)
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_GRID for i in issues)


def test_grid_check_skipped_for_sustained_articulation():
    """AC: pad/held nota naturalmente cai no downbeat — nao vira artificial."""
    ana = _analysis(8)
    notes = [
        _note(pitch=60, start_s=b * BAR_S, end_s=(b + 1) * BAR_S, velocity=70 + (b % 5))
        for b in range(8)
    ]
    tr = _track(notes)
    plan = _plan([_element(articulation="sustained")])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_GRID for i in issues)


def test_grid_check_skipped_when_note_falls_outside_bars():
    """Nota alem do ultimo bar: placement pega — artifice nao emite falso
    positivo por causa dela."""
    ana = _analysis(2)
    notes = [
        _note(pitch=60, start_s=i * 0.125, velocity=70 + (i % 5))
        for i in range(8)
    ]
    # Uma nota fora do range de bars.
    notes.append(_note(pitch=60, start_s=999.0, velocity=75))
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_GRID for i in issues)


# --- end chain -------------------------------------------------------------

def test_end_chain_flagged_when_every_note_ends_at_next_onset():
    """AC2: fim de cada nota coincide com onset da seguinte."""
    ana = _analysis(4)
    starts = [i * 0.20 + 0.007 for i in range(10)]  # off-grid para nao disparar grid
    notes = [
        _note(
            pitch=60 + (i % 3), start_s=starts[i],
            end_s=starts[i + 1] if i + 1 < len(starts) else starts[i] + 0.20,
            velocity=70 + (i % 4),
        )
        for i in range(10)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    chain = [i for i in issues if i.pattern == PATTERN_END_CHAIN]
    assert len(chain) == 1
    assert "breathing" in chain[0].message


def test_end_chain_not_flagged_when_any_gap_exists():
    ana = _analysis(4)
    starts = [i * 0.20 + 0.007 for i in range(10)]
    notes = [
        _note(
            pitch=60 + (i % 3), start_s=starts[i],
            end_s=starts[i + 1] if i + 1 < len(starts) else starts[i] + 0.20,
            velocity=70 + (i % 4),
        )
        for i in range(10)
    ]
    # Insere respiro entre nota 4 e 5.
    notes[4] = _note(
        pitch=notes[4].pitch, start_s=notes[4].start_s,
        end_s=notes[4].start_s + 0.05, velocity=notes[4].velocity,
    )
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_END_CHAIN for i in issues)


# --- duracao uniforme ------------------------------------------------------

def test_duration_uniform_flagged_when_all_same_duration():
    """AC3: todas as notas com a mesma duracao (dentro do bucket)."""
    ana = _analysis(4)
    notes = [
        _note(
            pitch=60 + (i % 4), start_s=0.13 + i * 0.20,
            end_s=0.13 + i * 0.20 + 0.10, velocity=70 + (i % 4),
        )
        for i in range(10)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    dur = [i for i in issues if i.pattern == PATTERN_DURATION_UNIFORM]
    assert len(dur) == 1
    assert "same duration" in dur[0].message


def test_duration_uniform_not_flagged_when_durations_vary():
    ana = _analysis(4)
    notes = [
        _note(
            pitch=60 + (i % 4), start_s=0.13 + i * 0.20,
            end_s=0.13 + i * 0.20 + (0.10 + (i % 3) * 0.05),
            velocity=70 + (i % 4),
        )
        for i in range(10)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_DURATION_UNIFORM for i in issues)


# --- velocity agrupada -----------------------------------------------------

def test_velocity_grouped_flagged_when_stddev_below_threshold():
    """AC4: stddev de velocity < 3 => agrupada demais."""
    ana = _analysis(4)
    notes = [
        _note(
            pitch=60 + (i % 3), start_s=0.13 + i * 0.20,
            end_s=0.13 + i * 0.20 + 0.10 + (i % 3) * 0.04,
            velocity=80 + (i % 3) - 1,  # variacao maxima de 2 unidades
        )
        for i in range(10)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    grouped = [i for i in issues if i.pattern == PATTERN_VELOCITY_GROUPED]
    assert len(grouped) == 1
    assert "stddev" in grouped[0].message
    assert "flat" in grouped[0].message


def test_velocity_grouped_not_flagged_when_stddev_high_enough():
    ana = _analysis(4)
    vels = [70, 90, 75, 95, 68, 88, 78, 92, 72, 96]
    notes = [
        _note(
            pitch=60 + (i % 3), start_s=0.13 + i * 0.20,
            end_s=0.13 + i * 0.20 + 0.10 + (i % 3) * 0.04, velocity=vels[i],
        )
        for i in range(10)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_VELOCITY_GROUPED for i in issues)


# --- velocity aleatoria sem acento ----------------------------------------

def test_velocity_random_no_accent_flagged_when_downbeat_not_stronger():
    """AC5: stddev alta, downbeat mean <= offbeat mean => jitter aleatorio."""
    ana = _analysis(4)
    # 16 notas espalhadas nos primeiros 2 bars. Notas 0/8 caem no downbeat,
    # o resto no offbeat. Deixo downbeat com velocity baixa e offbeat com alta.
    notes = []
    for b in range(2):
        for i in range(8):
            start = b * BAR_S + 0.007 + i * 0.24
            vel = 40 if i == 0 else (100 if i % 2 else 90)
            notes.append(_note(
                pitch=60 + (i % 3), start_s=start,
                end_s=start + 0.10 + (i % 3) * 0.04, velocity=vel,
            ))
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    rand = [i for i in issues if i.pattern == PATTERN_VELOCITY_RANDOM]
    assert len(rand) == 1
    assert "random jitter" in rand[0].message


def test_velocity_random_not_flagged_when_downbeat_accented():
    """Downbeat mais alto que offbeat => tem acento, passa."""
    ana = _analysis(4)
    notes = []
    for b in range(2):
        for i in range(8):
            start = b * BAR_S + 0.007 + i * 0.24
            vel = 120 if i == 0 else (60 + i * 2)
            notes.append(_note(
                pitch=60 + (i % 3), start_s=start,
                end_s=start + 0.10 + (i % 3) * 0.04, velocity=vel,
            ))
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_VELOCITY_RANDOM for i in issues)


# --- notas repetidas identicas --------------------------------------------

def test_repeated_notes_flagged_when_six_identical_in_a_row():
    """AC6: 6+ notas iguais (pitch/vel/duracao/espacamento) => ostinato."""
    ana = _analysis(4)
    step = 0.25
    notes = [
        _note(pitch=48, start_s=0.13 + i * step, end_s=0.13 + i * step + 0.12,
              velocity=80)
        for i in range(6)
    ]
    # Adiciona ruido antes para nao coincidir com grid nem end-chain.
    notes.append(_note(pitch=52, start_s=3.5, end_s=3.6, velocity=90))
    notes.append(_note(pitch=55, start_s=3.7, end_s=3.85, velocity=70))
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    rep = [i for i in issues if i.pattern == PATTERN_REPEATED_NOTES]
    assert len(rep) == 1
    assert "consecutive identical notes" in rep[0].message


def test_repeated_notes_not_flagged_when_velocity_varies():
    ana = _analysis(4)
    step = 0.25
    notes = [
        _note(pitch=48, start_s=0.13 + i * step, end_s=0.13 + i * step + 0.12,
              velocity=70 + (i * 3))
        for i in range(8)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_REPEATED_NOTES for i in issues)


# --- duplica source (kick / guitarra) -------------------------------------

def test_duplicates_kick_flagged_when_over_ninety_percent_match():
    """AC7: layer sem conteudo ritmico proprio (>=90% dos onsets = kick)."""
    kicks = [i * 0.5 for i in range(16)]
    ana = _analysis(bars=8, kicks=kicks)
    notes = [
        _note(pitch=40, start_s=k + 0.005, end_s=k + 0.10, velocity=80 + (i % 4))
        for i, k in enumerate(kicks[:12])
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    dup = [i for i in issues if i.pattern == PATTERN_DUPLICATES_SOURCE]
    assert len(dup) == 1
    assert "kick" in dup[0].message


def test_duplicates_guitar_flagged_when_over_ninety_percent_match():
    guitar = [
        GuitarNote(start=i * 0.35, pitch=50 + (i % 5), track="Guitar")
        for i in range(16)
    ]
    ana = _analysis(bars=8, guitar=guitar)
    notes = [
        _note(pitch=50, start_s=g.start + 0.005,
              end_s=g.start + 0.10, velocity=70 + (i % 4))
        for i, g in enumerate(guitar[:12])
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    dup = [i for i in issues if i.pattern == PATTERN_DUPLICATES_SOURCE]
    assert len(dup) == 1
    assert "guitar" in dup[0].message


def test_duplicates_not_flagged_when_track_has_own_rhythm():
    kicks = [i * 0.5 for i in range(16)]
    ana = _analysis(bars=8, kicks=kicks)
    # Metade dos onsets sao offsets deliberados que nao batem com kick.
    notes = [
        _note(pitch=40, start_s=(i * 0.5) + (0.25 if i % 2 else 0.005),
              end_s=(i * 0.5) + 0.10, velocity=70 + (i % 4))
        for i in range(12)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_DUPLICATES_SOURCE for i in issues)


def test_duplicates_skipped_when_source_lists_are_empty():
    """Sem kick/guitar detectados no source, nao ha o que duplicar."""
    ana = _analysis(bars=8)
    notes = [
        _note(pitch=40, start_s=i * 0.5, end_s=i * 0.5 + 0.10,
              velocity=70 + (i % 4))
        for i in range(12)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_DUPLICATES_SOURCE for i in issues)


# --- guards defensivos -----------------------------------------------------

def test_track_without_matching_element_is_ignored():
    ana = _analysis(4)
    stray = _track([_note(pitch=60, start_s=0.5)], element_id="ghost")
    plan = _plan([_element()])
    assert validate_artifice([stray], plan, ana) == []


def test_short_track_below_min_notes_skips_aggregate_checks():
    """Track com menos de MIN_NOTES_FOR_CHECK notas nao dispara os checks
    agregados (grid/chain/dur/velocity/duplicates). Repeated_notes tem piso
    proprio (REPEATED_STREAK_MIN); mantemos varia velocity aqui para nao
    disparar ostinato."""
    ana = _analysis(4)
    vels = [70, 90, 75, 95, 68, 88, 78]
    notes = [
        _note(
            pitch=60 + (i % 3), start_s=i * 0.13 + 0.007,
            end_s=i * 0.13 + 0.10 + (i % 3) * 0.03, velocity=vels[i],
        )
        for i in range(MIN_NOTES_FOR_CHECK - 1)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    assert validate_artifice([tr], plan, ana) == []


def test_short_track_below_repeated_streak_is_silent():
    """Menos de REPEATED_STREAK_MIN notas garantidamente nao dispara ostinato."""
    ana = _analysis(4)
    tr = _track([_note(pitch=60, start_s=0.5, velocity=80)])
    plan = _plan([_element()])
    assert validate_artifice([tr], plan, ana) == []


def test_velocity_random_needs_enough_split_between_downbeat_and_offbeat():
    """Se todas as notas caem no downbeat (ou todas no offbeat), o teste de
    accent nao tem grupo comparavel — nao dispara."""
    ana = _analysis(bars=8)
    # Todas as notas caem exatamente no bar start (downbeat) — offbeat vazio.
    notes = [
        _note(pitch=60, start_s=b * BAR_S + 0.005,
              end_s=b * BAR_S + 0.10, velocity=[40, 120, 45, 115, 50, 110, 55, 105][b])
        for b in range(8)
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_VELOCITY_RANDOM for i in issues)


def test_velocity_random_skipped_when_stddev_low():
    """Stddev de velocity abaixo do piso HIGH => nao entra na checagem de
    accent (o check de agrupada ja cobre o cenario)."""
    ana = _analysis(bars=8)
    notes = []
    for b in range(2):
        for i in range(8):
            start = b * BAR_S + 0.007 + i * 0.24
            notes.append(_note(
                pitch=60, start_s=start, end_s=start + 0.10 + (i % 3) * 0.04,
                velocity=80 + (i % 3),  # stddev bem abaixo de 15
            ))
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_VELOCITY_RANDOM for i in issues)


def test_repeated_notes_streak_resets_when_spacing_changes():
    """Espacamento inconsistente entre notas identicas nao caracteriza ostinato."""
    ana = _analysis(bars=4)
    notes = [
        _note(pitch=48, start_s=0.13, end_s=0.25, velocity=80),
        _note(pitch=48, start_s=0.38, end_s=0.50, velocity=80),  # spacing 0.25
        _note(pitch=48, start_s=0.63, end_s=0.75, velocity=80),  # spacing 0.25
        # Muda o espacamento — streak devia resetar aqui.
        _note(pitch=48, start_s=1.20, end_s=1.32, velocity=80),
        _note(pitch=48, start_s=1.32 + 0.10, end_s=1.32 + 0.22, velocity=80),
        _note(pitch=48, start_s=1.32 + 0.20, end_s=1.32 + 0.32, velocity=80),
    ]
    tr = _track(notes)
    plan = _plan([_element()])
    issues = validate_artifice([tr], plan, ana)
    assert not any(i.pattern == PATTERN_REPEATED_NOTES for i in issues)


def test_let_ring_articulation_is_also_exempt_from_grid_checks():
    ana = _analysis(bars=8)
    notes = [
        _note(pitch=60, start_s=b * BAR_S, end_s=(b + 1) * BAR_S,
              velocity=70 + (b % 5))
        for b in range(8)
    ]
    tr = _track(notes)
    plan = _plan([_element(articulation="let_ring")])
    issues = validate_artifice([tr], plan, ana)
    assert not any(
        i.pattern in (PATTERN_GRID, PATTERN_END_CHAIN, PATTERN_DURATION_UNIFORM)
        for i in issues
    )


# --- teste "cada anti-padrao detectado individualmente" (AC5 do PRD) ------

def _build_all_offenders_analysis() -> Analysis:
    return _analysis(bars=8, kicks=[i * 0.5 for i in range(16)])


def test_every_anti_pattern_fires_on_its_own_deliberately_robotic_input():
    """AC: alimenta MIDI deliberadamente robotico e verifica que CADA
    anti-padrao e detectado individualmente. Uma track ofensora por padrao."""
    ana = _build_all_offenders_analysis()
    plan = _plan([
        _element("grid_bot"),
        _element("chain_bot"),
        _element("dur_bot"),
        _element("vel_flat"),
        _element("vel_rand"),
        _element("ostinato"),
        _element("kick_dupe"),
    ])

    step = BAR_S / GRID_SUBDIVISIONS
    grid_notes = [
        _note(pitch=60, start_s=b * BAR_S + i * step, velocity=70 + (i % 5),
              end_s=b * BAR_S + i * step + 0.05 + (i % 3) * 0.02)
        for b in range(2) for i in range(8)
    ]
    grid_tr = _track(grid_notes, element_id="grid_bot", name="Grid")

    chain_starts = [i * 0.20 + 0.007 for i in range(10)]
    chain_notes = [
        _note(
            pitch=60 + (i % 3), start_s=chain_starts[i],
            end_s=chain_starts[i + 1] if i + 1 < len(chain_starts)
            else chain_starts[i] + 0.20,
            velocity=70 + (i % 4),
        )
        for i in range(10)
    ]
    chain_tr = _track(chain_notes, element_id="chain_bot", name="Chain")

    dur_notes = [
        _note(
            pitch=60 + (i % 3), start_s=0.13 + i * 0.20,
            end_s=0.13 + i * 0.20 + 0.10, velocity=70 + (i % 4),
        )
        for i in range(10)
    ]
    dur_tr = _track(dur_notes, element_id="dur_bot", name="Dur")

    flat_notes = [
        _note(
            pitch=60 + (i % 3), start_s=0.13 + i * 0.20,
            end_s=0.13 + i * 0.20 + 0.10 + (i % 3) * 0.04,
            velocity=80 + (i % 3) - 1,
        )
        for i in range(10)
    ]
    flat_tr = _track(flat_notes, element_id="vel_flat", name="Flat")

    rand_notes = []
    for b in range(2):
        for i in range(8):
            start = b * BAR_S + 0.007 + i * 0.24
            vel = 40 if i == 0 else (100 if i % 2 else 90)
            rand_notes.append(_note(
                pitch=60 + (i % 3), start_s=start,
                end_s=start + 0.10 + (i % 3) * 0.04, velocity=vel,
            ))
    rand_tr = _track(rand_notes, element_id="vel_rand", name="Rand")

    ostinato_notes = [
        _note(pitch=48, start_s=0.13 + i * 0.25, end_s=0.13 + i * 0.25 + 0.12,
              velocity=80)
        for i in range(6)
    ]
    ostinato_notes.append(_note(pitch=52, start_s=3.5, end_s=3.6, velocity=90))
    ostinato_notes.append(_note(pitch=55, start_s=3.72, end_s=3.85, velocity=70))
    ostinato_tr = _track(ostinato_notes, element_id="ostinato", name="Ost")

    dup_notes = [
        _note(pitch=40, start_s=k + 0.005, end_s=k + 0.10, velocity=80 + (i % 4))
        for i, k in enumerate(ana.kick_positions[:12])
    ]
    dup_tr = _track(dup_notes, element_id="kick_dupe", name="Kick copy")

    tracks = [grid_tr, chain_tr, dur_tr, flat_tr, rand_tr, ostinato_tr, dup_tr]
    issues = validate_artifice(tracks, plan, ana)
    patterns_by_element: dict[str, set[str]] = {}
    for i in issues:
        patterns_by_element.setdefault(i.element_id, set()).add(i.pattern)

    assert PATTERN_GRID in patterns_by_element["grid_bot"]
    assert PATTERN_END_CHAIN in patterns_by_element["chain_bot"]
    assert PATTERN_DURATION_UNIFORM in patterns_by_element["dur_bot"]
    assert PATTERN_VELOCITY_GROUPED in patterns_by_element["vel_flat"]
    assert PATTERN_VELOCITY_RANDOM in patterns_by_element["vel_rand"]
    assert PATTERN_REPEATED_NOTES in patterns_by_element["ostinato"]
    assert PATTERN_DUPLICATES_SOURCE in patterns_by_element["kick_dupe"]


# --- integracao com render real (AC: saida real nao dispara) ---------------

ANCORA_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _ancora_path() -> str:
    if os.path.exists(ANCORA_FIXTURE):
        return ANCORA_FIXTURE
    pytest.skip("ANCORA reference MIDI not available in this environment")


def _build_synthetic_source(tmp_path: Path) -> Path:
    """Fonte controlada: 8 compassos 4/4 a 120 bpm com piano + baixo."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (57, 60, 64):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=45, start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.extend([piano, bass])
    dest = tmp_path / "source.mid"
    pm.write(str(dest))
    return dest


def _synthetic_pad_plan(src: Path, tmp_path: Path) -> Path:
    from tools.plan import dump
    from tools.render import sha256_of_file
    plan = ArrangementPlan(
        version=1, seed=42,
        source_midi=SourceMidi(path=str(src), sha256=sha256_of_file(src)),
        route="cinematica_emocional",
        sections=[PlanSection(
            label="MAIN", kind="chorus", start_bar=0, end_bar=8,
            source="marker", protagonist="texture",
            energy={"densidade": 5, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3},
        )],
        elements=[Element(
            id="pad_main", role="pad", sections=["MAIN"],
            register=[48, 71], layers=2,
            sync_role="sustain_through", articulation="sustained",
            harmony="follow_chords",
            dynamics={"shape": "hold"},
            instrument={"plugin": "Omnisphere", "preset": "Desert Wind",
                        "verified": True},
        )],
    )
    plan_path = tmp_path / "plan.json"
    dump(plan, plan_path)
    return plan_path


def test_real_render_output_does_not_trigger_any_anti_pattern(tmp_path):
    """AC: saida de render real com a paleta desta rodada NAO dispara nenhum
    anti-padrao. Usa fonte sintetica pequena para nao depender do ANCORA."""
    src = _build_synthetic_source(tmp_path)
    plan_path = _synthetic_pad_plan(src, tmp_path)
    out_mid = tmp_path / "out.mid"
    report = render(plan_path, out_mid, source_path=src)
    assert report.artifice_issues == [], \
        f"expected no artifice; got {[i.message for i in report.artifice_issues]}"


def test_report_carries_artifice_issues_field(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan_path = _synthetic_pad_plan(src, tmp_path)
    out_mid = tmp_path / "out.mid"
    report = render(plan_path, out_mid, source_path=src)
    assert hasattr(report, "artifice_issues")
    assert isinstance(report.artifice_issues, list)


def test_render_report_formatter_prints_artifice_block(tmp_path):
    from tools.render import format_render_report
    src = _build_synthetic_source(tmp_path)
    plan_path = _synthetic_pad_plan(src, tmp_path)
    out_mid = tmp_path / "out.mid"
    report = render(plan_path, out_mid, source_path=src)
    printed = format_render_report(report)
    assert "Artifice:" in printed
