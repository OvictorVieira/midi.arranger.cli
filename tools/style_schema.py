"""Helpers compartilhados para o contrato de `style`.

`style` aparece no brief e no plano, e a mesma regra anticopia precisa valer
na API Python e na fachada de JSON Schema. Este modulo centraliza o formato
comum para evitar dois validadores quase iguais divergindo.
"""

from __future__ import annotations

import re
from typing import Any

# Nome de nota em notacao cientifica: C4, F#3, Bb-1. Mora aqui, e nao em
# `brief_schema`, porque brief e plano precisam da MESMA heuristica — a regra
# anticopia nao pode ser mais fraca de um lado que do outro.
NOTE_NAME_RE = re.compile(r"^[A-Ga-g](#|b|♯|♭)?-?\d+$")

# `researched_at`. A fachada JSON Schema e o dominio Python usam ESTE padrao,
# nunca dois: `date.fromisoformat` sozinho aceita `20260824` e `2026-W35-1`,
# que a fachada recusa, e isso seria duas verdades sobre a mesma data.
ISO_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
ISO_DATE_RE = re.compile(ISO_DATE_PATTERN)

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
STYLE_PITCH_KEYS = ("pitch", "note", "midi_note", "note_number", "midi_pitch", "pitches")
STYLE_TIME_KEYS = (
    "time", "start", "start_tick", "tick", "ticks", "position", "offset",
    # Sinonimos que um gerador de eventos usa com a mesma naturalidade. Sem
    # eles, {"pitch": 40, "onset": 0} atravessava a checagem de eventos.
    "onset", "dur", "duration", "beat", "bar", "step", "length",
)
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
            return path, "sequencia de tres ou mais numeros em faixa MIDI proibida"
        if any(isinstance(item, dict) and _object_has_pitch_and_time_keys(item) for item in value):
            return path, "array de eventos com altura e tempo proibido"
        if _looks_like_pair_sequence(value):
            return path, "sequencia de tres ou mais pares numericos proibida"
        if _looks_like_note_name_sequence(value):
            return path, "array de strings em formato de nome de nota proibido"
        for i, item in enumerate(value):
            violation = find_style_musical_content(item, f"{path}[{i}]")
            if violation is not None:
                return violation

    return None


def _join_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _looks_like_midi_pitch_sequence(value: list[Any]) -> bool:
    # Numeros, nao so inteiros: `[40.0, 42.0, 45.0]` e a mesma sequencia de
    # alturas escrita por um serializador que emite float.
    return len(value) >= 3 and all(
        _is_number(item) and MIDI_PITCH_MIN <= item <= MIDI_PITCH_MAX for item in value
    )


def _looks_like_note_name_sequence(value: list[Any]) -> bool:
    """Array de nomes de nota — `["C4", "D4", "E4"]` e uma melodia escrita.

    O brief ja recusava isso; o plano nao. Mesma regra dos dois lados.
    """
    return bool(value) and all(
        isinstance(item, str) and NOTE_NAME_RE.match(item.strip()) for item in value
    )


def _looks_like_pair_sequence(value: list[Any]) -> bool:
    """Tres ou mais pares numericos — a forma canonica de (altura, tempo).

    `[[40, 0], [42, 1], [45, 2]]` nao cai em nenhuma das outras checagens: nao
    e sequencia plana de numeros e nao e array de objetos. E, ainda assim, e um
    riff.
    """
    return len(value) >= 3 and all(is_style_parameter_pair(item) for item in value)


def _object_has_pitch_and_time_keys(value: dict[str, Any]) -> bool:
    keys = set(value)
    return bool(keys.intersection(STYLE_PITCH_KEYS)) and bool(keys.intersection(STYLE_TIME_KEYS))
