"""Testes do validador de colisao de registro (US-015).

Cobre:
- Regras de registro (sub/low/high) da secao 7.3 do persona.
- Relocacao em colisao resolvivel (sobe uma oitava).
- Aviso em colisao nao resolvivel (registro no topo do MIDI, ou regra que nao
  se resolve por relocacao).
- FR-17/FR-18: nenhum elemento e removido, nenhum layer e reduzido, nenhum
  campo alem de `register` e alterado.
"""

from __future__ import annotations

import copy

from tools.constants import REGISTER_BANDS
from tools.plan import (
    ArrangementPlan,
    Element,
    PlanSection,
    SourceMidi,
)
from tools.validators.collision import (
    C2_PITCH,
    C3_PITCH,
    C5_PITCH,
    DENSE_HARMONIES,
    LONG_ARTICULATIONS,
    MIDI_MAX,
    OCTAVE,
    _band_overlap,
    _is_dense,
    _is_long,
    _register_span,
    _registers_overlap,
    _shift_up_octave,
    _violates_high_rule,
    _violates_sub_rule,
    validate_collisions,
)

# --- fixtures ---------------------------------------------------------------

def _section(label: str = "VERSE 1", kind: str = "verse",
             start_bar: int = 0, end_bar: int = 8) -> PlanSection:
    return PlanSection(
        label=label,
        kind=kind,
        start_bar=start_bar,
        end_bar=end_bar,
        source="marker",
        protagonist="texture",
        energy={"densidade": 4, "impacto": 3, "largura": 4, "altura": 4, "instabilidade": 2},
    )


def _element(
    id: str,
    register: tuple[int, int],
    *,
    role: str = "pad",
    sync_role: str = "sustain_through",
    articulation: str = "sustained",
    harmony: str = "follow_chords",
    sections: tuple[str, ...] = ("VERSE 1",),
    layers: int = 1,
) -> Element:
    return Element(
        id=id,
        role=role,
        sections=list(sections),
        register=[register[0], register[1]],
        layers=layers,
        sync_role=sync_role,
        articulation=articulation,
        harmony=harmony,
        rationale=f"{role} ocupa registro {register[0]}-{register[1]} para validar colisao.",
    )


def _plan_with(elements: list[Element],
               sections: list[PlanSection] | None = None) -> ArrangementPlan:
    if sections is None:
        sections = [_section()]
    return ArrangementPlan(
        version=1,
        seed=7,
        source_midi=SourceMidi(path="~/Desktop/x.mid", sha256="b" * 64),
        route="cinematica_emocional",
        sections=sections,
        elements=elements,
    )


# --- constantes / vocabulario ------------------------------------------------

def test_band_boundaries_match_spec():
    """C2=36, C3=48, C5=72: fronteiras exatas alinhadas a REGISTER_BANDS."""
    assert C2_PITCH == 36
    assert C3_PITCH == 48
    assert C5_PITCH == 72
    assert REGISTER_BANDS["sub"] == (0, 35)
    assert REGISTER_BANDS["low"] == (36, 47)
    assert REGISTER_BANDS["mid"] == (48, 71)
    assert REGISTER_BANDS["high"] == (72, 127)


def test_octave_is_twelve_semitones():
    assert OCTAVE == 12
    assert MIDI_MAX == 127


def test_dense_harmonies_include_follow_chords():
    assert "follow_chords" in DENSE_HARMONIES


def test_long_articulations_include_sustained_letring_open():
    assert frozenset({"sustained", "let_ring", "open"}) == LONG_ARTICULATIONS


# --- helpers puros -----------------------------------------------------------

def test_is_dense_by_harmony():
    assert _is_dense(_element("a", (48, 72), harmony="follow_chords")) is True
    assert _is_dense(_element("b", (48, 72), harmony="pedal")) is False
    assert _is_dense(_element("c", (48, 72), harmony="free")) is False
    assert _is_dense(_element("d", (48, 72), harmony="unison_guitar")) is False


def test_is_long_by_articulation():
    for art in ("sustained", "let_ring", "open"):
        assert _is_long(_element("x", (48, 72), articulation=art)) is True
    for art in ("ghost", "staccato", "tight"):
        assert _is_long(_element("x", (48, 72), articulation=art)) is False


def test_register_span_is_high_minus_low():
    assert _register_span((36, 47)) == 11
    assert _register_span([48, 72]) == 24


