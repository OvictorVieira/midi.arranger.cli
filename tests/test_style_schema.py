"""Testes dos helpers compartilhados do contrato de `style`."""

from tools.style_schema import (
    find_style_musical_content,
    is_style_parameter_pair,
    style_technique_schema,
)


def test_style_technique_schema_can_close_additional_properties():
    schema = style_technique_schema(additional_properties=False)

    assert schema["required"] == ["name"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"name", "density", "rationale"}


def test_find_style_musical_content_rejects_content_key_with_path():
    violation = find_style_musical_content(
        {"bass": {"notes": [40, 42, 45]}},
        "style",
    )

    assert violation == (
        "style.bass.notes",
        "campo de conteudo musical proibido 'notes'",
    )


def test_find_style_musical_content_allows_parameter_pair_under_parameters():
    violation = find_style_musical_content(
        {"drums": {"parameters": {"velocity": [20, 45]}}},
        "style",
    )

    assert violation is None
    assert is_style_parameter_pair([20, 45])


def test_find_style_musical_content_rejects_pitch_sequence_in_innocent_field():
    violation = find_style_musical_content(
        {"bass": {"parameters": {"accent_shape": [40, 42, 45]}}},
        "style",
    )

    assert violation == (
        "style.bass.parameters.accent_shape",
        "sequencia de tres ou mais inteiros em faixa MIDI proibida",
    )


def test_find_style_musical_content_rejects_event_array_with_pitch_and_time():
    violation = find_style_musical_content(
        {"drums": {"parameters": {"accent_map": [{"pitch": 38, "time": 0.0}]}}},
        "style",
    )

    assert violation == (
        "style.drums.parameters.accent_map",
        "array de eventos com altura e tempo proibido",
    )


def test_find_style_musical_content_reports_nested_array_path():
    violation = find_style_musical_content(
        {"bass": {"parameters": {"accent_shapes": [[40, 42, 45]]}}},
        "style",
    )

    assert violation == (
        "style.bass.parameters.accent_shapes[0]",
        "sequencia de tres ou mais inteiros em faixa MIDI proibida",
    )
