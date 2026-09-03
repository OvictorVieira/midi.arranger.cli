"""Testes do validador de duas dimensoes na transicao (AC-14, issue #24)."""

from __future__ import annotations

from tools.analyze import Analysis, BarAnalysis
from tools.plan import ArrangementPlan, Element, PlanEdit, PlanSection, SourceMidi, Transition
from tools.validators.harmony import RenderedNote, RenderedTrack
from tools.validators.transitions import (
    MIN_DIMENSIONS_CHANGED,
    TRANSITION_DIMENSIONS,
    _compute_metrics,
    _drum_element_ids,
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


def _drum_element(id: str, section: str) -> Element:
    return Element(
        id=id, role="drums", sections=[section],
        register=[35, 59], layers=1,
        sync_role="sustain_through", articulation="sustained",
        harmony="follow_chords",
        rationale="Elemento fixture de bateria do validador de transicao.",
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
    bars = _t._bar_lookup(analysis)
    at_bar = plan.transitions[0].at_bar
    bounds_a = _t._window_bounds(at_bar, before=True, bars_by_index=bars)
    bounds_b = _t._window_bounds(at_bar, before=False, bars_by_index=bars)
    tracks = _strong_tracks()
    events_a = _t._notes_in_window(tracks, bounds_a)
    events_b = _t._notes_in_window(tracks, bounds_b)
    metrics_a = _t._compute_metrics(
        events_a, bounds_a[1] - bounds_a[0], drum_element_ids=frozenset(),
    )
    metrics_b = _t._compute_metrics(
        events_b, bounds_b[1] - bounds_b[0], drum_element_ids=frozenset(),
    )

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


# --- fronteira sem Transition declarado (Codex finding #1, PR #106) ---------

def test_boundary_without_declared_transition_still_checks_weak_transition():
    """AC-14 vale para TODA fronteira de secao, mesmo quando
    `plan.transitions` fica vazio — a IA pode encadear secoes adjacentes
    sem declarar um registro de `Transition` pra cada uma, e a validacao
    estrutural do plano nao exige isso. Sem esse regression test, o antigo
    early-return `if not plan.transitions: return []` deixava passar uma
    fronteira identica dos dois lados sem warning nenhum."""
    plan = _plan(dimensions_changed=[])
    plan.transitions = []
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    weak = [i for i in issues if i.kind == "weak_transition"]
    assert len(weak) == 1
    assert weak[0].from_section == "MAIN"
    assert weak[0].to_section == "NEXT"


def test_boundary_without_declared_transition_has_no_unrealized_intent():
    """Sem `Transition` declarado casando a fronteira nao ha intencao
    nenhuma pra comparar — so `weak_transition` pode aparecer, nunca
    `unrealized_intent`."""
    plan = _plan(dimensions_changed=[])
    plan.transitions = []
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    assert [i for i in issues if i.kind == "unrealized_intent"] == []


def test_only_one_section_has_no_boundary_to_check():
    plan = _plan(dimensions_changed=[])
    plan.transitions = []
    plan.sections = [plan.sections[0]]
    analysis = _analysis()
    issues = validate_transitions(_weak_tracks(), plan, analysis)
    assert issues == []


# --- ancoragem da janela em at_bar (Codex finding #2, PR #106) --------------

def test_window_is_anchored_to_at_bar_not_section_boundary():
    """`Transition.at_bar` declarado (4) diverge da fronteira natural
    entre as secoes (bar 8, `NEXT.start_bar`) — dado malformado, mas hoje
    valido. A regiao ao redor de `at_bar=4` (compassos [0,4) vs [4,8), em
    segundos [0,8) vs [8,16)) muda MUITAS dimensoes; a regiao ao redor da
    fronteira rotulada por `from_section`/`to_section` (compassos [4,8)
    vs [8,12), em segundos [8,16) vs [16,24)) e uma copia identica
    deslocada no tempo — se o validador ainda usasse a cauda/cabeca das
    secoes (bug antigo) em vez de `at_bar`, apareceria `weak_transition`
    aqui; ancorado corretamente em `at_bar`, nao aparece."""
    sections = [
        PlanSection(
            label="MAIN", kind="verse", start_bar=0, end_bar=8,
            source="marker", protagonist="texture",
            energy={"densidade": 4, "impacto": 4, "largura": 4,
                    "altura": 4, "instabilidade": 3},
        ),
        PlanSection(
            label="NEXT", kind="chorus", start_bar=8, end_bar=16,
            source="marker", protagonist="texture",
            energy={"densidade": 8, "impacto": 8, "largura": 8,
                    "altura": 8, "instabilidade": 5},
        ),
    ]
    plan = ArrangementPlan(
        version=1, seed=0,
        source_midi=SourceMidi(path="/dev/null", sha256="0" * 64),
        route="cinematica_emocional",
        sections=sections,
        elements=[_element("pad_a", "MAIN"), _element("pad_b1", "NEXT"), _element("pad_b2", "NEXT")],
        transitions=[
            Transition(
                at_bar=4, from_section="MAIN", to_section="NEXT",
                dimensions_changed=[],
                elements=["pad_b1"],
                technique="entrada antecipada",
            ),
        ],
    )
    analysis = _analysis(bars=16)

    # notas esparsas mono (t=[0,8), bars [0,4)): densidade baixa, registro
    # fechado num pitch, 1 elemento.
    notes_a = [_note(60, float(t)) for t in (0.0, 2.0, 4.0, 6.0)]
    track_a = RenderedTrack(element_id="pad_a", track_name="Pad A", notes=tuple(notes_a))

    # padrao de acorde denso repetido a cada 1s, IDENTICO nos dois lados
    # da fronteira ROTULADA (t=[8,16) e t=[16,24)) — se o validador usasse
    # essas janelas, seria uma transicao fraca (tudo igual).
    notes_b1: list[RenderedNote] = []
    notes_b2: list[RenderedNote] = []
    for offset in range(8):
        for base_t in (8.0, 16.0):
            t = base_t + offset
            notes_b1.append(_note(72, t))
            notes_b1.append(_note(76, t))
            notes_b2.append(_note(79, t))
    track_b1 = RenderedTrack(element_id="pad_b1", track_name="Pad B1", notes=tuple(notes_b1))
    track_b2 = RenderedTrack(element_id="pad_b2", track_name="Pad B2", notes=tuple(notes_b2))

    issues = validate_transitions([track_a, track_b1, track_b2], plan, analysis)
    weak = [i for i in issues if i.kind == "weak_transition"]
    assert weak == [], weak


# --- bateria nao conta como pitch (Codex finding #2, PR #106) ---------------

def _drum_swap_plan() -> ArrangementPlan:
    """Plano com UM elemento de bateria (`drum_a`) e UM elemento pitched
    (`pad_a`), ambos presentes nas duas secoes."""
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
        elements=[_drum_element("drum_a", "MAIN"), _element("pad_a", "MAIN")],
        transitions=[],
    )


def _hihat_ride_swap_tracks(*, extra_ghost_notes: bool = False) -> list[RenderedTrack]:
    """`drum_a` toca hi-hat fechado (42) na janela A (t=[0,8)) e SO troca a
    articulacao para ride (51) na janela B (t=[8,16)), mesma grade
    ritmica/velocity — troca de peca do kit, nao mudanca musical. `pad_a` e
    IDENTICO (mesmo pitch/onset relativo/duracao) dos dois lados — sem ele,
    qualquer dimensao que ainda enxergasse a bateria como pitch teria como
    unica fonte de dado a propria bateria."""
    hits_a = [_note(42, float(i) * 0.5, duration=0.1, velocity=80) for i in range(16)]
    if extra_ghost_notes:
        # Mudanca ritmica GENUINA (densidade) sobre a MESMA troca de peca —
        # tem que continuar contando, porque densidade e dimensao ritmica,
        # nao de pitch.
        hits_b = []
        for i in range(16):
            t = 8.0 + i * 0.5
            hits_b.append(_note(51, t, duration=0.1, velocity=80))
            hits_b.append(_note(51, t + 0.25, duration=0.05, velocity=40))
    else:
        hits_b = [_note(51, 8.0 + i * 0.5, duration=0.1, velocity=80) for i in range(16)]
    drum_a = RenderedTrack(
        element_id="drum_a", track_name="Drums",
        notes=tuple(hits_a + hits_b),
    )

    pad_notes = [_note(60, float(i), duration=0.9, velocity=70) for i in range(8)]
    pad_notes += [_note(60, 8.0 + i, duration=0.9, velocity=70) for i in range(8)]
    pad_a = RenderedTrack(element_id="pad_a", track_name="Pad A", notes=tuple(pad_notes))
    return [drum_a, pad_a]


def test_drum_ids_include_generated_drum_elements_and_drum_edits():
    plan = _drum_swap_plan()
    plan.edits = [PlanEdit(track="Kit", profile="drums", intensity=0.0)]
    ids = _drum_element_ids(plan)
    assert ids == {"drum_a", "source:Kit"}


def test_drum_ids_exclude_non_drum_elements_and_edits():
    plan = _drum_swap_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.0)]
    ids = _drum_element_ids(plan)
    assert "pad_a" not in ids
    assert "source:Bass" not in ids


