"""Fachada de tools sobre o maquinario deterministico.

Define o `Tool` de cada capacidade do modulo `tools/*`, registra no registry
global, e faz a ponte entre o payload JSON e a API Python subjacente.

Regras da fachada:
- NAO muda comportamento do maquinario. So embrulha.
- Levanta `ToolError` com codigo estavel e `path` apontando o campo em erro
  de dados — o agente precisa poder agir sobre o erro.
- Toda saida obedece ao `output_schema` declarado. A validacao roda no
  registry (`registry.call`) inclusive em producao — bug de tool aparece la.
- Descricoes sao PROMPT: dizem o que a tool faz, quando usar, quando NAO
  usar. Nao sao docstring de API.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pretty_midi

from . import analyze as analyze_mod
from . import plan as plan_mod
from . import plugins as plugins_mod
from . import render as render_mod
from . import sections as sections_mod
from . import techniques as techniques_mod
from .brief_schema import BRIEF_VALIDATE_TOOL
from .constants import REGISTER_BANDS
from .plan import (
    ARTICULATIONS,
    EDIT_INTENSITY_MAX,
    EDIT_INTENSITY_MIN,
    EDIT_PROFILES,
    ENERGY_AXES,
    ENERGY_MAX,
    ENERGY_MIN,
    HARMONY_MODES,
    MIDI_PITCH_MAX,
    MIDI_PITCH_MIN,
    PROTAGONISTS,
    ROUTES,
    SCHEMA_VERSION,
    SECTION_KINDS,
    SECTION_SOURCES,
    STYLE_CONFIDENCE_LEVELS,
    STYLE_FAMILIES,
    ArrangementPlan,
    PlanValidationError,
    SourceMidi,
)
from .registry import Tool, ToolError, register
from .tracks import TrackNameError, name_for_element
from .validators import (
    RenderedNote,
    RenderedTrack,
    validate_artifice,
    validate_collisions,
    validate_harmony,
    validate_persona,
    validate_placement,
)

KEY_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


# --- helpers de IO ---------------------------------------------------------

def _resolve_midi(path_str: str) -> Path:
    """Valida existencia/permissao/formato do MIDI e devolve o Path resolvido.

    Erros viram `ToolError` com codigo dedicado. Nunca stack trace.
    `midi_path` vazio nao chega aqui: input_schema exige minLength=1.
    """
    path = Path(path_str).expanduser()
    if not path.exists():
        raise ToolError(
            "E_MIDI_NOT_FOUND",
            f"arquivo MIDI nao encontrado: {path_str}",
            path="midi_path",
        )
    if not path.is_file():
        raise ToolError(
            "E_MIDI_NOT_FILE",
            f"caminho nao e arquivo: {path_str}",
            path="midi_path",
        )
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except PermissionError:
        raise ToolError(
            "E_MIDI_PERMISSION",
            f"sem permissao para ler {path_str}",
            path="midi_path",
        ) from None
    except OSError as exc:
        raise ToolError(
            "E_MIDI_IO",
            f"erro lendo MIDI {path_str}: {exc}",
            path="midi_path",
        ) from None
    if head != b"MThd":
        raise ToolError(
            "E_MIDI_INVALID",
            f"arquivo nao e MIDI valido (header != 'MThd'): {path_str}",
            path="midi_path",
        )
    return path


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --- schemas compartilhados ------------------------------------------------

_ENERGY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        axis: {"type": "integer", "minimum": ENERGY_MIN, "maximum": ENERGY_MAX}
        for axis in ENERGY_AXES
    },
    "required": list(ENERGY_AXES),
}


def _plan_style_technique_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "density": {
                "oneOf": [
                    {"type": "null"},
                    {"type": "number", "minimum": 0.0, "maximum": 1.0},
                ],
            },
            "rationale": {"type": ["string", "null"]},
        },
        "required": ["name"],
        "additionalProperties": False,
    }


def _plan_family_style_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "reference": {"type": "string", "minLength": 1},
            "researched_at": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
            "confidence": {"enum": list(STYLE_CONFIDENCE_LEVELS)},
            "techniques": {
                "type": "array",
                "items": _plan_style_technique_schema(),
            },
            "parameters": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": [
            "reference",
            "researched_at",
            "sources",
            "confidence",
            "techniques",
            "parameters",
        ],
        "additionalProperties": False,
    }


def _plan_schema() -> dict[str, Any]:
    """Schema JSON estrito do ArrangementPlan.

    Campos opacos ao maquinario (pattern, dynamics, instrument) declaram
    `additionalProperties: true` porque as chaves dependem do role e sao
    validadas por plan.validate + render, nao por JSON Schema.
    """
    return {
        "type": "object",
        "properties": {
            "version": {"type": "integer", "const": SCHEMA_VERSION},
            "seed": {"type": "integer"},
            "source_midi": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "sha256": {"type": "string", "minLength": 1},
                    "tempo": {"type": ["number", "null"]},
                    "key": {"type": ["string", "null"]},
                    "bars": {"type": ["integer", "null"]},
                },
                "required": ["path", "sha256"],
            },
            "route": {"enum": list(ROUTES)},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "style": {
                "type": "object",
                "properties": {
                    family: _plan_family_style_schema()
                    for family in STYLE_FAMILIES
                },
                "additionalProperties": False,
            },
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "minLength": 1},
                        "kind": {"enum": list(SECTION_KINDS)},
                        "start_bar": {"type": "integer", "minimum": 0},
                        "end_bar": {"type": "integer", "minimum": 0},
                        "source": {"enum": list(SECTION_SOURCES)},
                        "protagonist": {
                            "type": ["string", "null"],
                            "enum": [*PROTAGONISTS, None],
                        },
                        "energy": {
                            "oneOf": [{"type": "null"}, _ENERGY_SCHEMA],
                        },
                    },
                    "required": [
                        "label", "kind", "start_bar", "end_bar", "source",
                        "protagonist", "energy",
                    ],
                },
            },
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "role": {"type": "string", "minLength": 1},
                        "sections": {"type": "array", "items": {"type": "string"}},
                        "register": {
                            "type": "array",
                            "items": {
                                "type": "integer",
                                "minimum": MIDI_PITCH_MIN,
                                "maximum": MIDI_PITCH_MAX,
                            },
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "layers": {"type": "integer", "minimum": 1},
                        "sync_role": {"type": "string"},
                        "articulation": {"enum": list(ARTICULATIONS)},
                        "harmony": {"enum": list(HARMONY_MODES)},
                        "pattern": {
                            "oneOf": [
                                {"type": "null"},
                                {"type": "object", "additionalProperties": True},
                            ],
                        },
                        "degrees": {
                            "oneOf": [
                                {"type": "null"},
                                {"type": "array", "items": {"type": "integer"}},
                            ],
                        },
                        "dynamics": {
                            "oneOf": [
                                {"type": "null"},
                                {"type": "object", "additionalProperties": True},
                            ],
                        },
                        "instrument": {
                            "oneOf": [
                                {"type": "null"},
                                {"type": "object", "additionalProperties": True},
                            ],
                        },
                        "rationale": {"type": ["string", "null"]},
                        "is_protagonist": {"type": "boolean"},
                    },
                    "required": [
                        "id", "role", "sections", "register", "layers",
                        "sync_role", "articulation", "harmony",
                    ],
                },
            },
            "transitions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "at_bar": {"type": "integer", "minimum": 0},
                        "from_section": {"type": "string"},
                        "to_section": {"type": "string"},
                        "dimensions_changed": {
                            "type": "array", "items": {"type": "string"},
                        },
                        "elements": {
                            "type": "array", "items": {"type": "string"},
                        },
                        "technique": {"type": "string"},
                    },
                    "required": [
                        "at_bar", "from_section", "to_section",
                        "dimensions_changed", "elements", "technique",
                    ],
                },
            },
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "track": {"type": "string", "minLength": 1},
                        "profile": {"enum": list(EDIT_PROFILES)},
                        "intensity": {
                            "type": "number",
                            "minimum": EDIT_INTENSITY_MIN,
                            "maximum": EDIT_INTENSITY_MAX,
                        },
                    },
                    "required": ["track", "profile", "intensity"],
                },
            },
        },
        "required": [
            "version", "seed", "source_midi", "route", "sections", "elements",
        ],
    }


# --- analyze ---------------------------------------------------------------

ANALYZE_DESCRIPTION = (
    "Le um arquivo MIDI e extrai secoes, tonalidade, acordes por compasso, "
    "densidade por compasso, ocupacao de registro por banda e ancoras "
    "ritmicas (kick, snare, unisono de guitarra), alem de tempo, formula de "
    "compasso e a lista de tracks com nome e range. Use antes de qualquer "
    "decisao de arranjo. Nao modifica o arquivo. Se qualquer secao vier como "
    "'inferred', confirme o mapa com o usuario ANTES de prosseguir — o mapa "
    "inferido pode nao refletir a divisao real da musica."
)


def _tempo_of(pm: pretty_midi.PrettyMIDI) -> float:
    """Tempo inicial em BPM. Usa a primeira mudanca de tempo quando existe."""
    times, tempos = pm.get_tempo_changes()
    if len(tempos):
        return float(tempos[0])
    return 120.0  # fallback GM


def _time_signature_of(pm: pretty_midi.PrettyMIDI) -> tuple[int, int]:
    if pm.time_signature_changes:
        ts = pm.time_signature_changes[0]
        return int(ts.numerator), int(ts.denominator)
    return 4, 4


def _track_from_instrument(inst: pretty_midi.Instrument, name: str) -> dict[str, Any]:
    pitches = [n.pitch for n in inst.notes]
    return {
        "name": name,
        "note_count": len(inst.notes),
        "pitch_min": min(pitches) if pitches else None,
        "pitch_max": max(pitches) if pitches else None,
        "is_drum": bool(inst.is_drum),
    }


def _analyze_impl(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    src = _resolve_midi(payload["midi_path"])
    try:
        pm = pretty_midi.PrettyMIDI(str(src))
    except (OSError, ValueError, EOFError, KeyError) as exc:
        raise ToolError(
            "E_MIDI_PARSE",
            f"nao foi possivel carregar o MIDI: {exc}",
            path="midi_path",
        ) from None

    a = analyze_mod.analyze(str(src))
    secs = sections_mod.read_sections(str(src))

    tempo = _tempo_of(pm)
    num, den = _time_signature_of(pm)

    bars_out: list[dict[str, Any]] = []
    for bar in a.bars:
        occ = analyze_mod.register_occupancy(bar)
        register_occupancy = {
            band: sorted(tracks) for band, tracks in occ.items()
        }
        chord_out = (
            None if bar.chord is None
            else {"root": int(bar.chord.root), "quality": bar.chord.quality}
        )
        bars_out.append({
            "index": int(bar.index),
            "start_s": float(bar.start),
            "end_s": float(bar.end),
            "chord": chord_out,
            "note_count": int(sum(bar.notes_per_track.values())),
            "register_occupancy": register_occupancy,
        })

    tracks_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, inst in enumerate(pm.instruments):
        name = inst.name.strip() if inst.name and inst.name.strip() else f"track_{i}"
        # Preserva duplicatas de nome com sufixo estavel — indice de origem.
        if name in seen:
            name = f"{name}#{i}"
        seen.add(name)
        tracks_out.append(_track_from_instrument(inst, name))

    sections_out: list[dict[str, Any]] = [
        {
            "label": s.label,
            "kind": s.kind,
            "start_bar": int(s.start_bar),
            "end_bar": int(s.end_bar),
            "source": s.source,
        }
        for s in secs
    ]

    data = {
        "midi_path": str(src),
        "sha256": _sha256_of_file(src),
        "tempo": tempo,
        "time_signature": {"numerator": num, "denominator": den},
        "key_root": int(a.key_root),
        "key_name": KEY_NAMES[a.key_root % 12],
        "sections": sections_out,
        "bars": bars_out,
        "tracks": tracks_out,
        "rhythmic_anchors": {
            "kick_positions_s": [float(x) for x in a.kick_positions],
            "snare_positions_s": [float(x) for x in a.snare_positions],
            "guitar_unison_positions_s": [float(x) for x in a.guitar_unison_positions],
        },
    }

    warnings: list[dict[str, Any]] = []
    inferred = [s.label for s in secs if s.source == "inferred"]
    if inferred:
        warnings.append({
            "code": "W_INFERRED_SECTIONS",
            "message": (
                f"{len(inferred)} secao(oes) vieram por heuristica, nao por marker "
                f"({inferred!r}); confirme o mapa antes de arranjar."
            ),
            "path": "sections",
        })
    return data, warnings


_BAR_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "start_s": {"type": "number"},
        "end_s": {"type": "number"},
        "chord": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "root": {"type": "integer", "minimum": 0, "maximum": 11},
                        "quality": {"type": "string"},
                    },
                    "required": ["root", "quality"],
                },
            ],
        },
        "note_count": {"type": "integer", "minimum": 0},
        "register_occupancy": {
            "type": "object",
            "properties": {
                band: {"type": "array", "items": {"type": "string"}}
                for band in REGISTER_BANDS
            },
            "required": list(REGISTER_BANDS),
        },
    },
    "required": ["index", "start_s", "end_s", "chord", "note_count", "register_occupancy"],
}


ANALYZE_TOOL = Tool(
    name="analyze",
    description=ANALYZE_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {"midi_path": {"type": "string", "minLength": 1}},
        "required": ["midi_path"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "midi_path": {"type": "string"},
            "sha256": {"type": "string"},
            "tempo": {"type": "number"},
            "time_signature": {
                "type": "object",
                "properties": {
                    "numerator": {"type": "integer", "minimum": 1},
                    "denominator": {"type": "integer", "minimum": 1},
                },
                "required": ["numerator", "denominator"],
            },
            "key_root": {"type": "integer", "minimum": 0, "maximum": 11},
            "key_name": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "kind": {"type": "string"},
                        "start_bar": {"type": "integer", "minimum": 0},
                        "end_bar": {"type": "integer", "minimum": 0},
                        "source": {"enum": ["marker", "inferred"]},
                    },
                    "required": ["label", "kind", "start_bar", "end_bar", "source"],
                },
            },
            "bars": {"type": "array", "items": _BAR_SCHEMA},
            "tracks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "note_count": {"type": "integer", "minimum": 0},
                        "pitch_min": {"type": ["integer", "null"]},
                        "pitch_max": {"type": ["integer", "null"]},
                        "is_drum": {"type": "boolean"},
                    },
                    "required": ["name", "note_count", "pitch_min", "pitch_max", "is_drum"],
                },
            },
            "rhythmic_anchors": {
                "type": "object",
                "properties": {
                    "kick_positions_s": {"type": "array", "items": {"type": "number"}},
                    "snare_positions_s": {"type": "array", "items": {"type": "number"}},
                    "guitar_unison_positions_s": {
                        "type": "array", "items": {"type": "number"},
                    },
                },
                "required": [
                    "kick_positions_s", "snare_positions_s",
                    "guitar_unison_positions_s",
                ],
            },
        },
        "required": [
            "midi_path", "sha256", "tempo", "time_signature", "key_root",
            "key_name", "sections", "bars", "tracks", "rhythmic_anchors",
        ],
    },
    func=_analyze_impl,
)


# --- plan.skeleton --------------------------------------------------------

PLAN_SKELETON_DESCRIPTION = (
    "Constroi um esqueleto de arrangement-plan a partir de um MIDI analisado. "
    "Preenche `source_midi` (path, sha256, tempo, key, bars), copia as secoes "
    "detectadas pelo analyze com defaults reservados (protagonist='texture', "
    "energy=5 em todos os eixos) e devolve elements/transitions/edits vazios. "
    "Use como ponto de partida quando o agente vai decidir o arranjo do zero. "
    "NAO invente elementos aqui — a criacao dos elementos e da rota e decisao "
    "do agente na fase seguinte. Se `output_path` for informado, o plano e "
    "gravado tambem em disco (`plan.dump`). Se nao, o plano so volta no envelope."
)

_DEFAULT_SEED = 0
_DEFAULT_ROUTE = "cinematica_emocional"
_DEFAULT_PROTAGONIST = "texture"
_DEFAULT_ENERGY = {axis: 5 for axis in ENERGY_AXES}


def _plan_skeleton_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    src = _resolve_midi(payload["midi_path"])
    seed = int(payload.get("seed", _DEFAULT_SEED))
    # route ja e validado contra o enum pelo input_schema — chegar aqui com
    # valor fora do vocabulario e impossivel via registry.call.
    route = payload.get("route", _DEFAULT_ROUTE)
    output_path = payload.get("output_path")

    try:
        pm = pretty_midi.PrettyMIDI(str(src))
    except (OSError, ValueError, EOFError, KeyError) as exc:
        raise ToolError(
            "E_MIDI_PARSE",
            f"nao foi possivel carregar o MIDI: {exc}",
            path="midi_path",
        ) from None

    secs = sections_mod.read_sections(str(src))
    a = analyze_mod.analyze(str(src))
    tempo = _tempo_of(pm)

    plan_sections: list[plan_mod.PlanSection] = []
    for s in secs:
        kind = s.kind if s.kind in SECTION_KINDS else "verse"
        plan_sections.append(plan_mod.PlanSection(
            label=s.label or f"section_{s.start_bar}",
            kind=kind,
            start_bar=int(s.start_bar),
            end_bar=int(s.end_bar),
            source=s.source,
            protagonist=_DEFAULT_PROTAGONIST,
            energy=dict(_DEFAULT_ENERGY),
        ))

    plan_obj = ArrangementPlan(
        version=SCHEMA_VERSION,
        seed=seed,
        source_midi=SourceMidi(
            path=str(src),
            sha256=_sha256_of_file(src),
            tempo=tempo,
            key=KEY_NAMES[a.key_root % 12],
            bars=len(a.bars),
        ),
        route=route,
        sections=plan_sections,
        elements=[],
        assumptions=[],
        transitions=[],
        edits=[],
    )

    # Roda o validador do dominio; skeleton nunca deve sair invalido, entao
    # levantar aqui e bug de fachada.
    _skeleton_warnings = plan_mod.validate(plan_obj)

    plan_dict = plan_mod.to_dict(plan_obj)

    written_path: str | None = None
    if output_path:
        out = Path(output_path).expanduser()
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(plan_dict, indent=2), encoding="utf-8")
        except OSError as exc:
            raise ToolError(
                "E_OUTPUT_WRITE",
                f"nao consegui escrever plano em {output_path}: {exc}",
                path="output_path",
            ) from None
        written_path = str(out)

    warnings: list[dict[str, Any]] = []
    inferred = [s.label for s in secs if s.source == "inferred"]
    if inferred:
        warnings.append({
            "code": "W_INFERRED_SECTIONS",
            "message": (
                f"{len(inferred)} secao(oes) vieram por heuristica; "
                f"confirme o mapa antes de arranjar."
            ),
            "path": "plan.sections",
        })
    for w in _skeleton_warnings:
        warnings.append({
            "code": "W_PLAN",
            "message": w,
            "path": "plan",
        })
    return {"plan": plan_dict, "output_path": written_path}, warnings


PLAN_SKELETON_TOOL = Tool(
    name="plan.skeleton",
    description=PLAN_SKELETON_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "midi_path": {"type": "string", "minLength": 1},
            "seed": {"type": "integer", "minimum": 0},
            "route": {"enum": list(ROUTES)},
            "output_path": {"type": ["string", "null"]},
        },
        "required": ["midi_path"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "plan": _plan_schema(),
            "output_path": {"type": ["string", "null"]},
        },
        "required": ["plan", "output_path"],
    },
    func=_plan_skeleton_impl,
)


# --- plan.validate --------------------------------------------------------

PLAN_VALIDATE_DESCRIPTION = (
    "Valida um arrangement-plan contra o schema, contra os vocabularios "
    "fechados (route/kind/protagonist/sync_role/articulation/harmony/edits) "
    "e contra o MIDI de origem (existencia de secoes, tracks que os edits "
    "referenciam). Cobre: schema JSON, vocabularios, secoes que existem no "
    "MIDI, register em 0-127, layers>=1, protagonista unico por secao, "
    "rationale nao vazio. Use SEMPRE antes de chamar render — plano invalido "
    "no render vira erro tarde. O plano pode ser passado inline em `plan` ou "
    "por caminho em `plan_path` — exatamente um dos dois."
)


def _load_plan_from_dict(data: dict[str, Any]) -> ArrangementPlan:
    """Constroi ArrangementPlan a partir de dict, com erros mapeados."""
    try:
        return plan_mod.from_dict(data)
    except PlanValidationError:
        raise
    except KeyError as exc:
        raise ToolError(
            "E_PLAN_FIELD_MISSING",
            f"campo obrigatorio ausente no plano: {exc.args[0]!r}",
            path=str(exc.args[0]),
        ) from None
    except (TypeError, ValueError) as exc:
        raise ToolError(
            "E_PLAN_FIELD_TYPE",
            f"tipo invalido no plano: {exc}",
        ) from None


def _read_plan_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Extrai plan_dict do payload: `plan` inline OU `plan_path`.

    Compartilhado por plan.validate, render e validate — os tres aceitam a
    mesma dupla de campos. Erros viram ToolError com codigo dedicado.
    """
    if ("plan" in payload) == ("plan_path" in payload):
        raise ToolError(
            "E_PLAN_INPUT",
            "informe exatamente um: `plan` inline OU `plan_path`",
            hint="use plan={} para inline ou plan_path='...' para caminho",
        )
    if "plan" in payload:
        return payload["plan"]

    pp = Path(payload["plan_path"]).expanduser()
    if not pp.exists():
        raise ToolError(
            "E_PLAN_FILE_NOT_FOUND",
            f"arquivo de plano nao encontrado: {pp}",
            path="plan_path",
        )
    try:
        data = json.loads(pp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError(
            "E_PLAN_JSON",
            f"plano em {pp} nao e JSON valido: {exc.msg}",
            path="plan_path",
        ) from None
    except OSError as exc:
        raise ToolError(
            "E_PLAN_FILE_IO",
            f"erro lendo plano {pp}: {exc}",
            path="plan_path",
        ) from None
    if not isinstance(data, dict):
        raise ToolError(
            "E_PLAN_JSON",
            f"plano precisa ser objeto JSON; recebi {type(data).__name__}",
            path="plan_path",
        )
    return data


def _plan_validate_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan_dict = _read_plan_dict(payload)
    src = _resolve_midi(payload["midi_path"])

    errors: list[dict[str, Any]] = []
    domain_warnings: list[str] = []
    plan_obj: ArrangementPlan | None = None

    try:
        plan_obj = _load_plan_from_dict(plan_dict)
        domain_warnings = plan_mod.validate(plan_obj)
    except PlanValidationError as exc:
        errors.append({"path": exc.path, "message": exc.message})
    except ToolError as exc:
        errors.append({"path": exc.path, "message": exc.message})

    # Checagem que precisa do MIDI: edits apontam para tracks reais.
    if plan_obj is not None and plan_obj.edits:
        try:
            import mido
            mid = mido.MidiFile(str(src))
        except (OSError, ValueError, EOFError, KeyError) as exc:
            raise ToolError(
                "E_MIDI_PARSE",
                f"nao foi possivel carregar o MIDI de origem: {exc}",
                path="midi_path",
            ) from None
        from .edits import collect_track_names
        try:
            plan_mod.validate_edits_against_midi(
                plan_obj, collect_track_names(list(mid.tracks)),
            )
        except PlanValidationError as exc:
            errors.append({"path": exc.path, "message": exc.message})

    data = {
        "valid": len(errors) == 0,
        "errors": errors,
    }
    warnings: list[dict[str, Any]] = [
        {"code": "W_PLAN", "message": w, "path": "plan"} for w in domain_warnings
    ]
    return data, warnings


PLAN_VALIDATE_TOOL = Tool(
    name="plan.validate",
    description=PLAN_VALIDATE_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "plan": _plan_schema(),
            "plan_path": {"type": "string", "minLength": 1},
            "midi_path": {"type": "string", "minLength": 1},
        },
        "required": ["midi_path"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["path", "message"],
                },
            },
        },
        "required": ["valid", "errors"],
    },
    func=_plan_validate_impl,
)


