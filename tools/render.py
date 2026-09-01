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
from collections.abc import Callable
from dataclasses import dataclass, field
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
    _reject_style_techniques_without_brief,
    load,
    load_brief_instrument_tuning,
    normalize_style_defaults,
    validate_edits_against_midi,
)
from .plan import (
    validate as validate_plan,
)
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
DRUMS_CHANNEL = 9
"""Canal MIDI 10 (indice 0-based 9) — convencao General MIDI de percussao,
a mesma que Superior Drummer/Addictive Drums e qualquer DAW esperam para
reconhecer a track como kit em vez de instrumento melodico."""
HAT_ELEC_CHANNEL = 0
SUB_CHANNEL = 0
SUB_DROP_CHANNEL = 0
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
"""Nem bateria nem baixo (issue #20) consomem `element.pattern` nesta
rodada — todo controle vem de `element.register`/`energy` da secao. Campo
declarado em `pattern` para esses roles vira aviso de nao-suportado, mesma
politica do pad."""
HAT_ELEC_PATTERN_FIELDS: frozenset[str] = frozenset({"pattern_mode"})
SUB_PATTERN_FIELDS: frozenset[str] = frozenset({"follow"})
SUB_DROP_PATTERN_FIELDS: frozenset[str] = frozenset()

# Formato do carimbo de plugin/preset em meta-evento SMF de texto (0x01).
# Exemplo literal (documentado em docs/arquitetura.md):
#   "midi-arranger v1|role=drums|plugin=Superior Drummer|preset=Metal Kit|
#    verified=true|techniques=[drums.accent_hierarchy,drums.ghost_notes]"
# Coexiste com meta 0x03 (track_name); nunca substitui.
STAMP_PREFIX = "midi-arranger v1"


# --- excecoes ---------------------------------------------------------------

