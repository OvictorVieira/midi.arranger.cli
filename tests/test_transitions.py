"""Testes do validador de duas dimensoes na transicao (AC-14, issue #24)."""

from __future__ import annotations

from tools.analyze import Analysis, BarAnalysis
from tools.plan import ArrangementPlan, Element, PlanSection, SourceMidi, Transition
from tools.validators.harmony import RenderedNote, RenderedTrack
from tools.validators.transitions import (
    MIN_DIMENSIONS_CHANGED,
    TRANSITION_DIMENSIONS,
    format_issues,
    has_errors,
    validate_transitions,
)

BAR_S = 2.0


def _bar(index: int) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=None,
    )


def _analysis(bars: int = 8) -> Analysis:
    return Analysis(
        key_root=0,
        bars=[_bar(i) for i in range(bars)],
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _element(id: str, section: str) -> Element:
    return Element(
        id=id, role="pad", sections=[section],
        register=[48, 84], layers=1,
        sync_role="sustain_through", articulation="sustained",
        harmony="follow_chords",
        rationale="Elemento fixture do validador de transicao.",
    )


def _plan(*, dimensions_changed: list[str], elements: list[str] | None = None) -> ArrangementPlan:
    sections = [
        PlanSection(
            label="MAIN", kind="verse", start_bar=0, end_bar=4,
            source="marker", protagonist="texture",
            energy={"densidade": 4, "impacto": 4, "largura": 4,
                    "altura": 4, "instabilidade": 3},
        ),
        PlanSection(
            label="NEXT", kind="chorus", start_bar=4, end_bar=8,
            source="marker", protagonist="texture",
            energy={"densidade": 8, "impacto": 8, "largura": 8,
                    "altura": 8, "instabilidade": 5},
        ),
    ]
    return ArrangementPlan(
        version=1, seed=0,
        source_midi=SourceMidi(path="/dev/null", sha256="0" * 64),
        route="cinematica_emocional",
        sections=sections,
        elements=[_element("pad_a", "MAIN"), _element("pad_b1", "NEXT"), _element("pad_b2", "NEXT")],
        transitions=[
            Transition(
                at_bar=4, from_section="MAIN", to_section="NEXT",
                dimensions_changed=dimensions_changed,
                elements=elements or ["pad_b1"],
                technique="entrada com acorde aberto",
            ),
        ],
    )


def _note(pitch: int, start_s: float, duration: float = 0.4, velocity: int = 60) -> RenderedNote:
    return RenderedNote(pitch=pitch, start_s=start_s, end_s=start_s + duration, velocity=velocity)


def _strong_tracks() -> list[RenderedTrack]:
    """Janela A ('MAIN', t=[0,8)): 8 notas mono, C4, espacadas 1s, 1
    elemento. Janela B ('NEXT', t=[8,16)): acordes de 3 notas (C5 E5 G5)
    a cada 0.5s, divididos entre 2 elementos — muda TODAS as 8 dimensoes
    (densidade, subdivisao, registro, largura, textura, harmonia,
    perspectiva_espacial, protagonista)."""
    notes_a = [_note(60, float(i)) for i in range(8)]
    track_a = RenderedTrack(element_id="pad_a", track_name="Pad A", notes=tuple(notes_a))

    notes_b1: list[RenderedNote] = []
    notes_b2: list[RenderedNote] = []
    for i in range(16):
        t = 8.0 + i * 0.5
        notes_b1.append(_note(72, t))
        notes_b1.append(_note(76, t))
        notes_b2.append(_note(79, t))
    track_b1 = RenderedTrack(element_id="pad_b1", track_name="Pad B1", notes=tuple(notes_b1))
    track_b2 = RenderedTrack(element_id="pad_b2", track_name="Pad B2", notes=tuple(notes_b2))
    return [track_a, track_b1, track_b2]


def _weak_tracks() -> list[RenderedTrack]:
    """Mesmo padrao ritmico/harmonico/registro/textura dos dois lados da
    fronteira, so a velocity muda (60 -> 100) — a transicao 'fraca' que a
    persona descreve: 'muda so o volume'. Nenhuma das 8 dimensoes deveria
    acusar mudanca."""
    notes_a = [_note(60, float(i), velocity=60) for i in range(8)]
    notes_b = [_note(60, 8.0 + i, velocity=100) for i in range(8)]
    track_a = RenderedTrack(element_id="pad_a", track_name="Pad A", notes=tuple(notes_a))
    track_b = RenderedTrack(element_id="pad_a", track_name="Pad A", notes=tuple(notes_b))
    return [track_a, track_b]


# --- transicao valida --------------------------------------------------------

def test_strong_transition_produces_no_weak_issue():
    plan = _plan(dimensions_changed=["densidade", "registro"])
    analysis = _analysis()
    issues = validate_transitions(_strong_tracks(), plan, analysis)
    weak = [i for i in issues if i.kind == "weak_transition"]
    assert weak == []


def test_strong_transition_intent_matches_reality_no_divergence():
    plan = _plan(dimensions_changed=["densidade", "registro"])
    analysis = _analysis()
    issues = validate_transitions(_strong_tracks(), plan, analysis)
    unrealized = [i for i in issues if i.kind == "unrealized_intent"]
    assert unrealized == []


def test_strong_transition_changes_all_eight_dimensions():
    """Prova de verdade (nao so 'pelo menos 2') de que `_strong_tracks()`
    exercita as 8 dimensoes — usa as funcoes internas do modulo pra
    inspecionar o veredito por dimensao, em vez de inferir isso so pela
    ausencia de `weak_transition` (que so exige 2)."""
    from tools.validators import transitions as _t

    plan = _plan(dimensions_changed=[])
    analysis = _analysis()
    sections = {s.label: s for s in plan.sections}
    bars = _t._bar_lookup(analysis)
    bounds_a = _t._window_bounds(sections["MAIN"], tail=True, bars_by_index=bars)
    bounds_b = _t._window_bounds(sections["NEXT"], tail=False, bars_by_index=bars)
    tracks = _strong_tracks()
    events_a = _t._notes_in_window(tracks, bounds_a)
    events_b = _t._notes_in_window(tracks, bounds_b)
    metrics_a = _t._compute_metrics(events_a, bounds_a[1] - bounds_a[0])
    metrics_b = _t._compute_metrics(events_b, bounds_b[1] - bounds_b[0])

    changed = {
        dim: _t._changed(dim, metrics_a, metrics_b) for dim in TRANSITION_DIMENSIONS
    }
    assert all(changed.values()), changed


# --- transicao fraca ("muda so o volume") -----------------------------------

def test_weak_transition_is_flagged():
    plan = _plan(dimensions_changed=[])
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    weak = [i for i in issues if i.kind == "weak_transition"]
    assert len(weak) == 1


def test_weak_transition_names_the_unchanged_dimensions():
    plan = _plan(dimensions_changed=[])
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    weak = [i for i in issues if i.kind == "weak_transition"][0]
    # Velocity nao e uma das 8 dimensoes — com onset/pitch/duracao/
    # elemento identicos dos dois lados, as 8 ficam iguais.
    assert set(weak.dimensions) == set(TRANSITION_DIMENSIONS)
    assert weak.severity == "warning"
    for dim in TRANSITION_DIMENSIONS:
        assert dim in weak.message


def test_weak_transition_has_no_errors():
    plan = _plan(dimensions_changed=[])
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    assert not has_errors(issues)


# --- divergencia intencao x realidade ----------------------------------------

def test_declared_dimensions_not_realized_are_flagged():
    """Regra de negocio da issue: plano promete `densidade`/`registro`
    mas o render sai igual dos dois lados — exatamente o caso que este
    validador existe para pegar."""
    plan = _plan(dimensions_changed=["densidade", "registro"])
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    unrealized = [i for i in issues if i.kind == "unrealized_intent"]
    assert len(unrealized) == 1
    assert set(unrealized[0].dimensions) == {"densidade", "registro"}


def test_undeclared_unrealized_dimensions_are_not_flagged_as_divergence():
    """So dimensoes DECLARADAS pelo plano entram na checagem de
    divergencia — as outras 6 ficam iguais tambem, mas o plano nunca
    prometeu mudar elas, entao nao ha promessa quebrada ali."""
    plan = _plan(dimensions_changed=["densidade"])
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    unrealized = [i for i in issues if i.kind == "unrealized_intent"][0]
    assert unrealized.dimensions == ("densidade",)


def test_portuguese_and_english_aliases_both_match():
    plan_pt = _plan(dimensions_changed=["densidade"])
    plan_en = _plan(dimensions_changed=["density"])
    analysis = _analysis()
    issues_pt = [
        i for i in validate_transitions(_weak_tracks(), plan_pt, analysis)
        if i.kind == "unrealized_intent"
    ]
    issues_en = [
        i for i in validate_transitions(_weak_tracks(), plan_en, analysis)
        if i.kind == "unrealized_intent"
    ]
    assert issues_pt[0].dimensions == issues_en[0].dimensions == ("densidade",)


# --- robustez -----------------------------------------------------------

def test_no_transitions_returns_empty():
    plan = _plan(dimensions_changed=[])
    plan.transitions = []
    issues = validate_transitions(_strong_tracks(), plan, analysis=_analysis())
    assert issues == []


def test_unknown_section_label_is_skipped_not_crashed():
    plan = _plan(dimensions_changed=[])
    plan.transitions[0].from_section = "GHOST_SECTION"
    issues = validate_transitions(_strong_tracks(), plan, analysis=_analysis())
    assert issues == []


def test_empty_window_is_skipped_not_flagged():
    """Sem nota nenhuma de um dos lados nao ha dado pra medir — nao vira
    falso positivo de 'zero dimensoes mudaram'."""
    plan = _plan(dimensions_changed=[])
    only_a = [RenderedTrack(
        element_id="pad_a", track_name="Pad A",
        notes=tuple(_note(60, float(i)) for i in range(8)),
    )]
    issues = validate_transitions(only_a, plan, analysis=_analysis())
    assert issues == []


def test_format_issues_lists_every_warning():
    plan = _plan(dimensions_changed=[])
    issues = validate_transitions(_weak_tracks(), plan, analysis=_analysis())
    text = format_issues(issues)
    assert f"{len(issues)} warning" in text
    for i in issues:
        assert i.message in text


def test_format_issues_empty_is_ok():
    assert format_issues([]) == "Transitions: OK"


def test_min_dimensions_changed_is_two():
    """AC-14 e literal: 'ao menos DUAS'."""
    assert MIN_DIMENSIONS_CHANGED == 2