# --- helpers de reports para JSON ----------------------------------------

def _harmony_issue_to_dict(i) -> dict[str, Any]:
    return {
        "validator": "harmony",
        "severity": i.severity,
        "element_id": i.element_id,
        "track": i.track,
        "bar": int(i.bar),
        "pitch": int(i.pitch),
        "expected": i.expected,
        "message": i.message,
    }


def _placement_issue_to_dict(i) -> dict[str, Any]:
    return {
        "validator": "placement",
        "severity": i.severity,
        "element_id": i.element_id,
        "track": i.track,
        "bar": int(i.bar),
        "pitch": int(i.pitch),
        "section": i.section,
        "message": i.message,
    }


def _artifice_issue_to_dict(i) -> dict[str, Any]:
    return {
        "validator": "artifice",
        "severity": i.severity,
        "element_id": i.element_id,
        "track": i.track,
        "bar": int(i.bar),
        "pattern": i.pattern,
        "message": i.message,
    }


def _persona_issue_to_dict(i) -> dict[str, Any]:
    return {
        "validator": "persona",
        "severity": i.severity,
        "check": i.check,
        "section": i.section,
        "element_ids": list(i.element_ids),
        "message": i.message,
    }


def _collision_report_to_dict(rep) -> dict[str, Any]:
    return {
        "relocations": [
            {
                "element_id": r.element_id,
                "section_label": r.section_label,
                "from_register": list(r.from_register),
                "to_register": list(r.to_register),
                "reason": r.reason,
            }
            for r in rep.relocations
        ],
        "warnings": [
            {
                "element_ids": list(w.element_ids),
                "section_label": w.section_label,
                "bar_range": list(w.bar_range),
                "band": w.band,
                "reason": w.reason,
            }
            for w in rep.warnings
        ],
    }


