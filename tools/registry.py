"""Contrato e registry de tools (US-001).

Toda tool obedece ao mesmo contrato: entrada em JSON, saida em JSON, schemas
declarados, envelope estruturado de sucesso ou falha. E o que permite trocar
de agente sem reescrever nada e testar o maquinario inteiro sem modelo.

## Envelope

Sucesso:

    {
        "ok": true,
        "data": {...},                            # conforme output_schema
        "warnings": [
            {"code": "STR", "message": "...", "path": "..."},
        ],
    }

Falha:

    {
        "ok": false,
        "error": {
            "code": "STR",
            "message": "...",
            "path": "...",           # aponta o campo em erro de dados
            "hint": "...",           # o que fazer, quando ha caminho obvio
            "context": {...},        # dados auxiliares
        },
    }

`warnings` NAO bloqueiam sucesso — sao coisas que o usuario precisa saber
mas nao invalidam o resultado. `error` invalida.

## JSON Schema

Este modulo carrega um validador minimo. Nao dependemos de `jsonschema` porque
o unico permitido em tools/ e `mido` + `pretty_midi`. Os subsets aceitos:

- `type`: "object" | "array" | "string" | "integer" | "number" | "boolean" |
  "null", ou lista para uniao (ex.: `["string", "null"]`).
- `properties`, `required` — para objetos.
- `additionalProperties`: quando omitido, DEFAULT E FALSE. Campo desconhecido
  no input e ERRO, nunca ignorado em silencio.
- `items` — para arrays. `minItems`, `maxItems`.
- `enum`, `const`.
- `minimum`, `maximum` — para number/integer.
- `minLength`, `maxLength`, `pattern` — para string.
- `oneOf`, `anyOf` — combinadores.
- `x_forbid_style_musical_content` — varredura recursiva usada pelo
  arrangement-plan para impedir que `style` carregue conteudo musical.

Suficiente para descrever as tools deste repo. Nao suporta `$ref`, `allOf`,
`if/then/else` — se a necessidade surgir, adicione com teste, nao antes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# --- excecao de dominio -----------------------------------------------------


class ToolError(Exception):
    """Erro estruturado que vira `envelope.error`.

    Uma tool levanta `ToolError` quando a chamada nao pode produzir saida
    valida. O registry captura e devolve o envelope de falha; a tool nunca
    precisa formatar o envelope na mao.

    `code` e enumerado por tool — mensagens sao em portugues, acionaveis, e
    `path` aponta o campo exato quando o problema e de dados.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "",
        hint: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.path = path
        self.hint = hint
        self.context = context or {}
        super().__init__(f"{code}: {message}")


# --- validador de JSON Schema (subset) --------------------------------------


class SchemaError(ToolError):
    """Falha de validacao de entrada ou saida contra o schema declarado."""


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


def _is_parameter_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )


def _raise_style_musical_schema_error(code: str, path: str, reason: str) -> None:
    raise SchemaError(
        code,
        (
            f"{reason}; perfil de estilo aceita parametros de tecnica, "
            "nunca conteudo musical"
        ),
        path=path,
    )


def _object_has_pitch_and_time_keys(value: dict[str, Any]) -> bool:
    keys = set(value)
    return bool(keys.intersection(STYLE_PITCH_KEYS)) and bool(keys.intersection(STYLE_TIME_KEYS))


