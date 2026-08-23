"""Testes do contrato e do registry de tools (US-001)."""

from __future__ import annotations

import pytest

from tools import registry
from tools.registry import (
    SchemaError,
    Tool,
    ToolError,
    call,
    failure,
    list_tools,
    register,
    success,
    validate_input,
    validate_output,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Cada teste vive num registry vazio e restaura o original ao final."""
    snap = registry.snapshot()
    registry._REGISTRY.clear()
    try:
        yield
    finally:
        registry.restore(snap)


# --- envelope ---------------------------------------------------------------

def test_success_envelope_has_stable_shape():
    env = success({"foo": 1})
    assert env == {"ok": True, "data": {"foo": 1}, "warnings": []}


def test_success_envelope_carries_warnings():
    env = success({"foo": 1}, warnings=[{"code": "W_STR", "message": "ok"}])
    assert env["warnings"] == [{"code": "W_STR", "message": "ok", "path": ""}]


def test_success_envelope_rejects_warning_without_code_or_message():
    with pytest.raises(ValueError):
        success({}, warnings=[{"code": "X"}])
    with pytest.raises(ValueError):
        success({}, warnings=[{"message": "?"}])


def test_failure_envelope_has_all_error_keys():
    env = failure("E_STR", "msg", path="a.b", hint="try X", context={"k": 1})
    assert env == {
        "ok": False,
        "error": {
            "code": "E_STR",
            "message": "msg",
            "path": "a.b",
            "hint": "try X",
            "context": {"k": 1},
        },
    }


def test_failure_envelope_defaults_are_stable():
    env = failure("E_STR", "msg")
    assert env["error"] == {
        "code": "E_STR", "message": "msg", "path": "", "hint": "", "context": {},
    }


# --- schema: additionalProperties ------------------------------------------

def test_input_schema_defaults_to_additional_properties_false():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    validate_input({"a": 1}, schema)
    with pytest.raises(SchemaError) as exc:
        validate_input({"a": 1, "surprise": 2}, schema)
    assert exc.value.code == "E_INPUT_SCHEMA"
    assert exc.value.path == "surprise"
    assert "desconhecido" in exc.value.message


def test_output_schema_also_rejects_unknown_fields_by_default():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    validate_output({"a": 1}, schema)
    with pytest.raises(SchemaError) as exc:
        validate_output({"a": 1, "extra": True}, schema)
    assert exc.value.code == "E_OUTPUT_SCHEMA"
    assert exc.value.path == "extra"


def test_required_field_missing_emits_path():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "required": ["a", "b"],
    }
    with pytest.raises(SchemaError) as exc:
        validate_input({"a": 1}, schema)
    assert exc.value.path == "b"


# --- schema: types ---------------------------------------------------------

def test_type_integer_rejects_bool():
    schema = {"type": "integer"}
    validate_input(5, schema)
    with pytest.raises(SchemaError):
        validate_input(True, schema)


def test_type_number_accepts_int_and_float():
    validate_input(1, {"type": "number"})
    validate_input(1.5, {"type": "number"})
    with pytest.raises(SchemaError):
        validate_input("x", {"type": "number"})


def test_type_union_via_list():
    schema = {"type": ["string", "null"]}
    validate_input("x", schema)
    validate_input(None, schema)
    with pytest.raises(SchemaError):
        validate_input(1, schema)


# --- schema: strings/numbers/arrays/enum -----------------------------------

def test_string_min_max_length_and_pattern():
    with pytest.raises(SchemaError):
        validate_input("", {"type": "string", "minLength": 1})
    with pytest.raises(SchemaError):
        validate_input("abcde", {"type": "string", "maxLength": 3})
    with pytest.raises(SchemaError):
        validate_input("bar", {"type": "string", "pattern": "^foo"})
    validate_input("foobar", {"type": "string", "pattern": "^foo"})


def test_number_bounds():
    schema = {"type": "integer", "minimum": 0, "maximum": 10}
    validate_input(5, schema)
    with pytest.raises(SchemaError):
        validate_input(-1, schema)
    with pytest.raises(SchemaError):
        validate_input(11, schema)


def test_array_items_and_bounds():
    schema = {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 1,
        "maxItems": 3,
    }
    validate_input([1, 2], schema)
    with pytest.raises(SchemaError):
        validate_input([], schema)
    with pytest.raises(SchemaError):
        validate_input([1, 2, 3, 4], schema)
    with pytest.raises(SchemaError) as exc:
        validate_input([1, "x", 3], schema)
    assert exc.value.path == "[1]"


def test_enum_and_const():
    with pytest.raises(SchemaError):
        validate_input("z", {"enum": ["a", "b"]})
    validate_input("a", {"enum": ["a", "b"]})
    validate_input(42, {"const": 42})
    with pytest.raises(SchemaError):
        validate_input(41, {"const": 42})


def test_oneof_requires_exactly_one():
    schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    validate_input("x", schema)
    validate_input(1, schema)
    with pytest.raises(SchemaError):
        validate_input(1.5, schema)


def test_anyof_accepts_first_match():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    validate_input("x", schema)
    validate_input(1, schema)
    with pytest.raises(SchemaError):
        validate_input(1.5, schema)


def test_error_path_walks_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "inner": {
                "type": "object",
                "properties": {
                    "list": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["list"],
            },
        },
        "required": ["inner"],
    }
    with pytest.raises(SchemaError) as exc:
        validate_input({"inner": {"list": [1, "x"]}}, schema)
    assert exc.value.path == "inner.list[1]"


# --- registro ---------------------------------------------------------------

def _mk_tool(name: str = "sample.echo") -> Tool:
    return Tool(
        name=name,
        description="Ecoa a entrada como saida. Existe para teste do contrato.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        func=lambda p: {"value": p["value"]},
    )


def test_register_rejects_bad_name():
    with pytest.raises(ValueError):
        register(Tool(
            name="Bad-Name",
            description="x", input_schema={}, output_schema={}, func=lambda p: {},
        ))


def test_register_rejects_empty_description():
    with pytest.raises(ValueError):
        register(Tool(
            name="a.b", description="   ", input_schema={}, output_schema={}, func=lambda p: {},
        ))


def test_register_rejects_duplicate():
    register(_mk_tool())
    with pytest.raises(ValueError):
        register(_mk_tool())


def test_list_tools_returns_full_declarations_in_alphabetical_order():
    register(_mk_tool("b.tool"))
    register(_mk_tool("a.tool"))
    decls = list_tools()
    assert [d["name"] for d in decls] == ["a.tool", "b.tool"]
    d = decls[0]
    assert set(d) == {"name", "description", "input_schema", "output_schema"}


# --- call ------------------------------------------------------------------

def test_call_success_wraps_data_in_envelope():
    register(_mk_tool())
    env = call("sample.echo", {"value": 3})
    assert env == {"ok": True, "data": {"value": 3}, "warnings": []}


def test_call_success_with_warnings():
    tool = _mk_tool()
    tool = Tool(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, output_schema=tool.output_schema,
        func=lambda p: ({"value": p["value"]}, [{"code": "W_STR", "message": "aviso"}]),
    )
    register(tool)
    env = call("sample.echo", {"value": 1})
    assert env["ok"] is True
    assert env["warnings"] == [{"code": "W_STR", "message": "aviso", "path": ""}]


def test_call_unknown_tool_returns_failure_envelope():
    env = call("does.not.exist", {})
    assert env == {
        "ok": False,
        "error": {
            "code": "E_TOOL_NOT_FOUND",
            "message": "tool desconhecida: 'does.not.exist'",
            "path": "",
            "hint": "disponiveis: []",
            "context": {},
        },
    }


def test_call_input_schema_error_becomes_failure_envelope_with_path():
    register(_mk_tool())
    env = call("sample.echo", {"value": 1, "extra": True})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "extra"


def test_call_tool_error_becomes_failure_envelope_preserving_code():
    def raiser(_payload):
        raise ToolError(
            "E_CUSTOM", "falhou", path="value",
            hint="use outro valor", context={"tried": 1},
        )
    tool = _mk_tool()
    register(Tool(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, output_schema=tool.output_schema,
        func=raiser,
    ))
    env = call("sample.echo", {"value": 1})
    assert env == {
        "ok": False,
        "error": {
            "code": "E_CUSTOM", "message": "falhou", "path": "value",
            "hint": "use outro valor", "context": {"tried": 1},
        },
    }


def test_call_unexpected_exception_becomes_internal_error():
    def boom(_payload):
        raise RuntimeError("oops")
    tool = _mk_tool()
    register(Tool(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, output_schema=tool.output_schema,
        func=boom,
    ))
    env = call("sample.echo", {"value": 1})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INTERNAL"
    assert env["error"]["context"] == {"exception": "RuntimeError"}


def test_call_output_schema_error_becomes_failure_envelope():
    tool = _mk_tool()
    register(Tool(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, output_schema=tool.output_schema,
        func=lambda p: {"value": "not-int"},  # devolve string, schema pede int
    ))
    env = call("sample.echo", {"value": 1})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_OUTPUT_SCHEMA"
    assert env["error"]["path"] == "value"


def test_call_output_schema_rejects_unknown_field():
    tool = _mk_tool()
    register(Tool(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, output_schema=tool.output_schema,
        func=lambda p: {"value": 1, "leaked": "internals"},
    ))
    env = call("sample.echo", {"value": 1})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_OUTPUT_SCHEMA"
    assert env["error"]["path"] == "leaked"
