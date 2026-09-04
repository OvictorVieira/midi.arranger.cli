"""Renderer (US-016).

Consome `ArrangementPlan` + MIDI de origem e produz o MIDI de saida como
arquivo novo. Nunca sobrescreve o source. Preserva tempo, formula de
compasso, marcadores e as notas originais nota-a-nota (copia verbatim das
tracks do source via mido). Cada elemento do plano vira uma ou mais tracks
novas nomeadas pela convencao de US-013.

Nesta rodada (R1 — Motor) so o role `pad` gera tracks. Roles que ainda nao
existem no motor sao ignorados sem falha: entram no relatorio com nota
explicando que ficaram para rodadas seguintes, e nenhuma track e emitida
para eles. Isso mantem o pipeline end-to-end validavel antes da paleta
completa.

Determinismo:
- Copiar tracks do source verbatim (mensagem por mensagem) garante que as
  notas originais saem identicas.
- `mido.MidiFile.save` e deterministica dada a mesma representacao em
  memoria — dois renders com o mesmo plano e mesmo source produzem bytes
  identicos.
- Cada chamada de `generate_pad` recebe uma seed derivada de
  `sha256(plan.seed | element.id | section_label)`, entao mudanca de secao
  ou de elemento nao correlaciona o stagger entre eles, mas cada (plano,
  elemento, secao) e reprodutivel.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from typing import Any

import mido
import pretty_midi

from .analyze import Analysis, analyze
from .edits import EditReport, apply_edits, collect_track_names, track_name
from .palette.bass import BASS_ROLES, generate_bass
from .palette.drums import DRUMS_ROLES, generate_drums
from .palette.electronic import (
    HAT_ELEC_ROLES,
    SUB_DROP_ROLES,
    SUB_ROLES,
    bars_in_section,
    generate_hat_elec,
    generate_sub,
    generate_sub_drop,
)
from .palette.guitar import GUITAR_ROLES, generate_guitar
from .palette.harmonic import (
    DRONE_ROLES,
    KEYBOARD_ROLES,
    STRINGS_GHOST_RATIO,
    STRINGS_ROLES,
    STRINGS_TUTTI_MAX_VOICES,
    DroneNote,
    KeyboardNote,
    PadNote,
    StringsNote,
    check_tutti_uniqueness,
    generate_drone,
    generate_keyboard,
    generate_pad,
    generate_strings,
)
from .palette.rhythmic import (
    MOTOR_ROLES,
    RHYTHMIC_ROLES,
    SHADOW_ROLES,
    RhythmicNote,
    generate_motor,
    generate_rhythmic,
    generate_shadow,
)
from .palette.transitions import (
    DOWNER_ROLES,
    IMPACT_ROLES,
    REVERSE_ROLES,
    RISER_ROLES,
    generate_downer,
    generate_impact,
    generate_reverse,
    generate_riser,
)
from .plan import (
    ROLE_STYLE_FAMILIES,
    STYLE_FAMILIES,
    ArrangementPlan,
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    PlanValidationError,
    _canonicalize_authorized_name,
    _load_brief_authorized_techniques,
    _load_brief_excluded_families,
    _reject_style_techniques_without_brief,
    load,
    load_brief_instrument_tuning,
    normalize_style_defaults,
    validate_edits_against_midi,
)
from .plan import (
    validate as validate_plan,
)
from .style_schema import is_style_parameter_scalar
from .techniques import (
    TechniqueApplyResult,
    TechniqueContractError,
    TechniqueIndex,
    TechniquePhysicalError,
    TechniqueRecipeError,
    UnknownTechniqueError,
    apply_technique_with_warnings,
)
from .techniques import (
    build_index as build_techniques_index,
)
from .tracks import is_ascii_safe, name_for_element
from .validators.anticopy import (
    AntiCopyIssue,
    ReferenceSequence,
    load_reference_sequences,
    validate_anticopy,
)
from .validators.anticopy import format_issues as format_anticopy_issues
from .validators.artifice import ArtificeIssue, validate_artifice
from .validators.artifice import format_issues as format_artifice_issues
from .validators.collision import CollisionReport, validate_collisions
from .validators.harmony import (
    HarmonyIssue,
    RenderedNote,
    RenderedTrack,
    format_issues,
    validate_harmony,
)
from .validators.persona import PersonaIssue, validate_persona
from .validators.persona import format_issues as format_persona_issues
from .validators.placement import PlacementIssue, validate_placement
from .validators.placement import format_issues as format_placement_issues
from .validators.transitions import TransitionIssue, validate_transitions
from .validators.transitions import format_issues as format_transition_issues

# --- constantes -------------------------------------------------------------

DEFAULT_OUTPUT_SUFFIX = "_arranged.mid"
DEFAULT_OUTPUT_DIRNAME = "Desktop"
PAD_CHANNEL = 0
KEYBOARD_CHANNEL = 0
STRINGS_CHANNEL = 0
DRONE_CHANNEL = 0
RHYTHMIC_CHANNEL = 0
MOTOR_CHANNEL = 0
SHADOW_CHANNEL = 0
BASS_CHANNEL = 0
GUITAR_CHANNEL = 0
DRUMS_CHANNEL = 9
"""Canal MIDI 10 (indice 0-based 9) — convencao General MIDI de percussao,
a mesma que Superior Drummer/Addictive Drums e qualquer DAW esperam para
reconhecer a track como kit em vez de instrumento melodico."""
HAT_ELEC_CHANNEL = 0
SUB_CHANNEL = 0
SUB_DROP_CHANNEL = 0
RISER_CHANNEL = 0
DOWNER_CHANNEL = 0
IMPACT_CHANNEL = 0
REVERSE_CHANNEL = 0
SUSTAIN_CC = 64
EXPRESSION_CC = 11
KEYBOARD_PATTERN_FIELDS: frozenset[str] = frozenset({"use_sustain_cc64"})
STRINGS_PATTERN_FIELDS: frozenset[str] = frozenset({"tutti", "ghost_ratio"})
DRONE_PATTERN_FIELDS: frozenset[str] = frozenset({
    "pedal", "pedal_pitch", "filter_cycle_bars", "modulation_bars",
})
RHYTHMIC_PATTERN_FIELDS: frozenset[str] = frozenset({
    "pattern_bars", "mutate_every_bars", "interlock",
    "filter_cycle_bars", "velocity_cycle_bars", "custom_steps",
})
MOTOR_PATTERN_FIELDS: frozenset[str] = frozenset({
    "subdivision", "filter_cycle_bars", "velocity_cycle_bars", "custom_steps",
})
SHADOW_PATTERN_FIELDS: frozenset[str] = frozenset({
    "octave_shift", "tail_notes", "phrase_end_gap_s", "velocity_offset",
    "note_duration_s",
})
DRUMS_PATTERN_FIELDS: frozenset[str] = frozenset()
BASS_PATTERN_FIELDS: frozenset[str] = frozenset()
GUITAR_PATTERN_FIELDS: frozenset[str] = frozenset()
"""Nem bateria nem baixo (issue #20) consomem `element.pattern` nesta
rodada — todo controle vem de `element.register`/`energy` da secao. Campo
declarado em `pattern` para esses roles vira aviso de nao-suportado, mesma
politica do pad."""
HAT_ELEC_PATTERN_FIELDS: frozenset[str] = frozenset({"pattern_mode"})
SUB_PATTERN_FIELDS: frozenset[str] = frozenset({"follow"})
SUB_DROP_PATTERN_FIELDS: frozenset[str] = frozenset()
RISER_PATTERN_FIELDS: frozenset[str] = frozenset({"duration_bars"})
DOWNER_PATTERN_FIELDS: frozenset[str] = frozenset({"duration_bars"})
IMPACT_PATTERN_FIELDS: frozenset[str] = frozenset()
"""`impact` (issue #23) nao consome `element.pattern` nesta rodada — a
intensidade (soft/medium/hard) cicla pela ORDEM de `element.sections`
(`occurrence_index`), nunca por um campo declarado."""
REVERSE_PATTERN_FIELDS: frozenset[str] = frozenset({
    "duration_bars", "freeze_pitch", "freeze_velocity",
})
"""`freeze_pitch`/`freeze_velocity` (issue #23, modo `freeze`): a IA que
escreve o plano ja tem acesso ao ultimo evento da secao anterior (via
`analyze`) e declara o pitch/velocity a congelar explicitamente — nenhum
parametro sorteado sem origem declarada (AGENTS.md AC-21); o renderer
nunca inspeciona "a secao anterior" por conta propria."""

# Formato do carimbo de plugin/preset em meta-evento SMF de texto (0x01).
# Exemplo literal (documentado em docs/arquitetura.md):
#   "midi-arranger v1|role=drums|plugin=Superior Drummer|preset=Metal Kit|
#    verified=true|techniques=[drums.accent_hierarchy,drums.ghost_notes]"
# Coexiste com meta 0x03 (track_name); nunca substitui.
STAMP_PREFIX = "midi-arranger v1"


# --- excecoes ---------------------------------------------------------------

class RenderError(Exception):
    """Falha de render que nao pode ser silenciada."""


# --- filtro --only (issue #24, parte 2) -------------------------------------

ONLY_CATEGORIES: tuple[str, ...] = ("transitions", "harmonic", "rhythmic", "electronic")
"""Vocabulario FECHADO do filtro `only` de `render()`. `transitions` e
especial: seleciona os elementos citados em `plan.transitions[].elements`
("os elementos de fronteira"), independente de `role`. As outras tres
espelham os nomes dos modulos de `tools.palette` (harmonic/rhythmic/
electronic) — a mesma familia que a paleta ja usa para organizar os
roles, reaproveitada aqui em vez de inventar uma segunda taxonomia.
Bateria e baixo (`tools.palette.drums`/`tools.palette.bass`) nao entram em
nenhuma das tres categorias nomeadas pela issue — `only` filtra PRA essas
categorias, entao pedir `--only harmonic` legitimamente deixa bateria e
baixo de fora."""

_HARMONIC_FAMILY_ROLES: frozenset[str] = frozenset({
    "pad", *KEYBOARD_ROLES, *STRINGS_ROLES, *DRONE_ROLES,
})
_RHYTHMIC_FAMILY_ROLES: frozenset[str] = frozenset({
    *RHYTHMIC_ROLES, *MOTOR_ROLES, *SHADOW_ROLES,
})
_ELECTRONIC_FAMILY_ROLES: frozenset[str] = frozenset({
    *HAT_ELEC_ROLES, *SUB_ROLES, *SUB_DROP_ROLES,
})


def _normalize_only(only: str | Iterable[str] | None) -> frozenset[str] | None:
    """Normaliza `only` (string unica, string separada por virgula, ou
    lista de strings) para um conjunto de categorias. `None`/vazio
    significa "sem filtro" (comportamento atual, todos os elementos
    renderizam). Categoria fora de `ONLY_CATEGORIES` e `RenderError`
    explicito — nunca ignora em silencio uma categoria digitada errado."""
    if only is None:
        return None
    raw_parts = only.split(",") if isinstance(only, str) else list(only)
    parts = [p.strip() for p in raw_parts if p.strip()]
    if not parts:
        return None
    invalid = sorted({p for p in parts if p not in ONLY_CATEGORIES})
    if invalid:
        raise RenderError(
            f"only: unknown categor{'y' if len(invalid) == 1 else 'ies'} "
            f"{invalid!r}; valid: {list(ONLY_CATEGORIES)}"
        )
    return frozenset(parts)


def _element_matches_only(
    element: Element, plan: ArrangementPlan, categories: frozenset[str],
) -> bool:
    if "transitions" in categories:
        for t in plan.transitions:
            if element.id in t.elements:
                return True
    if "harmonic" in categories and element.role in _HARMONIC_FAMILY_ROLES:
        return True
    if "rhythmic" in categories and element.role in _RHYTHMIC_FAMILY_ROLES:
        return True
    return "electronic" in categories and element.role in _ELECTRONIC_FAMILY_ROLES


def _apply_only_filter(
    plan: ArrangementPlan, only: str | Iterable[str] | None,
) -> ArrangementPlan:
    """Filtra `plan.elements` pelas categorias de `only` (issue #24).

    So mexe em `elements` — `plan.edits` (humanizacao de track do usuario)
    e `plan.sections`/`plan.transitions` (estrutura da musica) ficam
    intocados; a "intencao" continua completa, so o subconjunto de
    elementos GERADOS muda. Roda DEPOIS de `validate_plan` (o plano
    inteiro, como autorado, precisa ser valido) e ANTES do loop de render
    — dali em diante todo o pipeline (colisao, elementos, validadores)
    enxerga so o `plan.elements` filtrado, entao "validadores rodam
    apenas sobre o que foi gerado" sai de graca da mesma estrutura de
    dados, sem checagem extra em cada validador."""
    categories = _normalize_only(only)
    if categories is None:
        return plan
    kept = [e for e in plan.elements if _element_matches_only(e, plan, categories)]
    return replace(plan, elements=kept)


def _reject_unauthorized_style_techniques(
    plan: ArrangementPlan, plan_dir: Path | None,
) -> None:
    """Barreira do render: recusa tecnica que o brief nao autoriza.

    Dupla defesa em relacao a `plan.validate` — mesmo `ArrangementPlan`
    construido em memoria, sem passar por `plan.load`, nao pode aplicar
    tecnica de familia cuja autorizacao nao cobre aquele nome. Recusa e
    sempre `RenderError` explicito citando familia e tecnica; nunca aplica
    parcial nem ignora em silencio. Roda ANTES de `validate_plan` para que
    a violacao de autorizacao vire `RenderError`, nao `PlanValidationError`.
    """
    if not plan.style:
        return
    families_present = [
        (family, entry)
        for family, entry in plan.style.items()
        if family in STYLE_FAMILIES
        and isinstance(entry, FamilyStyle)
        and entry.techniques
    ]
    if not families_present:
        return

    try:
        if plan.brief_ref is None:
            _reject_style_techniques_without_brief(plan.style)
            return
        authorized = _load_brief_authorized_techniques(plan, plan_dir)
    except PlanValidationError as exc:
        raise RenderError(f"{exc.path}: {exc.message}") from None

    index = build_techniques_index()
    for family, entry in families_present:
        allowed = authorized.get(family, set())
        for i, tech in enumerate(entry.techniques):
            canonical = _canonicalize_authorized_name(index, family, tech.name)
            if canonical is None or canonical not in allowed:
                shown = sorted(allowed) if allowed else "[]"
                raise RenderError(
                    f"style.{family}.techniques[{i}].name: technique "
                    f"{tech.name!r} not in authorized_techniques for "
                    f"family {family!r} (brief authorized: {shown})"
                )


def _reject_excluded_family_elements(
    plan: ArrangementPlan, plan_dir: Path | None,
) -> None:
    """Barreira do render: recusa criar familia vetada em `brief.excluded_families`.

    Dupla defesa em relacao a `plan.validate` (issue #17) — mesmo
    `ArrangementPlan` construido em memoria, sem passar por `plan.load`,
    nao pode gerar (`plan.elements`) conteudo de uma familia que o brief
    veta, mesmo que a IA tenha julgado (via `rationale`) que ela esta
    faltando no MIDI de origem. `plan.edits` fica fora do veto — edita
    track que ja existe, nunca cria familia nova. Sem `plan.brief_ref` nao
    ha veto declarado, entao nada e recusado aqui (mesmo default de
    `plan.validate`)."""
    if plan.brief_ref is None:
        return
    # `role` so tem familia quando e string reconhecida — plano malformado
    # (ex.: `role` nao-string vindo de `ArrangementPlan` construido em
    # memoria sem passar por `plan.load`) nao pode estourar `TypeError`
    # aqui: essa barreira roda ANTES de `validate_plan`, e e o proprio
    # `validate_plan` quem tem que reportar o tipo invalido como
    # `PlanValidationError` (achado do Codex na PR #105). Elemento
    # malformado so nao entra no calculo de familias vetadas.
    families_used = {
        _style_family_for_role(e.role)
        for e in plan.elements
        if isinstance(e.role, str)
    }
    families_used.discard(None)
    if not families_used:
        return

    try:
        excluded = _load_brief_excluded_families(plan, plan_dir)
    except PlanValidationError as exc:
        raise RenderError(f"{exc.path}: {exc.message}") from None

    for i, e in enumerate(plan.elements):
        if not isinstance(e.role, str):
            continue
        family = _style_family_for_role(e.role)
        if family is not None and family in excluded:
            raise RenderError(
                f"elements[{i}].role: role {e.role!r} belongs to family "
                f"{family!r}, which brief.excluded_families vetoes — a IA "
                f"nao pode criar essa familia mesmo julgando que falta "
                f"(rationale: {e.rationale!r})"
            )


# --- dataclasses do relatorio ----------------------------------------------

@dataclass
class ElementRationale:
    """Justificativa musical de um elemento no relatorio final (FR-22)."""
    element_id: str
    role: str
    rationale: str
    plugin: str
    preset: str
    verified: bool
    layers: int
    sections: tuple[str, ...]
    rendered: bool
    note: str = ""


@dataclass
class RenderReport:
    output_path: Path
    source_sha256: str
    seed: int
    collision: CollisionReport
    elements: list[ElementRationale] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    harmony_issues: list[HarmonyIssue] = field(default_factory=list)
    placement_issues: list[PlacementIssue] = field(default_factory=list)
    artifice_issues: list[ArtificeIssue] = field(default_factory=list)
    persona_issues: list[PersonaIssue] = field(default_factory=list)
    anticopy_issues: list[AntiCopyIssue] = field(default_factory=list)
    transition_issues: list[TransitionIssue] = field(default_factory=list)
    edits: list[EditReport] = field(default_factory=list)


# --- IO helpers -------------------------------------------------------------

def sha256_of_file(path: Path) -> str:
    """SHA-256 do conteudo do arquivo em hex."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_source_path(plan: ArrangementPlan) -> Path:
    return Path(plan.source_midi.path).expanduser()


def _default_output_path(source: Path) -> Path:
    # `Path.home()` lido no momento da chamada — permite override via HOME em teste.
    return Path.home() / DEFAULT_OUTPUT_DIRNAME / f"{source.stem}{DEFAULT_OUTPUT_SUFFIX}"


def _section_by_label(plan: ArrangementPlan, label: str) -> PlanSection | None:
    for s in plan.sections:
        if s.label == label:
            return s
    return None


def _section_energy_windows(
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
) -> tuple[dict[str, Any], ...]:
    """Converte `plan.sections[].energy` em janelas de tick (issue #45).

    `plan.validate()` ja garante `energy` presente em toda secao (5 eixos,
    0-10) — este helper so traduz `start_bar`/`end_bar` para ticks (mesmo
    caminho de `bars_in_section`, reusado pelos tres geradores eletronicos e
    aqui) e extrai o eixo `densidade`. E o unico consumidor de
    `plan.sections[].energy` no motor de tecnicas: `drums.ghost_notes` le o
    resultado via `context.parameters["sections"]`, canal separado de
    `style.<familia>.parameters` (mesmo padrao ja usado por `tuning`) —
    nunca passa pelo schema fechado a numero/par de `tools/style_schema.py`.
    Secao sem bar nenhum coberto por `analysis.bars` (janela vazia) fica de
    fora: o aplicador cai no default declarado quando nenhuma janela cobre
    um tick.
    """
    windows: list[dict[str, Any]] = []
    for section in plan.sections:
        if section.energy is None:
            continue
        bars = bars_in_section(section, analysis)
        if not bars:
            continue
        start_tick = int(round(pm.time_to_tick(bars[0].start)))
        end_tick = int(round(pm.time_to_tick(bars[-1].end)))
        if end_tick <= start_tick:
            continue
        windows.append({
            "start_tick": start_tick,
            "end_tick": end_tick,
            "kind": section.kind,
            "densidade": section.energy["densidade"],
        })
    return tuple(windows)


def _analysis_bar_windows(
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
) -> tuple[dict[str, Any], ...]:
    """Fronteiras REAIS de compasso (`analysis.bars`, o mapa de downbeat que
    `analyze` ja extraiu do MIDI e que respeita troca de compasso) — canal
    separado de `_section_energy_windows`, que so cobre os trechos com
    `plan.sections[].energy` declarada.

    Existe para consertar `drums.ghost_notes`: antes desta funcao, o motor de
    tecnicas assumia `ticks_per_beat * 4` como tamanho de compasso pra
    agrupar candidatos em cotas por compasso e pra escolher qual janela de
    secao aplicar a cada compasso — suposicao de 4/4 constante que quebra em
    3/4, 5/4 ou troca de compasso no meio da musica (bucket pode atravessar
    um compasso real ou partir um em dois). `analysis.bars` ja e a mesma
    fonte usada por `_section_energy_windows`/`bars_in_section`; aqui so
    convertemos TODOS os bars (nao so os cobertos por secao) pra tick, para
    o motor agrupar por compasso real em vez de reinventar o grid."""
    windows: list[dict[str, Any]] = []
    for bar in analysis.bars:
        start_tick = int(round(pm.time_to_tick(bar.start)))
        end_tick = int(round(pm.time_to_tick(bar.end)))
        if end_tick <= start_tick:
            continue
        windows.append({
            "start_tick": start_tick,
            "end_tick": end_tick,
            "index": bar.index,
        })
    return tuple(windows)


def _section_windows_cover_range(
    windows: tuple[dict[str, Any], ...],
    total_ticks: int,
) -> bool:
    """`True` quando `windows` cobre `[0, total_ticks)` sem buraco.

    `plan.validate()` permite `plan.sections[]` fora de ordem cronologica —
    ordem de LISTA nao e ordem de TICK. Checar so o primeiro/ultimo item da
    lista (ordem de declaracao) tanto falso-positiva em secoes fora de ordem
    que cobrem o intervalo inteiro quanto deixa passar em silencio um buraco
    NO MEIO (duas janelas nao-adjacentes com uma lacuna entre elas). Por
    isso: ordena por `start_tick` primeiro, depois anda pelos pares
    adjacentes procurando lacuna (proximo comeca depois do atual terminar)
    e so então confere a ponta inicial/final pela ordem de TICK."""
    if not windows:
        return False
    ordered = sorted(windows, key=lambda w: w["start_tick"])
    if ordered[0]["start_tick"] > 0:
        return False
    # Merge por varredura: `covered_until` e o fim do trecho contiguo ja
    # coberto ate aqui (nao so o fim da janela anterior por ordem de
    # `start_tick`) — janela que comeca depois de `covered_until` e um
    # buraco real; janela sobreposta/aninhada so estende `covered_until`.
    covered_until = ordered[0]["end_tick"]
    for window in ordered[1:]:
        if window["start_tick"] > covered_until:
            return False
        covered_until = max(covered_until, window["end_tick"])
    return covered_until >= total_ticks


def _drums_ghost_notes_authorized(plan: ArrangementPlan) -> bool:
    """`True` quando `plan.style.drums.techniques` declara `ghost_notes`.

    So usado para decidir se vale a pena checar cobertura de secao e emitir
    o aviso de default (issue #45) — plano que nunca vai rodar a tecnica nao
    precisa do aviso."""
    if not plan.style:
        return False
    drums_style = plan.style.get("drums")
    if drums_style is None:
        return False
    return any(
        t.name in ("ghost_notes", "drums.ghost_notes") for t in drums_style.techniques
    )


def _drums_ghost_notes_has_explicit_density(plan: ArrangementPlan) -> bool:
    """`True` quando a entrada `drums.ghost_notes` de
    `plan.style.drums.techniques[]` declara `density` explicito.

    Achado do Codex no PR #107: `bar_fraction` em
    `_apply_drums_ghost_notes` consulta `context.parameters["density"]`
    (que vem de `StyleTechnique.density`) ANTES de qualquer janela de
    secao — com override explicito, o caminho de default por secao
    (`densidade=5/10`) nunca e alcancado, entao o aviso de cobertura de
    secao seria falso nesse caso. So chamada depois que
    `_drums_ghost_notes_authorized` ja confirmou que a tecnica esta
    declarada, entao aqui so falta achar a entrada e checar `density`."""
    if not plan.style:
        return False
    drums_style = plan.style.get("drums")
    if drums_style is None:
        return False
    return any(
        t.name in ("ghost_notes", "drums.ghost_notes") and t.density is not None
        for t in drums_style.techniques
    )


def _element_seed(plan_seed: int, element_id: str, section_label: str) -> int:
    """Seed determinstica por (plano, elemento, secao). Evita correlacao
    de stagger entre secoes de um mesmo elemento e entre elementos que
    dividem uma secao."""
    payload = f"{plan_seed}|{element_id}|{section_label}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _element_track_name(element: Element, layer_index: int, layers: int) -> str:
    """Nome de track — comum a pad e teclado. Reaproveita as guardas de
    FR-14/FR-24 e ASCII do name_for_element."""
    inst = element.instrument or {}
    plugin = inst.get("plugin")
    preset = inst.get("preset")
    verified = bool(inst.get("verified", False))
    if not plugin or not preset:
        raise RenderError(
            f"element {element.id!r} missing instrument.plugin/preset — cannot name track"
        )
    display = element.id if layers == 1 else f"{element.id} L{layer_index + 1}"
    return name_for_element(display, element.role, str(plugin), str(preset), verified)


def _pattern_fields_for_role(role: str) -> frozenset[str]:
    if role == "pad":
        return frozenset()
    if role in KEYBOARD_ROLES:
        return KEYBOARD_PATTERN_FIELDS
    if role in STRINGS_ROLES:
        return STRINGS_PATTERN_FIELDS
    if role in DRONE_ROLES:
        return DRONE_PATTERN_FIELDS
    if role in RHYTHMIC_ROLES:
        return RHYTHMIC_PATTERN_FIELDS
    if role in MOTOR_ROLES:
        return MOTOR_PATTERN_FIELDS
    if role in SHADOW_ROLES:
        return SHADOW_PATTERN_FIELDS
    if role in DRUMS_ROLES:
        return DRUMS_PATTERN_FIELDS
    if role in BASS_ROLES:
        return BASS_PATTERN_FIELDS
    if role in GUITAR_ROLES:
        return GUITAR_PATTERN_FIELDS
    if role in HAT_ELEC_ROLES:
        return HAT_ELEC_PATTERN_FIELDS
    if role in SUB_ROLES:
        return SUB_PATTERN_FIELDS
    if role in SUB_DROP_ROLES:
        return SUB_DROP_PATTERN_FIELDS
    if role in RISER_ROLES:
        return RISER_PATTERN_FIELDS
    if role in DOWNER_ROLES:
        return DOWNER_PATTERN_FIELDS
    if role in IMPACT_ROLES:
        return IMPACT_PATTERN_FIELDS
    if role in REVERSE_ROLES:
        return REVERSE_PATTERN_FIELDS
    return frozenset()


def _unsupported_pattern_warnings(element: Element) -> list[str]:
    """Avisos para campos de `element.pattern` que o renderer nao consome.

    O plano e editavel a mao; um campo sem efeito precisa aparecer no
    relatorio em vez de ser aceito em silencio.
    """
    pattern = element.pattern or {}
    known = _pattern_fields_for_role(element.role)
    return [
        f"{element.id}: element.pattern.{key} is not supported for "
        f"role {element.role!r}; ignored"
        for key in sorted(pattern)
        if key not in known
    ]


def _style_confidence_warnings(plan: ArrangementPlan) -> list[str]:
    """Avisos quando o render usa perfil de estilo fraco ou default."""
    if not plan.style:
        return []

    warnings: list[str] = []
    for family in STYLE_FAMILIES:
        style = plan.style.get(family)
        if style is None:
            continue
        if style.confidence == "low":
            warnings.append(
                f"style.{family}: confidence low for reference "
                f"{style.reference!r}; rendering with weak research"
            )
        elif style.confidence == "default":
            warnings.append(
                f"style.{family}: confidence default; no style was researched "
                f"for family {family!r}; using {style.reference!r}"
            )
    return warnings


# Nome convencional do brief ao lado do plano (docs/arquitetura.md, skills/
# midi-brief/SKILL.md): `run` sempre escreve os dois na raiz do projeto.
_CONVENTIONAL_BRIEF_FILENAME = "arrangement-brief.json"


def _conventional_brief_excluded_families(candidate: Path) -> list[str]:
    """Le `excluded_families` do brief convencional, best-effort.

    Sem `plan.brief_ref` nao ha `sha256` pra verificar integridade — a
    leitura aqui e so pra decidir aviso vs erro em
    `_reject_missing_brief_ref_with_excluded_families`, entao e
    deliberadamente tolerante: JSON invalido, `excluded_families` ausente
    ou de tipo errado nao pode estourar aqui (isso e responsabilidade de
    `brief.validate`/`plan.validate` quando `brief_ref` de fato aponta pro
    arquivo); tratamos como "nenhuma familia vetada conhecida" e o aviso
    nao-bloqueante (`W_BRIEF_NOT_REFERENCED`) continua sendo o sinal.
    """
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    families = raw.get("excluded_families")
    if not isinstance(families, list):
        return []
    return [f for f in families if isinstance(f, str) and f in STYLE_FAMILIES]


def _reject_missing_brief_ref_with_excluded_families(
    plan: ArrangementPlan, plan_dir: Path | None,
) -> None:
    """Barreira do render: veto de brief convencional nao pode ser ignorado.

    Issue #105 (P1 do Codex, segunda rodada): um `W_BRIEF_NOT_REFERENCED`
    sozinho e so um aviso — nao impede o harness (nao-deterministico, ver
    AGENTS.md "A fronteira que nao se cruza") de ignorar a instrucao do
    prompt e gerar a familia vetada mesmo assim. Quando existe
    `arrangement-brief.json` na convencao ao lado do plano, `plan.brief_ref`
    nao aponta pra ele, E esse brief realmente declara
    `excluded_families` nao-vazio, o veto e CONSEQUENTE — o brief tem
    intencao explicita e o plano nunca a carregou. Isso vira `RenderError`
    e nao apenas aviso, porque so a tool (deterministica) pode garantir o
    veto; a IA do harness pode simplesmente ignorar um aviso.

    Brief inexistente, `brief_ref` ja apontando pro brief, ou brief
    convencional com `excluded_families` vazio/ausente continuam SEM
    bloquear — sao os mesmos fluxos legitimos que `W_BRIEF_NOT_REFERENCED`
    ja preservava (edit-only, sessao sem brief, brief que nunca declarou
    veto nenhum).
    """
    if plan.brief_ref is not None or plan_dir is None:
        return
    candidate = plan_dir / _CONVENTIONAL_BRIEF_FILENAME
    if not candidate.is_file():
        return
    excluded = _conventional_brief_excluded_families(candidate)
    if not excluded:
        return
    raise RenderError(
        f"plan.brief_ref: existe {candidate} vetando as familias "
        f"{sorted(excluded)!r} (brief.excluded_families), mas plan.brief_ref "
        "nao aponta pra ele — o veto nunca seria carregado. Defina "
        "plan.brief_ref (path + sha256 via tools.brief_ref.brief_sha256()) "
        "antes de renderizar."
    )


def _brief_not_referenced_warning(
    plan: ArrangementPlan, plan_dir: Path | None,
) -> str | None:
    """Detecta brief presente na convencao que o plano nao referencia.

    Issue #105 (P1 do Codex no PR do #17): `excluded_families` e um veto
    OPT-IN — so tem efeito quando `plan.brief_ref` aponta pro brief. Um
    plano sem `brief_ref` nunca falha por causa disso (edit-only, ou
    sessao legitimamente sem brief, continuam funcionando), mas se existe
    `arrangement-brief.json` bem ao lado do plano e ninguem referenciou,
    o gap fica mudo hoje: familia vetada pode ser criada sem que o veto
    jamais seja carregado. Isso vira aviso (nunca erro) para o gap ficar
    visivel no relatorio em vez de silencioso.

    Quando esse brief convencional realmente declara `excluded_families`
    nao-vazio, o gap deixa de ser so um aviso: veja
    `_reject_missing_brief_ref_with_excluded_families`, chamada antes
    dessa funcao em `render()`, que estoura `RenderError` nesse caso.
    """
    if plan.brief_ref is not None or plan_dir is None:
        return None
    candidate = plan_dir / _CONVENTIONAL_BRIEF_FILENAME
    if not candidate.is_file():
        return None
    return (
        f"W_BRIEF_NOT_REFERENCED: existe {candidate} mas plan.brief_ref "
        "nao aponta pra ele; sem brief_ref, excluded_families do brief nao "
        "tem efeito nenhum sobre este plano (veto e opt-in). Se este plano "
        "deveria respeitar o brief, defina plan.brief_ref (path + sha256 "
        "via tools.brief_ref.brief_sha256())."
    )


def _style_family_for_role(role: str) -> str | None:
    """Mapeia role renderizavel para a familia de `style` correspondente."""

    if role in STYLE_FAMILIES:
        return role
    return ROLE_STYLE_FAMILIES.get(role)


def _style_technique_seed(
    plan_seed: int,
    family: str,
    canonical: str,
    tool_target: str | None,
    *,
    edit_track: str | None = None,
) -> int:
    parts = [str(plan_seed), "style", family, canonical, tool_target or ""]
    if edit_track is not None:
        parts.extend(["edit", edit_track])
    payload = "|".join(parts).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


# Mapa profile -> familia de style, exposto para o motor de edits. `generic`
# nao tem familia e nao recebe tecnica de estilo (por design; documentado em
# AGENTS.md).
_EDIT_PROFILE_STYLE_FAMILIES = {
    "bass": "bass",
    "drums": "drums",
    "guitar": "guitar",
    "keys": "keys",
}


def _style_family_for_edit(profile: str) -> str | None:
    return _EDIT_PROFILE_STYLE_FAMILIES.get(profile)


def _normalize_tool_name(plugin: str | None) -> str | None:
    """Converte nome de plugin em chave de receita do manual.

    Ex.: "MODO Bass" -> "modo_bass", "Superior Drummer" -> "superior_drummer".
    Ausencia/vazio devolve `None`, deixando o motor usar `generic` sem
    emitir fallback artificial.
    """
    if not isinstance(plugin, str) or not plugin.strip():
        return None
    chars: list[str] = []
    previous_sep = False
    for ch in plugin.strip().lower():
        if ch.isalnum():
            chars.append(ch)
            previous_sep = False
        elif not previous_sep:
            chars.append("_")
            previous_sep = True
    normalized = "".join(chars).strip("_")
    return normalized or None


def _tool_target_for_element(element: Element) -> str | None:
    """Converte `instrument.plugin` em chave de receita do manual."""
    return _normalize_tool_name((element.instrument or {}).get("plugin"))


def _tool_target_for_edit(edit: PlanEdit) -> str | None:
    """Converte `edit.tool` (ferramenta-alvo declarada para a track editada)
    em chave de receita do manual — mesma normalizacao de
    `_tool_target_for_element`, para que uma track humanizada de
    `plan.edits` tambem possa pedir a receita especifica (ex. `modo_bass`)
    em vez de cair sempre no fallback `generic`."""
    return _normalize_tool_name(edit.tool)


def _canonical_style_technique(
    index: TechniqueIndex,
    family: str,
    name: str,
) -> str:
    """Resolve nome simples/canonico do plano para canonico da familia.

    `plan.validate()` ja rejeitou nomes invalidos; aqui mantemos a resolucao
    centralizada no indice para o render nao depender da forma escolhida pelo
    agente no JSON.
    """

    for technique in index.candidates(name):
        if technique.family == family:
            return technique.canonical
    raise RenderError(
        f"style.{family}: technique {name!r} is not available for family {family!r}"
    )


def _style_technique_parameters(
    style_parameters: dict[str, float | list[float]],
    density: float | None,
    style: str | None = None,
    tuning: tuple[int, ...] | None = None,
    sections: tuple[dict[str, Any], ...] | None = None,
    bars: tuple[dict[str, Any], ...] | None = None,
    drum_bar_quota: dict[str, dict[int, int]] | None = None,
    intensity: float | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = dict(style_parameters)
    if density is not None:
        parameters["density"] = float(density)
    if intensity is not None:
        # Intensidade semantica explicita de `StyleTechnique.intensity`
        # (issue #72) — canal separado de `density` (que ja comanda o
        # liga/desliga acima; ver `_run_style_pipeline`, que resolve
        # `effective_density` com `density` tendo precedencia sobre
        # `intensity` quando os dois estao declarados). Sempre exposta para
        # o aplicador que quiser ler o valor bruto, mesmo quando `density`
        # tambem esta presente.
        parameters["intensity"] = float(intensity)
    if sections:
        # Mesmo canal separado de `tuning` logo abaixo: janelas de tick de
        # `plan.sections[].energy` (issue #45), consumidas hoje so por
        # `drums.ghost_notes`. Nunca vem de `style.parameters` (schema
        # fechado a numero/par).
        parameters["sections"] = sections
    if bars:
        # Fronteiras REAIS de compasso (`analysis.bars`) — canal irmao de
        # `sections`, tambem so consumido por `drums.ghost_notes` hoje.
        # Existe pra parar de assumir `ticks_per_beat*4` como tamanho de
        # compasso (so vale em 4/4 constante).
        parameters["bars"] = bars
    if drum_bar_quota is not None:
        # Cota por compasso COMPARTILHADA entre TODOS os despachos de
        # tecnica de bateria de UMA chamada de `render()` — achado do Codex
        # no PR #107 (issue #45, segunda rodada): `_apply_drums_ghost_notes`
        # ja compartilha `bar_counts`/`bar_targets` entre tracks fisicas
        # DENTRO de uma so chamada (fix anterior), mas cada chamada de
        # `_run_style_pipeline` (uma por `plan.edits[]` com profile=drums, e
        # uma por elemento de bateria gerado) criava seu proprio dict local
        # do zero — duas edits ou uma edit + um elemento gerado, ambos
        # caindo no mesmo compasso, podiam somar o dobro (ou mais) do teto
        # anunciado no arquivo final. `render()` cria UM dict por chamada
        # (nunca global/modulo) e repassa aqui — mesmo canal separado de
        # `sections`/`bars`, so restrito a familia `drums`.
        parameters["drum_bar_quota"] = drum_bar_quota
    if style is not None:
        # `style` (dedo/palheta/slap) e a UNICA excecao numerica-only de
        # `style.parameters` — vem do vocabulario fechado de
        # `StyleTechnique.style`, ja validado em `plan.validate`, nunca de
        # texto livre.
        parameters["style"] = style
    if tuning is not None:
        # Afinacao declarada em `brief.instruments.<familia>` (issue #44),
        # repassada por `tools.plan.load_brief_instrument_tuning` — NAO vem
        # de `style.parameters` (schema restrito a numero/par) e sim de um
        # canal separado, exatamente como `tools/techniques/physical.py`
        # ja sabe ler (`_tuning_from_parameters`): sem isso a declaracao do
        # usuario e um parametro mentiroso, validada e ignorada.
        parameters["tuning"] = tuning
    return parameters


def _format_engine_warning(warning: dict) -> str:
    code = str(warning.get("code", "W_TECHNIQUE"))
    message = str(warning.get("message", "")).strip()
    path = str(warning.get("path", "")).strip()
    suffix = f" ({path})" if path else ""
    return f"{code}: {message}{suffix}" if message else f"{code}{suffix}"


def _tracks_as_midi(
    tracks: list[mido.MidiTrack],
    *,
    ticks_per_beat: int,
    midi_type: int,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=midi_type)
    mid.tracks.extend(tracks)
    return mid


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    """Serializa `mid` pra comparacao byte a byte — usado por
    `_run_style_pipeline` pra saber se uma tecnica de fato mudou algo, em
    vez de assumir que "foi despachada" significa "foi aplicada" (uma
    tecnica pode ser NO-OP interno legitimo, como `bass.string_selection`
    com `tool != "modo_bass"` ou contagem de corda sem convencao — nesses
    casos o motor devolve o MIDI intocado)."""

    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


def _tempo_track_from_pretty_midi(pm: pretty_midi.PrettyMIDI) -> mido.MidiTrack:
    track = mido.MidiTrack()
    previous_tick = 0
    tempo_times, tempi = pm.get_tempo_changes()
    for time_s, bpm in zip(tempo_times, tempi, strict=True):
        tick = int(round(pm.time_to_tick(float(time_s))))
        track.append(mido.MetaMessage(
            "set_tempo",
            tempo=mido.bpm2tempo(float(bpm)),
            time=tick - previous_tick,
        ))
        previous_tick = tick
    return track


def _normalize_range_scalar_parameters(
    parameters: dict[str, float | list[float]],
    resolved_technique: Any,
) -> dict[str, float | list[float]]:
    """Normaliza escalar para `[value, value]` quando a PROPRIA tecnica
    declara `range` para esse nome (achado #1 do Codex no PR #108).

    `plan.validate` aceita numero escalar dentro do `range` do manual como
    forma valida de `StyleTechnique.parameters` (`is_style_parameter_value`
    em `style_schema.py`) — um numero escalar e um par degenerado
    `[value, value]`. Mas aplicadores como `_apply_drums_ghost_notes`
    exigem `[min, max]` de verdade e explodem em `ValueError` quando
    recebem o escalar cru. Normalizar aqui, no ponto onde o dict funde e
    chega em `context.parameters`, cobre TODO consumidor de parametro
    range-shaped, nao so `ghost_notes` — a mesma barreira que ja rejeita
    parametro mentiroso: aceito pela validacao mas nao honrado no render.
    """
    declared = {parameter.name: parameter for parameter in resolved_technique.parameters}
    normalized: dict[str, float | list[float]] = {}
    for key, value in parameters.items():
        parameter = declared.get(key)
        if parameter is not None and parameter.range is not None and is_style_parameter_scalar(value):
            normalized[key] = [float(value), float(value)]
        else:
            normalized[key] = value
    return normalized


def _run_style_pipeline(
    current: mido.MidiFile,
    *,
    plan: ArrangementPlan,
    family: str,
    style: FamilyStyle,
    tool_target: str | None,
    index: TechniqueIndex,
    edit_track: str | None = None,
    tuning: tuple[int, ...] | None = None,
    section_windows: tuple[dict[str, Any], ...] | None = None,
    bar_windows: tuple[dict[str, Any], ...] | None = None,
    drum_bar_quota: dict[str, dict[int, int]] | None = None,
) -> tuple[mido.MidiFile, list[str], tuple[str, ...]]:
    """Roda cada tecnica de `style.<family>` sobre `current` em sequencia.

    Comum aos dois caminhos que aplicam estilo: tracks recem-renderizadas por
    elemento e tracks do MIDI de origem nomeadas em `plan.edits`. Warnings
    ganham prefixo com o nome da track quando o alvo e uma edit, para o
    relatorio identificar de qual track de origem partiu o aviso.

    `density` explicitamente <= 0.0 significa tecnica desligada (AGENTS.md):
    nem resolve receita nem dispara `W_NO_TOOL_RECIPE` para uma tecnica que
    nao vai fazer nada. O terceiro item do retorno lista, em ordem, apenas as
    tecnicas que de fato MUDARAM o MIDI — comparado por bytes antes/depois de
    cada despacho (`_midi_bytes`), nao apenas as que foram despachadas: um
    aplicador pode ser NO-OP interno legitimo mesmo com `density > 0` (ex.:
    `bass.string_selection` com `tool != "modo_bass"`, ou `bass.attack_style`
    sem `style` declarado), e nesse caso o carimbo nao pode alegar que a
    tecnica foi aplicada. `_stamp_edit_tracks` usa `bool(techniques)` direto
    (nao um flag separado de "foi despachada") pra decidir se `edit.tool`
    pode ser estampado como `plugin` — cobre de uma vez os casos de family
    ausente, `style.techniques` nao declarado, `density<=0.0` em tudo, e
    tecnica despachada mas NO-OP por outro motivo.
    """

    warnings: list[str] = []
    applied_names: list[str] = []
    warning_prefix = f"edit {edit_track!r}: " if edit_track is not None else ""
    # `sections`/`bars` so interessam a tecnicas de bateria (hoje, so
    # `drums.ghost_notes` le `context.parameters["sections"]`/`["bars"]`);
    # nas demais familias ficam de fora do dict de parametros pra nao poluir
    # o contexto de tecnicas que nunca vao olhar pra isso.
    family_section_windows = section_windows if family == "drums" else None
    family_bar_windows = bar_windows if family == "drums" else None
    # Mesma restricao de familia de `sections`/`bars` acima: a cota
    # compartilhada so importa a `drums.ghost_notes` hoje.
    family_drum_bar_quota = drum_bar_quota if family == "drums" else None
    # Cacheia a serializacao pra nao rodar `_midi_bytes` duas vezes por
    # despacho (uma vez como "before" da tecnica seguinte, outra como
    # "after" da tecnica anterior) — `current` so muda dentro deste loop.
    before_bytes = _midi_bytes(current)
    for technique in style.techniques:
        canonical = _canonical_style_technique(index, family, technique.name)
        # issue #72: `density` continua tendo precedencia quando declarado
        # (retrocompatibilidade byte-a-byte — plano v1 nunca declara
        # `intensity`); `intensity` so assume o papel de liga/desliga e de
        # magnitude quando `density` esta ausente.
        effective_density = (
            technique.density if technique.density is not None else technique.intensity
        )
        if effective_density is not None and effective_density <= 0.0:
            continue
        # Precedencia issue #72: `StyleTechnique.parameters` (nivel de
        # tecnica) funde por cima do legado `FamilyStyle.parameters` (nivel
        # de familia) — mais especifico vence, mesmo conflito ja avisado por
        # `tools.plan._warn_style_parameter_conflicts` em `plan.validate()`.
        # So os parametros relevantes para ESTA tecnica chegam ao
        # aplicador: o dict resultante nao carrega parametro de OUTRA
        # tecnica da mesma familia.
        merged_parameters = {**style.parameters, **technique.parameters}
        resolved_technique = index.get(canonical)
        if resolved_technique is not None:
            # achado #1 do Codex no PR #108: escalar dentro do range do
            # manual e forma valida em `plan.validate`, mas aplicadores
            # range-shaped (ex.: `_apply_drums_ghost_notes`) exigem
            # `[min, max]` de verdade. Normaliza ANTES do despacho, nao so
            # para `technique.parameters` — o legado
            # `style.<familia>.parameters` fundido acima tem o MESMO risco.
            merged_parameters = _normalize_range_scalar_parameters(
                merged_parameters, resolved_technique,
            )
        try:
            applied: TechniqueApplyResult = apply_technique_with_warnings(
                canonical,
                current,
                seed=_style_technique_seed(
                    plan.seed, family, canonical, tool_target,
                    edit_track=edit_track,
                ),
                parameters=_style_technique_parameters(
                    merged_parameters,
                    effective_density,
                    technique.style,
                    tuning,
                    family_section_windows,
                    family_bar_windows,
                    family_drum_bar_quota,
                    intensity=technique.intensity,
                ),
                tool=tool_target,
                index=index,
            )
        except (
            TechniqueContractError,
            TechniquePhysicalError,
            TechniqueRecipeError,
            UnknownTechniqueError,
        ) as exc:
            context = f" (edit {edit_track!r})" if edit_track is not None else ""
            raise RenderError(
                f"style.{family}.techniques{context}: {exc}"
            ) from None
        current = applied.result if isinstance(applied.result, mido.MidiFile) else current
        warnings.extend(
            f"{warning_prefix}{_format_engine_warning(w)}"
            for w in applied.warnings
        )
        after_bytes = _midi_bytes(current)
        if after_bytes != before_bytes:
            applied_names.append(canonical)
        before_bytes = after_bytes
    return current, warnings, tuple(applied_names)


def _apply_style_techniques_to_tracks(
    tracks: list[mido.MidiTrack],
    *,
    plan: ArrangementPlan,
    family: str | None,
    tool_target: str | None,
    ticks_per_beat: int,
    midi_type: int,
    index: TechniqueIndex | None,
    tuning_by_family: dict[str, tuple[int, ...]] | None = None,
    section_windows: tuple[dict[str, Any], ...] | None = None,
    bar_windows: tuple[dict[str, Any], ...] | None = None,
    drum_bar_quota: dict[str, dict[int, int]] | None = None,
) -> tuple[list[mido.MidiTrack], list[str], bool, tuple[str, ...]]:
    """Aplica tecnicas de `style.<family>` sobre tracks recem-renderizadas.

    As tracks do MIDI de origem nao entram aqui — quando uma edit aponta para
    uma track existente, o caminho e `_apply_style_techniques_to_edit_tracks`,
    que roda depois de `apply_edits` e antes do render por elemento.

    O quarto item do retorno lista as tecnicas de fato despachadas (nunca as
    que `density<=0.0` desligou) — ver `_run_style_pipeline`.
    """

    if family is None or not tracks or not plan.style:
        return tracks, [], False, ()
    style = plan.style.get(family)
    if style is None or not style.techniques:
        return tracks, [], False, ()
    if index is None:
        raise RenderError("internal error: missing techniques index for style render")

    current = _tracks_as_midi(
        tracks,
        ticks_per_beat=ticks_per_beat,
        midi_type=midi_type,
    )
    current, warnings, applied_names = _run_style_pipeline(
        current,
        plan=plan,
        family=family,
        style=style,
        tool_target=tool_target,
        index=index,
        tuning=(tuning_by_family or {}).get(family),
        section_windows=section_windows,
        bar_windows=bar_windows,
        drum_bar_quota=drum_bar_quota,
    )
    return list(current.tracks), warnings, True, applied_names


def _track_name_index(tracks: Iterable[mido.MidiTrack]) -> dict[str, list[int]]:
    """`track_name` -> lista de indices fisicos com esse nome, na ORDEM em
    que aparecem em `tracks`. Compartilhado entre `_apply_style_techniques_to_edit_tracks`
    e `_edit_drum_target_tracks` (aviso de cobertura de secao, achado do
    Codex no PR #107) para nao duplicar a mesma varredura."""
    name_to_indices: dict[str, list[int]] = {}
    for idx, tr in enumerate(tracks):
        name = track_name(tr)
        if name:
            name_to_indices.setdefault(name, []).append(idx)
    return name_to_indices


def _edit_drum_target_tracks(
    out_mid: mido.MidiFile,
    plan: ArrangementPlan,
) -> list[mido.MidiTrack]:
    """Tracks fisicas que `_apply_style_techniques_to_edit_tracks` de fato
    despacharia para a familia `drums` — mesma resolucao `edit.track` ->
    tracks fisicas por nome, sem repetir a familia/`style.techniques`
    checagem la (aqui so interessa achar o ALVO, nao rodar a tecnica).
    """
    if not plan.edits:
        return []
    name_to_indices = _track_name_index(out_mid.tracks)
    tracks: list[mido.MidiTrack] = []
    for edit in plan.edits:
        if _style_family_for_edit(edit.profile) != "drums":
            continue
        indices = name_to_indices.get(edit.track)
        if not indices:
            continue
        tracks.extend(out_mid.tracks[i] for i in indices)
    return tracks


def _drum_channel9_note_on_ticks(tracks: Iterable[mido.MidiTrack]) -> list[int]:
    """Ticks absolutos de todo `note_on` de velocity>0 no canal 9 (bateria,
    GM) de `tracks` — usado pelo aviso de cobertura de secao de
    `drums.ghost_notes` (achado do Codex no PR #107): a cobertura tem que
    ser medida sobre as notas do ALVO DE FATO despachado, nunca sobre o
    arquivo inteiro."""
    ticks: list[int] = []
    for track in tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if (
                msg.type == "note_on"
                and msg.velocity > 0
                and getattr(msg, "channel", None) == 9
            ):
                ticks.append(tick)
    return ticks


def _drum_ticks_outside_section_windows(
    windows: tuple[dict[str, Any], ...],
    ticks: list[int],
) -> bool:
    """`True` quando algum tick de `ticks` nao cai em nenhuma janela de
    `windows`. Checagem POR NOTA (nao por range/gap do arquivo inteiro):
    um alvo pode ter seu range geral atravessando um buraco de secao sem
    ter nota nenhuma DENTRO desse buraco — checar so o range falso-positiva
    nesse caso (achado do Codex no PR #107)."""
    return any(
        not any(w["start_tick"] <= tick < w["end_tick"] for w in windows)
        for tick in ticks
    )


def _apply_style_techniques_to_edit_tracks(
    out_mid: mido.MidiFile,
    *,
    plan: ArrangementPlan,
    index: TechniqueIndex | None,
    tuning_by_family: dict[str, tuple[int, ...]] | None = None,
    section_windows: tuple[dict[str, Any], ...] | None = None,
    bar_windows: tuple[dict[str, Any], ...] | None = None,
    drum_bar_quota: dict[str, dict[int, int]] | None = None,
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Aplica `style.<family>` sobre as tracks da origem nomeadas em `plan.edits`.

    Ordem inviolavel: `apply_edits` (humanizacao por profile) ja rodou; agora
    o motor de tecnicas atua sobre as mesmas tracks. Como toda nota vinda da
    origem e ESTRUTURAL por definicao, o contrato do nivel `technique` garante
    que o motor so acrescente ornamento; o nivel `humanize` pode mexer em
    velocity/timing/duracao das mesmas notas estruturais. Track nao nomeada em
    `plan.edits` continua saindo byte-identica: nao aparece aqui.

    Profile `generic` nao tem familia e nao recebe tecnica; isso e por design,
    nao erro. Familia sem `style` declarado (nem defaults) ou sem `techniques`
    tambem sai sem tocar na track.

    O segundo item do retorno mapeia `edit.track` -> tecnicas de fato
    despachadas (nunca as que `density<=0.0` desligou), para o carimbo em
    `_stamp_edit_tracks` refletir so o que realmente aconteceu na track.
    """

    if not plan.edits or not plan.style:
        return [], {}

    name_to_indices = _track_name_index(out_mid.tracks)

    warnings: list[str] = []
    applied_by_track: dict[str, tuple[str, ...]] = {}
    for edit in plan.edits:
        family = _style_family_for_edit(edit.profile)
        if family is None:
            continue
        style = plan.style.get(family)
        if style is None or not style.techniques:
            continue
        target_indices = name_to_indices.get(edit.track)
        if not target_indices:
            continue
        if index is None:
            raise RenderError(
                "internal error: missing techniques index for style render"
            )
        target_tracks = [out_mid.tracks[i] for i in target_indices]
        working = _tracks_as_midi(
            target_tracks,
            ticks_per_beat=out_mid.ticks_per_beat,
            midi_type=out_mid.type,
        )
        working, edit_warnings, applied_names = _run_style_pipeline(
            working,
            plan=plan,
            family=family,
            style=style,
            tool_target=_tool_target_for_edit(edit),
            index=index,
            edit_track=edit.track,
            tuning=(tuning_by_family or {}).get(family),
            section_windows=section_windows,
            bar_windows=bar_windows,
            drum_bar_quota=drum_bar_quota,
        )
        warnings.extend(edit_warnings)
        applied_by_track[edit.track] = applied_names
        for slot, new_track in zip(
            target_indices, working.tracks, strict=True,
        ):
            out_mid.tracks[slot] = new_track
    return warnings, applied_by_track


# --- carimbo de plugin/preset em meta text ---------------------------------

def _bool_stamp(value: bool) -> str:
    return "true" if value else "false"


def _format_stamp(
    *,
    role: str,
    plugin: str | None,
    preset: str | None,
    verified: bool,
    techniques: tuple[str, ...] = (),
    suggested_plugin: str | None = None,
    suggested_preset: str | None = None,
    suggested_verified: bool = False,
) -> str:
    """Formata o carimbo em `<prefixo>|k=v|k=v...`, com todos os valores ASCII.

    Ordem estavel dos campos: `role`, `plugin`, `preset`, `verified`,
    `techniques`, `suggested_plugin`, `suggested_preset`, `suggested_verified`.
    Campos vazios sao omitidos. Nunca inclui campo cujo valor nao passe pela
    checagem ASCII do `tools.tracks`; o meta-evento SMF de texto nao carrega
    encoding, entao bytes >127 ficam a merce do decoder do DAW.
    """

    def _guarded(field_name: str, value: str) -> str:
        if not is_ascii_safe(value):
            raise RenderError(
                f"stamp field {field_name!r} must be ASCII, got {value!r}"
            )
        if "|" in value:
            raise RenderError(
                f"stamp field {field_name!r} must not contain '|' — "
                f"separador reservado do carimbo (got {value!r})"
            )
        return value

    parts: list[str] = [STAMP_PREFIX, f"role={_guarded('role', role)}"]
    if plugin:
        parts.append(f"plugin={_guarded('plugin', plugin)}")
    if preset:
        parts.append(f"preset={_guarded('preset', preset)}")
    if plugin or preset:
        parts.append(f"verified={_bool_stamp(verified)}")
    if techniques:
        for i, name in enumerate(techniques):
            _guarded(f"techniques[{i}]", name)
        parts.append(f"techniques=[{','.join(techniques)}]")
    if suggested_plugin or suggested_preset:
        if suggested_plugin:
            parts.append(
                f"suggested_plugin={_guarded('suggested_plugin', suggested_plugin)}"
            )
        if suggested_preset:
            parts.append(
                f"suggested_preset={_guarded('suggested_preset', suggested_preset)}"
            )
        parts.append(f"suggested_verified={_bool_stamp(suggested_verified)}")
    return "|".join(parts)


def _insert_stamp(track: mido.MidiTrack, stamp: str) -> None:
    """Insere o carimbo como meta text logo apos o `track_name` em tick 0.

    Coexiste com o `track_name` — nunca substitui. Delta 0 preserva o tick
    absoluto de todas as mensagens seguintes.
    """
    text = mido.MetaMessage("text", text=stamp, time=0)
    for i, msg in enumerate(track):
        if msg.is_meta and msg.type == "track_name":
            track.insert(i + 1, text)
            return
    track.insert(0, text)


def _stamp_element_tracks(
    tracks: list[mido.MidiTrack],
    element: Element,
    *,
    techniques: tuple[str, ...],
) -> None:
    inst = element.instrument or {}
    plugin = str(inst.get("plugin", "")) or None
    preset = str(inst.get("preset", "")) or None
    verified = bool(inst.get("verified", False))
    stamp = _format_stamp(
        role=element.role,
        plugin=plugin,
        preset=preset,
        verified=verified,
        techniques=techniques,
    )
    for track in tracks:
        _insert_stamp(track, stamp)


def _stamp_edit_tracks(
    out_mid: mido.MidiFile,
    *,
    plan: ArrangementPlan,
    index: TechniqueIndex | None,
    applied_techniques: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Carimba plugin/preset/role/verified/techniques em cada track de `plan.edits`.

    Faz o mapa `edit.track` -> tracks do MIDI final apenas para as tracks
    nomeadas em `plan.edits` — tracks nao declaradas ficam byte-identicas ao
    source por definicao (ver AGENTS.md), e nao recebem carimbo.

    `applied_techniques`, quando passado (o mapa devolvido por
    `_apply_style_techniques_to_edit_tracks`), e a fonte da verdade das
    tecnicas de fato aplicadas — nunca lista tecnica que `density<=0.0`
    desligou. Omitido (chamada isolada, fora do pipeline de `render()`), cai
    no fallback antigo de listar todas as tecnicas DECLARADAS da familia.
    """
    if not plan.edits:
        return
    name_to_indices: dict[str, list[int]] = {}
    for idx, tr in enumerate(out_mid.tracks):
        name = track_name(tr)
        if name:
            name_to_indices.setdefault(name, []).append(idx)

    for edit in plan.edits:
        target_indices = name_to_indices.get(edit.track)
        if not target_indices:
            continue
        family = _style_family_for_edit(edit.profile)
        techniques: tuple[str, ...] = ()
        # `dispatched` e verdadeiro so quando `edit.tool` de fato ajudou a
        # produzir alguma tecnica APLICADA (`techniques` nao vazio) — nunca
        # so por family+style existirem ou por dispatch ter sido tentado.
        # Achado de auto-revisao (varias rodadas): `profile="generic"` (sem
        # familia), familia sem `style.techniques` declarado, TODAS as
        # tecnicas com `density<=0.0`, e uma tecnica despachada mas NO-OP
        # interno por outro motivo (ex.: `bass.attack_style` sem `style`)
        # sao todos casos em que nada de fato mudou na track — em nenhum
        # deles o carimbo pode alegar `plugin=edit.tool`. `applied_techniques`
        # (quando vem do pipeline real de `render()`) ja e por si so a fonte
        # de verdade: `bool(techniques)` cobre os quatro casos de uma vez.
        if applied_techniques is not None:
            techniques = applied_techniques.get(edit.track, ())
        elif family is not None and plan.style is not None:
            style = plan.style.get(family)
            if style is not None and style.techniques:
                if index is None:
                    raise RenderError(
                        "internal error: missing techniques index for stamp"
                    )
                techniques = tuple(
                    _canonical_style_technique(index, family, tech.name)
                    for tech in style.techniques
                )
        dispatched = bool(techniques)
        suggested = edit.suggested_instrument or {}
        suggested_plugin = suggested.get("plugin") if suggested else None
        suggested_preset = suggested.get("preset") if suggested else None
        suggested_verified = bool(suggested.get("verified", False)) if suggested else False
        # `edit.tool`, quando declarado, ja determinou qual receita de
        # tecnica foi de fato aplicada na track (ex.: keyswitch especifico
        # do MODO BASS gravado nas notas) — o carimbo precisa refletir isso
        # como `plugin`, nao so como sugestao, senao a track carrega dado
        # estrutural amarrado a uma ferramenta que o carimbo nao menciona.
        # So estampa quando o pipeline de tecnicas de fato rodou pra essa
        # track (`dispatched`) — senao `edit.tool` nunca foi consultado por
        # nada, e o carimbo mentiria sobre uma ferramenta que so foi
        # declarada no plano, nunca de fato usada.
        stamp = _format_stamp(
            role=edit.profile,
            plugin=edit.tool if dispatched else None,
            preset=None,
            verified=False,
            techniques=techniques,
            suggested_plugin=suggested_plugin,
            suggested_preset=suggested_preset,
            suggested_verified=suggested_verified,
        )
        for idx in target_indices:
            _insert_stamp(out_mid.tracks[idx], stamp)


# --- conversao note -> mido -------------------------------------------------

def _notes_to_track(
    notes: (
        list[PadNote] | list[KeyboardNote] | list[StringsNote]
        | list[DroneNote] | list[RhythmicNote]
    ),
    pm: pretty_midi.PrettyMIDI,
    track_name: str,
    channel: int,
    cc_events: list[tuple[float, int, int]] | None = None,
    pitch_bend_events: list[tuple[float, int]] | None = None,
) -> mido.MidiTrack:
    """Converte lista de notas (segundos absolutos) em `mido.MidiTrack`
    com deltas em ticks. Aceita notas duck-typed com `pitch`, `velocity`,
    `start_s`, `end_s` (PadNote, KeyboardNote, StringsNote e DroneNote
    satisfazem).
    Determinstica: ordena eventos por (tick, tipo, cc/pitch, valor).

    `cc_events` e lista de `(time_s, cc_number, value)` — CC64 do teclado
    e CC11 das strings vem por aqui. CC vem ANTES de note_on e DEPOIS de
    note_off no mesmo tick, para o motor de sustain do plugin nao
    capturar a nota que estava tentando finalizar.

    `pitch_bend_events` e lista de `(time_s, value)` com `value` no range
    bruto de pitchwheel (-8192..8191) — usado por `sub_drop`. Grava
    `pitchwheel` bruto, sem negociar RPN de bend range (ver
    `electronic.sub_drop` no manual)."""
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=track_name, time=0))

    # kind: 0 = note_off, 1 = cc, 2 = pitchwheel, 3 = note_on. Ordem no
    # mesmo tick: note_off (fecha nota anterior) -> cc/pitchwheel (mudanca
    # de estado) -> note_on (dispara nova nota). Isso evita que um CC64
    # down engula o note_off da nota que acabou de fechar, e garante que um
    # reset de pitch bend no mesmo tick do proximo note_on (ex.: drops de
    # sub_drop em sequencia) seja escrito ANTES do note_on — kind=2 <
    # kind=3 no `events.sort()` abaixo.
    events: list[tuple[int, int, int, int]] = []
    for n in notes:
        start_tick = int(round(pm.time_to_tick(n.start_s)))
        end_tick = int(round(pm.time_to_tick(n.end_s)))
        if end_tick <= start_tick:
            end_tick = start_tick + 1
        events.append((start_tick, 3, int(n.pitch), int(n.velocity)))
        events.append((end_tick, 0, int(n.pitch), 0))
    if cc_events:
        for time_s, cc_num, value in cc_events:
            tick = int(round(pm.time_to_tick(time_s)))
            events.append((tick, 1, int(cc_num), int(value)))
    if pitch_bend_events:
        for time_s, value in pitch_bend_events:
            tick = int(round(pm.time_to_tick(time_s)))
            events.append((tick, 2, 0, int(value)))
    events.sort()

    prev_tick = 0
    for tick, kind, pitch_or_cc, vel_or_value in events:
        delta = tick - prev_tick
        if kind == 3:
            tr.append(mido.Message(
                "note_on", channel=channel, note=pitch_or_cc,
                velocity=vel_or_value, time=delta,
            ))
        elif kind == 1:
            tr.append(mido.Message(
                "control_change", channel=channel, control=pitch_or_cc,
                value=vel_or_value, time=delta,
            ))
        elif kind == 2:
            tr.append(mido.Message(
                "pitchwheel", channel=channel, pitch=vel_or_value, time=delta,
            ))
        else:
            tr.append(mido.Message(
                "note_off", channel=channel, note=pitch_or_cc,
                velocity=0, time=delta,
            ))
        prev_tick = tick
    return tr


def _rendered_tracks_from_midi_tracks(
    element: Element,
    tracks: list[mido.MidiTrack],
    pm: pretty_midi.PrettyMIDI,
    *,
    ticks_per_beat: int,
    midi_type: int,
) -> list[RenderedTrack]:
    """Reconstroi notas renderizadas a partir do MIDI final do elemento.

    O motor de tecnicas pode acrescentar ornamentos depois do gerador de role;
    os validadores precisam enxergar essas notas reais, nao apenas a lista
    original retornada pela paleta.
    """

    temp_mid = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=midi_type)
    temp_mid.tracks.append(_tempo_track_from_pretty_midi(pm))
    temp_mid.tracks.extend(tracks)
    payload = BytesIO()
    temp_mid.save(file=payload)
    payload.seek(0)
    parsed = pretty_midi.PrettyMIDI(payload)

    rendered: list[RenderedTrack] = []
    for fallback_index, instrument in enumerate(parsed.instruments):
        track_name = instrument.name or f"{element.id} L{fallback_index + 1}"
        rendered.append(RenderedTrack(
            element_id=element.id,
            track_name=track_name,
            notes=tuple(
                RenderedNote(
                    pitch=int(note.pitch),
                    velocity=int(note.velocity),
                    start_s=float(note.start),
                    end_s=float(note.end),
                )
                for note in instrument.notes
            ),
        ))
    return rendered


def _rendered_tracks_from_instrument_list(
    instruments: Iterable[pretty_midi.Instrument],
) -> list[RenderedTrack]:
    """Converte `pretty_midi.Instrument` de tracks de ORIGEM/EDICAO (ja
    identificadas como tal pelo chamador — nao ha `Element` do plano pra
    elas) em `RenderedTrack` sintetica com `element_id` `source:<nome>`.

    Compartilhado entre `_rendered_tracks_from_source_tracks` (fresh render
    em `render()`, issue #24 finding 1) e a fachada `tools.validate`
    (`tools.contract._rendered_tracks_from_midi`, issue #24 finding 1 —
    auditoria de um MIDI JA renderizado, que reconstroi as mesmas tracks de
    origem/edicao a partir dos instrumentos nao casados a nenhum elemento).
    `is_drum` preserva `pretty_midi.Instrument.is_drum` (canal 10 GM) por
    track — inclusive tracks de origem NAO declaradas em `plan.edits`
    (issue #24 finding 3), que nao tem `PlanEdit`/`Element` nenhum pra
    casar por `role`/`profile` em
    `tools.validators.transitions._drum_element_ids`.
    """
    rendered: list[RenderedTrack] = []
    for fallback_index, instrument in enumerate(instruments):
        track_name = instrument.name or f"source L{fallback_index + 1}"
        rendered.append(RenderedTrack(
            element_id=f"source:{track_name}",
            track_name=track_name,
            is_drum=bool(instrument.is_drum),
            notes=tuple(
                RenderedNote(
                    pitch=int(note.pitch),
                    velocity=int(note.velocity),
                    start_s=float(note.start),
                    end_s=float(note.end),
                )
                for note in instrument.notes
            ),
        ))
    return rendered


def _rendered_tracks_from_source_tracks(
    tracks: list[mido.MidiTrack],
    *,
    ticks_per_beat: int,
    midi_type: int,
) -> list[RenderedTrack]:
    """Reconstroi `RenderedTrack` a partir das tracks de ORIGEM (clonadas de
    `src_mid`, editadas por `plan.edits` ou nao) no MIDI final.

    Existe para `validate_transitions` (issue #24 finding 1): um plano que
    so mexe no material do usuario via `plan.edits`, sem `plan.elements[]`
    nenhum, precisa que as duas metades da fronteira tenham dado real pra
    medir — sem isso, `events_a`/`events_b` ficariam sempre vazios e toda
    fronteira seria pulada, mesmo com o MIDI de saida bem diferente dos
    dois lados. `element_id` sintetico `source:<nome da track>` identifica
    cada track fisica de origem como entidade distinta (nao ha `Element`
    do plano pra essas tracks) — mesmo esquema que
    `tools.validators.transitions._drum_element_ids` usa pra casar track de
    `plan.edits` com `profile == "drums"`.
    """
    temp_mid = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=midi_type)
    temp_mid.tracks.extend(tracks)
    payload = BytesIO()
    temp_mid.save(file=payload)
    payload.seek(0)
    parsed = pretty_midi.PrettyMIDI(payload)
    return _rendered_tracks_from_instrument_list(parsed.instruments)


def _clone_source_tracks(src: mido.MidiFile) -> list[mido.MidiTrack]:
    """Copia cada track do source em `MidiTrack` novo com mensagens copiadas.
    Preserva ordem e conteudo — as tracks originais saem intactas."""
    out: list[mido.MidiTrack] = []
    for tr in src.tracks:
        new = mido.MidiTrack()
        for msg in tr:
            new.append(msg.copy())
        out.append(new)
    return out


# --- geradores por role -----------------------------------------------------

def _layers_to_tracks(
    element: Element,
    layer_notes: (
        list[list[PadNote]]
        | list[list[KeyboardNote]]
        | list[list[StringsNote]]
        | list[list[DroneNote]]
        | list[list[RhythmicNote]]
    ),
    pm: pretty_midi.PrettyMIDI,
    channel: int,
    layer_ccs: list[list[tuple[float, int, int]]] | None = None,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Converte as notas acumuladas por layer em MidiTrack + RenderedTrack.

    Compartilhado entre pad, teclado e strings — a diferenca esta so em
    `layer_ccs` (CC64 do teclado, CC11 das strings, ambos como
    `(time_s, cc, value)`). Assinatura duck-typed: qualquer nota com
    pitch/velocity/start_s/end_s serve."""
    midi_tracks: list[mido.MidiTrack] = []
    rendered: list[RenderedTrack] = []
    for i, notes in enumerate(layer_notes):
        name = _element_track_name(element, i, element.layers)
        ccs = layer_ccs[i] if layer_ccs is not None else None
        midi_tracks.append(_notes_to_track(
            notes, pm, name, channel, cc_events=ccs or None,
        ))
        rendered.append(RenderedTrack(
            element_id=element.id,
            track_name=name,
            notes=tuple(
                RenderedNote(
                    pitch=n.pitch, velocity=n.velocity,
                    start_s=n.start_s, end_s=n.end_s,
                )
                for n in notes
            ),
        ))
    return midi_tracks, rendered


def _iter_element_sections(
    element: Element, plan: ArrangementPlan,
) -> list[tuple[PlanSection, int]]:
    """Devolve pares (secao, seed) para cada label de `element.sections` que
    resolve para uma secao declarada no plano. Guarda defensiva — o
    plan.validate ja rejeita labels desconhecidos, mas o renderer ignora em
    silencio caso o plano tenha sido mutado em teste."""
    result: list[tuple[PlanSection, int]] = []
    for label in element.sections:
        section = _section_by_label(plan, label)
        if section is None:
            continue
        seed = _element_seed(plan.seed, element.id, label)
        result.append((section, seed))
    return result


def _render_keyboard_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de piano ou Rhodes. Uma track por layer; notas de todas
    as secoes concatenadas na mesma layer. CC64 opcional por elemento via
    `element.pattern['use_sustain_cc64']` (default False — spec 'nao e
    default')."""
    layer_notes: list[list[KeyboardNote]] = [[] for _ in range(element.layers)]
    layer_ccs: list[list[tuple[float, int, int]]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}
    pattern = element.pattern or {}
    use_sustain = bool(pattern.get("use_sustain_cc64", False))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_keyboard(
            analysis,
            section,
            role=element.role,
            register=register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=dyn,
            use_sustain_cc64=use_sustain,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)
            for p in layer.pedal_events:
                layer_ccs[i].append((p.time_s, SUSTAIN_CC, p.value))

    return _layers_to_tracks(element, layer_notes, pm, channel, layer_ccs)


def _render_strings_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de strings ou choir. Cada layer e uma voz independente
    da linha — a AC pede attack diferente por voz + CC11 por voz. Voice
    count = `element.layers` (para strings o vocabulario 'layers' significa
    'vozes independentes', nao unisono). `tutti` e `ghost_ratio` sao lidos
    de `element.pattern`."""
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}
    pattern = element.pattern or {}
    tutti = bool(pattern.get("tutti", False))
    voices = min(element.layers, STRINGS_TUTTI_MAX_VOICES) if tutti else element.layers
    layer_notes: list[list[StringsNote]] = [[] for _ in range(voices)]
    layer_ccs: list[list[tuple[float, int, int]]] = [[] for _ in range(voices)]
    ghost_ratio = float(pattern.get("ghost_ratio", STRINGS_GHOST_RATIO))

    for section, seed in _iter_element_sections(element, plan):
        section_voices = generate_strings(
            analysis,
            section,
            role=element.role,
            register=register,
            voices=voices,
            articulation=element.articulation,
            dynamics=dyn,
            tutti=tutti,
            ghost_ratio=ghost_ratio,
            seed=seed,
        )
        for i, voice in enumerate(section_voices):
            layer_notes[i].extend(voice.notes)
            for ev in voice.expression_events:
                layer_ccs[i].append((ev.time_s, EXPRESSION_CC, ev.value))

    return _layers_to_tracks(element, layer_notes, pm, channel, layer_ccs)


def _strings_tutti_layer_warning(element: Element) -> str | None:
    pattern = element.pattern or {}
    if (
        element.role in STRINGS_ROLES
        and bool(pattern.get("tutti", False))
        and element.layers > STRINGS_TUTTI_MAX_VOICES
    ):
        return (
            f"{element.id}: element.layers={element.layers} reduced to "
            f"{STRINGS_TUTTI_MAX_VOICES} for strings tutti"
        )
    return None


def _accumulate_cc_layers(
    layer_notes: list[list],
    layer_ccs: list[list[tuple[float, int, int]]],
    layers,
) -> None:
    """Estende `layer_notes` e `layer_ccs` in-place com as notas e CC events
    de `layers`. Compartilhado entre drone e rhythmic — ambos produzem
    `Layer(notes, cc_events)` com eventos ja carregando (time_s, cc, value)."""
    for i, layer in enumerate(layers):
        layer_notes[i].extend(layer.notes)
        for ev in layer.cc_events:
            layer_ccs[i].append((ev.time_s, ev.cc, ev.value))


@dataclass
class _RhythmicElementContext:
    """Prelude compartilhado por _render_rhythmic_element e
    _render_motor_element — mesmo shape de buckets, mesmo unpack de
    `element` (register/dyn/pattern) e mesmos defaults dos knobs comuns
    de padrao (filter_cycle_bars, velocity_cycle_bars, custom_steps)."""
    layer_notes: list[list[RhythmicNote]]
    layer_ccs: list[list[tuple[float, int, int]]]
    register: tuple[int, int]
    dyn: dict
    pattern: dict
    filter_cycle_bars: int
    velocity_cycle_bars: int
    custom_steps: object


def _rhythmic_element_context(element: Element) -> _RhythmicElementContext:
    pattern = element.pattern or {}
    return _RhythmicElementContext(
        layer_notes=[[] for _ in range(element.layers)],
        layer_ccs=[[] for _ in range(element.layers)],
        register=(int(element.register[0]), int(element.register[1])),
        dyn=element.dynamics or {},
        pattern=pattern,
        filter_cycle_bars=int(pattern.get("filter_cycle_bars", 4)),
        velocity_cycle_bars=int(pattern.get("velocity_cycle_bars", 2)),
        custom_steps=pattern.get("custom_steps"),
    )


def _drive_rhythmic_layers(
    ctx: _RhythmicElementContext,
    element: Element,
    plan: ArrangementPlan,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
    per_section,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Loop compartilhado de rhythmic/motor: itera secoes, chama
    `per_section(section, seed)` (deve devolver a lista de layers do
    gerador), acumula CCs e finaliza via `_layers_to_tracks`."""
    for section, seed in _iter_element_sections(element, plan):
        layers = per_section(section, seed)
        _accumulate_cc_layers(ctx.layer_notes, ctx.layer_ccs, layers)
    return _layers_to_tracks(
        element, ctx.layer_notes, pm, channel, ctx.layer_ccs,
    )


def _render_drone_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de drone / nota-pedal. Uma track por layer; notas
    unicas sustentadas por secao. Modo pedal via `element.pattern['pedal']`
    desativa CC74/CC11. `pattern['pedal_pitch']` seleciona tonica ou
    quinta; `pattern['filter_cycle_bars']` e `pattern['modulation_bars']`
    controlam os ciclos dos CCs."""
    layer_notes: list[list[DroneNote]] = [[] for _ in range(element.layers)]
    layer_ccs: list[list[tuple[float, int, int]]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    pattern = element.pattern or {}
    pedal = bool(pattern.get("pedal", False))
    pedal_pitch = str(pattern.get("pedal_pitch", "tonic"))
    filter_cycle_bars = int(
        pattern.get("filter_cycle_bars", 4),
    )
    modulation_bars = int(
        pattern.get("modulation_bars", 16),
    )

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_drone(
            analysis,
            section,
            register=register,
            layers=element.layers,
            pedal=pedal,
            pedal_pitch=pedal_pitch,
            filter_cycle_bars=filter_cycle_bars,
            modulation_bars=modulation_bars,
            seed=seed,
        )
        _accumulate_cc_layers(layer_notes, layer_ccs, layers)

    return _layers_to_tracks(element, layer_notes, pm, channel, layer_ccs)


def _render_rhythmic_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de arp / rhythmic_machine. Uma track por layer; notas de
    todas as secoes concatenadas na mesma layer. Controles via
    `element.pattern`: `pattern_bars` (1 ou 2), `mutate_every_bars` (4/8 ou
    None), `interlock` (bool), `filter_cycle_bars`, `velocity_cycle_bars`,
    `custom_steps` (padrao 0/1 opcional)."""
    ctx = _rhythmic_element_context(element)
    pattern_bars = int(ctx.pattern.get("pattern_bars", 1))
    mutate_raw = ctx.pattern.get("mutate_every_bars")
    mutate_every_bars = int(mutate_raw) if mutate_raw is not None else None
    interlock = bool(ctx.pattern.get("interlock", False))

    def per_section(section, seed):
        return generate_rhythmic(
            analysis, section,
            role=element.role,
            register=ctx.register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=ctx.dyn,
            pattern_bars=pattern_bars,
            mutate_every_bars=mutate_every_bars,
            interlock=interlock,
            filter_cycle_bars=ctx.filter_cycle_bars,
            velocity_cycle_bars=ctx.velocity_cycle_bars,
            custom_steps=ctx.custom_steps,
            seed=seed,
        )

    return _drive_rhythmic_layers(ctx, element, plan, pm, channel, per_section)


def _render_motor_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de motor. Uma track por layer; notas de todas as
    secoes concatenadas na mesma layer. Controles via `element.pattern`:
    `subdivision` (eighth/sixteenth), `filter_cycle_bars`,
    `velocity_cycle_bars`, `custom_steps` (padrao 0/1 opcional)."""
    ctx = _rhythmic_element_context(element)
    subdivision = str(ctx.pattern.get("subdivision", "sixteenth"))

    def per_section(section, seed):
        return generate_motor(
            analysis, section,
            role=element.role,
            subdivision=subdivision,
            register=ctx.register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=ctx.dyn,
            filter_cycle_bars=ctx.filter_cycle_bars,
            velocity_cycle_bars=ctx.velocity_cycle_bars,
            custom_steps=ctx.custom_steps,
            seed=seed,
        )

    return _drive_rhythmic_layers(ctx, element, plan, pm, channel, per_section)


def _render_shadow_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de shadow — dobra o fim de cada frase de guitarra.
    Controles via `element.pattern`: `octave_shift` (+/-12),
    `tail_notes`, `phrase_end_gap_s`, `velocity_offset`,
    `note_duration_s`."""
    layer_notes: list[list[RhythmicNote]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}
    pattern = element.pattern or {}
    octave_shift = int(pattern.get("octave_shift", 12))
    tail_notes = int(pattern.get("tail_notes", 2))
    phrase_end_gap_s = float(pattern.get("phrase_end_gap_s", 0.5))
    velocity_offset = int(pattern.get("velocity_offset", -25))
    note_duration_s = float(pattern.get("note_duration_s", 0.35))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_shadow(
            analysis,
            section,
            role=element.role,
            register=register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=dyn,
            octave_shift=octave_shift,
            tail_notes=tail_notes,
            phrase_end_gap_s=phrase_end_gap_s,
            velocity_offset=velocity_offset,
            note_duration_s=note_duration_s,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


def _render_hat_elec_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de hi-hat eletronico. Pitch/gate/velocity/offset vem do
    manual (`electronic.hat_elec`) via `generate_hat_elec`. Controle via
    `element.pattern`: `pattern_mode` (`sixteenth`/`gaps`/`half_time`)."""
    layer_notes: list[list[RhythmicNote]] = [[] for _ in range(element.layers)]
    pattern = element.pattern or {}
    pattern_mode = str(pattern.get("pattern_mode", "sixteenth"))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_hat_elec(
            analysis, section,
            layers=element.layers,
            pattern_mode=pattern_mode,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


def _render_sub_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera a track de sub-bass de breakdown. SEMPRE uma unica layer — nao
    ha flag que ligue polifonia neste elemento (AGENTS.md). Controle via
    `element.pattern`: `follow` (`tonic`/`kick`/`riff`); `riff` usa
    `element.degrees`."""
    layer_notes: list[RhythmicNote] = []
    register = (int(element.register[0]), int(element.register[1]))
    pattern = element.pattern or {}
    follow = str(pattern.get("follow", "tonic"))
    degrees = tuple(element.degrees) if element.degrees else None

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_sub(
            analysis, section,
            register=register,
            follow=follow,
            degrees=degrees,
            seed=seed,
        )
        layer_notes.extend(layers[0].notes)

    return _layers_to_tracks(element, [layer_notes], pm, channel)


def _render_sub_drop_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera a track de sub-drop: um evento pontual por secao declarada, na
    fronteira (inicio) daquela secao. SEMPRE nota unica — nao ha branch
    capaz de emitir mais de uma nota por evento."""
    register = (int(element.register[0]), int(element.register[1]))
    notes: list[RhythmicNote] = []
    bends: list[tuple[float, int]] = []

    for section, seed in _iter_element_sections(element, plan):
        bars = bars_in_section(section, analysis)
        if not bars:
            continue
        boundary_s = bars[0].start
        event = generate_sub_drop(
            analysis, boundary_s, register=register, seed=seed,
        )
        notes.append(event.note)
        bends.extend((pb.time_s, pb.value) for pb in event.pitch_bend)

    name = _element_track_name(element, 0, 1)
    track = _notes_to_track(
        notes, pm, name, channel, pitch_bend_events=bends or None,
    )
    rendered = RenderedTrack(
        element_id=element.id,
        track_name=name,
        notes=tuple(
            RenderedNote(
                pitch=n.pitch, velocity=n.velocity,
                start_s=n.start_s, end_s=n.end_s,
            )
            for n in notes
        ),
    )
    return [track], [rendered]


def _render_riser_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera a track de riser: rampa ascendente por secao declarada,
    terminando ANTES do downbeat daquela secao (issue #23)."""
    register = (int(element.register[0]), int(element.register[1]))
    pattern = element.pattern or {}
    duration_bars = pattern.get("duration_bars")
    degrees = tuple(element.degrees) if element.degrees else None

    notes: list[RhythmicNote] = []
    ccs: list[tuple[float, int, int]] = []
    for section, seed in _iter_element_sections(element, plan):
        bars = bars_in_section(section, analysis)
        if not bars:
            continue
        boundary_s = bars[0].start
        event = generate_riser(
            analysis, boundary_s, register=register,
            duration_bars=duration_bars, degrees=degrees, seed=seed,
        )
        notes.extend(event.notes)
        ccs.extend((e.time_s, e.cc, e.value) for e in event.cc_events)

    return _point_event_to_tracks(element, notes, ccs, pm, channel)


def _render_downer_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera a track de downer: mesma mecanica do riser, invertida (issue #23)."""
    register = (int(element.register[0]), int(element.register[1]))
    pattern = element.pattern or {}
    duration_bars = pattern.get("duration_bars")
    degrees = tuple(element.degrees) if element.degrees else None

    notes: list[RhythmicNote] = []
    ccs: list[tuple[float, int, int]] = []
    for section, seed in _iter_element_sections(element, plan):
        bars = bars_in_section(section, analysis)
        if not bars:
            continue
        boundary_s = bars[0].start
        event = generate_downer(
            analysis, boundary_s, register=register,
            duration_bars=duration_bars, degrees=degrees, seed=seed,
        )
        notes.extend(event.notes)
        ccs.extend((e.time_s, e.cc, e.value) for e in event.cc_events)

    return _point_event_to_tracks(element, notes, ccs, pm, channel)


def _render_impact_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera a track de impacto: hit alinhado no downbeat de cada secao
    declarada, em camadas com caudas divergentes. `occurrence_index` segue
    a ORDEM de `element.sections` — impactos repetidos ciclam pelas tres
    intensidades do manual, nunca repetem identico (issue #23)."""
    register = (int(element.register[0]), int(element.register[1]))

    notes: list[RhythmicNote] = []
    for occurrence_index, (section, seed) in enumerate(_iter_element_sections(element, plan)):
        bars = bars_in_section(section, analysis)
        if not bars:
            continue
        boundary_s = bars[0].start
        event = generate_impact(
            analysis, boundary_s, register=register,
            occurrence_index=occurrence_index, seed=seed,
        )
        notes.extend(event.notes)

    return _point_event_to_tracks(element, notes, [], pm, channel)


def _render_reverse_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera a track de reverse/meia-lua: swell que RESOLVE exatamente no
    downbeat de cada secao declarada (issue #23). `pattern.freeze_pitch`/
    `freeze_velocity` (opcionais): modo `freeze` — a IA que escreve o plano
    declara o ultimo evento da secao anterior a congelar como fonte."""
    register = (int(element.register[0]), int(element.register[1]))
    pattern = element.pattern or {}
    duration_bars = pattern.get("duration_bars")
    freeze_pitch = pattern.get("freeze_pitch")
    freeze_velocity = pattern.get("freeze_velocity")

    notes: list[RhythmicNote] = []
    ccs: list[tuple[float, int, int]] = []
    for section, seed in _iter_element_sections(element, plan):
        bars = bars_in_section(section, analysis)
        if not bars:
            continue
        boundary_s = bars[0].start
        event = generate_reverse(
            analysis, boundary_s, register=register,
            duration_bars=duration_bars, freeze_pitch=freeze_pitch,
            freeze_velocity=freeze_velocity, seed=seed,
        )
        notes.extend(event.notes)
        ccs.extend((e.time_s, e.cc, e.value) for e in event.cc_events)

    return _point_event_to_tracks(element, notes, ccs, pm, channel)


def _point_event_to_tracks(
    element: Element,
    notes: list[RhythmicNote],
    ccs: list[tuple[float, int, int]],
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Empacota as notas + CC acumuladas de um gerador de transicao
    (riser/downer/impact/reverse) em uma unica track — sempre 1 layer,
    mesmo padrao de `_render_sub_drop_element`. Compartilhado entre os
    quatro roles de `tools.palette.transitions` para nao duplicar o
    seam `_notes_to_track`/`RenderedTrack`."""
    name = _element_track_name(element, 0, 1)
    track = _notes_to_track(notes, pm, name, channel, cc_events=ccs or None)
    rendered = RenderedTrack(
        element_id=element.id,
        track_name=name,
        notes=tuple(
            RenderedNote(
                pitch=n.pitch, velocity=n.velocity,
                start_s=n.start_s, end_s=n.end_s,
            )
            for n in notes
        ),
    )
    return [track], [rendered]


def _render_pad_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera as tracks de um elemento pad. Uma track por layer; notas de todas
    as secoes concatenadas na mesma layer.

    Devolve tambem `RenderedTrack`s espelhando cada track emitida — insumo
    do validador harmonico (`validate_harmony`)."""
    layer_notes: list[list[PadNote]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_pad(
            analysis,
            section,
            register=register,
            layers=element.layers,
            dynamics=dyn,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


def _render_drums_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de bateria do zero (issue #20). Uma track por layer;
    notas de todas as secoes concatenadas na mesma layer. A levada le
    `section.energy` — nao consome `element.pattern` nesta rodada."""
    layer_notes: list[list[RhythmicNote]] = [[] for _ in range(element.layers)]

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_drums(
            analysis,
            section,
            role=element.role,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=element.dynamics,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


def _render_bass_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de baixo do zero (issue #20). Uma track por layer;
    notas de todas as secoes concatenadas na mesma layer. A linha segue o
    campo harmonico (raiz/terca/quinta do acorde vigente) e as ancoras de
    kick de `analysis.kick_positions`."""
    layer_notes: list[list[RhythmicNote]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_bass(
            analysis,
            section,
            role=element.role,
            register=register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=element.dynamics,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


def _render_guitar_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de guitarra ritmica do zero (issue #19). Uma track por
    layer; notas de todas as secoes concatenadas na mesma layer. Cada
    golpe e um voicing power-chord do campo harmonico vigente, sempre
    checado contra a afinacao/registro declarados
    (`tools.palette.guitar.generate_guitar`)."""
    layer_notes: list[list[RhythmicNote]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_guitar(
            analysis,
            section,
            role=element.role,
            register=register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=element.dynamics,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


_RenderElementFn = Callable[
    [Element, ArrangementPlan, Analysis, pretty_midi.PrettyMIDI, int],
    tuple[list[mido.MidiTrack], list[RenderedTrack]],
]


@dataclass(frozen=True)
class _RoleRenderer:
    render: _RenderElementFn
    channel: int


def _build_role_renderers() -> dict[str, _RoleRenderer]:
    return {
        "pad": _RoleRenderer(_render_pad_element, PAD_CHANNEL),
        **{
            role: _RoleRenderer(_render_keyboard_element, KEYBOARD_CHANNEL)
            for role in KEYBOARD_ROLES
        },
        **{
            role: _RoleRenderer(_render_strings_element, STRINGS_CHANNEL)
            for role in STRINGS_ROLES
        },
        **{
            role: _RoleRenderer(_render_drone_element, DRONE_CHANNEL)
            for role in DRONE_ROLES
        },
        **{
            role: _RoleRenderer(_render_rhythmic_element, RHYTHMIC_CHANNEL)
            for role in RHYTHMIC_ROLES
        },
        **{
            role: _RoleRenderer(_render_motor_element, MOTOR_CHANNEL)
            for role in MOTOR_ROLES
        },
        **{
            role: _RoleRenderer(_render_shadow_element, SHADOW_CHANNEL)
            for role in SHADOW_ROLES
        },
        **{
            role: _RoleRenderer(_render_drums_element, DRUMS_CHANNEL)
            for role in DRUMS_ROLES
        },
        **{
            role: _RoleRenderer(_render_bass_element, BASS_CHANNEL)
            for role in BASS_ROLES
        },
        **{
            role: _RoleRenderer(_render_guitar_element, GUITAR_CHANNEL)
            for role in GUITAR_ROLES
        },
        **{
            role: _RoleRenderer(_render_hat_elec_element, HAT_ELEC_CHANNEL)
            for role in HAT_ELEC_ROLES
        },
        **{
            role: _RoleRenderer(_render_sub_element, SUB_CHANNEL)
            for role in SUB_ROLES
        },
        **{
            role: _RoleRenderer(_render_sub_drop_element, SUB_DROP_CHANNEL)
            for role in SUB_DROP_ROLES
        },
        **{
            role: _RoleRenderer(_render_riser_element, RISER_CHANNEL)
            for role in RISER_ROLES
        },
        **{
            role: _RoleRenderer(_render_downer_element, DOWNER_CHANNEL)
            for role in DOWNER_ROLES
        },
        **{
            role: _RoleRenderer(_render_impact_element, IMPACT_CHANNEL)
            for role in IMPACT_ROLES
        },
        **{
            role: _RoleRenderer(_render_reverse_element, REVERSE_CHANNEL)
            for role in REVERSE_ROLES
        },
    }


_ROLE_RENDERERS = _build_role_renderers()
SUPPORTED_ROLES: frozenset[str] = frozenset(_ROLE_RENDERERS)


# --- API principal ----------------------------------------------------------

PlanArg = ArrangementPlan | str | Path


def render(
    plan: PlanArg,
    output_path: str | Path | None = None,
    *,
    source_path: str | Path | None = None,
    strict_persona: bool = False,
    plan_dir: str | Path | None = None,
    reference_corpus: Iterable[str | Path] | Iterable[ReferenceSequence] | None = None,
    only: str | Iterable[str] | None = None,
) -> RenderReport:
    """Renderiza `plan` sobre o MIDI de origem em um arquivo novo.

    Args:
      plan: `ArrangementPlan` ja carregado ou caminho para `arrangement-plan.json`.
      output_path: caminho de saida. Default: `~/Desktop/<name>_arranged.mid`.
      source_path: override do caminho de origem. Default: `plan.source_midi.path`.
      only: filtro de `plan.elements` (issue #24) — string ou lista de
        strings de `ONLY_CATEGORIES` ("transitions", "harmonic",
        "rhythmic", "electronic"; aceita string unica separada por
        virgula, ex. `"transitions,harmonic"`). `None` (default) renderiza
        todos os elementos, comportamento atual. Elemento fora das
        categorias pedidas nao gera track nem aparece no relatorio, e os
        validadores rodam so sobre o que sobrou.

    Raises:
      RenderError: source inexistente, output apontaria para o source,
        elemento pad sem instrument.plugin/preset, tecnica de
        `style.<familia>.techniques[]` fora de
        `brief.style.<familia>.authorized_techniques`, `plan.elements[]`
        gerando familia vetada em `brief.excluded_families` (issue #17), OU
        `plan.brief_ref` ausente enquanto o `arrangement-brief.json`
        convencional declara `excluded_families` nao-vazio (issue #105,
        segunda rodada) — as tres barreiras rodam antes de `validate_plan`,
        entao violacao vira `RenderError`, nao `PlanValidationError`.
      PlanValidationError: quando `plan` e invalido, vindo de caminho ou
        construido em memoria.

    Efeitos: cria diretorio-pai do output se nao existir. Nunca modifica o
    source. Pode mutar `Element.register` do plano em memoria via validator
    de colisao (mesma semantica de `validate_collisions`).
    """
    # `plan_dir` ancora `brief_ref.path` relativo. Quando `plan` vem como
    # caminho, sai dele; quando vem como objeto ja carregado — o caso das
    # fachadas em `tools/contract.py`, que leem o JSON antes —, quem chama
    # tem que informar, senao o relativo resolveria contra o cwd e o brief
    # ao lado do plano nao seria encontrado.
    resolved_plan_dir: Path | None = (
        Path(plan_dir).expanduser() if plan_dir is not None else None
    )
    if not isinstance(plan, ArrangementPlan):
        plan_path = Path(plan).expanduser()
        resolved_plan_dir = plan_path.parent
        plan = load(plan_path)
    plan_dir = resolved_plan_dir
    _reject_unauthorized_style_techniques(plan, plan_dir)
    _reject_excluded_family_elements(plan, plan_dir)
    # Achado do Codex na PR #105, terceira rodada: a barreira nova de
    # brief-nao-referenciado nao inspeciona nenhum campo do plano (so
    # `plan.brief_ref`, `plan_dir` e o arquivo de brief), entao nao tinha
    # o mesmo risco de TypeError das outras duas barreiras acima — mas
    # ainda assim disparava RenderError incondicionalmente, mesmo quando
    # o PROPRIO plano e invalido por outro motivo (ex.: `Element.role`
    # nao-string) e deveria falhar como `PlanValidationError` primeiro.
    # Confirma a validade estrutural aqui (chamada extra e barata —
    # `validate_plan` e read-only) antes de rodar a barreira nova;
    # plano invalido cai direto na `validate_plan(plan, plan_dir)` de
    # baixo, que levanta o `PlanValidationError` de verdade.
    try:
        validate_plan(plan, plan_dir)
    except PlanValidationError:
        pass
    else:
        _reject_missing_brief_ref_with_excluded_families(plan, plan_dir)
    validate_plan(plan, plan_dir)
    plan = normalize_style_defaults(plan)
    plan = _apply_only_filter(plan, only)

    src = Path(source_path).expanduser() if source_path else _resolve_source_path(plan)
    if not src.exists():
        raise RenderError(f"source MIDI not found: {src}")

    out_path = Path(output_path).expanduser() if output_path else _default_output_path(src)
    if out_path.resolve() == src.resolve():
        raise RenderError(f"output would overwrite source: {out_path}")

    source_hash = sha256_of_file(src)
    warnings: list[str] = _style_confidence_warnings(plan)
    brief_gap_warning = _brief_not_referenced_warning(plan, plan_dir)
    if brief_gap_warning is not None:
        warnings.append(brief_gap_warning)
    if plan.source_midi.sha256 and plan.source_midi.sha256 != source_hash:
        warnings.append(
            f"source_midi.sha256 mismatch (plan={plan.source_midi.sha256[:12]}..., "
            f"file={source_hash[:12]}...); rendering anyway"
        )

    pm = pretty_midi.PrettyMIDI(str(src))
    analysis = analyze(str(src))

    collision_report = validate_collisions(plan)

    # charset: usa o default do mido (latin-1), que e o que a spec MIDI 1.0
    # assume para meta-texto. Forcar utf-8 aqui gravava bytes >127 que o
    # decoder do DAW nao e obrigado a entender — ver o bloco de constantes
    # em tracks.py. Todo nome emitido por name_for_element ja e ASCII.
    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(
        ticks_per_beat=src_mid.ticks_per_beat,
        type=src_mid.type,
    )
    out_mid.tracks.extend(_clone_source_tracks(src_mid))
    # Numero de tracks clonadas da origem, capturado ANTES de qualquer track
    # gerada ser anexada — usado depois (issue #24 finding 1) para fatiar
    # `out_mid.tracks` de volta em "origem/edicao" vs "elemento gerado" na
    # hora de montar o insumo do validador de transicao.
    source_track_count = len(out_mid.tracks)

    # Edits opt-in (FR-28): humaniza tracks nomeadas em `plan.edits` in-place
    # nas tracks ja clonadas. Tracks nao nomeadas ficam byte-identicas.
    # Validacao de existencia de track roda AQUI (nao em plan.py) porque
    # so aqui temos o MIDI carregado.
    edit_reports: list[EditReport] = []
    if plan.edits:
        validate_edits_against_midi(plan, collect_track_names(out_mid.tracks))
        edit_reports = apply_edits(
            list(out_mid.tracks), plan.edits, plan.seed, pm,
        )

    element_reports: list[ElementRationale] = []
    rendered_tracks: list[RenderedTrack] = []
    style_index = (
        build_techniques_index()
        if plan.style and any(style.techniques for style in plan.style.values())
        else None
    )
    # Afinacao declarada em `brief.instruments.<familia>` (issue #44) —
    # caminho MINIMO ate `TechniqueContext.parameters["tuning"]`, ver
    # `tools.plan.load_brief_instrument_tuning`. Familia ausente do brief,
    # `known=false` ou brief sem `instruments` devolvem dict vazio e o
    # motor de tecnicas cai no default fisico de `physical.py`, igual antes.
    tuning_by_family = load_brief_instrument_tuning(plan, plan_dir)
    # Janelas de tick de `plan.sections[].energy` (issue #45) — canal
    # separado de `style.parameters`, mesmo padrao de `tuning_by_family`
    # acima. Unico consumidor hoje e `drums.ghost_notes`, mas o calculo e
    # generico o bastante pra qualquer tecnica futura que precise de
    # energia por secao.
    section_windows = _section_energy_windows(plan, analysis, pm)
    # Fronteiras REAIS de compasso (issue #45 finding do Codex no PR #107) —
    # `analysis.bars` respeita a troca de compasso do MIDI de origem, ao
    # contrario do bucket `ticks_per_beat*4` que o motor usava antes so pra
    # 4/4. Mesmo canal de `context.parameters`, chave separada (`bars`).
    bar_windows = _analysis_bar_windows(analysis, pm)
    # `drums.ghost_notes` cai no default declarado (densidade=5/10,
    # `tools/techniques/engine.py::_apply_drums_ghost_notes`) sempre que um
    # tick de nota de bateria nao esta coberto por nenhuma janela de
    # `plan.sections` — cauda antes da 1a secao, depois da ultima, OU
    # BURACO NO MEIO entre duas secoes nao adjacentes. A suposicao so
    # importa relatar quando a tecnica de fato foi autorizada nesta musica E
    # o plano nao declara `density` explicito (`bar_fraction` consulta o
    # override explicito ANTES de qualquer janela de secao, entao o default
    # nunca e alcancado nesse caso — achado do Codex no PR #107, primeira
    # rodada). Segunda rodada do Codex no PR #107: o aviso so pode olhar as
    # notas dos ALVOS DE FATO despachados para a familia `drums`
    # (`plan.edits` com `profile=drums` que casam track, mais elementos
    # gerados que mapeiam pra familia `drums`) — nunca "o arquivo inteiro",
    # que tanto dispara sem alvo nenhum de bateria quanto falso-positiva com
    # uma track NAO-bateria (baixo, teclas) que se estende alem das secoes.
    # `drum_target_ticks` acumula os ticks REAIS por familia enquanto o
    # pipeline roda: a parte de `plan.edits` abaixo, a parte de elemento
    # gerado dentro do loop logo a seguir.
    ghost_notes_default_path = _drums_ghost_notes_authorized(
        plan
    ) and not _drums_ghost_notes_has_explicit_density(plan)
    drum_target_ticks: list[int] = (
        _drum_channel9_note_on_ticks(_edit_drum_target_tracks(out_mid, plan))
        if ghost_notes_default_path
        else []
    )
    # Cota por compasso COMPARTILHADA entre TODOS os despachos de tecnica de
    # bateria de UMA chamada de `render()` — achado do Codex no PR #107
    # (issue #45, segunda rodada): duas edits distintas de bateria, ou uma
    # edit de bateria mais um elemento de bateria gerado, cada um caindo em
    # `_run_style_pipeline` numa chamada SEPARADA, podiam somar mais que
    # `max_per_bar` ghosts no mesmo compasso no arquivo final porque cada
    # chamada recriava `bar_counts`/`bar_targets` do zero
    # (`tools/techniques/engine.py::_apply_drums_ghost_notes`). Um dict por
    # `render()` (nunca global/modulo — resetado a cada chamada, preserva
    # determinismo entre renders separados) resolve isso; ver
    # `_style_technique_parameters`.
    drum_bar_quota: dict[str, dict[int, int]] = {}
    # Ordem: primeiro `apply_edits` (humanizacao por profile), depois o motor
    # de tecnicas nas mesmas tracks da origem. Assim as tecnicas de estilo
    # alcancam a bateria real do usuario — sem esse passo, `style.<familia>`
    # so afeta elemento gerado, e o produto nao entrega o que promete.
    edit_technique_warnings, edit_applied_techniques = (
        _apply_style_techniques_to_edit_tracks(
            out_mid, plan=plan, index=style_index,
            tuning_by_family=tuning_by_family,
            section_windows=section_windows,
            bar_windows=bar_windows,
            drum_bar_quota=drum_bar_quota,
        )
    )
    warnings.extend(edit_technique_warnings)
    for e in plan.elements:
        warnings.extend(_unsupported_pattern_warnings(e))
        layer_warning = _strings_tutti_layer_warning(e)
        if layer_warning is not None:
            warnings.append(layer_warning)
        inst = e.instrument or {}
        report_entry = ElementRationale(
            element_id=e.id,
            role=e.role,
            rationale=e.rationale or "",
            plugin=str(inst.get("plugin", "")),
            preset=str(inst.get("preset", "")),
            verified=bool(inst.get("verified", False)),
            layers=e.layers,
            sections=tuple(e.sections),
            rendered=False,
        )
        role_renderer = _ROLE_RENDERERS.get(e.role)
        if role_renderer is not None:
            midi_tracks, rendered = role_renderer.render(
                e, plan, analysis, pm, role_renderer.channel,
            )
            element_style_family = _style_family_for_role(e.role)
            if ghost_notes_default_path and element_style_family == "drums":
                # Notas do elemento gerado ANTES do despacho de tecnica —
                # exatamente o que `drums.ghost_notes` vai receber como
                # "notas ja existentes" nessa track.
                drum_target_ticks.extend(
                    _drum_channel9_note_on_ticks(midi_tracks)
                )
            (
                midi_tracks,
                technique_warnings,
                technique_applied,
                element_techniques,
            ) = _apply_style_techniques_to_tracks(
                midi_tracks,
                plan=plan,
                family=element_style_family,
                tool_target=_tool_target_for_element(e),
                ticks_per_beat=out_mid.ticks_per_beat,
                midi_type=out_mid.type,
                index=style_index,
                tuning_by_family=tuning_by_family,
                section_windows=section_windows,
                bar_windows=bar_windows,
                drum_bar_quota=drum_bar_quota,
            )
            warnings.extend(technique_warnings)
            _stamp_element_tracks(midi_tracks, e, techniques=element_techniques)
            out_mid.tracks.extend(midi_tracks)
            rendered_tracks.extend(
                _rendered_tracks_from_midi_tracks(
                    e,
                    midi_tracks,
                    pm,
                    ticks_per_beat=out_mid.ticks_per_beat,
                    midi_type=out_mid.type,
                )
                if technique_applied
                else rendered
            )
            report_entry.rendered = True
        else:
            msg = (
                f"role {e.role!r} not implemented in R1 motor — element skipped "
                f"(no track emitted)"
            )
            report_entry.note = msg
            warnings.append(f"{e.id}: {msg}")
        element_reports.append(report_entry)

    if drum_target_ticks and _drum_ticks_outside_section_windows(
        section_windows, drum_target_ticks
    ):
        warnings.append(
            "drums.ghost_notes: trecho do MIDI de origem fora de "
            "plan.sections declaradas — densidade de ghost assumida no "
            "default (densidade=5/10, sem multiplicador de kind) nesse "
            "trecho"
        )

    # Carimba as tracks de `plan.edits` com role, tecnicas aplicadas e
    # (quando declarada) sugestao de plugin/preset. Depois de `apply_edits` e
    # do motor de tecnicas — assim o carimbo reflete o que o arranjador de
    # fato fez naquela track.
    _stamp_edit_tracks(
        out_mid, plan=plan, index=style_index,
        applied_techniques=edit_applied_techniques,
    )

    warnings.extend(check_tutti_uniqueness(plan))

    harmony_issues = validate_harmony(rendered_tracks, plan, analysis)
    placement_issues = validate_placement(rendered_tracks, plan, analysis)
    artifice_issues = validate_artifice(rendered_tracks, plan, analysis)
    persona_issues = validate_persona(
        plan, rendered_tracks, analysis, strict=strict_persona,
    )
    # Anticopia (AC-16): so roda quando ha corpus. AC-15 (structural — sem
    # sequencia de nota em `style`) ja foi barrado por `plan.validate` acima.
    # Aceita paths (str/Path) OU `ReferenceSequence` ja extraidas — para
    # testes determinsticos sem tocar filesystem.
    corpus_sequences: list[ReferenceSequence] = []
    if reference_corpus is not None:
        materialized = list(reference_corpus)
        if all(isinstance(item, ReferenceSequence) for item in materialized):
            corpus_sequences = list(materialized)   # type: ignore[arg-type]
        else:
            corpus_sequences = load_reference_sequences(materialized)   # type: ignore[arg-type]
    anticopy_issues = validate_anticopy(
        rendered_tracks, plan, analysis, corpus=corpus_sequences or None,
    )
    # AC-14: roda sobre o `plan` (ja filtrado por `only`, se pedido) e o
    # OUTPUT COMPLETO — `rendered_tracks` (elementos gerados) MAIS as
    # tracks de origem/edicao (`out_mid.tracks[:source_track_count]`, ja
    # com `apply_edits`/tecnicas de estilo aplicados in-place) — ver
    # docstring de `tools.validators.transitions`, secao "Fronteiras vem de
    # tracks de origem tambem" (issue #24 finding 1).
    source_rendered_tracks = _rendered_tracks_from_source_tracks(
        list(out_mid.tracks[:source_track_count]),
        ticks_per_beat=out_mid.ticks_per_beat,
        midi_type=out_mid.type,
    )
    transition_issues = validate_transitions(
        rendered_tracks + source_rendered_tracks, plan, analysis,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_mid.save(str(out_path))

    return RenderReport(
        output_path=out_path,
        source_sha256=source_hash,
        seed=plan.seed,
        collision=collision_report,
        elements=element_reports,
        warnings=warnings,
        harmony_issues=harmony_issues,
        placement_issues=placement_issues,
        artifice_issues=artifice_issues,
        persona_issues=persona_issues,
        anticopy_issues=anticopy_issues,
        transition_issues=transition_issues,
        edits=edit_reports,
    )


# --- relatorio pretty-print (consumido pelo CLI de US-017) -----------------

def format_render_report(report: RenderReport) -> str:
    """Pretty-print do relatorio, com rationale por elemento (FR-22) e o
    resultado do validador de colisao."""
    lines = [
        f"Rendered: {report.output_path}",
        f"Source sha256: {report.source_sha256}",
        f"Seed: {report.seed}",
        "",
        "Elements:",
    ]
    for r in report.elements:
        marker = "✓" if r.verified else "?"
        status = "rendered" if r.rendered else "skipped"
        lines.append(
            f"  - {r.element_id} ({r.role}, {status}, layers={r.layers}): "
            f"{r.plugin} / {r.preset} {marker}"
        )
        if r.rationale:
            lines.append(f"      rationale: {r.rationale}")
        if r.note:
            lines.append(f"      note: {r.note}")
    if report.collision.relocations:
        lines.append("")
        lines.append("Collision relocations:")
        for rel in report.collision.relocations:
            lines.append(
                f"  - {rel.element_id} @ {rel.section_label}: "
                f"{rel.from_register} -> {rel.to_register} ({rel.reason})"
            )
    if report.collision.warnings:
        lines.append("")
        lines.append("Collision warnings:")
        for w in report.collision.warnings:
            lines.append(
                f"  - {' + '.join(w.element_ids)} @ {w.section_label} "
                f"bars {w.bar_range} ({w.band}): {w.reason}"
            )
    if report.edits:
        lines.append("")
        lines.append("Edits applied:")
        for ed in report.edits:
            tracks_label = (
                "track"
                if ed.tracks_matched == 1
                else "tracks"
            )
            lines.append(
                f"  - {ed.track} (profile={ed.profile}, intensity={ed.intensity:.2f}): "
                f"{ed.notes_touched} notes across {ed.tracks_matched} {tracks_label}, "
                f"mean offset {ed.mean_offset_ms:+.2f}ms"
            )
    if report.warnings:
        lines.append("")
        lines.append("Render warnings:")
        for w in report.warnings:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append(format_issues(report.harmony_issues))
    lines.append("")
    lines.append(format_placement_issues(report.placement_issues))
    lines.append("")
    lines.append(format_artifice_issues(report.artifice_issues))
    lines.append("")
    lines.append(format_persona_issues(report.persona_issues))
    lines.append("")
    lines.append(format_anticopy_issues(report.anticopy_issues))
    lines.append("")
    lines.append(format_transition_issues(report.transition_issues))
    return "\n".join(lines)


__all__ = [
    "DEFAULT_OUTPUT_SUFFIX",
    "ONLY_CATEGORIES",
    "ElementRationale",
    "PAD_CHANNEL",
    "RenderError",
    "RenderReport",
    "SUPPORTED_ROLES",
    "format_render_report",
    "render",
    "sha256_of_file",
]
