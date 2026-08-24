"""Testes das tools techniques.list e techniques.describe (US-006)."""

from __future__ import annotations

import pytest

from tools import contract as _contract  # noqa: F401
from tools.registry import call, get


@pytest.mark.parametrize("name", ["techniques.list", "techniques.describe"])
def test_tool_has_prompt_description(name: str):
    t = get(name)
    assert t is not None
    assert len(t.description) > 80
    assert "Use" in t.description or "use" in t.description


# --- techniques.list ------------------------------------------------------

def test_list_no_filter_returns_all_documented_families():
    env = call("techniques.list", {})
    assert env["ok"] is True
    canonicals = {t["canonical"] for t in env["data"]["techniques"]}
    assert "drums.ghost_notes" in canonicals
    assert "drums.flam" in canonicals
    assert "drums.microtiming" in canonicals


def test_list_filter_by_family_drums_returns_only_drums():
    env = call("techniques.list", {"family": "drums"})
    assert env["ok"] is True
    for t in env["data"]["techniques"]:
        assert t["family"] == "drums"


def test_list_filter_by_unknown_family_returns_empty_with_warning():
    env = call("techniques.list", {"family": "harpsichord"})
    assert env["ok"] is True
    assert env["data"]["techniques"] == []
    codes = [w["code"] for w in env["warnings"]]
    assert "W_TECHNIQUES_EMPTY" in codes


def test_list_with_tool_returns_recipe_per_technique():
    env = call("techniques.list", {"family": "drums", "tool": "superior_drummer"})
    assert env["ok"] is True
    # Toda tecnica devolvida traz a receita para essa ferramenta.
    for t in env["data"]["techniques"]:
        assert "recipe" in t


def test_list_unknown_field_returns_error():
    env = call("techniques.list", {"tool": "sd3", "surprise": True})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


# --- techniques.describe --------------------------------------------------

def test_describe_ghost_notes_with_superior_drummer_returns_notes_velocity():
    env = call("techniques.describe", {
        "name": "drums.ghost_notes", "tool": "superior_drummer",
    })
    assert env["ok"] is True
    d = env["data"]
    assert d["canonical"] == "drums.ghost_notes"
    assert d["tool"] == "superior_drummer"
    assert d["recipe"]["notes"] == [38]
    params = {p["name"]: p for p in d["parameters"]}
    assert params["velocity"]["range"] == [20, 45]


def test_describe_without_tool_returns_generic_and_warns():
    env = call("techniques.describe", {"name": "drums.ghost_notes"})
    assert env["ok"] is True
    assert env["data"]["tool"] == "generic"
    codes = [w["code"] for w in env["warnings"]]
    assert "W_NO_TOOL" in codes


def test_describe_unknown_technique_returns_error_with_hint():
    env = call("techniques.describe", {"name": "flanm"})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_TECHNIQUE_NOT_FOUND"
    assert "flam" in env["error"]["hint"]


def test_describe_canonical_name_works():
    env = call("techniques.describe", {"name": "drums.microtiming"})
    assert env["ok"] is True
    assert env["data"]["canonical"] == "drums.microtiming"


def test_describe_tool_without_recipe_warns_and_falls_back():
    env = call("techniques.describe", {
        "name": "microtiming", "tool": "reaper_wat",
    })
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    assert "W_NO_TOOL_RECIPE" in codes
    assert env["data"]["tool"] == "generic"


def test_describe_carries_source_manual():
    env = call("techniques.describe", {"name": "drums.ghost_notes"})
    assert env["ok"] is True
    assert env["data"]["source_manual"] == "tecnicas_bateria_midi.md"


