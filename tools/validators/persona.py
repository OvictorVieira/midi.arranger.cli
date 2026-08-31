"""Validador de persona (FR-27, US-010).

Prova estrutural de que o plano segue a persona ARQUITETO documentada em
`knoledgebase/persona_produtor_metal_moderno.md`. Todos os avisos apontam
uma diretriz — nao fisica. Diretriz nao vira erro no default; a flag
`--strict-persona` do CLI promove aviso a erro para revisao editorial
mais dura (equivalente ao `--allow-*` invertido dos outros validadores).

Diferente de harmony/placement/artifice, este validador roda sobre o
PLANO (e sobre a analise) — nao precisa das RenderedTracks. A persona
descreve a INTENCAO do arranjo; o resultado renderizado ja e coberto
pelos outros validadores. O parametro `rendered_tracks` entra na
assinatura por simetria com os outros validadores e para permitir uma
verificacao futura que compare intencao x saida sem quebrar a API.

Cada verificacao cita a secao da persona da qual saiu — regra sem
fonte NAO entra aqui. As referencias em docstring nao sao decorativas;
sao o contrato: qualquer ajuste de limiar exige nova leitura da fonte.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..analyze import Analysis
from ..constants import REGISTER_BANDS
from ..plan import ArrangementPlan, Element
from .harmony import RenderedTrack

# --- constantes -------------------------------------------------------------

SUPPORT_SYNC_ROLES: frozenset[str] = frozenset({
    "kick_support", "response", "sustain_through",
})
"""Sync roles considerados 'de apoio' ao protagonista da secao.
Persona `knoledgebase/persona_produtor_metal_moderno.md` secao 5.3
('Hierarquia de uma seção'): 'Somente um deve dominar por vez. Os
demais apoiam, respondem ou criam moldura.' — kick_support (fundacao),
response (chamada/resposta) e sustain_through (moldura sustentada) sao
os tres papeis de suporte no vocabulario SYNC_ROLES."""

# Paleta caracteristica por rota. Extraida da persona:
# - cinematica_emocional: secao 5.3 ('Paleta de timbres atribuivel a essa
#   abordagem') + secao 3.7 (arco emocional/piano/pad/drone/strings).
# - organica_inquietante: secao 4.1-4.6 ('sound design como psicologia e
#   materia', Rhodes/piano degradado, drone, pad, shadow como dobra e
#   texture).
# - hook_eletronico_pesado: secao 5.4-5.6 ('Rhythmic Machines / Arena
#   Leads / Seismic Subs' + secao 8.2 'Motor/Interlocking/Shadow' como
#   gramatica ritmica).
ROUTE_PALETTES: dict[str, frozenset[str]] = {
    "cinematica_emocional": frozenset({
        "piano", "rhodes", "strings", "choir", "pad", "drone",
    }),
    "organica_inquietante": frozenset({
        "rhodes", "piano", "pad", "drone", "shadow", "strings",
    }),
    "hook_eletronico_pesado": frozenset({
        "arp", "rhythmic_machine", "motor", "drone", "shadow",
        "hat_elec", "sub", "sub_drop",
    }),
}
"""Paletas por rota. Elemento fora da paleta da rota declarada dispara
aviso — nao bloqueia, e diretriz."""

SHORT_EVENT_ARTICULATIONS: frozenset[str] = frozenset({"ghost", "staccato"})
"""Articulacoes que caracterizam 'evento curto'. Persona secao 7.3
('Regras de registro', item 4): 'Acima de C5, usar ear candy, oitavas,
brilho, reverses e respostas curtas.' — evento curto no alto e o oposto
funcional de textura larga (pad/drone) ocupando a mesma banda."""

TEXTURE_ROLES: frozenset[str] = frozenset({"pad", "drone"})
"""Roles que se comportam como TEXTURA sustentada. Persona secao 2
('Resumo executivo', item 4): 'textura — ruído, gravações de campo,
vozes transformadas, pianos danificados e synths desafinados criam
identidade' + secao 7.4 (drone/pedal). Pad e drone sao os dois roles
implementados que caem nessa categoria."""

HIGH_BAND: tuple[int, int] = REGISTER_BANDS["high"]
"""Banda 'high' (72-127 MIDI). Persona secao 7.3, item 4:
'Acima de C5 (72 MIDI), usar ear candy...'. Textura sustentada na
mesma banda que um evento curto = mascara vs assinatura — a persona
recomenda que a textura recue quando o evento curto entra."""

DENSITY_HIGH_THRESHOLD: int = 8
"""Densidade declarada >= 8 considerada 'muito alta'. Persona secao
10.1 (Escala de energia): densidade 10 = 'espectro cheio / multiplas
fontes'; 8 ja e proximo do maximo. Secao com esse valor mas SEM
elemento declarado no plano denuncia intencao musical incoerente."""

DENSITY_LOW_THRESHOLD: int = 2
"""Densidade declarada <= 2 considerada 'muito baixa'. Persona secao
10.1: densidade 0 = 'uma fonte'. Secao rala com muitos elementos
declarados denuncia contradicao entre intencao (rala) e plano
(cheio)."""

CROWDED_COUNT_THRESHOLD: int = 4
"""Contagem >= 4 elementos ativos em secao de densidade <=2 dispara
aviso de contradicao. Persona secao 10.1 sugere reduzir densidade
elevando so alguns eixos por vez; 4+ elementos ativos com densidade
2 e a inversao mais gritante possivel."""

# Severidade default — persona e diretriz, nao fisica. --strict-persona
# no CLI promove todo aviso a erro (sem duplicacao no relatorio).
SEVERITY_WARNING: str = "warning"
SEVERITY_ERROR: str = "error"

# Chaves nomeadas dos avisos (vocabulario fechado). Facilita filtrar por
# tipo no consumidor sem parsear a mensagem.
CHECK_PROTAGONIST_COMPETITION: str = "protagonist_register_competition"
CHECK_ROUTE_PALETTE: str = "route_palette_mismatch"
CHECK_DENSITY_INVERSION: str = "density_count_inversion"
CHECK_TEXTURE_VS_SHORT: str = "texture_high_vs_short_event"

ALL_CHECKS: tuple[str, ...] = (
    CHECK_PROTAGONIST_COMPETITION,
    CHECK_ROUTE_PALETTE,
    CHECK_DENSITY_INVERSION,
    CHECK_TEXTURE_VS_SHORT,
)


# --- dataclass do issue -----------------------------------------------------

@dataclass(frozen=True)
class PersonaIssue:
    """Diretriz da persona violada.

    - severity: default `warning`. `error` apenas quando o CLI passou
      `--strict-persona`.
    - check: chave do vocabulario `ALL_CHECKS`.
    - section: label da secao envolvida (`-` quando o aviso e do plano
      inteiro — ex: elemento fora da paleta da rota).
    - element_ids: tupla de element_ids envolvidos (1 ou mais). Ordenada
      alfabeticamente para tornar a mensagem estavel entre execucoes.
    - message: mensagem pronta para exibicao.
    """
    severity: str
    check: str
    section: str
    element_ids: tuple[str, ...]
    message: str


# --- helpers ----------------------------------------------------------------

def _registers_overlap(a: Element, b: Element) -> bool:
    """Registro [low, high] de dois elementos se toca (intervalo fechado)."""
    return a.register[0] <= b.register[1] and a.register[1] >= b.register[0]


def _touches_high_band(element: Element) -> bool:
    """Registro do elemento cruza a banda `high` (72-127)."""
    lo, hi = element.register[0], element.register[1]
    return lo <= HIGH_BAND[1] and hi >= HIGH_BAND[0]


def _elements_in_section(plan: ArrangementPlan, label: str) -> list[Element]:
    return [e for e in plan.elements if label in e.sections]


def _issue(
    severity: str,
    check: str,
    section: str,
    element_ids: tuple[str, ...],
    detail: str,
) -> PersonaIssue:
    """Constroi `PersonaIssue` com mensagem uniforme.

    Formato: "[<check>] section 'S', elements <ids>: <detail>."
    """
    ids_repr = ",".join(element_ids) if element_ids else "-"
    message = (
        f"[{check}] section {section!r}, elements [{ids_repr}]: {detail}"
    )
    return PersonaIssue(
        severity=severity,
        check=check,
        section=section,
        element_ids=element_ids,
        message=message,
    )


# --- checagens --------------------------------------------------------------

def _check_protagonist_competition(
    plan: ArrangementPlan,
    severity: str,
) -> list[PersonaIssue]:
    """Elemento que compete com o protagonista da secao no mesmo registro.

    Persona `knoledgebase/persona_produtor_metal_moderno.md` secao 5.3
    ('Hierarquia de uma secao'): 'Somente um deve dominar por vez. Os
    demais apoiam, respondem ou criam moldura. Se dois protagonistas
    competem, alternar por frase ou dividir registros.' Quando o
    elemento sombreia o registro do protagonista mas seu sync_role NAO
    e um dos de apoio (SUPPORT_SYNC_ROLES), a diretriz e violada."""
    issues: list[PersonaIssue] = []
    protagonists_by_section: dict[str, Element] = {}
    for e in plan.elements:
        if not e.is_protagonist:
            continue
        for label in e.sections:
            # plan.validate ja garante um unico is_protagonist por secao;
            # o dict acima teria conflito antes de aqui.
            protagonists_by_section[label] = e

    for label, protagonist in protagonists_by_section.items():
        others = [
            e for e in _elements_in_section(plan, label)
            if e.id != protagonist.id
        ]
        for other in others:
            if not _registers_overlap(protagonist, other):
                continue
            if other.sync_role in SUPPORT_SYNC_ROLES:
                continue
            ids = tuple(sorted((protagonist.id, other.id)))
            issues.append(_issue(
                severity, CHECK_PROTAGONIST_COMPETITION, label, ids,
                f"element {other.id!r} shares register "
                f"{list(other.register)} with protagonist {protagonist.id!r} "
                f"{list(protagonist.register)} but sync_role "
                f"{other.sync_role!r} is not a support role "
                f"(expected one of {sorted(SUPPORT_SYNC_ROLES)})",
            ))
    return issues


def _check_route_palette(
    plan: ArrangementPlan,
    severity: str,
) -> list[PersonaIssue]:
    """Elemento cujo role nao pertence a paleta da rota declarada.

    Persona `knoledgebase/persona_produtor_metal_moderno.md` secao 14
    (secao 'TRÊS ROTAS' do prompt de sistema): as tres rotas tem paletas
    distintas — cinematica_emocional (arco/piano/textura), organica
    inquietante (samples autorais/Rhodes/drone) e hook_eletronico_pesado
    (arp-riff/sub/vocal). Role fora da paleta da rota escolhida indica
    que o plano esta misturando gramaticas — o formato entrega o mapa,
    a persona nao veta, so avisa."""
    palette = ROUTE_PALETTES[plan.route]
    issues: list[PersonaIssue] = []
    for e in plan.elements:
        if e.role in palette:
            continue
        issues.append(_issue(
            severity, CHECK_ROUTE_PALETTE, "-", (e.id,),
            f"role {e.role!r} is outside the palette of route "
            f"{plan.route!r} (expected one of {sorted(palette)})",
        ))
    return issues


def _check_density_inversion(
    plan: ArrangementPlan,
    severity: str,
) -> list[PersonaIssue]:
    """Inversao forte entre `energy.densidade` declarada e contagem de
    elementos ativos no plano.

    Persona `knoledgebase/persona_produtor_metal_moderno.md` secao 10.1
    ('Escala de energia'): densidade 0 = 'uma fonte', densidade 10 =
    'espectro cheio / multiplas fontes'. Duas inversoes graves valem
    aviso: (a) secao com densidade >= DENSITY_HIGH_THRESHOLD e ZERO
    elementos declarados no plano (arco esperado sem intencao musical
    escrita), (b) secao com densidade <= DENSITY_LOW_THRESHOLD e
    CROWDED_COUNT_THRESHOLD+ elementos ativos (secao intencionalmente
    rala virou salada). Fora desses extremos, a diretriz reconhece que
    as trilhas do source (guitarra/baixo/bateria/voz) ja contribuem
    densidade — o plano so declara camadas novas."""
    issues: list[PersonaIssue] = []
    for section in plan.sections:
        if section.energy is None:
            continue
        densidade = section.energy.get("densidade")
        if densidade is None:
            continue
        active = _elements_in_section(plan, section.label)
        count = len(active)
        ids = tuple(sorted(e.id for e in active))
        if densidade >= DENSITY_HIGH_THRESHOLD and count == 0:
            issues.append(_issue(
                severity, CHECK_DENSITY_INVERSION, section.label, (),
                f"densidade axis={densidade} but plan declares 0 elements — "
                f"section expects a peak yet nothing carries it",
            ))
        elif (
            densidade <= DENSITY_LOW_THRESHOLD
            and count >= CROWDED_COUNT_THRESHOLD
        ):
            issues.append(_issue(
                severity, CHECK_DENSITY_INVERSION, section.label, ids,
                f"densidade axis={densidade} but plan declares {count} "
                f"active elements — sparse section is crowded",
            ))
    return issues


def _check_texture_vs_short(
    plan: ArrangementPlan,
    severity: str,
) -> list[PersonaIssue]:
    """Textura (pad/drone) na banda `high` convivendo com evento curto
    (ghost/staccato) na mesma secao.

    Persona `knoledgebase/persona_produtor_metal_moderno.md` secao 7.3
    ('Regras de registro', item 4): 'Acima de C5, usar ear candy,
    oitavas, brilho, reverses e respostas curtas.' Textura sustentada
    (pad/drone) ocupando C5+ mascara qualquer evento curto de assinatura
    que a persona reserva para essa banda — a diretriz pede que a
    textura recue OU o evento curto va para outra banda."""
    issues: list[PersonaIssue] = []
    for section in plan.sections:
        active = _elements_in_section(plan, section.label)
        texture_high = [
            e for e in active
            if e.role in TEXTURE_ROLES and _touches_high_band(e)
        ]
        short_events = [
            e for e in active
            if e.articulation in SHORT_EVENT_ARTICULATIONS
        ]
        if not texture_high or not short_events:
            continue
        ids = tuple(sorted(
            {e.id for e in texture_high} | {e.id for e in short_events}
        ))
        texture_ids = sorted(e.id for e in texture_high)
        short_ids = sorted(e.id for e in short_events)
        issues.append(_issue(
            severity, CHECK_TEXTURE_VS_SHORT, section.label, ids,
            f"texture {texture_ids} touches high band {list(HIGH_BAND)} "
            f"while short-event elements {short_ids} share the section — "
            f"texture masks the short event or vice-versa",
        ))
    return issues


# --- API principal ----------------------------------------------------------

def validate_persona(
    plan: ArrangementPlan,
    rendered_tracks: list[RenderedTrack],  # noqa: ARG001 - reservado
    analysis: Analysis,  # noqa: ARG001 - reservado
    *,
    strict: bool = False,
) -> list[PersonaIssue]:
    """Roda todas as verificacoes da persona sobre o plano.

    Args:
      plan: `ArrangementPlan` ja validado por `plan.validate`.
      rendered_tracks: passado por simetria com os outros validadores.
        Reservado para uma checagem futura que compare intencao (plano)
        x saida (tracks) sem quebrar a API. As checagens desta rodada
        so leem o plano.
      analysis: passado por simetria; nao consumido aqui — a persona
        vive no plano, nao na analise do audio.
      strict: quando True, todos os avisos sao promovidos a `error`.
        Mesma semantica do `--strict-persona` do CLI.
    """
    severity = SEVERITY_ERROR if strict else SEVERITY_WARNING
    issues: list[PersonaIssue] = []
    issues.extend(_check_protagonist_competition(plan, severity))
    issues.extend(_check_route_palette(plan, severity))
    issues.extend(_check_density_inversion(plan, severity))
    issues.extend(_check_texture_vs_short(plan, severity))
    return issues


def has_errors(issues: list[PersonaIssue]) -> bool:
    """True quando ha pelo menos uma severidade `error` na lista."""
    return any(i.severity == SEVERITY_ERROR for i in issues)


def format_issues(issues: list[PersonaIssue]) -> str:
    """Pretty-print para o relatorio do render."""
    if not issues:
        return "Persona: OK"
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    warnings = [i for i in issues if i.severity == SEVERITY_WARNING]
    lines = [
        f"Persona issues: {len(errors)} error(s), {len(warnings)} warning(s)"
    ]
    for i in errors:
        lines.append(f"  [ERROR]   {i.message}")
    for i in warnings:
        lines.append(f"  [WARNING] {i.message}")
    return "\n".join(lines)


__all__ = [
    "ALL_CHECKS",
    "CHECK_DENSITY_INVERSION",
    "CHECK_PROTAGONIST_COMPETITION",
    "CHECK_ROUTE_PALETTE",
    "CHECK_TEXTURE_VS_SHORT",
    "CROWDED_COUNT_THRESHOLD",
    "DENSITY_HIGH_THRESHOLD",
    "DENSITY_LOW_THRESHOLD",
    "HIGH_BAND",
    "PersonaIssue",
    "ROUTE_PALETTES",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SHORT_EVENT_ARTICULATIONS",
    "SUPPORT_SYNC_ROLES",
    "TEXTURE_ROLES",
    "format_issues",
    "has_errors",
    "validate_persona",
]