_HARMONY_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "validator": {"const": "harmony"},
        "severity": {"type": "string"},
        "element_id": {"type": "string"},
        "track": {"type": "string"},
        "bar": {"type": "integer"},
        "pitch": {"type": "integer"},
        "expected": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": [
        "validator", "severity", "element_id", "track", "bar", "pitch",
        "expected", "message",
    ],
}

_PLACEMENT_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "validator": {"const": "placement"},
        "severity": {"type": "string"},
        "element_id": {"type": "string"},
        "track": {"type": "string"},
        "bar": {"type": "integer"},
        "pitch": {"type": "integer"},
        "section": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": [
        "validator", "severity", "element_id", "track", "bar", "pitch",
        "section", "message",
    ],
}

_ARTIFICE_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "validator": {"const": "artifice"},
        "severity": {"type": "string"},
        "element_id": {"type": "string"},
        "track": {"type": "string"},
        "bar": {"type": "integer"},
        "pattern": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": [
        "validator", "severity", "element_id", "track", "bar", "pattern",
        "message",
    ],
}

_PERSONA_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "validator": {"const": "persona"},
        "severity": {"type": "string"},
        "check": {"type": "string"},
        "section": {"type": "string"},
        "element_ids": {"type": "array", "items": {"type": "string"}},
        "message": {"type": "string"},
    },
    "required": [
        "validator", "severity", "check", "section", "element_ids", "message",
    ],
}

