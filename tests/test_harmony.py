"""Testes do validador harmonico (FR-25, US-001)."""

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
    analyze,
)
from tools.plan import ArrangementPlan, Element, PlanSection, SourceMidi
from tools.render import render
from tools.validators.harmony import (
    NATURAL_MINOR_INTERVALS,
    PEDAL_INTERVALS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    HarmonyIssue,
    RenderedNote,
    RenderedTrack,
    chord_pcs,
    degrees_pcs,
    format_issues,
    has_errors,
    key_scale_pcs,
    pitch_name,
    validate_harmony,
)

# --- fixtures ---------------------------------------------------------------

BAR_S = 2.0


def _bar(index: int, chord: Chord | None) -> BarAnalysis:
    return BarAnalysis(
        index=index,
        start=index * BAR_S,
        end=(index + 1) * BAR_S,
        chord=chord,
    )


def _analysis(
    bars: list[BarAnalysis],
    key_root: int = 9,  # A minor by default (metal-friendly, matches ANCORA)
    guitar_notes: list[GuitarNote] | None = None,
) -> Analysis:
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=guitar_notes or [],
    )


def _element(
    id: str = "pad_main",
    *,
    harmony: str = "follow_chords",
    degrees: list[int] | None = None,
    articulation: str = "sustained",
    sections: tuple[str, ...] = ("MAIN",),
    register: tuple[int, int] = (48, 71),
) -> Element:
    return Element(
        id=id,
        role="pad",
        sections=list(sections),
        register=[register[0], register[1]],
        layers=1,
        sync_role="sustain_through",
        articulation=articulation,
        harmony=harmony,
        degrees=degrees,
    )


def _section(label: str = "MAIN", start_bar: int = 0, end_bar: int = 4) -> PlanSection:
    return PlanSection(
        label=label,
        kind="chorus",
        start_bar=start_bar,
        end_bar=end_bar,
        source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


def _plan(elements: list[Element], sections: list[PlanSection] | None = None) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path="/dev/null", sha256="0" * 64),
        route="cinematica_emocional",
        sections=sections or [_section()],
        elements=elements,
    )


def _note(pitch: int, start_s: float, end_s: float | None = None) -> RenderedNote:
    return RenderedNote(pitch=pitch, start_s=start_s, end_s=end_s or (start_s + 0.5))


def _track(notes: list[RenderedNote], element_id: str = "pad_main", name: str = "Pad") -> RenderedTrack:
    return RenderedTrack(element_id=element_id, track_name=name, notes=tuple(notes))


# --- helpers puros ----------------------------------------------------------

def test_pitch_name_matches_yamaha_convention():
    """Yamaha/Logic convention: middle C (MIDI 60) = C4, so pitch 54 = F#3
    (example from AC)."""
    assert pitch_name(60) == "C4"
    assert pitch_name(54) == "F#3"
    assert pitch_name(45) == "A2"
    assert pitch_name(69) == "A4"
    assert pitch_name(48) == "C3"


def test_chord_pcs_major_minor_power():
    assert chord_pcs(Chord(root=0, quality="major")) == frozenset({0, 4, 7})
    assert chord_pcs(Chord(root=9, quality="minor")) == frozenset({9, 0, 4})
    assert chord_pcs(Chord(root=2, quality="power")) == frozenset({2, 9})
    assert chord_pcs(Chord(root=0, quality="unknown")) is None
    assert chord_pcs(None) is None


def test_key_scale_natural_minor():
    assert key_scale_pcs(9) == frozenset({(9 + i) % 12 for i in NATURAL_MINOR_INTERVALS})


def test_degrees_pcs_wraps_beyond_seven():
    # A minor: degree 1=A(9), 3=C(0), 5=E(4), 8=A(9), 10=C(0).
    assert degrees_pcs(9, [1, 3, 5]) == frozenset({9, 0, 4})
    assert degrees_pcs(9, [1, 5, 8, 10]) == frozenset({9, 4, 0})


def test_degrees_pcs_ignores_invalid_entries():
    # Zero, negatives, and non-int entries are silently skipped.
    assert degrees_pcs(9, [0, -1, "x", 1]) == frozenset({9})  # type: ignore[list-item]
    assert degrees_pcs(9, [0]) == frozenset()


def test_pedal_intervals_are_root_and_fifth():
    assert frozenset({0, 7}) == PEDAL_INTERVALS


# --- pedal ------------------------------------------------------------------

