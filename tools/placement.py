"""Validador de placement e densidade (FR-26, US-002).

Valida a SAIDA RENDERIZADA (tracks geradas). Toda nota de elemento tem que
ter onset dentro dos ticks de alguma secao declarada em `elements[].sections`.
Nota com onset fora da uniao das secoes declaradas do elemento = **erro**
nomeando elemento, track, compasso e secao mais proxima (FR-26 do spec).

Cauda de articulacoes `let_ring`/`sustained` pode ultrapassar a fronteira ate
no MAXIMO o primeiro compasso da secao seguinte — dois compassos alem = erro.
Roles de transicao (`TRANSITION_EXEMPT_ROLES`) sao isentos por natureza. A
lista fica vazia nesta rodada; a rodada 3 preenche quando riser/impact
entrarem.

Densidade real (notas/compasso por elemento x secao) e comparada com a media
das densidades dos elementos ativos naquela secao. Quando a secao declara
`energy.densidade <= 3` (secao intencionalmente rala), qualquer elemento com
densidade > `DENSITY_MULTIPLIER` x media dispara **aviso** com numeros. Nunca
bloqueia — a regra-mae do documento e "o mapa manda", desvio de densidade e
diagnostico, nao veto.

Elemento declarado numa secao sem produzir nota nela = **aviso** de cobertura,
tambem nao-bloqueante.

Consome apenas `analysis.py` (bars com start/end em segundos) e o proprio
plano. Nao reimplementa deteccao de compasso.
"""

from __future__ import annotations

from dataclasses import dataclass

from .analyze import Analysis, BarAnalysis, bar_number, find_bar
from .harmony_validator import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    RenderedTrack,
    pitch_name,
)
from .plan import ArrangementPlan, PlanSection

# --- constantes -------------------------------------------------------------

TRANSITION_EXEMPT_ROLES: frozenset[str] = frozenset()
"""Roles isentos do validador — evento de transicao entra em qualquer secao
por natureza (riser desce sobre a fronteira, impact estoura no downbeat da
proxima secao). Vazio nesta rodada; a rodada 3 preenche quando a paleta
eletronica de transicao entrar (FR-14/FR-26)."""

TAIL_ALLOWED_ARTICULATIONS: frozenset[str] = frozenset({"sustained", "let_ring"})
"""Articulacoes cuja cauda pode ultrapassar a fronteira da secao ate o
primeiro compasso da seguinte. Outras articulacoes (staccato/tight/ghost/
open) so validam onset — a duracao nao dispara verificacao de tail."""

DENSITY_LOW_AXIS_THRESHOLD: int = 3
"""Limiar de `energy.densidade` abaixo do qual a checagem de densidade
dispara. Secao com densidade declarada <= 3 e uma secao intencionalmente
rala — elemento denso ali se torna notavel e vira aviso."""

DENSITY_MULTIPLIER: float = 2.0
"""Multiplo da media de densidade da secao a partir do qual um elemento
recebe aviso. Um elemento com 3x a media chama a atencao; 2x ja e o piso
default. Configuravel aqui como constante em vez de arg — validador roda
sem parametros para o CLI e o teste."""

TAIL_TOLERANCE_S: float = 1e-6
"""Folga (segundos) para comparar `end_s` com a fronteira do bar limite —
evita disparar erro por diferenca de arredondamento float."""

BAR_FALLBACK_INDEX: int = 0
"""Numero de compasso reportado quando o onset caiu antes do primeiro bar
da analise. Mesmo padrao do harmony_validator (`_bar_number` fallback)."""


# --- dataclass do issue -----------------------------------------------------

@dataclass(frozen=True)
class PlacementIssue:
    """Problema de placement/densidade em um elemento renderizado.

    - severity: `error` (bloqueia render sem --allow-placement) ou `warning`.
    - element_id: id do elemento no plano.
    - track: nome da track gerada. `-` quando o issue nao aponta para uma
      nota especifica (ex: aviso de cobertura de secao vazia).
    - bar: numero do compasso (1-based). 0 quando o onset caiu fora do range
      de bars da analise ou o issue nao tem bar associado.
    - pitch: pitch MIDI da nota ofensora. 0 para issues sem nota (cobertura).
    - section: label da secao envolvida (mais proxima do onset em erro; a
      propria em aviso de cobertura ou densidade).
    - message: mensagem pronta para exibicao.
    """
    severity: str
    element_id: str
    track: str
    bar: int
    pitch: int
    section: str
    message: str


# --- helpers ----------------------------------------------------------------

def _get_bar_by_index(analysis: Analysis, index: int) -> BarAnalysis | None:
    """Bar com `index` na analise; None quando nao existe."""
    for bar in analysis.bars:
        if bar.index == index:
            return bar
    return None


def _containing_section(
    bar: BarAnalysis | None,
    sections: list[PlanSection],
) -> PlanSection | None:
    """Primeira secao declarada que contem `bar.index` (0-based, half-open
    [start_bar, end_bar))."""
    if bar is None:
        return None
    idx = bar.index
    for s in sections:
        if s.start_bar <= idx < s.end_bar:
            return s
    return None