def _issues_schema_block() -> dict[str, Any]:
    """Reaproveita o bloco `harmony_issues/placement_issues/... + collision`.

    Compartilhado entre render e validate — ambos devolvem o mesmo core de
    relatorio dos validadores.
    """
    return {
        "collision": _COLLISION_SCHEMA,
        "harmony_issues": {"type": "array", "items": _HARMONY_ISSUE_SCHEMA},
        "placement_issues": {"type": "array", "items": _PLACEMENT_ISSUE_SCHEMA},
        "artifice_issues": {"type": "array", "items": _ARTIFICE_ISSUE_SCHEMA},
        "persona_issues": {"type": "array", "items": _PERSONA_ISSUE_SCHEMA},
    }


_ISSUES_REQUIRED = (
    "collision", "harmony_issues", "placement_issues",
    "artifice_issues", "persona_issues",
)


_COLLISION_SCHEMA = {
    "type": "object",
    "properties": {
        "relocations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "string"},
                    "section_label": {"type": "string"},
                    "from_register": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2, "maxItems": 2,
                    },
                    "to_register": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2, "maxItems": 2,
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "element_id", "section_label", "from_register",
                    "to_register", "reason",
                ],
            },
        },
        "warnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element_ids": {
                        "type": "array", "items": {"type": "string"},
                    },
                    "section_label": {"type": "string"},
                    "bar_range": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2, "maxItems": 2,
                    },
                    "band": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "element_ids", "section_label", "bar_range", "band", "reason",
                ],
            },
        },
    },
    "required": ["relocations", "warnings"],
}


