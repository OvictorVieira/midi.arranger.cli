"""Testes dos helpers compartilhados do contrato de `style`."""

import pytest

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
        "sequencia de tres ou mais numeros em faixa MIDI proibida",
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
        "sequencia de tres ou mais numeros em faixa MIDI proibida",
    )


# --- furos fechados no review do PR #43 ------------------------------------
#
# Todos os vetores abaixo passavam pelo validador antes do review. Nenhum era
# alcancavel pelo schema tipado, porque `additionalProperties: false` fecha as
# chaves de `style` — mas a checagem por FORMA e a segunda camada, e ela nao
# pode ser mais fraca do que anuncia. Um dia `additionalProperties` afrouxa e
# a segunda camada e a unica que sobra.

@pytest.mark.parametrize(
    ("valor", "descricao"),
    [
        ([40.0, 42.0, 45.0], "alturas escritas como float por um serializador"),
        ([[40, 0], [42, 1], [45, 2]], "pares (altura, tempo) — a forma canonica de um riff"),
        (["C4", "D4", "E4"], "nomes de nota; o brief ja recusava, o plano nao"),
        ([{"pitch": 40, "onset": 0}], "evento com `onset` em vez de `time`"),
        ([{"note": 40, "dur": 120}], "evento com `dur` em vez de `time`"),
    ],
)
def test_musical_content_smuggling_vectors_are_rejected(valor, descricao):
    violation = find_style_musical_content({"bass": {"x": valor}}, "style")

    assert violation is not None, f"passou: {descricao}"
    assert violation[0] == "style.bass.x"


@pytest.mark.parametrize(
    "valor",
    [
        [20, 45],
        [0.5, 1.0],
        ["http://exemplo.com/a", "https://exemplo.com/b"],
        [[40, 0], [42, 1]],
    ],
)
def test_legitimate_values_survive_the_hardened_scan(valor):
    """A checagem endurecida nao pode passar a recusar dado legitimo.

    Par de parametro, fracao, lista de fontes e um par solto continuam validos
    — dois pares nao formam sequencia, e URL nao e nome de nota.
    """
    assert find_style_musical_content({"drums": {"parameters": {"v": valor}}}, "style") is None