def _nearest_section(
    bar: BarAnalysis | None,
    sections: list[PlanSection],
) -> PlanSection | None:
    """Secao declarada mais proxima do bar (menor distancia em compassos).
    Empate resolvido pela ordem de declaracao. None quando sections vazia."""
    if not sections:
        return None
    if bar is None:
        # Sem bar identificado — reporta a primeira secao declarada como
        # referencia mais provavel; alternativa seria "nenhuma", mas o
        # relatorio quer sempre uma dica de onde a nota deveria ter caido.
        return sections[0]
    idx = bar.index

    def distance(s: PlanSection) -> int:
        if idx < s.start_bar:
            return s.start_bar - idx
        if idx >= s.end_bar:
            return idx - (s.end_bar - 1)
        return 0

    return min(sections, key=distance)


def _issue(
    severity: str,
    element_id: str,
    track: str,
    bar: int,
    pitch: int,
    section: str,
    detail: str,
) -> PlacementIssue:
    """Constroi `PlacementIssue` com mensagem uniforme.

    Formato: "element X, track T, bar N, section 'S': <detail>."
    Um formato so garante que quem le o relatorio nao precisa aprender dois
    estilos entre validadores.
    """
    message = (
        f"element {element_id!r}, track {track!r}, bar {bar}, "
        f"section {section!r}: {detail}"
    )
    return PlacementIssue(
        severity=severity,
        element_id=element_id,
        track=track,
        bar=bar,
        pitch=pitch,
        section=section,
        message=message,
    )


# --- checagens --------------------------------------------------------------

def _check_note_placement(
    element_id: str,
    articulation: str,
    track: RenderedTrack,
    declared_sections: list[PlanSection],
    analysis: Analysis,
) -> tuple[list[PlacementIssue], dict[str, int]]:
    """Valida onset e tail de cada nota da track.

    Devolve (issues, notes_per_section) — a contagem por secao alimenta as
    checagens de cobertura e densidade sem uma segunda passada."""
    issues: list[PlacementIssue] = []
    notes_per_section: dict[str, int] = {s.label: 0 for s in declared_sections}
    check_tail = articulation in TAIL_ALLOWED_ARTICULATIONS

    for note in track.notes:
        bar = find_bar(analysis, note.start_s)
        bar_num = bar_number(bar)
        containing = _containing_section(bar, declared_sections)

        if containing is None:
            nearest = _nearest_section(bar, declared_sections)
            nearest_label = nearest.label if nearest is not None else "-"
            issues.append(_issue(
                SEVERITY_ERROR, element_id, track.track_name,
                bar_num, note.pitch, nearest_label,
                f"note {pitch_name(note.pitch)} onset outside declared "
                f"sections; nearest section is {nearest_label!r}",
            ))
            continue

        notes_per_section[containing.label] += 1

        if not check_tail:
            continue

        # Cauda permitida: fim da nota ate o final do primeiro bar apos a
        # secao (index = section.end_bar). Se a analise nao tem esse bar,
        # nao ha proximo compasso — musica termina, nao ha erro possivel.
        limit_bar = _get_bar_by_index(analysis, containing.end_bar)
        if limit_bar is None:
            continue
        if note.end_s > limit_bar.end + TAIL_TOLERANCE_S:
            end_bar_1based = containing.end_bar + 1  # limit_bar em 1-based
            issues.append(_issue(
                SEVERITY_ERROR, element_id, track.track_name,
                bar_num, note.pitch, containing.label,
                f"sustained note {pitch_name(note.pitch)} tail extends beyond "
                f"bar {end_bar_1based} (first bar past section {containing.label!r})",
            ))

    return issues, notes_per_section


def _check_coverage(
    element_id: str,
    declared_sections: list[PlanSection],
    notes_per_section: dict[str, int],
) -> list[PlacementIssue]:
    """Aviso quando um elemento nao produziu nota em uma secao declarada."""
    issues: list[PlacementIssue] = []
    for s in declared_sections:
        if notes_per_section.get(s.label, 0) > 0:
            continue
        issues.append(_issue(
            SEVERITY_WARNING, element_id, "-",
            s.start_bar + 1, 0, s.label,
            f"element declared in section {s.label!r} but produced no notes there",
        ))
    return issues


def _bar_count(section: PlanSection) -> int:
    """Compassos declarados na secao. Piso 1 para evitar divisao por zero
    caso uma secao invalida entre com end_bar <= start_bar (plan.validate
    ja rejeita, mas defensivo aqui vale mais que uma excecao no meio do
    relatorio)."""
    return max(1, section.end_bar - section.start_bar)