# --- render ----------------------------------------------------------------

RENDER_DESCRIPTION = (
    "Renderiza um arrangement-plan sobre um MIDI de origem. Cada elemento do "
    "plano vira uma ou mais tracks novas nomeadas por convencao; as tracks "
    "originais saem NOTA A NOTA IDENTICAS (nada declarado para edit fica "
    "byte-identico no arquivo de saida). Roda todos os validadores (colisao, "
    "harmonia, placement, artifice, persona) e devolve o relatorio LEGIVEL "
    "POR MAQUINA — severidade por item — para o agente fechar o loop. Nunca "
    "sobrescreve o MIDI de origem: se `output_path` colidir com `midi_path`, "
    "erro. Mesmo plano + mesmo source + mesma seed produz arquivo byte-identico. "
    "Use apos plan.validate estar limpo — nao gaste ciclo renderizando plano invalido."
)


def _render_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan_dict = _read_plan_dict(payload)
    src = _resolve_midi(payload["midi_path"])
    source_hash_before = _sha256_of_file(src)

    try:
        plan_obj = plan_mod.from_dict(plan_dict)
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(
            "E_PLAN_INVALID",
            f"plano invalido: {exc}",
            path="plan",
        ) from None

    if "seed" in payload:
        plan_obj.seed = int(payload["seed"])

    strict_persona = bool(payload.get("strict_persona", False))
    output_path = payload.get("output_path")

    try:
        report = render_mod.render(
            plan_obj,
            output_path=output_path,
            source_path=str(src),
            strict_persona=strict_persona,
        )
    except plan_mod.PlanValidationError as exc:
        raise ToolError(
            "E_PLAN_INVALID",
            exc.message,
            path=exc.path,
        ) from None
    except render_mod.RenderError as exc:
        raise ToolError(
            "E_RENDER",
            str(exc),
        ) from None
    except TrackNameError as exc:
        raise ToolError(
            "E_TRACK_NAME",
            str(exc),
        ) from None

    source_hash_after = _sha256_of_file(src)
    if source_hash_after != source_hash_before:
        # Nao deveria ocorrer — render nao mexe no source. Se ocorreu, e bug
        # do proprio render; nao mascare em warning silencioso.
        raise ToolError(
            "E_SOURCE_MUTATED",
            f"hash do MIDI de origem mudou durante render "
            f"(antes={source_hash_before[:12]}..., depois={source_hash_after[:12]}...)",
            path="midi_path",
        )

    data = {
        "output_path": str(report.output_path),
        "source_sha256": report.source_sha256,
        "seed": int(report.seed),
        "elements": [
            {
                "element_id": e.element_id,
                "role": e.role,
                "rationale": e.rationale,
                "plugin": e.plugin,
                "preset": e.preset,
                "verified": bool(e.verified),
                "layers": int(e.layers),
                "sections": list(e.sections),
                "rendered": bool(e.rendered),
                "note": e.note,
            }
            for e in report.elements
        ],
        "collision": _collision_report_to_dict(report.collision),
        "harmony_issues": [_harmony_issue_to_dict(i) for i in report.harmony_issues],
        "placement_issues": [_placement_issue_to_dict(i) for i in report.placement_issues],
        "artifice_issues": [_artifice_issue_to_dict(i) for i in report.artifice_issues],
        "persona_issues": [_persona_issue_to_dict(i) for i in report.persona_issues],
        "edits": [
            {
                "track": ed.track,
                "profile": ed.profile,
                "intensity": float(ed.intensity),
                "notes_touched": int(ed.notes_touched),
                "mean_offset_ms": float(ed.mean_offset_ms),
                "tracks_matched": int(ed.tracks_matched),
            }
            for ed in report.edits
        ],
    }

    warnings: list[dict[str, Any]] = [
        {"code": "W_RENDER", "message": w, "path": ""} for w in report.warnings
    ]
    return data, warnings