def test_hihat_to_ride_swap_alone_is_not_a_register_or_harmony_change():
    """A troca de articulacao SOZINHA (hi-hat 42 -> ride 51) nao pode ser a
    segunda dimensao que satisfaz AC-14 — com o pad pitched identico dos
    dois lados, a fronteira inteira fica fraca."""
    plan = _drum_swap_plan()
    analysis = _analysis()
    issues = validate_transitions(_hihat_ride_swap_tracks(), plan, analysis)
    weak = [i for i in issues if i.kind == "weak_transition"]
    assert len(weak) == 1
    assert "registro" in weak[0].dimensions
    assert "harmonia" in weak[0].dimensions
    assert "largura" in weak[0].dimensions


def test_drum_notes_excluded_from_registro_and_harmonia_metrics():
    """Direto na unidade de medicao: com so bateria na janela (sem pad
    pitched nenhum), `registro`/`largura`/`harmonia` ficam SEM DADO (None),
    nao "sem mudanca" — nao ha nota pitched pra medir."""
    drum_only = [n for n in _hihat_ride_swap_tracks() if n.element_id == "drum_a"]
    events_a = [("drum_a", n) for n in drum_only[0].notes if n.start_s < 8.0]
    events_b = [("drum_a", n) for n in drum_only[0].notes if n.start_s >= 8.0]
    metrics_a = _compute_metrics(events_a, 8.0, drum_element_ids=frozenset({"drum_a"}))
    metrics_b = _compute_metrics(events_b, 8.0, drum_element_ids=frozenset({"drum_a"}))
    assert metrics_a.registro is None
    assert metrics_a.largura is None
    assert metrics_a.harmonia is None
    assert metrics_b.registro is None
    assert metrics_b.largura is None
    assert metrics_b.harmonia is None