class RenderError(Exception):
    """Falha de render que nao pode ser silenciada."""


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
    if role in HAT_ELEC_ROLES:
        return HAT_ELEC_PATTERN_FIELDS
    if role in SUB_ROLES:
        return SUB_PATTERN_FIELDS
    if role in SUB_DROP_ROLES:
        return SUB_DROP_PATTERN_FIELDS
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
) -> dict[str, Any]:
    parameters: dict[str, Any] = dict(style_parameters)
    if density is not None:
        parameters["density"] = float(density)
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
) -> tuple[mido.MidiFile, list[str]]:
    """Roda cada tecnica de `style.<family>` sobre `current` em sequencia.

    Comum aos dois caminhos que aplicam estilo: tracks recem-renderizadas por
    elemento e tracks do MIDI de origem nomeadas em `plan.edits`. Warnings
    ganham prefixo com o nome da track quando o alvo e uma edit, para o
    relatorio identificar de qual track de origem partiu o aviso.
    """

    warnings: list[str] = []
    warning_prefix = f"edit {edit_track!r}: " if edit_track is not None else ""
    for technique in style.techniques:
        canonical = _canonical_style_technique(index, family, technique.name)
        try:
            applied: TechniqueApplyResult = apply_technique_with_warnings(
                canonical,
                current,
                seed=_style_technique_seed(
                    plan.seed, family, canonical, tool_target,
                    edit_track=edit_track,
                ),
                parameters=_style_technique_parameters(
                    style.parameters,
                    technique.density,
                    technique.style,
                    tuning,
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
    return current, warnings


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
) -> tuple[list[mido.MidiTrack], list[str], bool]:
    """Aplica tecnicas de `style.<family>` sobre tracks recem-renderizadas.

    As tracks do MIDI de origem nao entram aqui — quando uma edit aponta para
    uma track existente, o caminho e `_apply_style_techniques_to_edit_tracks`,
    que roda depois de `apply_edits` e antes do render por elemento.
    """

    if family is None or not tracks or not plan.style:
        return tracks, [], False
    style = plan.style.get(family)
    if style is None or not style.techniques:
        return tracks, [], False
    if index is None:
        raise RenderError("internal error: missing techniques index for style render")

    current = _tracks_as_midi(
        tracks,
        ticks_per_beat=ticks_per_beat,
        midi_type=midi_type,
    )
    current, warnings = _run_style_pipeline(
        current,
        plan=plan,
        family=family,
        style=style,
        tool_target=tool_target,
        index=index,
        tuning=(tuning_by_family or {}).get(family),
    )
    return list(current.tracks), warnings, True


def _apply_style_techniques_to_edit_tracks(
    out_mid: mido.MidiFile,
    *,
    plan: ArrangementPlan,
    index: TechniqueIndex | None,
    tuning_by_family: dict[str, tuple[int, ...]] | None = None,
) -> list[str]:
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
    """

    if not plan.edits or not plan.style:
        return []

    name_to_indices: dict[str, list[int]] = {}
    for idx, tr in enumerate(out_mid.tracks):
        name = track_name(tr)
        if name:
            name_to_indices.setdefault(name, []).append(idx)

    warnings: list[str] = []
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
        working, edit_warnings = _run_style_pipeline(
            working,
            plan=plan,
            family=family,
            style=style,
            tool_target=_tool_target_for_edit(edit),
            index=index,
            edit_track=edit.track,
            tuning=(tuning_by_family or {}).get(family),
        )
        warnings.extend(edit_warnings)
        for slot, new_track in zip(
            target_indices, working.tracks, strict=True,
        ):
            out_mid.tracks[slot] = new_track
    return warnings


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
) -> None:
    """Carimba plugin/preset/role/verified/techniques em cada track de `plan.edits`.

    Faz o mapa `edit.track` -> tracks do MIDI final apenas para as tracks
    nomeadas em `plan.edits` — tracks nao declaradas ficam byte-identicas ao
    source por definicao (ver AGENTS.md), e nao recebem carimbo.
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
        if family is not None and plan.style is not None:
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
        suggested = edit.suggested_instrument or {}
        suggested_plugin = suggested.get("plugin") if suggested else None
        suggested_preset = suggested.get("preset") if suggested else None
        suggested_verified = bool(suggested.get("verified", False)) if suggested else False
        stamp = _format_stamp(
            role=edit.profile,
            plugin=None,
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
) -> RenderReport:
    """Renderiza `plan` sobre o MIDI de origem em um arquivo novo.

    Args:
      plan: `ArrangementPlan` ja carregado ou caminho para `arrangement-plan.json`.
      output_path: caminho de saida. Default: `~/Desktop/<name>_arranged.mid`.
      source_path: override do caminho de origem. Default: `plan.source_midi.path`.

    Raises:
      RenderError: source inexistente, output apontaria para o source,
        elemento pad sem instrument.plugin/preset, OU tecnica de
        `style.<familia>.techniques[]` fora de
        `brief.style.<familia>.authorized_techniques` (a barreira do
        render roda antes de `validate_plan`, entao violacao de
        autorizacao vira `RenderError`, nao `PlanValidationError`).
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
    validate_plan(plan, plan_dir)
    plan = normalize_style_defaults(plan)

    src = Path(source_path).expanduser() if source_path else _resolve_source_path(plan)
    if not src.exists():
        raise RenderError(f"source MIDI not found: {src}")

    out_path = Path(output_path).expanduser() if output_path else _default_output_path(src)
    if out_path.resolve() == src.resolve():
        raise RenderError(f"output would overwrite source: {out_path}")

    source_hash = sha256_of_file(src)
    warnings: list[str] = _style_confidence_warnings(plan)
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
    # Ordem: primeiro `apply_edits` (humanizacao por profile), depois o motor
    # de tecnicas nas mesmas tracks da origem. Assim as tecnicas de estilo
    # alcancam a bateria real do usuario — sem esse passo, `style.<familia>`
    # so afeta elemento gerado, e o produto nao entrega o que promete.
    warnings.extend(
        _apply_style_techniques_to_edit_tracks(
            out_mid, plan=plan, index=style_index,
            tuning_by_family=tuning_by_family,
        )
    )
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
            (
                midi_tracks,
                technique_warnings,
                technique_applied,
            ) = _apply_style_techniques_to_tracks(
                midi_tracks,
                plan=plan,
                family=_style_family_for_role(e.role),
                tool_target=_tool_target_for_element(e),
                ticks_per_beat=out_mid.ticks_per_beat,
                midi_type=out_mid.type,
                index=style_index,
                tuning_by_family=tuning_by_family,
            )
            warnings.extend(technique_warnings)
            element_family = _style_family_for_role(e.role)
            element_techniques: tuple[str, ...] = ()
            if (
                technique_applied
                and element_family is not None
                and plan.style is not None
                and style_index is not None
            ):
                family_style = plan.style.get(element_family)
                if family_style is not None:
                    element_techniques = tuple(
                        _canonical_style_technique(
                            style_index, element_family, tech.name,
                        )
                        for tech in family_style.techniques
                    )
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

    # Carimba as tracks de `plan.edits` com role, tecnicas aplicadas e
    # (quando declarada) sugestao de plugin/preset. Depois de `apply_edits` e
    # do motor de tecnicas — assim o carimbo reflete o que o arranjador de
    # fato fez naquela track.
    _stamp_edit_tracks(out_mid, plan=plan, index=style_index)

    warnings.extend(check_tutti_uniqueness(plan))

    harmony_issues = validate_harmony(rendered_tracks, plan, analysis)
    placement_issues = validate_placement(rendered_tracks, plan, analysis)
    artifice_issues = validate_artifice(rendered_tracks, plan, analysis)
    persona_issues = validate_persona(
        plan, rendered_tracks, analysis, strict=strict_persona,
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
    return "\n".join(lines)


__all__ = [
    "DEFAULT_OUTPUT_SUFFIX",
    "ElementRationale",
    "PAD_CHANNEL",
    "RenderError",
    "RenderReport",
    "SUPPORTED_ROLES",
    "format_render_report",
    "render",
    "sha256_of_file",
]
