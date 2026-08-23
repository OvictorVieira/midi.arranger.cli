"""Testes do validador de placement e densidade (FR-26, US-002)."""

from __future__ import annotations

import os
from pathlib import Path

import pretty_midi
import pytest

from tools.analyze import (
    Analysis,
    BarAnalysis,
    Chord,
    analyze,
)
from tools.plan import ArrangementPlan, Element, PlanSection, SourceMidi
from tools.render import render
from tools.validators.harmony import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    RenderedNote,
    RenderedTrack,
)
from tools.validators.placement import (
    DENSITY_LOW_AXIS_THRESHOLD,
    DENSITY_MULTIPLIER,
    TAIL_ALLOWED_ARTICULATIONS,
    TRANSITION_EXEMPT_ROLES,
    PlacementIssue,
    format_issues,
    has_errors,
    validate_placement,
)

# --- fixtures ---------------------------------------------------------------

BAR_S = 2.0


def _bar(index: int, chord: Chord | None = None) -> BarAnalysis:
    return BarAnalysis(
        index=index,
        start=index * BAR_S,
        end=(index + 1) * BAR_S,
        chord=chord,
    )


def _analysis(bars: list[BarAnalysis], key_root: int = 9) -> Analysis:
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(
    label: str,
    start_bar: int,
    end_bar: int,
    *,
    densidade: int = 5,
) -> PlanSection:
    return PlanSection(
        label=label,
        kind="chorus",
        start_bar=start_bar,
        end_bar=end_bar,
        source="marker",
        protagonist="texture",
        energy={
            "densidade": densidade, "impacto": 5, "largura": 5,
            "altura": 5, "instabilidade": 3,
        },
    )


def _element(
    id: str = "pad_main",
    *,
    role: str = "pad",
    sections: tuple[str, ...] = ("MAIN",),
    articulation: str = "sustained",
) -> Element:
    return Element(
        id=id, role=role, sections=list(sections),
        register=[48, 71], layers=1,
        sync_role="sustain_through", articulation=articulation,
        harmony="follow_chords",
    )


def _plan(
    elements: list[Element],
    sections: list[PlanSection] | None = None,
) -> ArrangementPlan:
    return ArrangementPlan(
        version=1, seed=0,
        source_midi=SourceMidi(path="/dev/null", sha256="0" * 64),
        route="cinematica_emocional",
        sections=sections or [_section("MAIN", 0, 4)],
        elements=elements,
    )


def _note(pitch: int, start_s: float, end_s: float | None = None) -> RenderedNote:
    return RenderedNote(pitch=pitch, start_s=start_s, end_s=end_s or (start_s + 0.5))


def _track(
    notes: list[RenderedNote],
    element_id: str = "pad_main",
    name: str = "Pad",
) -> RenderedTrack:
    return RenderedTrack(element_id=element_id, track_name=name, notes=tuple(notes))


# --- constantes exportadas -------------------------------------------------

def test_transition_exempt_roles_starts_empty_this_round():
    """R2 nao tem elemento de transicao ainda; a lista fica preenchida na
    rodada 3 (riser/impact)."""
    assert frozenset() == TRANSITION_EXEMPT_ROLES


def test_tail_allowed_articulations_is_sustained_and_let_ring():
    assert frozenset({"sustained", "let_ring"}) == TAIL_ALLOWED_ARTICULATIONS


def test_density_thresholds_are_stable():
    # 3 e 2.0 sao os defaults do AC de US-002; qualquer mudanca vira nova
    # constante comentada com justificativa (mesmo padrao do harmony_validator).
    assert DENSITY_LOW_AXIS_THRESHOLD == 3
    assert DENSITY_MULTIPLIER == 2.0


# --- placement: onset dentro/fora da secao ---------------------------------

def test_note_inside_declared_section_passes():
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element(sections=("MAIN",))
    tr = _track([
        _note(pitch=60, start_s=0.5),
        _note(pitch=60, start_s=BAR_S + 0.5),
    ])
    plan = _plan([el], [_section("MAIN", 0, 4)])
    assert validate_placement([tr], plan, ana) == []


