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


# --- catalogo de capacidades (issue #74) ----------------------------------

def test_list_entries_carry_implemented_and_level():
    """Toda entrada declara `implemented` (bool) e `level`.

    O nivel e `humanize` ou `technique` quando implementada, e `null` quando
    a tecnica so existe no manual. Sem esses campos o consumidor (skill,
    harness, futuro MCP) nao consegue distinguir capacidade real de
    capacidade futura, e o brief acaba autorizando tecnica sem aplicador.
    """
    env = call("techniques.list", {})
    assert env["ok"] is True
    for t in env["data"]["techniques"]:
        assert isinstance(t["implemented"], bool), t
        if t["implemented"]:
            assert t["level"] in {"humanize", "technique"}, t
        else:
            assert t["level"] is None, t


def test_list_marks_bass_slide_as_not_implemented():
    """`bass.slide` esta documentado no manual mas fora do motor.

    A issue #74 exige que apareca no catalogo como capacidade futura
    (`implemented=False`), nunca como algo aplicavel agora — o vicio
    aceitar-e-ignorar ja foi rejeitado nesta base.
    """
    env = call("techniques.list", {"family": "bass"})
    assert env["ok"] is True
    entries = {t["canonical"]: t for t in env["data"]["techniques"]}
    assert "bass.slide" in entries
    assert entries["bass.slide"]["implemented"] is False
    assert entries["bass.slide"]["level"] is None


def test_list_marks_keys_melody_lead_as_not_implemented():
    """`keys.melody_lead` e uma das dez tecnicas de teclas documentadas mas
    fora do motor (ver `AGENTS.md` e o inventario da issue #14)."""
    env = call("techniques.list", {"family": "keys"})
    entries = {t["canonical"]: t for t in env["data"]["techniques"]}
    assert "keys.melody_lead" in entries
    assert entries["keys.melody_lead"]["implemented"] is False
    assert entries["keys.melody_lead"]["level"] is None


def test_list_marks_guitar_natural_harmonics_as_not_implemented():
    """`guitar.natural_harmonics` exigiria transpor a nota estrutural pelo
    intervalo do parcial (mudanca de pitch estrutural, proibida fora da
    excecao de bateria) — fica documentada mas sem aplicador, mesmo
    precedente de `bass.harmonic`. O catalogo deixa isso explicito em vez
    de deixar o brief autorizar algo que o motor nao executa."""
    env = call("techniques.list", {"family": "guitar"})
    entries = {t["canonical"]: t for t in env["data"]["techniques"]}
    assert "guitar.natural_harmonics" in entries
    assert entries["guitar.natural_harmonics"]["implemented"] is False
    assert entries["guitar.natural_harmonics"]["level"] is None


def test_list_marks_guitar_palm_mute_as_implemented_technique_level():
    env = call("techniques.list", {"family": "guitar"})
    entries = {t["canonical"]: t for t in env["data"]["techniques"]}
    assert entries["guitar.palm_mute"]["implemented"] is True
    assert entries["guitar.palm_mute"]["level"] == "technique"
    assert entries["guitar.double_tracking"]["implemented"] is True
    assert entries["guitar.double_tracking"]["level"] == "technique"


def test_list_marks_drums_ghost_notes_as_implemented_technique_level():
    env = call("techniques.list", {"family": "drums"})
    entries = {t["canonical"]: t for t in env["data"]["techniques"]}
    assert entries["drums.ghost_notes"]["implemented"] is True
    assert entries["drums.ghost_notes"]["level"] == "technique"
    assert entries["drums.microtiming"]["implemented"] is True
    assert entries["drums.microtiming"]["level"] == "humanize"


def test_list_implemented_only_filters_documented_capacities():
    """`implemented_only=True` esconde tecnica documentada sem aplicador.

    O catalogo e derivado do indice dos manuais e do registro real do motor,
    nunca uma lista paralela: o total sob esse filtro tem que bater com
    `SUPPORTED_TECHNIQUES`, e nao pode haver `implemented=False` na saida.
    """
    from tools.techniques import SUPPORTED_TECHNIQUES

    env = call("techniques.list", {"implemented_only": True})
    assert env["ok"] is True
    canonicals = [t["canonical"] for t in env["data"]["techniques"]]
    assert set(canonicals) == set(SUPPORTED_TECHNIQUES)
    for t in env["data"]["techniques"]:
        assert t["implemented"] is True
        assert t["level"] in {"humanize", "technique"}


def test_list_default_still_returns_documented_but_unimplemented():
    """Sem `implemented_only`, o catalogo continua enumerando capacidade
    futura — bass.slide, guitar.natural_harmonics, keys.melody_lead
    precisam aparecer para o consumidor saber que existem como pesquisa,
    mesmo que nao possam ser autorizadas."""
    env = call("techniques.list", {})
    canonicals = {t["canonical"] for t in env["data"]["techniques"]}
    assert {"bass.slide", "guitar.natural_harmonics", "keys.melody_lead"} <= canonicals


def test_list_all_24_implemented_techniques_appear_as_implemented():
    """As 24 tecnicas atualmente executaveis (drums 8, bass 7, guitar 5,
    keys 4) precisam aparecer marcadas como implementadas. Regressao aqui
    denuncia ou um aplicador registrado sem manual ou o catalogo caido
    fora de sincronia com `SUPPORTED_TECHNIQUES`."""
    from tools.techniques import SUPPORTED_TECHNIQUES

    assert len(SUPPORTED_TECHNIQUES) == 24
    env = call("techniques.list", {})
    entries = {t["canonical"]: t for t in env["data"]["techniques"]}
    for canonical in SUPPORTED_TECHNIQUES:
        assert canonical in entries, (
            f"{canonical} registrada no motor mas ausente do catalogo"
        )
        assert entries[canonical]["implemented"] is True
        assert entries[canonical]["level"] in {"humanize", "technique"}


def test_list_catalog_is_derived_from_index_and_registry_not_hardcoded():
    """AGENTS.md manda derivar o catalogo do indice + registro real, nunca
    de lista paralela. O total sem filtro tem que bater com o indice inteiro
    dos manuais, e as tecnicas implementadas com o registro."""
    from tools.techniques import SUPPORTED_TECHNIQUES, build_index

    idx = build_index()
    env = call("techniques.list", {})
    catalog = env["data"]["techniques"]
    assert len(catalog) == len(idx.techniques)
    implemented_in_catalog = {
        t["canonical"] for t in catalog if t["implemented"]
    }
    assert implemented_in_catalog == set(SUPPORTED_TECHNIQUES)


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


def test_describe_accepts_parameter_value_object():
    env = call("techniques.describe", {
        "name": "drums.buzz_roll",
        "tool": "superior_drummer",
    })

    assert env["ok"] is True
    params = {p["name"]: p for p in env["data"]["parameters"]}
    assert params["velocity_ramp"]["value"]["shape"] == "linear"


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