def test_registers_overlap_detects_intersection():
    assert _registers_overlap((36, 47), (40, 60)) is True
    assert _registers_overlap((36, 47), (48, 60)) is False
    assert _registers_overlap((36, 47), (47, 60)) is True  # boundary partilhada


def test_band_overlap_finds_all_touched_bands():
    reg = (30, 50)  # atravessa sub, low, mid
    assert _band_overlap(reg, "sub") is True
    assert _band_overlap(reg, "low") is True
    assert _band_overlap(reg, "mid") is True
    assert _band_overlap(reg, "high") is False


def test_shift_up_octave_moves_up_twelve_semitones():
    assert _shift_up_octave((30, 40)) == (42, 52)
    assert _shift_up_octave((60, 72)) == (72, 84)


def test_shift_up_octave_returns_none_when_exceeds_midi_max():
    assert _shift_up_octave((100, 115)) == (112, 127)
    assert _shift_up_octave((100, 116)) is None
    assert _shift_up_octave((MIDI_MAX - 5, MIDI_MAX)) is None


# --- deteccao pura ----------------------------------------------------------

def test_violates_sub_rule_true_for_dense_below_c2_with_wide_span():
    e = _element("sub_bad", (24, 34), harmony="follow_chords")
    assert _register_span(e.register) == 10
    assert _violates_sub_rule(e) is True


def test_violates_sub_rule_false_for_fifth_only():
    """Nota + quinta (span == 7) e permitido abaixo de C2."""
    e = _element("sub_ok", (24, 31), harmony="follow_chords")
    assert _register_span(e.register) == 7
    assert _violates_sub_rule(e) is False


def test_violates_sub_rule_false_for_single_note():
    e = _element("sub_single", (30, 30), harmony="follow_chords")
    assert _violates_sub_rule(e) is False


def test_violates_sub_rule_false_when_reaches_c2():
    """Regra so pega quem esta INTEIRAMENTE abaixo de C2."""
    e = _element("straddle", (30, 40), harmony="follow_chords")
    assert _violates_sub_rule(e) is False


def test_violates_sub_rule_false_for_non_dense():
    """Pedal ou drone abaixo de C2 e permitido — a proibicao e para acorde denso."""
    e = _element("drone", (24, 34), harmony="pedal")
    assert _violates_sub_rule(e) is False


def test_violates_high_rule_true_for_sustained_above_c5():
    e = _element("bad_high", (76, 90), articulation="sustained")
    assert _violates_high_rule(e) is True


def test_violates_high_rule_false_for_short_events_above_c5():
    e = _element("ok_high", (76, 90), articulation="staccato")
    assert _violates_high_rule(e) is False


def test_violates_high_rule_false_when_register_starts_below_c5():
    """Regra so pega quem esta INTEIRAMENTE acima de C5."""
    e = _element("straddle", (60, 80), articulation="sustained")
    assert _violates_high_rule(e) is False


# --- integracao: colisoes resolviveis ---------------------------------------

def test_resolvable_collision_two_dense_in_low_relocates_the_lowest_one_up_an_octave():
    """AC: 'Teste verifica relocacao em colisao resolvivel'."""
    lower = _element("lower_pad", (36, 47), harmony="follow_chords")
    upper = _element("upper_pad", (40, 50), harmony="follow_chords")
    plan = _plan_with([lower, upper])

    original_lower = tuple(lower.register)

    report = validate_collisions(plan)

    # A relocacao subiu o mais grave uma oitava
    assert len(report.relocations) == 1
    reloc = report.relocations[0]
    assert reloc.element_id == "lower_pad"
    assert reloc.section_label == "VERSE 1"
    assert reloc.from_register == original_lower
    assert reloc.to_register == (48, 59)
    assert lower.register == [48, 59]

    # O outro ficou intocado
    assert upper.register == [40, 50]

    # Nao sobrou nenhum aviso — colisao 100% resolvida
    assert report.warnings == []


def test_resolvable_sub_violation_relocates_up_one_octave():
    """Acorde denso abaixo de C2 sobe uma oitava para virar body em low/mid."""
    dense_sub = _element("sub_pad", (24, 34), harmony="follow_chords")
    plan = _plan_with([dense_sub])

    report = validate_collisions(plan)

    assert len(report.relocations) == 1
    assert report.relocations[0].element_id == "sub_pad"
    assert report.relocations[0].from_register == (24, 34)
    assert report.relocations[0].to_register == (36, 46)
    assert dense_sub.register == [36, 46]
    assert report.warnings == []


