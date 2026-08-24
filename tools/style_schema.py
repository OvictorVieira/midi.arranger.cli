"""Helpers compartilhados para o contrato de `style`.

`style` aparece no brief e no plano, e a mesma regra anticopia precisa valer
na API Python e na fachada de JSON Schema. Este modulo centraliza o formato
comum para evitar dois validadores quase iguais divergindo.
"""

from __future__ import annotations

from typing import Any

STYLE_MUSICAL_CONTENT_KEYS = (
    "notes",
    "pattern",
    "riff",
    "melody",
    "groove",
    "sequence",
    "midi",
    "phrase",
    "lick",
    "motif",
)
STYLE_PITCH_KEYS = ("pitch", "note", "midi_note", "note_number")
STYLE_TIME_KEYS = ("time", "start", "start_tick", "tick", "ticks", "position", "offset")
MIDI_PITCH_MIN = 0
MIDI_PITCH_MAX = 127


def style_technique_schema(*, additional_properties: bool | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
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
    }
    if additional_properties is not None:
        schema["additionalProperties"] = additional_properties
    return schema


def is_style_parameter_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )


def find_style_musical_content(
    value: Any,
    path: str,
    *,
    allow_parameter_pair: bool = False,
) -> tuple[str, str] | None:
    """Retorna a primeira violacao anticopia em `style`, ou `None`.

    O retorno e `(path, reason)` para que cada camada preserve seu proprio tipo
    de erro (`PlanValidationError` no dominio, `SchemaError` na fachada).
    """
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _join_path(path, key)
            if key in STYLE_MUSICAL_CONTENT_KEYS:
                return child_path, f"campo de conteudo musical proibido {key!r}"
            violation = find_style_musical_content(
                child,
                child_path,
                allow_parameter_pair=path.endswith(".parameters"),
            )
            if violation is not None:
                return violation
        return None

    if isinstance(value, list):
        if allow_parameter_pair and is_style_parameter_pair(value):
            return None
        if _looks_like_midi_pitch_sequence(value):
            return path, "sequencia de tres ou mais inteiros em faixa MIDI proibida"
        if any(isinstance(item, dict) and _object_has_pitch_and_time_keys(item) for item in value):
            return path, "array de eventos com altura e tempo proibido"
        for i, item in enumerate(value):
            violation = find_style_musical_content(item, f"{path}[{i}]")
            if violation is not None:
                return violation

    return None


def _join_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _looks_like_midi_pitch_sequence(value: list[Any]) -> bool:
    return (
        len(value) >= 3
        and all(
            isinstance(item, int)
            and not isinstance(item, bool)
            and MIDI_PITCH_MIN <= item <= MIDI_PITCH_MAX
            for item in value
        )
    )


def _object_has_pitch_and_time_keys(value: dict[str, Any]) -> bool:
    keys = set(value)
    return bool(keys.intersection(STYLE_PITCH_KEYS)) and bool(keys.intersection(STYLE_TIME_KEYS))