_ELEMENT_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "element_id": {"type": "string"},
        "role": {"type": "string"},
        "rationale": {"type": "string"},
        "plugin": {"type": "string"},
        "preset": {"type": "string"},
        "verified": {"type": "boolean"},
        "layers": {"type": "integer"},
        "sections": {"type": "array", "items": {"type": "string"}},
        "rendered": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": [
        "element_id", "role", "rationale", "plugin", "preset", "verified",
        "layers", "sections", "rendered", "note",
    ],
}

_EDIT_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "track": {"type": "string"},
        "profile": {"type": "string"},
        "intensity": {"type": "number"},
        "notes_touched": {"type": "integer"},
        "mean_offset_ms": {"type": "number"},
        "tracks_matched": {"type": "integer"},
    },
    "required": [
        "track", "profile", "intensity", "notes_touched", "mean_offset_ms",
        "tracks_matched",
    ],
}


RENDER_TOOL = Tool(
    name="render",
    description=RENDER_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "midi_path": {"type": "string", "minLength": 1},
            "plan": _plan_schema(),
            "plan_path": {"type": "string", "minLength": 1},
            "output_path": {"type": ["string", "null"]},
            "seed": {"type": "integer", "minimum": 0},
            "strict_persona": {"type": "boolean"},
        },
        "required": ["midi_path"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "output_path": {"type": "string"},
            "source_sha256": {"type": "string"},
            "seed": {"type": "integer"},
            "elements": {"type": "array", "items": _ELEMENT_REPORT_SCHEMA},
            **_issues_schema_block(),
            "edits": {"type": "array", "items": _EDIT_REPORT_SCHEMA},
        },
        "required": [
            "output_path", "source_sha256", "seed", "elements",
            *_ISSUES_REQUIRED, "edits",
        ],
    },
    func=_render_impl,
)


# --- validate --------------------------------------------------------------

VALIDATE_DESCRIPTION = (
    "Roda os validadores (colisao, harmonia, placement, artifice, persona) "
    "sobre um MIDI JA renderizado, casando as tracks do arquivo aos elementos "
    "do plano pelo nome canonico. NAO reexecuta o render. Use para reauditar "
    "um arquivo antes de aceita-lo, ou para checar um arquivo que veio de "
    "outra origem contra o plano. Se o MIDI renderizado nao tiver as tracks "
    "esperadas de algum elemento, o warning correspondente aparece — o agente "
    "ve que o MIDI nao bate com o plano em vez de silenciar. Precisa do plano "
    "(inline ou por caminho), do MIDI de origem (`midi_path`) e do MIDI "
    "renderizado (`rendered_path`)."
)


def _rendered_tracks_from_midi(
    midi_path: str, plan_obj: plan_mod.ArrangementPlan,
) -> tuple[list[RenderedTrack], list[str]]:
    """Reconstroi RenderedTracks a partir de um MIDI renderizado.

    Casa por nome canonico usando `name_for_element`. Devolve tambem a lista
    de elementos cujas tracks nao foram encontradas — sinal de que o arquivo
    nao corresponde ao plano.
    """
    try:
        pm = pretty_midi.PrettyMIDI(midi_path)
    except (OSError, ValueError, EOFError, KeyError) as exc:
        raise ToolError(
            "E_MIDI_PARSE",
            f"nao foi possivel carregar o MIDI renderizado: {exc}",
            path="rendered_path",
        ) from None

    by_name: dict[str, list[pretty_midi.Instrument]] = {}
    for inst in pm.instruments:
        if inst.name:
            by_name.setdefault(inst.name, []).append(inst)

    rendered: list[RenderedTrack] = []
    missing: list[str] = []
    for el in plan_obj.elements:
        inst_meta = el.instrument or {}
        plugin = str(inst_meta.get("plugin", "")).strip()
        preset = str(inst_meta.get("preset", "")).strip()
        verified = bool(inst_meta.get("verified", False))
        if not plugin or not preset:
            missing.append(el.id)
            continue
        found_any = False
        for layer_index in range(el.layers):
            display = el.id if el.layers == 1 else f"{el.id} L{layer_index + 1}"
            try:
                tname = name_for_element(display, el.role, plugin, preset, verified)
            except TrackNameError:
                continue
            for inst in by_name.get(tname, []):
                notes = tuple(
                    RenderedNote(
                        pitch=int(n.pitch),
                        start_s=float(n.start),
                        end_s=float(n.end),
                        velocity=int(n.velocity),
                    )
                    for n in inst.notes
                )
                rendered.append(RenderedTrack(
                    element_id=el.id, track_name=tname, notes=notes,
                ))
                found_any = True
        if not found_any:
            missing.append(el.id)
    return rendered, missing


def _validate_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan_dict = _read_plan_dict(payload)
    src = _resolve_midi(payload["midi_path"])
    rendered_path = _resolve_midi(payload["rendered_path"])
    if rendered_path.resolve() == src.resolve():
        raise ToolError(
            "E_RENDERED_IS_SOURCE",
            f"rendered_path e o proprio midi_path: {src}",
            path="rendered_path",
        )

    try:
        plan_obj = plan_mod.from_dict(plan_dict)
        plan_mod.validate(plan_obj)
    except (KeyError, TypeError, ValueError, plan_mod.PlanValidationError) as exc:
        raise ToolError(
            "E_PLAN_INVALID",
            f"plano invalido: {exc}",
            path="plan",
        ) from None

    analysis = analyze_mod.analyze(str(src))
    rendered_tracks, missing = _rendered_tracks_from_midi(str(rendered_path), plan_obj)

    collision = validate_collisions(plan_obj)
    harmony = validate_harmony(rendered_tracks, plan_obj, analysis)
    placement = validate_placement(rendered_tracks, plan_obj, analysis)
    artifice = validate_artifice(rendered_tracks, plan_obj, analysis)
    persona = validate_persona(
        plan_obj, rendered_tracks, analysis,
        strict=bool(payload.get("strict_persona", False)),
    )

    data = {
        "rendered_path": str(rendered_path),
        "rendered_sha256": _sha256_of_file(rendered_path),
        "collision": _collision_report_to_dict(collision),
        "harmony_issues": [_harmony_issue_to_dict(i) for i in harmony],
        "placement_issues": [_placement_issue_to_dict(i) for i in placement],
        "artifice_issues": [_artifice_issue_to_dict(i) for i in artifice],
        "persona_issues": [_persona_issue_to_dict(i) for i in persona],
    }

    warnings: list[dict[str, Any]] = []
    if missing:
        warnings.append({
            "code": "W_ELEMENTS_MISSING_IN_RENDER",
            "message": (
                f"{len(missing)} elemento(s) do plano nao tem track correspondente "
                f"no MIDI renderizado: {missing!r}"
            ),
            "path": "rendered_path",
        })
    return data, warnings