def test_iterative_relocation_when_three_dense_share_low_band():
    """Tres elementos densos em low: relocacao roda repetidamente ate resolver."""
    a = _element("a", (36, 46), harmony="follow_chords")
    b = _element("b", (38, 47), harmony="follow_chords")
    c = _element("c", (40, 47), harmony="follow_chords")
    plan = _plan_with([a, b, c])

    report = validate_collisions(plan)

    # Duas relocacoes: a sobe primeiro (mais grave), depois b sobe (agora e o mais grave em low)
    assert [r.element_id for r in report.relocations] == ["a", "b"]
    assert a.register == [48, 58]
    assert b.register == [50, 59]
    assert c.register == [40, 47]  # ficou sozinho em low, ok
    assert report.warnings == []


# --- integracao: colisoes NAO resolviveis ------------------------------------

def test_unresolvable_sub_violation_when_shift_exceeds_midi_max():
    """AC: 'Teste verifica o aviso em colisao nao resolvivel'.

    Se relocar uma oitava jogaria o topo alem de 127, o validador NAO move e
    emite aviso.
    """
    # Registro logicamente absurdo mas valido no schema: topo bem acima de 115
    # e ainda 'abaixo de C2' e impossivel (contradicao). Usar cenario de high:
    # articulation sustained em [116, 127] — a regra 'high' avisa direto.
    high_sustain = _element("sky_pad", (120, 127), articulation="sustained")
    plan = _plan_with([high_sustain])

    report = validate_collisions(plan)

    assert report.relocations == []
    assert len(report.warnings) == 1
    w = report.warnings[0]
    assert w.element_ids == ("sky_pad",)
    assert w.section_label == "VERSE 1"
    assert w.band == "high"
    assert "sustained" in w.reason


def test_unresolvable_low_pair_when_relocation_would_exceed_midi_max():
    """Duas camadas densas em low + uma delas ja no topo do MIDI = aviso, sem relocar."""
    # Elemento cujo topo esta em 116: shift_up_octave -> topo 128 > 127 = None.
    top_dense = _element("cannot_move", (36, 116), harmony="follow_chords")
    other_dense = _element("also_low", (40, 47), harmony="follow_chords")
    plan = _plan_with([top_dense, other_dense])

    original_top = tuple(top_dense.register)
    original_other = tuple(other_dense.register)

    report = validate_collisions(plan)

    # `also_low` e o mais grave por register[0]? nao: top_dense.register[0]=36 < 40 = other.register[0].
    # Entao o "mais grave" e top_dense. shift falha -> aviso, break.
    # Nenhuma relocacao aplicada.
    assert report.relocations == []
    assert len(report.warnings) == 1
    w = report.warnings[0]
    assert set(w.element_ids) == {"cannot_move", "also_low"}
    assert w.band == "low"
    assert w.section_label == "VERSE 1"
    assert w.bar_range == (0, 8)
    # Nenhum elemento foi tocado
    assert tuple(top_dense.register) == original_top
    assert tuple(other_dense.register) == original_other


def test_warning_carries_section_and_bar_range():
    """O aviso deve explicar qual par colide em qual compasso."""
    section = _section(label="CHORUS 2", kind="chorus", start_bar=32, end_bar=48)
    # Ambos densos em low e AMBOS com topo perto de 127 — shift_up_octave falha.
    a = _element("pad_a", (36, 116), harmony="follow_chords", sections=("CHORUS 2",))
    b = _element("pad_b", (40, 116), harmony="follow_chords", sections=("CHORUS 2",))
    plan = _plan_with([a, b], sections=[section])

    report = validate_collisions(plan)

    assert len(report.warnings) >= 1
    w = report.warnings[0]
    assert w.section_label == "CHORUS 2"
    assert w.bar_range == (32, 48)
    assert set(w.element_ids) == {"pad_a", "pad_b"}
    assert w.band == "low"


# --- FR-17 e FR-18: nunca deleta / reduz -------------------------------------