def test_genuine_drum_density_change_still_counts_despite_pitch_swap():
    """A MESMA troca hi-hat->ride, mas agora com o dobro de golpes (ghost
    notes) do lado B — mudanca ritmica de verdade sobre a bateria continua
    contando para `densidade`, mesmo com `registro`/`largura`/`harmonia`
    excluidos da bateria (a exclusao e SO das dimensoes de pitch)."""
    from tools.validators.transitions import _changed

    tracks = _hihat_ride_swap_tracks(extra_ghost_notes=True)
    drum = next(t for t in tracks if t.element_id == "drum_a")
    events_a = [("drum_a", n) for n in drum.notes if n.start_s < 8.0]
    events_b = [("drum_a", n) for n in drum.notes if n.start_s >= 8.0]
    metrics_a = _compute_metrics(events_a, 8.0, drum_element_ids=frozenset({"drum_a"}))
    metrics_b = _compute_metrics(events_b, 8.0, drum_element_ids=frozenset({"drum_a"}))

    assert metrics_a.densidade == 2.0
    assert metrics_b.densidade == 4.0
    assert _changed("densidade", metrics_a, metrics_b) is True
    # registro/largura/harmonia continuam sem dado — bateria pura na janela.
    assert metrics_a.registro is None and metrics_b.registro is None