def test_describe_ambiguous_bare_name_errors_with_candidates():
    """`ghost_notes` existe em bateria e em baixo.

    Escolher uma em silencio entregaria a receita da familia errada, e o MIDI
    sairia errado sem nenhum erro no caminho. O contrato manda errar com os
    candidatos.
    """
    env = call("techniques.describe", {"name": "ghost_notes"})

    assert env["ok"] is False
    assert env["error"]["code"] == "E_TECHNIQUE_AMBIGUOUS"
    assert env["error"]["path"] == "name"
    assert "bass.ghost_notes" in env["error"]["hint"]
    assert "drums.ghost_notes" in env["error"]["hint"]


def test_describe_ambiguous_name_is_resolved_by_target_tool():
    """A ferramenta-alvo desambigua quando so uma familia tem receita para ela."""
    drums = call("techniques.describe", {"name": "ghost_notes", "tool": "superior_drummer"})
    bass = call("techniques.describe", {"name": "ghost_notes", "tool": "modo_bass"})

    assert drums["ok"] is True
    assert drums["data"]["canonical"] == "drums.ghost_notes"
    assert bass["ok"] is True
    assert bass["data"]["canonical"] == "bass.ghost_notes"


@pytest.mark.parametrize(
    ("name", "tool", "canonical"),
    [
        ("palm_mute", "shreddage3", "guitar.palm_mute"),
        ("palm_mute", "modo_bass", "bass.palm_mute"),
        ("vibrato", "ample", "guitar.vibrato"),
        ("vibrato", "modo_bass", "bass.vibrato"),
        ("slide", "musiclab_reallpc", "guitar.slide"),
        ("slide", "modo_bass", "bass.slide"),
        ("hammer_pull", "shreddage3", "guitar.hammer_pull"),
        ("hammer_pull", "modo_bass", "bass.hammer_pull"),
    ],
)
def test_guitar_manual_collides_with_bass_and_the_tool_still_resolves(
    name: str, tool: str, canonical: str,
):
    """O manual de guitarra colide com o de baixo em quatro nomes.

    Sao tecnicas que existem de verdade nos dois instrumentos, com receitas
    completamente diferentes. Entregar a de baixo para uma guitarra produziria
    MIDI errado sem erro nenhum no caminho — a mesma classe de falha que ja
    aconteceu com `ghost_notes`.
    """
    env = call("techniques.describe", {"name": name, "tool": tool})

    assert env["ok"] is True
    assert env["data"]["canonical"] == canonical
    assert env["data"]["tool"] == tool


@pytest.mark.parametrize(
    "name", ["palm_mute", "vibrato", "slide", "hammer_pull"],
)
def test_names_shared_by_guitar_and_bass_are_ambiguous_without_a_tool(name: str):
    env = call("techniques.describe", {"name": name})

    assert env["ok"] is False
    assert env["error"]["code"] == "E_TECHNIQUE_AMBIGUOUS"
    assert f"bass.{name}" in env["error"]["hint"]
    assert f"guitar.{name}" in env["error"]["hint"]


@pytest.mark.parametrize(
    ("tool", "canonical"),
    [
        ("modo_bass", "bass.vibrato"),
        ("shreddage3", "guitar.vibrato"),
        ("rhodes", "keys.vibrato"),
    ],
)
def test_vibrato_exists_in_three_families_and_the_tool_still_resolves(
    tool: str, canonical: str,
):
    """`vibrato` e a primeira tecnica a existir em tres familias.

    Sao tres receitas incompativeis: no baixo e CC1 com o rate do MODO BASS,
    na guitarra e aftertouch ou LFO amostrado, e no teclado e um LFO cuja taxa
    fixa e justamente o que denuncia programacao. Escolher a errada em silencio
    produz MIDI plausivel e errado.
    """
    env = call("techniques.describe", {"name": "vibrato", "tool": tool})

    assert env["ok"] is True
    assert env["data"]["canonical"] == canonical


def test_vibrato_without_a_tool_lists_all_three_candidates():
    env = call("techniques.describe", {"name": "vibrato"})

    assert env["ok"] is False
    for canonical in ("bass.vibrato", "guitar.vibrato", "keys.vibrato"):
        assert canonical in env["error"]["hint"]