def test_no_element_is_deleted_in_any_scenario():
    """AC: 'nenhum elemento e deletado pelo validador em nenhum cenario'."""
    scenarios = [
        # 1) plano limpo
        [
            _element("clean", (48, 60), harmony="follow_chords"),
        ],
        # 2) colisao resolvivel low
        [
            _element("l1", (36, 47), harmony="follow_chords"),
            _element("l2", (40, 47), harmony="follow_chords"),
        ],
        # 3) sub denso
        [
            _element("sub_dense", (24, 34), harmony="follow_chords"),
        ],
        # 4) high sustain (nao resolvivel)
        [
            _element("high_sustain", (76, 90), articulation="sustained"),
        ],
        # 5) low pair no topo (nao resolvivel)
        [
            _element("top_l", (36, 116), harmony="follow_chords"),
            _element("mid_l", (40, 47), harmony="follow_chords"),
        ],
    ]
    for elements in scenarios:
        plan = _plan_with(elements)
        n_before = len(plan.elements)
        ids_before = [e.id for e in plan.elements]
        layers_before = [e.layers for e in plan.elements]

        validate_collisions(plan)

        assert len(plan.elements) == n_before, "validator dropped an element"
        assert [e.id for e in plan.elements] == ids_before, "validator reordered/renamed elements"
        assert [e.layers for e in plan.elements] == layers_before, "validator changed layers count"


def test_validator_only_mutates_register_field():
    """Alem de `register`, nenhum campo do elemento deve ser tocado."""
    a = _element("l1", (36, 47), harmony="follow_chords")
    b = _element("l2", (40, 47), harmony="follow_chords")
    plan = _plan_with([a, b])
    snapshot = copy.deepcopy(plan)

    validate_collisions(plan)

    for original, current in zip(snapshot.elements, plan.elements, strict=True):
        assert original.id == current.id
        assert original.role == current.role
        assert original.sections == current.sections
        assert original.layers == current.layers
        assert original.sync_role == current.sync_role
        assert original.articulation == current.articulation
        assert original.harmony == current.harmony
        assert original.is_protagonist == current.is_protagonist


def test_validator_does_not_touch_sections_or_source_midi():
    a = _element("only", (48, 60), harmony="follow_chords")
    plan = _plan_with([a])
    snapshot = copy.deepcopy(plan)

    validate_collisions(plan)

    assert plan.sections == snapshot.sections
    assert plan.source_midi == snapshot.source_midi
    assert plan.route == snapshot.route


# --- comportamento nao-colidente --------------------------------------------

def test_no_collision_in_mid_band_multiple_bodies_ok():
    """C3-C5 e o 'corpo harmonico' — multiplas camadas densas convivem."""
    a = _element("piano", (48, 60), harmony="follow_chords")
    b = _element("rhodes", (55, 71), harmony="follow_chords")
    c = _element("pad", (50, 65), harmony="follow_chords")
    plan = _plan_with([a, b, c])

    report = validate_collisions(plan)

    assert report.relocations == []
    assert report.warnings == []
    assert a.register == [48, 60]
    assert b.register == [55, 71]
    assert c.register == [50, 65]


def test_pedal_elements_do_not_trigger_low_collision():
    """Regra 2 so olha para elementos densos (harmony=follow_chords). Pedal/free ficam de fora."""
    pedal_a = _element("drone_a", (36, 47), harmony="pedal")
    pedal_b = _element("drone_b", (40, 47), harmony="pedal")
    plan = _plan_with([pedal_a, pedal_b])

    report = validate_collisions(plan)

    assert report.relocations == []
    assert report.warnings == []


def test_single_dense_in_low_is_ok():
    """Um so elemento denso em low nao e colisao (colisao e por par)."""
    e = _element("solo_low", (36, 47), harmony="follow_chords")
    plan = _plan_with([e])

    report = validate_collisions(plan)

    assert report.relocations == []
    assert report.warnings == []
    assert e.register == [36, 47]


def test_element_only_in_section_it_declared():
    """Elemento nao presente na secao nao entra no calculo de colisao daquela secao."""
    sec_a = _section(label="VERSE 1", start_bar=0, end_bar=8)
    sec_b = _section(label="CHORUS 1", kind="chorus", start_bar=8, end_bar=24)
    a = _element("verse_pad", (36, 47), harmony="follow_chords", sections=("VERSE 1",))
    b = _element("chorus_pad", (40, 47), harmony="follow_chords", sections=("CHORUS 1",))
    plan = _plan_with([a, b], sections=[sec_a, sec_b])

    report = validate_collisions(plan)

    # Dividem faixa de registro mas NAO dividem secao — nao colidem.
    assert report.relocations == []
    assert report.warnings == []
