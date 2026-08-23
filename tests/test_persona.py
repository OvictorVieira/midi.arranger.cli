"""Testes do validador de persona (FR-27, US-010)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tools.analyze import Analysis, BarAnalysis
from tools.plan import (
    ArrangementPlan,
    Element,
    PlanSection,
    SourceMidi,
    load,
    validate,
)
from tools.validators.persona import (
    ALL_CHECKS,
    CHECK_DENSITY_INVERSION,
    CHECK_PROTAGONIST_COMPETITION,
    CHECK_ROUTE_PALETTE,
    CHECK_TEXTURE_VS_SHORT,
    CROWDED_COUNT_THRESHOLD,
    DENSITY_HIGH_THRESHOLD,
    DENSITY_LOW_THRESHOLD,
    HIGH_BAND,
    ROUTE_PALETTES,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SHORT_EVENT_ARTICULATIONS,
    SUPPORT_SYNC_ROLES,
    TEXTURE_ROLES,
    format_issues,
    has_errors,
    validate_persona,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_PLAN = FIXTURES_DIR / "plan_ancora_golden.json"


# --- fixtures ---------------------------------------------------------------

def _empty_analysis() -> Analysis:
    return Analysis(
        key_root=9,
        bars=[BarAnalysis(index=i, start=i * 2.0, end=(i + 1) * 2.0, chord=None)
              for i in range(4)],
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(
    label: str = "MAIN",
    *,
    start_bar: int = 0,
    end_bar: int = 4,
    densidade: int = 5,
    protagonist: str = "texture",
    kind: str = "chorus",
) -> PlanSection:
    return PlanSection(
        label=label, kind=kind,
        start_bar=start_bar, end_bar=end_bar,
        source="marker", protagonist=protagonist,
        energy={"densidade": densidade, "impacto": 5, "largura": 5,
                "altura": 5, "instabilidade": 3},
    )


def _element(
    id: str,
    *,
    role: str = "pad",
    sections: tuple[str, ...] = ("MAIN",),
    register: tuple[int, int] = (48, 71),
    sync_role: str = "sustain_through",
    articulation: str = "sustained",
    harmony: str = "free",
    is_protagonist: bool = False,
) -> Element:
    return Element(
        id=id, role=role, sections=list(sections),
        register=list(register), layers=1,
        sync_role=sync_role, articulation=articulation,
        harmony=harmony,
        is_protagonist=is_protagonist,
    )


def _plan(
    elements: list[Element],
    sections: list[PlanSection] | None = None,
    *,
    route: str = "cinematica_emocional",
) -> ArrangementPlan:
    return ArrangementPlan(
        version=1, seed=0,
        source_midi=SourceMidi(path="/dev/null", sha256="0" * 64),
        route=route,
        sections=sections or [_section("MAIN", start_bar=0, end_bar=4)],
        elements=elements,
    )


# --- constantes exportadas --------------------------------------------------

def test_support_sync_roles_matches_spec_vocabulary():
    """Os tres papeis de apoio vem do vocabulario SYNC_ROLES."""
    assert frozenset({
        "kick_support", "response", "sustain_through",
    }) == SUPPORT_SYNC_ROLES


def test_route_palettes_cover_declared_routes():
    """Cada rota do plan.ROUTES tem uma paleta declarada."""
    from tools.plan import ROUTES
    for route in ROUTES:
        assert route in ROUTE_PALETTES
        assert ROUTE_PALETTES[route], f"paleta vazia para {route!r}"


def test_short_event_articulations_is_ghost_and_staccato():
    assert frozenset({"ghost", "staccato"}) == SHORT_EVENT_ARTICULATIONS


def test_texture_roles_are_pad_and_drone():
    assert frozenset({"pad", "drone"}) == TEXTURE_ROLES


def test_high_band_starts_at_c5():
    """C5 = 72 MIDI (persona secao 7.3, item 4)."""
    assert HIGH_BAND[0] == 72
    assert HIGH_BAND[1] == 127


def test_density_thresholds_are_stable():
    """Limiares do AC devem permanecer estaveis; mudanca vira nova constante
    comentada com justificativa (mesmo padrao dos outros validadores)."""
    assert DENSITY_HIGH_THRESHOLD == 8
    assert DENSITY_LOW_THRESHOLD == 2
    assert CROWDED_COUNT_THRESHOLD == 4


def test_all_checks_lists_every_public_check():
    assert set(ALL_CHECKS) == {
        CHECK_PROTAGONIST_COMPETITION,
        CHECK_ROUTE_PALETTE,
        CHECK_DENSITY_INVERSION,
        CHECK_TEXTURE_VS_SHORT,
    }


# --- checagem 1: competicao com protagonista -------------------------------

def test_protagonist_alone_no_warning():
    """Elemento protagonista sozinho na secao = zero avisos."""
    ana = _empty_analysis()
    prot = _element("prot", is_protagonist=True)
    plan = _plan([prot])
    assert validate_persona(plan, [], ana) == []


def test_support_element_with_support_sync_role_no_warning():
    """Elemento com sync_role de apoio dividindo registro do protagonista =
    diretriz respeitada, zero avisos."""
    ana = _empty_analysis()
    prot = _element("prot", is_protagonist=True, register=(48, 71))
    support = _element(
        "sub", role="drone", register=(40, 60),
        sync_role="sustain_through",
    )
    plan = _plan([prot, support])
    assert validate_persona(plan, [], ana) == []


def test_competing_element_same_register_non_support_role_warns():
    """AC: elemento no registro do protagonista sem sync_role de apoio
    dispara aviso citando o par."""
    ana = _empty_analysis()
    prot = _element("prot", is_protagonist=True, register=(48, 71))
    rival = _element(
        "rival", role="rhodes", register=(50, 70),
        sync_role="guitar_unison",
    )
    plan = _plan([prot, rival])
    issues = validate_persona(plan, [], ana)
    competition = [i for i in issues if i.check == CHECK_PROTAGONIST_COMPETITION]
    assert len(competition) == 1
    i = competition[0]
    assert i.severity == SEVERITY_WARNING
    assert i.section == "MAIN"
    assert i.element_ids == ("prot", "rival")
    assert "prot" in i.message and "rival" in i.message
    assert "guitar_unison" in i.message


def test_competing_element_disjoint_register_no_warning():
    """Registro fora do protagonista = sem competicao mesmo sem support."""
    ana = _empty_analysis()
    prot = _element("prot", is_protagonist=True, register=(60, 71))
    hi = _element("hi", role="rhodes", register=(84, 96), sync_role="anticipation")
    plan = _plan([prot, hi])
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_PROTAGONIST_COMPETITION] == []


def test_no_protagonist_in_section_no_competition_warning():
    """Secao sem is_protagonist declarado nao aciona a checagem."""
    ana = _empty_analysis()
    a = _element("a", role="pad", sync_role="anticipation")
    b = _element("b", role="rhodes", sync_role="guitar_unison")
    plan = _plan([a, b])
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_PROTAGONIST_COMPETITION] == []


# --- checagem 2: rota x paleta ----------------------------------------------

def test_element_in_route_palette_no_warning():
    """Piano em cinematica_emocional = paleta correta, sem aviso."""
    ana = _empty_analysis()
    plan = _plan([_element("p", role="piano")], route="cinematica_emocional")
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_ROUTE_PALETTE] == []


def test_element_outside_route_palette_warns():
    """AC: role fora da paleta da rota declarada = aviso.

    motor pertence a hook_eletronico_pesado, nao a cinematica_emocional."""
    ana = _empty_analysis()
    motor = _element("m", role="motor", articulation="tight")
    plan = _plan([motor], route="cinematica_emocional")
    issues = validate_persona(plan, [], ana)
    palette = [i for i in issues if i.check == CHECK_ROUTE_PALETTE]
    assert len(palette) == 1
    i = palette[0]
    assert i.severity == SEVERITY_WARNING
    assert i.element_ids == ("m",)
    assert "motor" in i.message
    assert "cinematica_emocional" in i.message


def test_hook_route_accepts_rhythmic_machine():
    """rhythmic_machine em hook_eletronico_pesado = paleta correta."""
    ana = _empty_analysis()
    plan = _plan(
        [_element("r", role="rhythmic_machine", articulation="tight")],
        route="hook_eletronico_pesado",
    )
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_ROUTE_PALETTE] == []


# --- checagem 3: densidade x contagem ---------------------------------------

def test_high_density_zero_elements_warns():
    """AC: densidade >= 8 sem nenhum elemento no plano = inversao forte."""
    ana = _empty_analysis()
    # Secao com densidade 9 mas nenhum elemento a declara.
    plan = _plan(
        [_element("keep_elsewhere", sections=("OTHER",))],
        sections=[
            _section("PEAK", start_bar=0, end_bar=2, densidade=9),
            _section("OTHER", start_bar=2, end_bar=4, densidade=5),
        ],
    )
    issues = validate_persona(plan, [], ana)
    inv = [i for i in issues if i.check == CHECK_DENSITY_INVERSION]
    assert len(inv) == 1
    i = inv[0]
    assert i.section == "PEAK"
    assert "densidade axis=9" in i.message
    assert "0 elements" in i.message


def test_low_density_crowded_warns_with_numbers():
    """AC: densidade <= 2 com muitos elementos = inversao. Mensagem com numeros."""
    ana = _empty_analysis()
    labels = tuple(f"e{i}" for i in range(4))
    elements = [_element(lb, role="pad") for lb in labels]
    plan = _plan(
        elements,
        sections=[_section("QUIET", start_bar=0, end_bar=4, densidade=1)],
    )
    # Cada elemento declara sections=("MAIN",) por default; substituimos.
    plan = replace(plan, elements=[replace(e, sections=["QUIET"]) for e in elements])
    issues = validate_persona(plan, [], ana)
    inv = [i for i in issues if i.check == CHECK_DENSITY_INVERSION]
    assert len(inv) == 1
    i = inv[0]
    assert i.section == "QUIET"
    assert "densidade axis=1" in i.message
    assert "4 active" in i.message


def test_moderate_density_no_inversion_warning():
    """Densidade 5 com 2 elementos ativos = zona normal, sem aviso."""
    ana = _empty_analysis()
    plan = _plan([
        _element("a", role="pad"),
        _element("b", role="rhodes"),
    ])
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_DENSITY_INVERSION] == []


# --- checagem 4: textura no alto vs evento curto ----------------------------

def test_pad_high_and_ghost_element_same_section_warns():
    """AC: pad tocando banda high + ghost na mesma secao = aviso."""
    ana = _empty_analysis()
    pad = _element("p", role="pad", register=(60, 90))
    hit = _element("h", role="piano", articulation="ghost", register=(48, 71))
    plan = _plan([pad, hit])
    issues = validate_persona(plan, [], ana)
    tex = [i for i in issues if i.check == CHECK_TEXTURE_VS_SHORT]
    assert len(tex) == 1
    i = tex[0]
    assert i.section == "MAIN"
    assert "p" in i.element_ids and "h" in i.element_ids
    assert "72" in i.message


def test_pad_below_high_band_no_texture_warning():
    """Pad no mid (48-71) nao toca high — sem aviso mesmo com ghost."""
    ana = _empty_analysis()
    pad = _element("p", role="pad", register=(48, 71))
    hit = _element("h", role="piano", articulation="ghost")
    plan = _plan([pad, hit])
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_TEXTURE_VS_SHORT] == []


def test_pad_high_without_short_event_no_warning():
    """Pad alto sozinho ou com tight/open na mesma secao nao aciona."""
    ana = _empty_analysis()
    pad = _element("p", role="pad", register=(60, 90))
    tight = _element("t", role="rhodes", articulation="tight")
    plan = _plan([pad, tight])
    issues = validate_persona(plan, [], ana)
    assert [i for i in issues if i.check == CHECK_TEXTURE_VS_SHORT] == []


# --- strict mode ------------------------------------------------------------

def test_strict_mode_promotes_warnings_to_errors():
    """`strict=True` promove todos os avisos a erro (mesma msg, outra severidade)."""
    ana = _empty_analysis()
    motor = _element("m", role="motor", articulation="tight")
    plan = _plan([motor], route="cinematica_emocional")
    issues = validate_persona(plan, [], ana, strict=True)
    assert issues, "esperava pelo menos o aviso de paleta"
    assert all(i.severity == SEVERITY_ERROR for i in issues)
    assert has_errors(issues) is True


def test_default_mode_yields_warnings_only():
    ana = _empty_analysis()
    motor = _element("m", role="motor", articulation="tight")
    plan = _plan([motor], route="cinematica_emocional")
    issues = validate_persona(plan, [], ana)
    assert issues
    assert all(i.severity == SEVERITY_WARNING for i in issues)
    assert has_errors(issues) is False


# --- format_issues ----------------------------------------------------------

def test_format_issues_reports_ok_when_empty():
    assert format_issues([]) == "Persona: OK"


def test_format_issues_lists_errors_and_warnings_with_counts():
    ana = _empty_analysis()
    motor = _element("m", role="motor", articulation="tight")
    plan = _plan([motor], route="cinematica_emocional")
    issues = validate_persona(plan, [], ana)
    text = format_issues(issues)
    assert "Persona issues" in text
    assert "warning" in text
    assert "[WARNING]" in text


def test_format_issues_includes_errors_when_strict():
    ana = _empty_analysis()
    motor = _element("m", role="motor", articulation="tight")
    plan = _plan([motor], route="cinematica_emocional")
    issues = validate_persona(plan, [], ana, strict=True)
    text = format_issues(issues)
    assert "[ERROR]" in text


# --- ignora argumentos reservados -------------------------------------------

def test_ignores_rendered_tracks_and_analysis_this_round():
    """rendered_tracks e analysis passam pela API mas nao afetam o resultado
    nesta rodada — a persona vive no plano."""
    ana = _empty_analysis()
    prot = _element("prot", is_protagonist=True, register=(48, 71))
    plan = _plan([prot])
    baseline = validate_persona(plan, [], ana)
    from tools.validators.harmony import RenderedNote, RenderedTrack
    fake_tracks = [RenderedTrack(
        element_id="prot", track_name="fake",
        notes=(RenderedNote(pitch=60, start_s=0.0, end_s=0.5, velocity=80),),
    )]
    with_tracks = validate_persona(plan, fake_tracks, ana)
    assert baseline == with_tracks


# --- regressao golden -------------------------------------------------------

def test_golden_ancora_plan_zero_persona_warnings():
    """AC: plano golden do ANCORA = zero avisos da persona.

    O golden foi escrito para ser plano-referencia; qualquer aviso indica
    ou drift no plano ou regressao no validador."""
    if not GOLDEN_PLAN.exists():
        pytest.skip(f"Golden plan ausente em {GOLDEN_PLAN}")
    plan = load(GOLDEN_PLAN)
    validate(plan)  # sanity: golden ainda passa no schema
    ana = _empty_analysis()
    issues = validate_persona(plan, [], ana)
    assert issues == [], (
        "Golden ANCORA: persona deveria passar sem avisos. "
        f"Avisos observados: {[i.message for i in issues]}"
    )