def test_pedal_root_only_passes():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="pedal")
    tr = _track([_note(pitch=45, start_s=0.0), _note(pitch=57, start_s=1.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert issues == []


def test_pedal_fifth_of_key_passes():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="pedal")
    # A minor -> fifth is E (pc 4). E2=40, E3=52.
    tr = _track([_note(pitch=40, start_s=0.0), _note(pitch=52, start_s=1.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert issues == []


def test_pedal_wrong_pitch_class_is_error():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="pedal")
    # C is neither root nor fifth of A key. Pitch 48 = C3 (Yamaha/Logic conv).
    tr = _track([_note(pitch=48, start_s=0.0), _note(pitch=48, start_s=1.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 2
    for issue in issues:
        assert issue.severity == SEVERITY_ERROR
        assert issue.element_id == "pad_main"
        assert issue.bar == 1
        assert issue.pitch == 48
        assert "C3" in issue.message
        assert "pad_main" in issue.message


def test_pedal_empty_track_produces_no_issue():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="pedal")
    issues = validate_harmony([_track([], name="Pedal")], _plan([el]), ana)
    assert issues == []


def test_pedal_note_outside_any_bar_reports_bar_zero():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="pedal")
    # Onset way past the last bar (bar ends at 2.0s) — _find_bar returns None
    # and _bar_number falls back to 0.
    tr = _track([_note(pitch=48, start_s=99.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].bar == 0


def test_pedal_multiple_distinct_pitches_is_error():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="pedal")
    # A and E both allowed pcs, but 2 distinct = still error (pedal is a single note).
    tr = _track([_note(pitch=45, start_s=0.0), _note(pitch=40, start_s=0.5)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR
    assert "single pitch class" in issues[0].message


# --- free -------------------------------------------------------------------

def test_free_outside_scale_is_warning_not_error():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="free")
    # A minor scale: A B C D E F G. C# (61) is out of scale.
    tr = _track([_note(pitch=61, start_s=0.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_WARNING
    assert issues[0].pitch == 61


def test_free_inside_scale_no_issue():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="free")
    # A, B, C, D, E, F, G all in A minor.
    tr = _track([_note(pitch=45, start_s=0.0), _note(pitch=48, start_s=0.5)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert issues == []


# --- follow_chords ---------------------------------------------------------

def test_follow_chords_pad_correct_passes():
    """AC: pad follow_chords correto passa."""
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="follow_chords")
    # A minor chord: A(9), C(0), E(4). Pad plays those.
    tr = _track([
        _note(pitch=57, start_s=0.0, end_s=1.9),   # A3
        _note(pitch=60, start_s=0.0, end_s=1.9),   # C4
        _note(pitch=64, start_s=0.0, end_s=1.9),   # E4
    ])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert issues == []


def test_follow_chords_one_wrong_note_fails_with_bar_and_pitch():
    """AC: mesma configuracao com uma nota trocada manualmente falha citando
    compasso e nota."""
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="follow_chords")
    # Swap C(60) for F#(66) — outside A minor chord.
    tr = _track([
        _note(pitch=57, start_s=0.0, end_s=1.9),
        _note(pitch=66, start_s=0.0, end_s=1.9),
        _note(pitch=64, start_s=0.0, end_s=1.9),
    ])
    issues = validate_harmony([tr], _plan([el]), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert len(errors) == 1
    e = errors[0]
    assert e.pitch == 66
    assert e.bar == 1
    assert "F#4" in e.message
    assert "pad_main" in e.message


def test_follow_chords_degrees_restrict_to_declared_pcs():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    # degrees=[1,5] over A minor => allowed pcs = {A, E} only.
    el = _element(harmony="follow_chords", degrees=[1, 5])
    # C(60) is in the chord but NOT in declared degrees => error.
    tr = _track([_note(pitch=60, start_s=0.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR
    assert "degrees" in issues[0].message


def test_bar_without_chord_falls_back_to_key_and_warns_not_blocks():
    """FR-25: compasso sem acorde determinavel cai para escala do tom e
    avisa em vez de bloquear."""
    ana = _analysis([_bar(0, None)])  # no chord detected
    el = _element(harmony="follow_chords")
    # A minor scale contains A(45). Should be fine (warning only, no error).
    tr = _track([_note(pitch=45, start_s=0.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    warns = [i for i in issues if i.severity == SEVERITY_WARNING]
    assert errors == []
    assert warns and "no deterministic chord" in warns[0].message


def test_bar_without_chord_outside_key_scale_is_warning_not_error():
    ana = _analysis([_bar(0, None)])
    el = _element(harmony="follow_chords")
    # C# (61) is out of A minor scale.
    tr = _track([_note(pitch=61, start_s=0.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert all(i.severity == SEVERITY_WARNING for i in issues)


def test_cross_bar_sustain_without_next_bar_produces_no_warning():
    """Sustain que atravessa o ultimo compasso da musica nao dispara aviso
    de proximo acorde — _next_bar retorna None e o loop segue silencioso."""
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="follow_chords")
    tr = _track([_note(pitch=57, start_s=0.0, end_s=BAR_S + 0.5)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert issues == []


def test_follow_chords_onset_outside_any_bar_falls_back_to_key_scale():
    """Nota gerada com onset alem do ultimo compasso da analise cai na
    escala do tom (bar=None => is_fallback=True) e vira aviso, nao erro."""
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element(harmony="follow_chords")
    # A(45) is in A minor scale, onset way past bar 0 => fallback warning only.
    tr = _track([_note(pitch=45, start_s=99.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert all(i.severity == SEVERITY_WARNING for i in issues)
    assert any("no deterministic chord" in i.message for i in issues)


def test_unknown_harmony_mode_is_silently_ignored():
    """Vocabulario de `harmony` e validado por plan.py; se um modo novo
    aparecer sem atualizar o validador, ele nao levanta — cai no branch
    defensivo e devolve zero issues."""
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element()
    el.harmony = "future_mode"
    tr = _track([_note(pitch=48, start_s=0.0)])
    assert validate_harmony([tr], _plan([el]), ana) == []


def test_track_without_matching_element_is_ignored():
    """Track cujo element_id nao existe no plano nao dispara nada — guarda
    defensiva do renderer."""
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))])
    el = _element()
    stray = _track([_note(pitch=48, start_s=0.0)], element_id="ghost")
    assert validate_harmony([stray], _plan([el]), ana) == []


def test_cross_bar_sustain_conflicting_with_next_chord_is_warning():
    """FR-25: nota que atravessa compassos e valida pelo onset; aviso se
    conflitar fortemente com o acorde seguinte enquanto sustenta."""
    bars = [
        _bar(0, Chord(root=9, quality="minor")),  # A minor: A,C,E
        _bar(1, Chord(root=5, quality="major")),  # F major: F,A,C
    ]
    ana = _analysis(bars)
    el = _element(harmony="follow_chords")
    # E note (64) is in A minor chord (onset OK) but not in F major chord.
    tr = _track([_note(pitch=64, start_s=0.0, end_s=BAR_S + 0.5)])
    issues = validate_harmony([tr], _plan([el]), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    warns = [i for i in issues if i.severity == SEVERITY_WARNING]
    assert errors == []
    assert len(warns) == 1
    assert "crosses into bar 2" in warns[0].message


# --- unison_guitar ---------------------------------------------------------

def test_unison_guitar_matching_pitch_class_passes():
    ana = _analysis(
        [_bar(0, Chord(root=9, quality="minor"))],
        guitar_notes=[GuitarNote(start=0.0, pitch=45, track="Guitar 1")],
    )
    el = _element(harmony="unison_guitar")
    # Same pitch class (A) an octave up.
    tr = _track([_note(pitch=57, start_s=0.005)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert issues == []


def test_unison_guitar_no_matching_onset_is_error():
    ana = _analysis(
        [_bar(0, Chord(root=9, quality="minor"))],
        guitar_notes=[GuitarNote(start=0.0, pitch=45, track="Guitar 1")],
    )
    el = _element(harmony="unison_guitar")
    tr = _track([_note(pitch=57, start_s=1.0)])  # far outside window
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR
    assert "unison" in issues[0].message


def test_unison_guitar_wrong_pitch_class_is_error():
    ana = _analysis(
        [_bar(0, Chord(root=9, quality="minor"))],
        guitar_notes=[GuitarNote(start=0.0, pitch=45, track="Guitar 1")],
    )
    el = _element(harmony="unison_guitar")
    tr = _track([_note(pitch=64, start_s=0.0)])  # E, not A
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR


def test_unison_guitar_all_guitar_notes_before_window_returns_no_match():
    """Nota gerada com onset muito tarde: todas as guitar notes ficam antes
    da janela; _find_unison_match percorre a lista sem achar match e cai no
    return final."""
    ana = _analysis(
        [_bar(0, Chord(root=9, quality="minor"))],
        guitar_notes=[
            GuitarNote(start=0.0, pitch=45, track="Guitar 1"),
            GuitarNote(start=0.1, pitch=45, track="Guitar 1"),
        ],
    )
    el = _element(harmony="unison_guitar")
    tr = _track([_note(pitch=57, start_s=1.5)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR


def test_unison_guitar_early_abort_when_guitar_note_past_window():
    """guitar_notes ordenado por start: se a proxima nota ja passou a janela
    superior, _find_unison_match aborta cedo e devolve None."""
    ana = _analysis(
        [_bar(0, Chord(root=9, quality="minor"))],
        guitar_notes=[GuitarNote(start=5.0, pitch=45, track="Guitar 1")],
    )
    el = _element(harmony="unison_guitar")
    tr = _track([_note(pitch=57, start_s=0.0)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR


def test_unison_guitar_no_guitar_notes_emits_single_error():
    ana = _analysis([_bar(0, Chord(root=9, quality="minor"))], guitar_notes=[])
    el = _element(harmony="unison_guitar")
    tr = _track([_note(pitch=45, start_s=0.0), _note(pitch=45, start_s=0.5)])
    issues = validate_harmony([tr], _plan([el]), ana)
    assert len(issues) == 1
    assert "no guitar notes" in issues[0].message


# --- utility ---------------------------------------------------------------

def test_has_errors_true_with_error_false_with_only_warnings():
    warn = HarmonyIssue(
        severity=SEVERITY_WARNING, element_id="x", track="X", bar=1,
        pitch=60, expected="", message="",
    )
    err = HarmonyIssue(
        severity=SEVERITY_ERROR, element_id="x", track="X", bar=1,
        pitch=60, expected="", message="",
    )
    assert not has_errors([warn])
    assert has_errors([warn, err])


def test_format_issues_all_ok_emits_ok():
    assert format_issues([]) == "Harmony: OK"


def test_format_issues_lists_errors_and_warnings():
    err = HarmonyIssue(
        severity=SEVERITY_ERROR, element_id="a", track="A", bar=1,
        pitch=60, expected="", message="bad note",
    )
    warn = HarmonyIssue(
        severity=SEVERITY_WARNING, element_id="a", track="A", bar=1,
        pitch=60, expected="", message="mild warning",
    )
    out = format_issues([warn, err])
    assert "1 error(s)" in out
    assert "1 warning(s)" in out
    assert "bad note" in out
    assert "mild warning" in out


# --- integracao com render (usa gerador de pad real) ----------------------

def _build_synthetic_source(tmp_path: Path) -> Path:
    """MIDI: 4 compassos em 4/4 a 120bpm, piano com triade A minor + baixo A2."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    # A minor triad in every bar: A3(57), C4(60), E4(64)
    for bar in range(4):
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


def _hash_bytes(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pad_plan(source: Path) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=7,
        source_midi=SourceMidi(path=str(source), sha256=_hash_bytes(source)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN", kind="chorus", start_bar=0, end_bar=4,
                source="marker", protagonist="texture",
                energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
            ),
        ],
        elements=[
            Element(
                id="pad_main", role="pad", sections=["MAIN"],
                register=[48, 71], layers=1,
                sync_role="sustain_through", articulation="sustained",
                harmony="follow_chords",
                dynamics={"shape": "hold"},
                instrument={"plugin": "Omnisphere", "preset": "Desert Wind", "verified": True},
                rationale="test pad",
            ),
        ],
    )


def test_render_pad_follow_chords_produces_no_harmony_errors(tmp_path):
    """AC: pad follow_chords correto passa nos validadores."""
    src = _build_synthetic_source(tmp_path)
    out = tmp_path / "out.mid"
    report = render(_pad_plan(src), out)
    errors = [i for i in report.harmony_issues if i.severity == SEVERITY_ERROR]
    assert errors == [], f"unexpected errors: {[i.message for i in errors]}"


def test_render_report_carries_harmony_issues_list(tmp_path):
    src = _build_synthetic_source(tmp_path)
    out = tmp_path / "out.mid"
    report = render(_pad_plan(src), out)
    assert isinstance(report.harmony_issues, list)


# --- regressao ANCORA ------------------------------------------------------

ANCORA_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _ancora_path() -> str:
    if os.path.exists(ANCORA_FIXTURE):
        return ANCORA_FIXTURE
    pytest.skip("ANCORA reference MIDI not available in this environment")


def test_ancora_pad_render_passes_harmony(tmp_path):
    """AC: render real do ANCORA com pad passa sem erro."""
    src_ref = _ancora_path()
    import shutil
    src = tmp_path / "ancora.mid"
    shutil.copy(src_ref, src)

    ana = analyze(str(src))
    # Build minimal plan: one MAIN section spanning all bars, one pad.
    n_bars = len(ana.bars)
    if n_bars == 0:
        pytest.skip("ANCORA analysis returned no bars")
    plan = ArrangementPlan(
        version=1, seed=1,
        source_midi=SourceMidi(path=str(src), sha256=_hash_bytes(src)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN", kind="chorus", start_bar=0, end_bar=n_bars,
                source="marker", protagonist="texture",
                energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
            ),
        ],
        elements=[
            Element(
                id="pad_main", role="pad", sections=["MAIN"],
                register=[48, 71], layers=1,
                sync_role="sustain_through", articulation="sustained",
                harmony="follow_chords",
                dynamics={"shape": "hold"},
                instrument={"plugin": "Omnisphere", "preset": "Desert Wind", "verified": True},
            ),
        ],
    )
    report = render(plan, tmp_path / "out.mid")
    errors = [i for i in report.harmony_issues if i.severity == SEVERITY_ERROR]
    assert errors == [], (
        "ANCORA regression: pad follow_chords should not error harmony; "
        f"got: {[i.message for i in errors[:5]]}"
    )
