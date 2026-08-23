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
from . import sections as sections_mod
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
    ArrangementPlan,
    PlanValidationError,
    SourceMidi,
)
from .registry import Tool, ToolError, register

KEY_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


# --- helpers de IO ---------------------------------------------------------

def _resolve_midi(path_str: str) -> Path:
    """Valida existencia/permissao/formato do MIDI e devolve o Path resolvido.

    Erros viram `ToolError` com codigo dedicado. Nunca stack trace.
    """
    if not path_str:
        raise ToolError("E_MIDI_PATH", "midi_path vazio", path="midi_path")
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
                        "sync_role", "articulation", "harmony", "pattern",
                        "degrees", "dynamics", "instrument", "rationale",
                        "is_protagonist",
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
            "assumptions", "transitions", "edits",
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
    route = payload.get("route", _DEFAULT_ROUTE)
    if route not in ROUTES:
        raise ToolError(
            "E_ROUTE_INVALID",
            f"rota {route!r} fora do vocabulario {list(ROUTES)}",
            path="route",
        )
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


def _plan_validate_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if ("plan" in payload) == ("plan_path" in payload):
        raise ToolError(
            "E_PLAN_INPUT",
            "informe exatamente um: `plan` inline OU `plan_path`",
            hint="use plan={} para inline ou plan_path='...' para caminho",
        )

    plan_dict: dict[str, Any]
    if "plan_path" in payload:
        pp = Path(payload["plan_path"]).expanduser()
        if not pp.exists():
            raise ToolError(
                "E_PLAN_FILE_NOT_FOUND",
                f"arquivo de plano nao encontrado: {pp}",
                path="plan_path",
            )
        try:
            plan_dict = json.loads(pp.read_text(encoding="utf-8"))
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
        if not isinstance(plan_dict, dict):
            raise ToolError(
                "E_PLAN_JSON",
                f"plano precisa ser objeto JSON; recebi {type(plan_dict).__name__}",
                path="plan_path",
            )
    else:
        plan_dict = payload["plan"]

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


# --- registro --------------------------------------------------------------

def bootstrap() -> None:
    """Registra todas as tools no registry global.

    Idempotente: chamar duas vezes nao explode; ja registrada e mantida.
    """
    from .registry import get as _get
    for tool in (ANALYZE_TOOL, PLAN_SKELETON_TOOL, PLAN_VALIDATE_TOOL):
        if _get(tool.name) is None:
            register(tool)


# Registro no import — o CLI e testes ganham as tools sem precisar
# chamar bootstrap na mao.
bootstrap()


__all__ = [
    "ANALYZE_TOOL",
    "PLAN_SKELETON_TOOL",
    "PLAN_VALIDATE_TOOL",
    "bootstrap",
]
