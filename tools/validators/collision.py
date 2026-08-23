"""Validador de colisao de registro (US-015).

Regras (secao 7.3 do persona, secao 6 do spec):
- Abaixo de C2 (< 36): nota unica ou quinta. Voicing denso proibido.
- Entre C2 e C3 (36-47): sem acorde fechado quando ha densidade concorrente.
- Entre C3 e C5 (48-71): corpo harmonico livre — sem colisao aqui.
- Acima de C5 (>= 72): apenas evento curto (ghost/staccato/tight).

Mapa de convivencia: cada `Element` do plano declara `register` [low, high] e
como nao competir via `harmony` e `articulation`. O validador olha pares que
dividem secao e detecta violacoes.

Ao detectar colisao, RELOCALIZA — sobe o registro do elemento uma oitava
inteira. Quando a relocacao nao resolve (elemento ja no topo, ou regra que
nao se resolve por deslocamento de oitava), emite aviso com o par colidente,
a secao e a faixa de compassos.

FR-17 e FR-18: NUNCA remove elemento, NUNCA reduz layers, NUNCA reduz a
contagem por conta propria. So mexe em `Element.register`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..constants import REGISTER_BANDS
from ..plan import ArrangementPlan, Element

# Fronteiras exatas (semitons MIDI). Alinhadas a REGISTER_BANDS do spec.
C2_PITCH = 36
C3_PITCH = 48
C5_PITCH = 72
MIDI_MAX = 127
OCTAVE = 12

DENSE_HARMONIES: frozenset[str] = frozenset({"follow_chords"})
LONG_ARTICULATIONS: frozenset[str] = frozenset({"sustained", "let_ring", "open"})
FIFTH_SEMITONES = 7


# --- dataclasses do relatorio ------------------------------------------------

@dataclass(frozen=True)
class CollisionRelocation:
    """Registro de uma relocacao aplicada em resposta a uma colisao resolvivel."""
    element_id: str
    section_label: str
    from_register: tuple[int, int]
    to_register: tuple[int, int]
    reason: str


@dataclass(frozen=True)
class CollisionWarning:
    """Colisao que a relocacao nao resolveu.

    Carrega o par (ou grupo) de elementos colidentes, a secao e a faixa de
    compassos afetada — atende ao AC "avisa e explica qual par de elementos
    colide em qual compasso".
    """
    element_ids: tuple[str, ...]
    section_label: str
    bar_range: tuple[int, int]
    band: str
    reason: str


@dataclass
class CollisionReport:
    relocations: list[CollisionRelocation] = field(default_factory=list)
    warnings: list[CollisionWarning] = field(default_factory=list)


# --- funcoes puras -----------------------------------------------------------

def _is_dense(e: Element) -> bool:
    return e.harmony in DENSE_HARMONIES


def _is_long(e: Element) -> bool:
    return e.articulation in LONG_ARTICULATIONS


def _register_span(reg: tuple[int, int] | list[int]) -> int:
    return reg[1] - reg[0]


def _registers_overlap(a: tuple[int, int] | list[int],
                       b: tuple[int, int] | list[int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _band_overlap(reg: tuple[int, int] | list[int], band: str) -> bool:
    lo, hi = REGISTER_BANDS[band]
    return reg[0] <= hi and reg[1] >= lo


def _shift_up_octave(reg: tuple[int, int]) -> tuple[int, int] | None:
    new_hi = reg[1] + OCTAVE
    if new_hi > MIDI_MAX:
        return None
    return (reg[0] + OCTAVE, new_hi)


def _reg_tuple(e: Element) -> tuple[int, int]:
    return (e.register[0], e.register[1])


# --- deteccao de violacoes ---------------------------------------------------

def _violates_sub_rule(e: Element) -> bool:
    """Elemento denso inteiramente abaixo de C2 com span alem da quinta justa."""
    if e.register[1] >= C2_PITCH:
        return False
    if not _is_dense(e):
        return False
    return _register_span(e.register) > FIFTH_SEMITONES


def _dense_pairs_in_low(elements: Iterable[Element]) -> list[Element]:
    """Elementos densos cujo registro overlappa a banda C2-C3."""
    return [
        e for e in elements
        if _is_dense(e) and _band_overlap(e.register, "low")
    ]


def _violates_high_rule(e: Element) -> bool:
    """Elemento cujo registro esta inteiramente acima de C5 com articulacao longa."""
    if e.register[0] < C5_PITCH:
        return False
    return _is_long(e)


# --- API principal -----------------------------------------------------------

def validate_collisions(plan: ArrangementPlan) -> CollisionReport:
    """Roda todas as regras sobre `plan` e devolve o `CollisionReport`.

    Mutacao: `Element.register` pode ser sobrescrito in-place ao relocar
    (sempre em oitavas inteiras). Nenhum elemento e removido; nenhum layer e
    reduzido; nenhum campo alem de `register` e alterado.
    """
    report = CollisionReport()

    for section in plan.sections:
        elements = [e for e in plan.elements if section.label in e.sections]
        bars = (section.start_bar, section.end_bar)

        _apply_sub_rule(elements, section.label, bars, report)
        _apply_low_pair_rule(elements, section.label, bars, report)
        _apply_high_rule(elements, section.label, bars, report)

    return report


def _apply_sub_rule(
    elements: list[Element],
    section_label: str,
    bars: tuple[int, int],
    report: CollisionReport,
) -> None:
    """Regra 1: abaixo de C2 apenas nota unica ou quinta."""
    for e in elements:
        if not _violates_sub_rule(e):
            continue
        original = _reg_tuple(e)
        new_reg = _shift_up_octave(original)
        if new_reg is None:
            report.warnings.append(CollisionWarning(
                element_ids=(e.id,),
                section_label=section_label,
                bar_range=bars,
                band="sub",
                reason=(
                    f"element {e.id!r} sits entirely below C2 with dense harmony "
                    f"(span {_register_span(original)} semitones > perfect fifth); "
                    f"cannot relocate up one octave (would exceed MIDI {MIDI_MAX})"
                ),
            ))
            continue
        e.register = [new_reg[0], new_reg[1]]
        report.relocations.append(CollisionRelocation(
            element_id=e.id,
            section_label=section_label,
            from_register=original,
            to_register=new_reg,
            reason=(
                "below C2 requires single note or fifth; relocated up one octave"
            ),
        ))


def _apply_low_pair_rule(
    elements: list[Element],
    section_label: str,
    bars: tuple[int, int],
    report: CollisionReport,
) -> None:
    """Regra 2: enquanto houver 2+ elementos densos overlappando C2-C3, sobe o mais grave uma oitava."""
    violators = _dense_pairs_in_low(elements)
    # Guarda contra loop infinito: no maximo N tentativas de relocacao por secao.
    max_iterations = max(1, len(elements)) * 2
    while len(violators) >= 2 and max_iterations > 0:
        max_iterations -= 1
        # Sobe o mais grave: ordena por register[0] ascendente, id como tie-break
        # deterministico.
        target = sorted(violators, key=lambda e: (e.register[0], e.id))[0]
        original = _reg_tuple(target)
        new_reg = _shift_up_octave(original)
        if new_reg is None:
            report.warnings.append(CollisionWarning(
                element_ids=tuple(sorted(e.id for e in violators)),
                section_label=section_label,
                bar_range=bars,
                band="low",
                reason=(
                    f"{len(violators)} dense elements overlap C2-C3 band; "
                    f"{target.id!r} cannot relocate up one octave "
                    f"(would exceed MIDI {MIDI_MAX})"
                ),
            ))
            return
        target.register = [new_reg[0], new_reg[1]]
        report.relocations.append(CollisionRelocation(
            element_id=target.id,
            section_label=section_label,
            from_register=original,
            to_register=new_reg,
            reason=(
                "multiple dense elements overlap C2-C3 band; "
                "relocated the lowest one up one octave"
            ),
        ))
        violators = _dense_pairs_in_low(elements)


def _apply_high_rule(
    elements: list[Element],
    section_label: str,
    bars: tuple[int, int],
    report: CollisionReport,
) -> None:
    """Regra 4: acima de C5, apenas evento curto.

    Relocacao nao resolve — o problema e a articulacao, nao o registro. Emite
    aviso para o usuario editar `articulation` no plano.
    """
    for e in elements:
        if not _violates_high_rule(e):
            continue
        report.warnings.append(CollisionWarning(
            element_ids=(e.id,),
            section_label=section_label,
            bar_range=bars,
            band="high",
            reason=(
                f"element {e.id!r} has long articulation {e.articulation!r} "
                f"entirely above C5; high band requires short events "
                f"(ghost/staccato/tight)"
            ),
        ))