VALIDATE_TOOL = Tool(
    name="validate",
    description=VALIDATE_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "midi_path": {"type": "string", "minLength": 1},
            "rendered_path": {"type": "string", "minLength": 1},
            "plan": _plan_schema(),
            "plan_path": {"type": "string", "minLength": 1},
            "strict_persona": {"type": "boolean"},
        },
        "required": ["midi_path", "rendered_path"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "rendered_path": {"type": "string"},
            "rendered_sha256": {"type": "string"},
            **_issues_schema_block(),
        },
        "required": [
            "rendered_path", "rendered_sha256", *_ISSUES_REQUIRED,
        ],
    },
    func=_validate_impl,
)


# --- plugins.scan ---------------------------------------------------------

PLUGINS_SCAN_DESCRIPTION = (
    "Inventaria os plugins AU/VST/VST3 instalados na maquina, com o papel "
    "sugerido para cada um (pad, arp, piano, strings, bass, sub, fx, drums, "
    "sampler, amp). Use antes de sugerir plugin/preset — e a UNICA tool que "
    "legitimamente varia entre maquinas. A saida declara `from_cache` para o "
    "agente saber se esta vendo dado fresco. Passe `cache_path` para reutilizar "
    "o cache; sem cache, sempre scan novo. `dirs` sobrescreve os diretorios "
    "varridos (util em teste). Nao modifica o sistema; nao acessa rede."
)


def _plugins_scan_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dirs_input = payload.get("dirs")
    if dirs_input is None:
        dirs = plugins_mod.DEFAULT_PLUGIN_DIRS
    else:
        dirs = tuple(Path(d).expanduser() for d in dirs_input)

    cache_path = payload.get("cache_path")
    from_cache = False

    if cache_path:
        cp = Path(cache_path).expanduser()
        # Reproduz load_or_scan com sinal de cache-hit: precisamos DIZER ao
        # agente se veio de cache, coisa que load_or_scan sozinha nao devolve.
        current_mtimes = plugins_mod._dir_mtimes(dirs)
        cached = plugins_mod._load_cache(cp)
        if cached is not None and cached.get("mtimes") == current_mtimes:
            try:
                plugins = [plugins_mod.Plugin.from_dict(p) for p in cached["plugins"]]
                from_cache = True
            except (KeyError, TypeError):
                plugins = plugins_mod.scan(dirs)
                plugins_mod._write_cache(cp, current_mtimes, plugins)
        else:
            plugins = plugins_mod.scan(dirs)
            plugins_mod._write_cache(cp, current_mtimes, plugins)
    else:
        plugins = plugins_mod.scan(dirs)

    data = {
        "from_cache": from_cache,
        "plugins": [
            {
                "name": p.name,
                "manufacturer": p.manufacturer,
                "format": p.format,
                "path": p.path,
                "roles": list(p.roles),
            }
            for p in plugins
        ],
    }
    return data, []


PLUGINS_SCAN_TOOL = Tool(
    name="plugins.scan",
    description=PLUGINS_SCAN_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "dirs": {
                "oneOf": [
                    {"type": "null"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            "cache_path": {"type": ["string", "null"]},
        },
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "from_cache": {"type": "boolean"},
            "plugins": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "manufacturer": {"type": ["string", "null"]},
                        "format": {"type": "string"},
                        "path": {"type": ["string", "null"]},
                        "roles": {
                            "type": "array", "items": {"type": "string"},
                        },
                    },
                    "required": ["name", "manufacturer", "format", "path", "roles"],
                },
            },
        },
        "required": ["from_cache", "plugins"],
    },
    func=_plugins_scan_impl,
)


# --- techniques.list / techniques.describe -------------------------------

TECHNIQUES_LIST_DESCRIPTION = (
    "Lista as tecnicas catalogadas no indice (derivado dos manuais em "
    "knowledge/tecnicas). Use antes de sugerir tecnica no plano — ESTE E O "
    "VOCABULARIO FECHADO, o que impede o modelo de inventar tecnica que "
    "ninguem sabe executar. Filtros: `family` (drums, bass, keys, guitar) e "
    "`tool` (superior_drummer, addictive_drums, logic_sampler, ...) — quando "
    "`tool` esta presente, a saida inclui a receita para essa ferramenta e "
    "esconde tecnicas que nao tem receita ali. Sem filtro, devolve tudo."
)

TECHNIQUES_DESCRIBE_DESCRIPTION = (
    "Devolve a receita completa de uma tecnica: o que e musicalmente, como "
    "reproduzir em MIDI na ferramenta-alvo (nota, keyswitch, CC, velocity, "
    "gate, offset, curva), o fallback generico, as regras de posicao, as "
    "contraindicacoes e a fonte de cada numero. Use ao gerar a receita de "
    "execucao no plano. Sem `tool`, devolve o fallback generico e AVISA que "
    "esta sem ferramenta. Tecnica inexistente retorna erro com hint listando "
    "as mais parecidas."
)


def _technique_summary_dict(t: techniques_mod.Technique) -> dict[str, Any]:
    return {
        "canonical": t.canonical,
        "name": t.name,
        "family": t.family,
        "summary": t.summary,
        "verified": t.verified,
        "parameters": [p.to_dict() for p in t.parameters],
        "tools_available": sorted(t.tools.keys()),
    }


def _techniques_list_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        idx = techniques_mod.build_index()
    except techniques_mod.TechniqueError as exc:
        raise ToolError(
            "E_TECHNIQUES_INDEX",
            f"falha ao construir indice de tecnicas: {exc}",
        ) from None

    family = payload.get("family")
    tool_target = payload.get("tool")

    techniques = idx.by_family(family)
    if tool_target:
        techniques = tuple(
            t for t in techniques
            if tool_target in t.tools or "generic" in t.tools
        )

    out = []
    for t in techniques:
        entry = _technique_summary_dict(t)
        if tool_target:
            entry["recipe"] = t.tools.get(tool_target) or t.tools.get("generic", {})
        out.append(entry)

    warnings: list[dict[str, Any]] = []
    if not out:
        warnings.append({
            "code": "W_TECHNIQUES_EMPTY",
            "message": (
                f"nenhuma tecnica retornada para family={family!r} tool={tool_target!r}. "
                f"Familias disponiveis: {sorted({t.family for t in idx.techniques})!r}"
            ),
            "path": "",
        })
    return {"techniques": out}, warnings