def _validate_no_style_musical_content(
    value: Any,
    path: str,
    code: str,
    *,
    allow_parameter_pair: bool = False,
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _join(path, key)
            if key in STYLE_MUSICAL_CONTENT_KEYS:
                _raise_style_musical_schema_error(
                    code,
                    child_path,
                    f"campo de conteudo musical proibido {key!r}",
                )
            _validate_no_style_musical_content(
                child,
                child_path,
                code,
                allow_parameter_pair=path.endswith(".parameters"),
            )
        return

    if isinstance(value, list):
        if allow_parameter_pair and _is_parameter_pair(value):
            return
        if (
            len(value) >= 3
            and all(
                isinstance(item, int)
                and not isinstance(item, bool)
                and MIDI_PITCH_MIN <= item <= MIDI_PITCH_MAX
                for item in value
            )
        ):
            _raise_style_musical_schema_error(
                code,
                path,
                "sequencia de tres ou mais inteiros em faixa MIDI proibida",
            )
        if any(isinstance(item, dict) and _object_has_pitch_and_time_keys(item) for item in value):
            _raise_style_musical_schema_error(
                code,
                path,
                "array de eventos com altura e tempo proibido",
            )
        for i, item in enumerate(value):
            _validate_no_style_musical_content(item, f"{path}[{i}]", code)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        # bool e subclasse de int em Python — recusamos explicitamente.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float))
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValueError(f"tipo desconhecido no schema: {expected!r}")


def _validate(value: Any, schema: dict[str, Any], path: str, code: str) -> None:
    """Valida `value` contra `schema`. Levanta `SchemaError` no primeiro erro.

    `path` acumula o caminho JSON pointer-like (`elements[3].register[1]`) para
    apontar exatamente o campo em falha. `code` e o codigo de erro a atribuir
    (E_INPUT_SCHEMA ou E_OUTPUT_SCHEMA).
    """
    if schema.get("x_forbid_style_musical_content"):
        _validate_no_style_musical_content(value, path, code)

    # oneOf / anyOf sao avaliados antes de type — as branches costumam
    # divergir exatamente em type.
    if "oneOf" in schema:
        matches = 0
        last_err = None
        for sub in schema["oneOf"]:
            try:
                _validate(value, sub, path, code)
            except SchemaError as exc:
                last_err = exc
                continue
            matches += 1
        if matches == 0:
            raise SchemaError(
                code,
                f"nao casou com nenhuma variante de oneOf ({last_err.message if last_err else '?'})",
                path=path,
            )
        if matches > 1:
            raise SchemaError(
                code,
                f"casou com {matches} variantes de oneOf; deveria casar com exatamente uma",
                path=path,
            )
        return
    if "anyOf" in schema:
        last_err = None
        for sub in schema["anyOf"]:
            try:
                _validate(value, sub, path, code)
                return
            except SchemaError as exc:
                last_err = exc
                continue
        raise SchemaError(
            code,
            f"nao casou com nenhuma variante de anyOf ({last_err.message if last_err else '?'})",
            path=path,
        )

    if "const" in schema and value != schema["const"]:
        raise SchemaError(
            code,
            f"esperava valor constante {schema['const']!r}, recebi {value!r}",
            path=path,
        )

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(
            code,
            f"valor {value!r} fora do enum {schema['enum']!r}",
            path=path,
        )

    if "type" in schema:
        expected = schema["type"]
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, t) for t in types):
            got = type(value).__name__
            raise SchemaError(
                code,
                f"esperava tipo {expected!r}, recebi {got}",
                path=path,
            )

    if isinstance(value, dict):
        _validate_object(value, schema, path, code)
    elif isinstance(value, list):
        _validate_array(value, schema, path, code)
    elif isinstance(value, str):
        _validate_string(value, schema, path, code)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path, code)


def _validate_object(
    value: dict[str, Any], schema: dict[str, Any], path: str, code: str,
) -> None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    # DEFAULT INVIOLAVEL: campo desconhecido e erro. AC US-001.
    additional = schema.get("additionalProperties", False)

    for key in required:
        if key not in value:
            raise SchemaError(
                code,
                f"campo obrigatorio ausente: {key!r}",
                path=_join(path, key),
            )

    for key, sub_value in value.items():
        if key in properties:
            _validate(sub_value, properties[key], _join(path, key), code)
        elif additional is False:
            raise SchemaError(
                code,
                f"campo desconhecido: {key!r}",
                path=_join(path, key),
                hint=(
                    f"campos aceitos: {sorted(properties)}"
                    if properties else "esta tool nao aceita nenhum campo"
                ),
            )
        elif isinstance(additional, dict):
            _validate(sub_value, additional, _join(path, key), code)