def _check_density(
    plan: ArrangementPlan,
    element_notes: dict[str, dict[str, int]],
) -> list[PlacementIssue]:
    """Aviso de densidade por secao rala.

    Para cada secao com `energy.densidade <= DENSITY_LOW_AXIS_THRESHOLD`,
    calcula a densidade (notas/bar) de cada elemento declarado nela e emite
    aviso quando um elemento excede `DENSITY_MULTIPLIER` x media do grupo.
    Grupo com um unico elemento ativo nao dispara (nada com que comparar)."""
    issues: list[PlacementIssue] = []
    sections_by_label = {s.label: s for s in plan.sections}

    per_section: dict[str, list[tuple[str, float]]] = {}
    for element_id, buckets in element_notes.items():
        for label, count in buckets.items():
            if count <= 0:
                continue
            section = sections_by_label.get(label)
            if section is None:
                continue
            density = count / _bar_count(section)
            per_section.setdefault(label, []).append((element_id, density))

    for label, entries in per_section.items():
        section = sections_by_label[label]
        if section.energy is None:
            continue
        axis = section.energy.get("densidade")
        if axis is None or axis > DENSITY_LOW_AXIS_THRESHOLD:
            continue
        if len(entries) < 2:
            # Nada com que comparar — mean == densidade do unico elemento,
            # o limiar 2x nunca dispara sozinho. Silencio evita ruido.
            continue
        mean = sum(d for _, d in entries) / len(entries)
        limit = DENSITY_MULTIPLIER * mean
        for element_id, density in entries:
            if density <= limit:
                continue
            issues.append(_issue(
                SEVERITY_WARNING, element_id, "-",
                section.start_bar + 1, 0, label,
                f"density {density:.2f} notes/bar exceeds {DENSITY_MULTIPLIER:.1f}x "
                f"section mean {mean:.2f} (declared densidade axis={axis})",
            ))
    return issues


# --- API principal ----------------------------------------------------------

def validate_placement(
    rendered_tracks: list[RenderedTrack],
    plan: ArrangementPlan,
    analysis: Analysis,
) -> list[PlacementIssue]:
    """Roda placement + cobertura + densidade sobre as tracks geradas.

    Track cujo `element_id` nao aparece no plano e ignorada — mesmo contrato
    do harmony_validator. Elementos com role em `TRANSITION_EXEMPT_ROLES`
    saem sem checagem (isencao por natureza no FR-26). Elementos declarados
    no plano sem track renderizada nao geram aviso de cobertura — o renderer
    pode ter pulado (role nao implementado); o aviso e sobre saida, nao
    intencao.
    """
    issues: list[PlacementIssue] = []
    elements_by_id = {e.id: e for e in plan.elements}
    sections_by_label = {s.label: s for s in plan.sections}

    tracks_by_element: dict[str, list[RenderedTrack]] = {}
    for track in rendered_tracks:
        tracks_by_element.setdefault(track.element_id, []).append(track)

    # Contagem por (element_id, section_label) — alimenta a checagem de
    # densidade global apos o loop.
    element_notes: dict[str, dict[str, int]] = {}

    for element_id, tracks in tracks_by_element.items():
        element = elements_by_id.get(element_id)
        if element is None:
            continue
        if element.role in TRANSITION_EXEMPT_ROLES:
            continue

        declared_sections = [
            sections_by_label[label]
            for label in element.sections
            if label in sections_by_label
        ]
        if not declared_sections:
            # Plano invalido teria sido barrado por plan.validate; guarda
            # defensiva no caso de mutacao em teste.
            continue

        aggregated: dict[str, int] = {s.label: 0 for s in declared_sections}
        for track in tracks:
            track_issues, notes_per_section = _check_note_placement(
                element_id, element.articulation, track,
                declared_sections, analysis,
            )
            issues.extend(track_issues)
            for label, count in notes_per_section.items():
                aggregated[label] += count

        issues.extend(_check_coverage(element_id, declared_sections, aggregated))
        element_notes[element_id] = aggregated

    issues.extend(_check_density(plan, element_notes))
    return issues


def has_errors(issues: list[PlacementIssue]) -> bool:
    """True quando ha pelo menos uma severidade `error` na lista."""
    return any(i.severity == SEVERITY_ERROR for i in issues)


def format_issues(issues: list[PlacementIssue]) -> str:
    """Pretty-print para o relatorio do render (erros primeiro)."""
    if not issues:
        return "Placement: OK"
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    warnings = [i for i in issues if i.severity == SEVERITY_WARNING]
    lines = [
        f"Placement issues: {len(errors)} error(s), {len(warnings)} warning(s)"
    ]
    for i in errors:
        lines.append(f"  [ERROR]   {i.message}")
    for i in warnings:
        lines.append(f"  [WARNING] {i.message}")
    return "\n".join(lines)


__all__ = [
    "BAR_FALLBACK_INDEX",
    "DENSITY_LOW_AXIS_THRESHOLD",
    "DENSITY_MULTIPLIER",
    "PlacementIssue",
    "TAIL_ALLOWED_ARTICULATIONS",
    "TAIL_TOLERANCE_S",
    "TRANSITION_EXEMPT_ROLES",
    "format_issues",
    "has_errors",
    "validate_placement",
]
