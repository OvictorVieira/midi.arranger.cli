"""Testes do mapa de energia e protagonista (US-010).

Cobre:
- energy: dict com os 5 eixos (densidade, impacto, largura, altura, instabilidade),
  valores int em 0-10, sem chave extra ou faltando;
- protagonist obrigatorio em cada secao;
- Element.is_protagonist bool default False;
- BLOQUEIO quando 2+ elementos na mesma secao declaram is_protagonist=True;
- AVISO (nao-bloqueante) quando todos os 5 eixos sobem entre secoes consecutivas.
"""

from __future__ import annotations

import copy

import pytest

from tools.plan import (
    ENERGY_AXES,
    ArrangementPlan,
    Element,
    PlanSection,
    PlanValidationError,
    SourceMidi,
    from_dict,
    to_dict,
    validate,
)

# --- fixture ----------------------------------------------------------------

def _valid_plan() -> ArrangementPlan:
    """Plano minimo com energy e protagonist em todas as secoes."""
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path="~/Desktop/x.mid", sha256="a" * 64),
        route="hook_eletronico_pesado",
        sections=[
            PlanSection(
                label="INTRO",
                kind="intro",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={"densidade": 2, "impacto": 1, "largura": 3, "altura": 2, "instabilidade": 1},
            ),
            PlanSection(
                label="CHORUS 1",
                kind="chorus",
                start_bar=8,
                end_bar=24,
                source="marker",
                protagonist="vocal_hook",
                energy={"densidade": 8, "impacto": 7, "largura": 9, "altura": 8, "instabilidade": 3},
            ),
        ],
        elements=[
            Element(
                id="pad_intro",
                role="pad",
                sections=["INTRO"],
                register=[48, 72],
                layers=1,
                sync_role="sustain_through",
                articulation="sustained",
                harmony="follow_chords",
                rationale="Pad sustenta a intro para validar energia e protagonismo.",
            ),
        ],
    )


# --- eixos de energia -------------------------------------------------------

def test_energy_axes_are_exactly_the_spec_five():
    """AC: 'os 5 eixos 0-10 (densidade, impacto, largura, altura, instabilidade)'."""
    assert ENERGY_AXES == ("densidade", "impacto", "largura", "altura", "instabilidade")


def test_validate_accepts_plan_with_full_energy_and_protagonists():
    validate(_valid_plan())  # nao levanta


def test_rejects_missing_energy_axis():
    plan = _valid_plan()
    del plan.sections[0].energy["densidade"]  # type: ignore[index]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].energy"
    assert "densidade" in exc.value.message


def test_rejects_unknown_energy_axis():
    plan = _valid_plan()
    plan.sections[0].energy["swing"] = 5  # type: ignore[index]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].energy"
    assert "swing" in exc.value.message


def test_rejects_energy_value_out_of_range():
    plan = _valid_plan()
    plan.sections[0].energy["impacto"] = 11  # type: ignore[index]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].energy.impacto"
    assert "0-10" in exc.value.message


def test_rejects_negative_energy_value():
    plan = _valid_plan()
    plan.sections[0].energy["altura"] = -1  # type: ignore[index]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].energy.altura"


def test_rejects_non_int_energy_value():
    plan = _valid_plan()
    plan.sections[0].energy["largura"] = 3.5  # type: ignore[index,assignment]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].energy.largura"


def test_rejects_missing_energy_dict():
    plan = _valid_plan()
    plan.sections[0].energy = None
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].energy"


def test_rejects_missing_protagonist():
    plan = _valid_plan()
    plan.sections[0].protagonist = None
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].protagonist"


# --- aviso: todos os 5 eixos sobem simultaneamente --------------------------

def test_warns_when_all_five_axes_rise_between_consecutive_sections():
    """AC: 'AVISA sem bloquear quando todos os 5 eixos sobem simultaneamente'."""
    plan = _valid_plan()
    warnings = validate(plan)
    assert len(warnings) == 1
    w = warnings[0]
    assert "INTRO" in w and "CHORUS 1" in w
    assert "all 5 energy axes rise" in w


def test_no_warning_when_at_least_one_axis_holds_or_drops():
    plan = _valid_plan()
    plan.sections[1].energy["instabilidade"] = 1  # cai vs INTRO=1 (nao sobe)
    warnings = validate(plan)
    assert warnings == []


def test_warning_is_non_blocking():
    """AVISO nunca deve virar excecao — o plano segue valido."""
    plan = _valid_plan()
    # duas secoes com todos os eixos subindo: gera aviso mas nao bloqueia
    warnings = validate(plan)
    assert isinstance(warnings, list)
    assert len(warnings) >= 1


# --- bloqueio: dois protagonistas na mesma secao ---------------------------

def test_blocks_when_two_elements_declare_protagonist_in_same_section():
    """AC: 'BLOQUEIA quando duas camadas na mesma secao declaram ser protagonista'."""
    plan = _valid_plan()
    plan.elements[0].is_protagonist = True
    plan.elements.append(
        Element(
            id="riff_intro",
            role="guitar",
            sections=["INTRO"],
            register=[40, 60],
            layers=1,
            sync_role="guitar_unison",
            articulation="tight",
            harmony="unison_guitar",
            rationale="Guitarra dobra o riff da intro para testar conflito de protagonismo.",
            is_protagonist=True,
        )
    )
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert "protagonist_conflict" in exc.value.path
    assert "INTRO" in exc.value.path
    assert "pad_intro" in exc.value.message
    assert "riff_intro" in exc.value.message


def test_single_protagonist_per_section_is_ok():
    plan = _valid_plan()
    plan.elements[0].is_protagonist = True
    validate(plan)  # nao levanta


def test_no_protagonist_declared_is_ok():
    """Ninguem se declarar protagonista tambem passa — o protagonista da secao
    e propriedade da secao; is_protagonist do elemento e reforco opcional."""
    plan = _valid_plan()
    for e in plan.elements:
        assert e.is_protagonist is False
    validate(plan)


def test_same_element_declared_in_two_sections_is_not_a_conflict():
    """Um elemento is_protagonist=True em duas secoes diferentes NAO conflita
    com si mesmo — o bloqueio e por SECAO, nao por elemento."""
    plan = _valid_plan()
    plan.elements[0].sections = ["INTRO", "CHORUS 1"]
    plan.elements[0].is_protagonist = True
    validate(plan)  # nao levanta


def test_rejects_non_bool_is_protagonist():
    plan = _valid_plan()
    plan.elements[0].is_protagonist = "yes"  # type: ignore[assignment]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].is_protagonist"


# --- round-trip carrega is_protagonist -------------------------------------

def test_round_trip_preserves_is_protagonist_true():
    plan = _valid_plan()
    plan.elements[0].is_protagonist = True
    assert from_dict(to_dict(plan)) == plan


def test_round_trip_preserves_is_protagonist_false_default():
    plan = _valid_plan()
    reloaded = from_dict(to_dict(plan))
    assert reloaded.elements[0].is_protagonist is False


def test_from_dict_defaults_is_protagonist_to_false_when_absent():
    """Planos antigos em disco (sem o campo) carregam com False — compat."""
    plan = _valid_plan()
    data = to_dict(plan)
    del data["elements"][0]["is_protagonist"]
    reloaded = from_dict(data)
    assert reloaded.elements[0].is_protagonist is False


# --- validate() nao muta ---------------------------------------------------

def test_validate_does_not_mutate_plan_with_warnings():
    plan = _valid_plan()  # ja gera aviso de 5 eixos subindo
    snapshot = copy.deepcopy(plan)
    validate(plan)
    assert plan == snapshot