def _validate_array(
    value: list[Any], schema: dict[str, Any], path: str, code: str,
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        raise SchemaError(
            code, f"array com {len(value)} < minItems={schema['minItems']}",
            path=path,
        )
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        raise SchemaError(
            code, f"array com {len(value)} > maxItems={schema['maxItems']}",
            path=path,
        )
    items = schema.get("items")
    if items is None:
        return
    for i, item in enumerate(value):
        _validate(item, items, f"{path}[{i}]", code)


def _validate_string(value: str, schema: dict[str, Any], path: str, code: str) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise SchemaError(
            code, f"string com {len(value)} < minLength={schema['minLength']}",
            path=path,
        )
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise SchemaError(
            code, f"string com {len(value)} > maxLength={schema['maxLength']}",
            path=path,
        )
    if "pattern" in schema and not re.search(schema["pattern"], value):
        raise SchemaError(
            code, f"string nao casa com padrao {schema['pattern']!r}",
            path=path,
        )


def _validate_number(
    value: int | float, schema: dict[str, Any], path: str, code: str,
) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise SchemaError(
            code, f"valor {value} < minimum={schema['minimum']}",
            path=path,
        )
    if "maximum" in schema and value > schema["maximum"]:
        raise SchemaError(
            code, f"valor {value} > maximum={schema['maximum']}",
            path=path,
        )


def _join(base: str, key: str) -> str:
    if not base:
        return key
    return f"{base}.{key}"


def validate_input(payload: Any, schema: dict[str, Any]) -> None:
    """Valida `payload` contra `schema` de entrada de uma tool."""
    _validate(payload, schema, "", "E_INPUT_SCHEMA")


def validate_output(data: Any, schema: dict[str, Any]) -> None:
    """Valida `data` contra `schema` de saida de uma tool."""
    _validate(data, schema, "", "E_OUTPUT_SCHEMA")


# --- envelopes --------------------------------------------------------------


def success(
    data: Any, warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Constroi um envelope de sucesso.

    `warnings` e uma lista de dicts `{code, message, path}`. Codigo obrigatorio;
    `path` opcional e default `""`.
    """
    warn_list: list[dict[str, Any]] = []
    for w in warnings or []:
        if "code" not in w or "message" not in w:
            raise ValueError(f"warning sem code/message: {w!r}")
        warn_list.append({
            "code": w["code"],
            "message": w["message"],
            "path": w.get("path", ""),
        })
    return {"ok": True, "data": data, "warnings": warn_list}


def failure(
    code: str,
    message: str,
    *,
    path: str = "",
    hint: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Constroi um envelope de falha."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "path": path,
            "hint": hint,
            "context": context or {},
        },
    }


# --- registro ---------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


@dataclass(frozen=True)
class Tool:
    """Declaracao de uma tool.

    - `name`: namespaced com ponto (ex.: `plan.validate`, `techniques.list`).
    - `description`: TEXTO ESCRITO PARA O MODELO LER. Diz o que a tool faz,
      quando usar, quando NAO usar. Nao e docstring de API.
    - `input_schema`, `output_schema`: JSON Schema (subset do modulo).
    - `func`: `payload -> (data, warnings)` ou `payload -> data`. Se retornar
      tupla, o segundo elemento e a lista de warnings.
    """
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    func: Callable[[dict[str, Any]], Any]

    def declaration(self) -> dict[str, Any]:
        """Dict serializavel com a declaracao completa desta tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    """Registra `tool` no registry global.

    Devolve o proprio tool para permitir `TOOL = register(Tool(...))` no
    modulo, facilitando teste direto sem passar pelo registry.
    """
    if not _NAME_RE.match(tool.name):
        raise ValueError(
            f"nome de tool invalido: {tool.name!r} "
            f"(esperado: namespace.snake_case, ex.: plan.validate)"
        )
    if not tool.description.strip():
        raise ValueError(f"tool {tool.name!r} sem descricao — descricao vazia nao ajuda o agente")
    if not isinstance(tool.input_schema, dict) or not isinstance(tool.output_schema, dict):
        raise ValueError(f"tool {tool.name!r}: schemas precisam ser dict")
    if tool.name in _REGISTRY:
        raise ValueError(f"tool {tool.name!r} ja registrada")
    _REGISTRY[tool.name] = tool
    return tool


def unregister(name: str) -> None:
    """Remove `name` do registry. Existe para teste; producao nao usa."""
    _REGISTRY.pop(name, None)


def get(name: str) -> Tool | None:
    """Recupera a tool `name` ou `None` se nao registrada."""
    return _REGISTRY.get(name)


def list_tools() -> list[dict[str, Any]]:
    """Devolve a declaracao completa de todas as tools registradas.

    Ordem estavel: alfabetica pelo nome.
    """
    return [tool.declaration() for _, tool in sorted(_REGISTRY.items())]


def call(name: str, payload: Any) -> dict[str, Any]:
    """Executa a tool `name` com `payload` e devolve o envelope.

    Fluxo:
      1. Resolve a tool. Ausente -> E_TOOL_NOT_FOUND.
      2. Valida `payload` contra `input_schema`. Erro -> E_INPUT_SCHEMA.
      3. Executa `func(payload)`. Excecao esperada: `ToolError` — vira o
         envelope de falha com o codigo da propria tool. Qualquer outra
         excecao vaza como E_INTERNAL, com a classe e a mensagem no context.
      4. Valida a saida contra `output_schema`. Falha aqui e bug da tool —
         E_OUTPUT_SCHEMA no envelope. A validacao roda inclusive em producao.
    """
    tool = _REGISTRY.get(name)
    if tool is None:
        return failure(
            "E_TOOL_NOT_FOUND",
            f"tool desconhecida: {name!r}",
            hint=f"disponiveis: {sorted(_REGISTRY)}",
        )

    try:
        validate_input(payload, tool.input_schema)
    except SchemaError as exc:
        return failure(exc.code, exc.message, path=exc.path, hint=exc.hint, context=exc.context)

    try:
        result = tool.func(payload)
    except ToolError as exc:
        return failure(exc.code, exc.message, path=exc.path, hint=exc.hint, context=exc.context)
    except Exception as exc:  # noqa: BLE001
        return failure(
            "E_INTERNAL",
            f"erro interno em {name!r}: {exc}",
            context={"exception": type(exc).__name__},
        )

    if isinstance(result, tuple):
        if len(result) != 2:
            return failure(
                "E_INTERNAL",
                f"tool {name!r} devolveu tupla de tamanho {len(result)}; esperado (data, warnings)",
            )
        data, warnings = result
    else:
        data, warnings = result, []

    try:
        validate_output(data, tool.output_schema)
    except SchemaError as exc:
        return failure(
            exc.code,
            f"saida da tool {name!r} nao valida contra output_schema: {exc.message}",
            path=exc.path,
            context={"tool": name},
        )

    return success(data, warnings)


# --- reset (uso em teste) ---------------------------------------------------


@dataclass
class _RegistrySnapshot:
    tools: dict[str, Tool] = field(default_factory=dict)


def snapshot() -> _RegistrySnapshot:
    """Captura o estado atual do registry — para restaurar depois de teste."""
    return _RegistrySnapshot(tools=dict(_REGISTRY))


def restore(snap: _RegistrySnapshot) -> None:
    """Restaura o registry a partir de `snap`. Uso em teste."""
    _REGISTRY.clear()
    _REGISTRY.update(snap.tools)


__all__ = [
    "SchemaError",
    "Tool",
    "ToolError",
    "call",
    "failure",
    "get",
    "list_tools",
    "register",
    "restore",
    "snapshot",
    "success",
    "unregister",
    "validate_input",
    "validate_output",
]