def test_note_outside_declared_section_is_error_with_nearest():
    """AC: elemento declarado em [CHORUS 1] com nota forcada em [VERSE] gera
    erro citando a nota."""
    ana = _analysis([_bar(i) for i in range(6)])
    el = _element(sections=("CHORUS_1",))
    sections = [_section("VERSE", 0, 2), _section("CHORUS_1", 2, 4)]
    # Nota em bar 0 (segundos 0-2) — fora de CHORUS_1 (bars 2-4).
    tr = _track([_note(pitch=60, start_s=0.5)])
    issues = validate_placement([tr], _plan([el], sections), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert len(errors) == 1
    e = errors[0]
    assert e.element_id == "pad_main"
    assert e.bar == 1
    assert e.pitch == 60
    assert e.section == "CHORUS_1"
    assert "onset outside" in e.message
    assert "CHORUS_1" in e.message


def test_onset_beyond_last_bar_reports_bar_zero_and_nearest_section():
    ana = _analysis([_bar(i) for i in range(2)])
    el = _element(sections=("MAIN",))
    tr = _track([_note(pitch=60, start_s=999.0)])
    issues = validate_placement([tr], _plan([el], [_section("MAIN", 0, 2)]), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert len(errors) == 1
    assert errors[0].bar == 0
    assert errors[0].section == "MAIN"


def test_note_before_first_declared_section_picks_nearest_by_distance():
    ana = _analysis([_bar(i) for i in range(8)])
    el = _element(sections=("A", "B"))
    sections = [_section("A", 4, 6), _section("B", 6, 8)]
    # Nota em bar 0 — mais perto de A (dist 4) que de B (dist 6).
    tr = _track([_note(pitch=60, start_s=0.5)])
    issues = validate_placement([tr], _plan([el], sections), ana)
    assert issues[0].section == "A"


def test_note_after_all_declared_sections_picks_nearest_by_distance():
    ana = _analysis([_bar(i) for i in range(8)])
    el = _element(sections=("A", "B"))
    sections = [_section("A", 0, 2), _section("B", 2, 4)]
    # Nota em bar 7 — mais perto de B (bar 3 e o ultimo de B, dist 4)
    # que de A (bar 1 e o ultimo de A, dist 6).
    tr = _track([_note(pitch=60, start_s=7 * BAR_S + 0.1)])
    issues = validate_placement([tr], _plan([el], sections), ana)
    assert issues[0].section == "B"


# --- tail: sustained/let_ring ---------------------------------------------

def test_sustained_tail_crossing_one_bar_passes():
    """AC: let_ring atravessando 1 compasso passa."""
    ana = _analysis([_bar(i) for i in range(6)])
    el = _element(sections=("MAIN",), articulation="sustained")
    sections = [_section("MAIN", 0, 4)]
    # Nota comeca em bar 3 (index 3, MAIN half-open [0,4) => contains bar 3),
    # end_s cruza para bar 4 (primeiro bar apos a secao) mas nao alem.
    tr = _track([_note(
        pitch=60, start_s=3 * BAR_S + 0.5, end_s=5 * BAR_S - 0.1,
    )])
    issues = validate_placement([tr], _plan([el], sections), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert errors == []


def test_let_ring_tail_crossing_two_bars_is_error():
    """AC: let_ring atravessando 2 compassos falha."""
    ana = _analysis([_bar(i) for i in range(8)])
    el = _element(sections=("MAIN",), articulation="let_ring")
    sections = [_section("MAIN", 0, 4)]
    # end_s > final do bar 4 (5*BAR_S) => cauda estende para bar 5 = erro.
    tr = _track([_note(
        pitch=60, start_s=3 * BAR_S + 0.5, end_s=6 * BAR_S - 0.1,
    )])
    issues = validate_placement([tr], _plan([el], sections), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert len(errors) == 1
    assert "tail extends beyond" in errors[0].message
    assert "MAIN" in errors[0].message


def test_sustained_tail_at_song_end_produces_no_error():
    """Sem bar seguinte (musica termina), qualquer cauda sustentada passa —
    o validador nao pode inventar bar que a analise nao viu."""
    ana = _analysis([_bar(0), _bar(1)])
    el = _element(sections=("MAIN",), articulation="sustained")
    sections = [_section("MAIN", 0, 2)]
    tr = _track([_note(
        pitch=60, start_s=BAR_S + 0.1, end_s=BAR_S * 5,
    )])
    issues = validate_placement([tr], _plan([el], sections), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert errors == []


def test_non_tail_articulation_does_not_check_tail():
    """Staccato/tight nao tem regra de cauda — validador so olha onset."""
    ana = _analysis([_bar(i) for i in range(6)])
    el = _element(sections=("MAIN",), articulation="staccato")
    sections = [_section("MAIN", 0, 4)]
    # end_s extremo, mas onset dentro da secao — sem regra de cauda, passa.
    tr = _track([_note(
        pitch=60, start_s=0.5, end_s=999.0,
    )])
    issues = validate_placement([tr], _plan([el], sections), ana)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    assert errors == []


# --- coverage ---------------------------------------------------------------

def test_empty_section_generates_coverage_warning():
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element(sections=("A", "B"))
    sections = [_section("A", 0, 2), _section("B", 2, 4)]
    # Nota so em A (bar 0).
    tr = _track([_note(pitch=60, start_s=0.5)])
    issues = validate_placement([tr], _plan([el], sections), ana)
    warnings = [i for i in issues if i.severity == SEVERITY_WARNING]
    assert any(i.section == "B" and "produced no notes" in i.message for i in warnings)
    assert not any(i.section == "A" and "produced no notes" in i.message for i in warnings)


def test_no_coverage_warning_for_element_without_any_track():
    """Elemento no plano cujo renderer pulou (nao emitiu track alguma) NAO
    vira aviso — o aviso e sobre saida, nao intencao."""
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element()
    plan = _plan([el], [_section("MAIN", 0, 4)])
    issues = validate_placement([], plan, ana)
    assert issues == []


# --- densidade -------------------------------------------------------------

def test_high_density_in_low_density_section_warns_with_numbers():
    """AC: secao com densidade 2 e elemento denso gera aviso com numeros.

    Precisa de 3+ elementos ativos: incluir a densidade do proprio elemento
    na media impede que 2 elementos disparem entre si (matematicamente, com
    apenas 2 densidades a > b > 0, b nunca excede 2 * (a+b)/2 = a+b)."""
    ana = _analysis([_bar(i) for i in range(4)])
    # Secao com densidade axis = 2 (<= 3, dispara scrutinio).
    sections = [_section("MAIN", 0, 4, densidade=2)]
    q1 = _element("pad_q1", articulation="sustained")
    q2 = _element("pad_q2", articulation="sustained")
    dense = _element("arp_dense", articulation="sustained")
    # q1, q2: 4 notas em 4 bars = 1.0 nota/bar cada
    # dense: 40 notas em 4 bars = 10 notas/bar
    # mean = (1 + 1 + 10) / 3 = 4.0; dense (10) > 2 * 4.0 = 8.0 => aviso.
    q_notes_1 = [_note(pitch=60, start_s=b * BAR_S + 0.1) for b in range(4)]
    q_notes_2 = [_note(pitch=62, start_s=b * BAR_S + 0.2) for b in range(4)]
    dense_notes = [
        _note(pitch=64, start_s=b * BAR_S + i * 0.05)
        for b in range(4) for i in range(10)
    ]
    plan = _plan([q1, q2, dense], sections)
    issues = validate_placement(
        [_track(q_notes_1, element_id="pad_q1"),
         _track(q_notes_2, element_id="pad_q2"),
         _track(dense_notes, element_id="arp_dense")],
        plan, ana,
    )
    warnings = [i for i in issues if i.severity == SEVERITY_WARNING
                and i.element_id == "arp_dense"
                and "density" in i.message]
    assert warnings, f"expected density warning; issues={[i.message for i in issues]}"
    msg = warnings[0].message
    assert "notes/bar" in msg
    assert "mean" in msg
    assert "densidade axis=2" in msg


def test_density_warning_suppressed_when_axis_above_threshold():
    """Secao com densidade axis > 3 nao dispara aviso — o mapa manda."""
    ana = _analysis([_bar(i) for i in range(4)])
    sections = [_section("MAIN", 0, 4, densidade=8)]
    q1 = _element("q1", articulation="sustained")
    q2 = _element("q2", articulation="sustained")
    dense = _element("arp_dense", articulation="sustained")
    q_notes = [_note(pitch=60, start_s=b * BAR_S + 0.1) for b in range(4)]
    dense_notes = [
        _note(pitch=64, start_s=b * BAR_S + i * 0.05)
        for b in range(4) for i in range(10)
    ]
    plan = _plan([q1, q2, dense], sections)
    issues = validate_placement(
        [_track(q_notes, element_id="q1"),
         _track(q_notes, element_id="q2"),
         _track(dense_notes, element_id="arp_dense")],
        plan, ana,
    )
    warnings = [i for i in issues if "density" in i.message]
    assert warnings == []


def test_density_warning_needs_multiple_elements_in_section():
    """Elemento solo na secao nao dispara aviso — nao ha o que comparar."""
    ana = _analysis([_bar(i) for i in range(4)])
    sections = [_section("MAIN", 0, 4, densidade=2)]
    el = _element("solo", articulation="sustained")
    notes = [
        _note(pitch=60, start_s=b * BAR_S + i * 0.05)
        for b in range(4) for i in range(20)
    ]
    plan = _plan([el], sections)
    issues = validate_placement([_track(notes, element_id="solo")], plan, ana)
    warnings = [i for i in issues if "density" in i.message]
    assert warnings == []


def test_density_warning_ignored_when_energy_missing():
    """Secao sem energy dict (defensivo — plan.validate ja bloqueia): a
    checagem de densidade sai silenciosamente sem KeyError."""
    ana = _analysis([_bar(i) for i in range(4)])
    section = _section("MAIN", 0, 4)
    section.energy = None
    sections = [section]
    el1 = _element("a", articulation="sustained")
    el2 = _element("b", articulation="sustained")
    el3 = _element("c", articulation="sustained")
    plan = _plan([el1, el2, el3], sections)
    notes_a = [_note(pitch=60, start_s=b * BAR_S + 0.1) for b in range(4)]
    notes_c = [_note(pitch=64, start_s=b * BAR_S + i * 0.05)
               for b in range(4) for i in range(10)]
    issues = validate_placement(
        [_track(notes_a, element_id="a"),
         _track(notes_a, element_id="b"),
         _track(notes_c, element_id="c")],
        plan, ana,
    )
    assert not any("density" in i.message for i in issues)


# --- guards defensivos -----------------------------------------------------

def test_track_without_matching_element_is_ignored():
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element()
    stray = _track([_note(pitch=60, start_s=0.5)], element_id="ghost")
    plan = _plan([el], [_section("MAIN", 0, 4)])
    assert validate_placement([stray], plan, ana) == []


def test_element_with_no_declared_sections_is_silent():
    """Defensivo: se sections for lista vazia (plan.validate ja rejeita mas
    testes constroem Element direto), o validador nao levanta."""
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element()
    el.sections = []
    tr = _track([_note(pitch=60, start_s=0.5)])
    plan = _plan([el])
    assert validate_placement([tr], plan, ana) == []


def test_element_with_section_label_not_in_plan_is_silent():
    """Defensivo: label declarado no elemento que nao existe em
    plan.sections cai fora do dict de sections_by_label. O validador nao
    levanta — plan.validate ja bloqueia isso antes do render."""
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element(sections=("GHOST",))
    tr = _track([_note(pitch=60, start_s=0.5)])
    plan = _plan([el], [_section("MAIN", 0, 4)])
    assert validate_placement([tr], plan, ana) == []


def test_transition_exempt_role_bypasses_all_checks(monkeypatch):
    """Isencao por role — quando um role entra em TRANSITION_EXEMPT_ROLES
    (rodada 3), notas fora de secao viram silencio."""
    from tools.validators import placement
    monkeypatch.setattr(placement, "TRANSITION_EXEMPT_ROLES", frozenset({"riser"}))
    ana = _analysis([_bar(i) for i in range(4)])
    el = _element(role="riser", sections=("MAIN",))
    # Note WAY outside declared section — normally would error.
    tr = _track([_note(pitch=60, start_s=999.0)])
    plan = _plan([el], [_section("MAIN", 0, 4)])
    assert placement.validate_placement([tr], plan, ana) == []


def test_density_ignores_sections_with_no_notes():
    """Secao onde ninguem tocou nao entra no per_section dict — o loop
    principal so passa quando count > 0. Sem KeyError, sem aviso."""
    ana = _analysis([_bar(i) for i in range(4)])
    sections = [_section("MAIN", 0, 2, densidade=2), _section("B", 2, 4, densidade=2)]
    a = _element("a", sections=("MAIN", "B"), articulation="sustained")
    b = _element("b", sections=("MAIN", "B"), articulation="sustained")
    # ambos tocam so em MAIN
    notes = [_note(pitch=60, start_s=0.1)]
    plan = _plan([a, b], sections)
    issues = validate_placement(
        [_track(notes, element_id="a"),
         _track(notes, element_id="b")],
        plan, ana,
    )
    # B nao dispara density (nada nela); MAIN tambem nao (densidades iguais).
    assert not any(i.section == "B" and "density" in i.message for i in issues)


# --- utility ---------------------------------------------------------------

def test_has_errors_true_with_error():
    warn = PlacementIssue(
        severity=SEVERITY_WARNING, element_id="x", track="X", bar=1,
        pitch=60, section="MAIN", message="",
    )
    err = PlacementIssue(
        severity=SEVERITY_ERROR, element_id="x", track="X", bar=1,
        pitch=60, section="MAIN", message="",
    )
    assert not has_errors([warn])
    assert has_errors([warn, err])


def test_format_issues_ok_when_empty():
    assert format_issues([]) == "Placement: OK"


def test_format_issues_lists_errors_and_warnings():
    err = PlacementIssue(
        severity=SEVERITY_ERROR, element_id="a", track="A", bar=1,
        pitch=60, section="MAIN", message="oops",
    )
    warn = PlacementIssue(
        severity=SEVERITY_WARNING, element_id="a", track="A", bar=1,
        pitch=60, section="MAIN", message="mild",
    )
    out = format_issues([warn, err])
    assert "1 error(s)" in out
    assert "1 warning(s)" in out
    assert "oops" in out
    assert "mild" in out


# --- integracao com render (sanity + report shape) ------------------------

def _build_synthetic_source(tmp_path: Path) -> Path:
    """MIDI: 4 compassos em 4/4 a 120bpm — triade Am + baixo A2."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
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
        version=1, seed=1,
        source_midi=SourceMidi(path=str(source), sha256=_hash_bytes(source)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN", kind="chorus", start_bar=0, end_bar=4,
                source="marker", protagonist="texture",
                energy={"densidade": 5, "impacto": 5, "largura": 5,
                        "altura": 5, "instabilidade": 3},
            ),
        ],
        elements=[
            Element(
                id="pad_main", role="pad", sections=["MAIN"],
                register=[48, 71], layers=1,
                sync_role="sustain_through", articulation="sustained",
                harmony="follow_chords",
                dynamics={"shape": "hold"},
                instrument={"plugin": "Omnisphere", "preset": "Desert Wind",
                            "verified": True},
            ),
        ],
    )


def test_render_report_carries_placement_issues_list(tmp_path):
    src = _build_synthetic_source(tmp_path)
    out = tmp_path / "out.mid"
    report = render(_pad_plan(src), out)
    assert isinstance(report.placement_issues, list)


def test_render_pad_produces_no_placement_errors(tmp_path):
    """Pad gerado respeita section boundaries por construcao — sem erros."""
    src = _build_synthetic_source(tmp_path)
    out = tmp_path / "out.mid"
    report = render(_pad_plan(src), out)
    errors = [i for i in report.placement_issues if i.severity == SEVERITY_ERROR]
    assert errors == []


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


def test_ancora_pad_render_passes_placement(tmp_path):
    """AC: render real do ANCORA com pad passa sem erro de placement."""
    src_ref = _ancora_path()
    import shutil
    src = tmp_path / "ancora.mid"
    shutil.copy(src_ref, src)

    ana = analyze(str(src))
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
                energy={"densidade": 5, "impacto": 5, "largura": 5,
                        "altura": 5, "instabilidade": 3},
            ),
        ],
        elements=[
            Element(
                id="pad_main", role="pad", sections=["MAIN"],
                register=[48, 71], layers=1,
                sync_role="sustain_through", articulation="sustained",
                harmony="follow_chords",
                dynamics={"shape": "hold"},
                instrument={"plugin": "Omnisphere", "preset": "Desert Wind",
                            "verified": True},
            ),
        ],
    )
    report = render(plan, tmp_path / "out.mid")
    errors = [i for i in report.placement_issues if i.severity == SEVERITY_ERROR]
    assert errors == [], (
        "ANCORA regression: pad in single MAIN section should not error placement; "
        f"got: {[i.message for i in errors[:5]]}"
    )