def _techniques_describe_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import difflib

    try:
        idx = techniques_mod.build_index()
    except techniques_mod.TechniqueError as exc:
        raise ToolError(
            "E_TECHNIQUES_INDEX",
            f"falha ao construir indice de tecnicas: {exc}",
        ) from None

    name = payload["name"]
    tool_target = payload.get("tool")

    found = idx.candidates(name)
    # Nome cru que existe em mais de uma familia — `ghost_notes` esta em bateria
    # e em baixo. A ferramenta-alvo desambigua quando so uma das familias tem
    # receita para ela. Sem isso, ERRO com os candidatos: escolher em silencio
    # entregaria a receita da familia errada.
    if len(found) > 1 and tool_target:
        narrowed = tuple(t for t in found if tool_target in t.tools)
        if len(narrowed) == 1:
            found = narrowed
    if len(found) > 1:
        raise ToolError(
            "E_TECHNIQUE_AMBIGUOUS",
            f"tecnica {name!r} existe em mais de uma familia",
            path="name",
            hint=(
                "informe o nome canonico ou uma ferramenta-alvo que resolva: "
                f"{[t.canonical for t in found]}"
            ),
        )

    t = found[0] if found else None
    if t is None:
        candidates = list(idx.names()) + [tt.name for tt in idx.techniques]
        matches = difflib.get_close_matches(name, candidates, n=5, cutoff=0.4)
        raise ToolError(
            "E_TECHNIQUE_NOT_FOUND",
            f"tecnica {name!r} nao existe no indice",
            path="name",
            hint=(
                f"tecnicas parecidas: {matches}"
                if matches else
                f"tecnicas disponiveis: {list(idx.names())}"
            ),
        )

    recipe: dict[str, Any] = t.tools.get("generic", {})
    used_generic = True
    if tool_target and tool_target in t.tools:
        recipe = t.tools[tool_target]
        used_generic = False

    data = {
        "canonical": t.canonical,
        "name": t.name,
        "family": t.family,
        "summary": t.summary,
        "verified": t.verified,
        "description": t.description,
        "parameters": [p.to_dict() for p in t.parameters],
        "tool": tool_target if tool_target and not used_generic else "generic",
        "recipe": recipe,
        "source_manual": t.source_manual,
    }
    warnings: list[dict[str, Any]] = []
    if tool_target is None:
        warnings.append({
            "code": "W_NO_TOOL",
            "message": (
                "sem `tool` declarada; devolvendo receita generica. "
                f"Ferramentas disponiveis para esta tecnica: {sorted(t.tools.keys())!r}"
            ),
            "path": "tool",
        })
    elif used_generic:
        warnings.append({
            "code": "W_NO_TOOL_RECIPE",
            "message": (
                f"tecnica {t.canonical!r} nao tem receita para tool={tool_target!r}; "
                f"devolvendo fallback generico. Disponiveis: {sorted(t.tools.keys())!r}"
            ),
            "path": "tool",
        })
    return data, warnings


_TECHNIQUE_PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "value": {},
        "range": {
            "oneOf": [
                {"type": "null"},
                {"type": "array", "minItems": 2, "maxItems": 2},
            ],
        },
        "source": {"type": ["string", "null"]},
    },
    "required": ["name", "value", "range", "source"],
}

_TECHNIQUE_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "canonical": {"type": "string"},
        "name": {"type": "string"},
        "family": {"type": "string"},
        "summary": {"type": "string"},
        "verified": {"type": "boolean"},
        "parameters": {"type": "array", "items": _TECHNIQUE_PARAMETER_SCHEMA},
        "tools_available": {"type": "array", "items": {"type": "string"}},
        "recipe": {"type": "object", "additionalProperties": True},
    },
    "required": [
        "canonical", "name", "family", "summary", "verified",
        "parameters", "tools_available",
    ],
}

TECHNIQUES_LIST_TOOL = Tool(
    name="techniques.list",
    description=TECHNIQUES_LIST_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "family": {"type": ["string", "null"]},
            "tool": {"type": ["string", "null"]},
        },
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "techniques": {"type": "array", "items": _TECHNIQUE_SUMMARY_SCHEMA},
        },
        "required": ["techniques"],
    },
    func=_techniques_list_impl,
)


TECHNIQUES_DESCRIBE_TOOL = Tool(
    name="techniques.describe",
    description=TECHNIQUES_DESCRIBE_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "tool": {"type": ["string", "null"]},
        },
        "required": ["name"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "canonical": {"type": "string"},
            "name": {"type": "string"},
            "family": {"type": "string"},
            "summary": {"type": "string"},
            "verified": {"type": "boolean"},
            "description": {"type": "string"},
            "parameters": {"type": "array", "items": _TECHNIQUE_PARAMETER_SCHEMA},
            "tool": {"type": "string"},
            "recipe": {"type": "object", "additionalProperties": True},
            "source_manual": {"type": "string"},
        },
        "required": [
            "canonical", "name", "family", "summary", "verified",
            "description", "parameters", "tool", "recipe", "source_manual",
        ],
    },
    func=_techniques_describe_impl,
)


# --- registro --------------------------------------------------------------

def bootstrap() -> None:
    """Registra todas as tools no registry global.

    Idempotente: chamar duas vezes nao explode; ja registrada e mantida.
    """
    from .registry import get as _get
    for tool in (
        ANALYZE_TOOL, PLAN_SKELETON_TOOL, PLAN_VALIDATE_TOOL,
        RENDER_TOOL, VALIDATE_TOOL, PLUGINS_SCAN_TOOL,
        TECHNIQUES_LIST_TOOL, TECHNIQUES_DESCRIBE_TOOL,
        BRIEF_VALIDATE_TOOL,
    ):
        if _get(tool.name) is None:
            register(tool)


# Registro no import — o CLI e testes ganham as tools sem precisar
# chamar bootstrap na mao.
bootstrap()


__all__ = [
    "ANALYZE_TOOL",
    "BRIEF_VALIDATE_TOOL",
    "PLAN_SKELETON_TOOL",
    "PLAN_VALIDATE_TOOL",
    "PLUGINS_SCAN_TOOL",
    "RENDER_TOOL",
    "TECHNIQUES_DESCRIBE_TOOL",
    "TECHNIQUES_LIST_TOOL",
    "VALIDATE_TOOL",
    "bootstrap",
]
