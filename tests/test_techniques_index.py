"""Testes do indice de tecnicas derivado dos manuais (US-005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.techniques import (
    Technique,
    TechniqueError,
    build_index,
    parse_manual,
)

MANUALS_DIR = (
    Path(__file__).resolve().parent.parent / "knowledge" / "tecnicas"
)


# --- manual real de bateria ------------------------------------------------

def test_index_finds_all_techniques_from_versioned_manuals():
    """Os manuais versionados tem exatamente os blocos que documentamos.

    Este teste amarra os canonicos que a US-005 exige exista no indice.
    Adicionar um bloco novo no manual quebra o teste — atualize-o AO adicionar
    a tecnica, para o time saber o que passou a existir."""
    idx = build_index(MANUALS_DIR)
    canonicals = sorted(t.canonical for t in idx.techniques)
    assert canonicals == sorted([
        "bass.attack_style",
        "bass.ghost_notes",
        "bass.hammer_pull",
        "bass.harmonic",
        "bass.let_ring",
        "bass.palm_mute",
        "bass.slide",
        "bass.string_selection",
        "bass.velocity_contour",
        "bass.vibrato",
        "drums.accent_hierarchy",
        "drums.articulation_diff",
        "drums.buzz_roll",
        "drums.cymbal_choke",
        "drums.flam",
        "drums.ghost_notes",
        "drums.microtiming",
        "keys.bass_anticipation",
        "keys.hand_asynchrony",
        "keys.melody_lead",
        "keys.rolled_chord",
        "keys.syncopated_pedal",
        "keys.voice_dynamics",
    ])


def test_ghost_notes_has_expected_parameters_and_generic_notes():
    idx = build_index(MANUALS_DIR)
    gn = idx.get("drums.ghost_notes")
    assert gn is not None
    assert gn.verified is True
    p = {p.name: p for p in gn.parameters}
    assert p["velocity"].range == (20, 45)
    assert p["velocity"].source is not None
    assert gn.tools["generic"]["notes"] == [38]
    assert gn.tools["addictive_drums"]["notes"] == [38, 40]


def test_technique_marked_unverified_when_any_param_has_no_source():
    """Flam declara `verified: true` no bloco mas tem um parametro (razao)
    sem fonte — o indice DERRUBA verified para False."""
    idx = build_index(MANUALS_DIR)
    flam = idx.get("drums.flam")
    assert flam is not None
    assert flam.verified is False


def test_by_family_filters_correctly():
    idx = build_index(MANUALS_DIR)
    assert len(idx.by_family("drums")) == 7
    assert len(idx.by_family("bass")) == 10
    assert len(idx.by_family("keys")) == 6
    assert idx.by_family("guitar") == ()


# --- manuals malformados ---------------------------------------------------

def test_manual_without_technique_blocks_fails(tmp_path: Path):
    m = tmp_path / "empty.md"
    m.write_text("# vazio\n\nsem blocos aqui.\n", encoding="utf-8")
    with pytest.raises(TechniqueError, match="nenhum bloco"):
        parse_manual(m)


def test_manual_with_bad_json_fails(tmp_path: Path):
    m = tmp_path / "bad.md"
    m.write_text(
        "# manual\n\n```technique\n{not json}\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(TechniqueError, match="JSON"):
        parse_manual(m)


def test_manual_missing_required_field_fails(tmp_path: Path):
    m = tmp_path / "bad.md"
    m.write_text(
        "```technique\n"
        '{"name": "x", "family": "y", "summary": "z"}\n'
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(TechniqueError, match="verified"):
        parse_manual(m)


def test_manual_with_unknown_top_field_fails(tmp_path: Path):
    m = tmp_path / "bad.md"
    m.write_text(
        "```technique\n"
        '{"name": "x", "family": "y", "summary": "z", "verified": true, "wat": 1}\n'
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(TechniqueError, match="desconhecidos"):
        parse_manual(m)


def test_parameter_with_unknown_field_fails(tmp_path: Path):
    m = tmp_path / "bad.md"
    m.write_text(
        "```technique\n"
        '{"name": "x", "family": "y", "summary": "z", "verified": true, '
        '"parameters": [{"name": "p", "surprise": 1}]}\n'
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(TechniqueError, match="parametro tem"):
        parse_manual(m)


# --- diretorio ------------------------------------------------------------

def test_index_picks_up_new_manual_without_code_change(tmp_path: Path):
    """Copia o manual real e adiciona um novo — o indice cresce sem tocar Python."""
    src = MANUALS_DIR / "tecnicas_bateria_midi.md"
    dest_dir = tmp_path / "tecnicas"
    dest_dir.mkdir()
    (dest_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    new_manual = dest_dir / "tecnicas_bass_midi.md"
    new_manual.write_text(
        "# Baixo\n\n"
        "```technique\n"
        '{\n'
        '  "name": "slapping",\n'
        '  "family": "bass",\n'
        '  "summary": "Bater com o polegar; velocity alta e transiente destacado.",\n'
        '  "verified": true,\n'
        '  "parameters": [\n'
        '    {"name": "velocity", "range": [110, 127], "source": "manual do usuario"}\n'
        '  ],\n'
        '  "tools": {"generic": {"note": "usar range acima de 110"}}\n'
        '}\n'
        "```\n",
        encoding="utf-8",
    )

    idx = build_index(dest_dir)
    assert idx.get("bass.slapping") is not None
    # As tecnicas do manual antigo ainda estao la.
    assert idx.get("drums.ghost_notes") is not None


def test_duplicate_technique_across_manuals_fails(tmp_path: Path):
    dest_dir = tmp_path / "tecnicas"
    dest_dir.mkdir()
    block = (
        "```technique\n"
        '{"name": "x", "family": "y", "summary": "s", "verified": true}\n'
        "```\n"
    )
    (dest_dir / "a.md").write_text(block, encoding="utf-8")
    (dest_dir / "b.md").write_text(block, encoding="utf-8")
    with pytest.raises(TechniqueError, match="duplicada"):
        build_index(dest_dir)


def test_empty_directory_fails(tmp_path: Path):
    dest_dir = tmp_path / "vazio"
    dest_dir.mkdir()
    with pytest.raises(TechniqueError):
        build_index(dest_dir)


def test_missing_directory_fails(tmp_path: Path):
    with pytest.raises(TechniqueError, match="nao existe"):
        build_index(tmp_path / "does-not-exist")


# --- Technique dataclass ---------------------------------------------------

def test_technique_to_dict_shape():
    t = Technique(
        canonical="a.b", name="b", family="a",
        summary="s", verified=True, description="d",
    )
    d = t.to_dict()
    assert set(d) == {
        "canonical", "name", "family", "summary", "verified",
        "description", "parameters", "tools", "source_manual",
    }


# --- rotas de erro do _parse_block/_parse_parameter ---------------------

def _one_block_manual(tmp_path: Path, block_json: str) -> Path:
    m = tmp_path / "bad.md"
    m.write_text(f"```technique\n{block_json}\n```\n", encoding="utf-8")
    return m


def test_block_not_object_json_fails(tmp_path: Path):
    m = _one_block_manual(tmp_path, "[1,2,3]")
    with pytest.raises(TechniqueError, match="objeto JSON"):
        parse_manual(m)


def test_name_must_be_nonempty_string(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name": "", "family": "y", "summary": "s", "verified": true}',
    )
    with pytest.raises(TechniqueError, match="`name`"):
        parse_manual(m)


def test_family_must_be_nonempty_string(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name": "x", "family": "", "summary": "s", "verified": true}',
    )
    with pytest.raises(TechniqueError, match="`family`"):
        parse_manual(m)


def test_summary_must_be_nonempty_string(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name": "x", "family": "y", "summary": "", "verified": true}',
    )
    with pytest.raises(TechniqueError, match="`summary`"):
        parse_manual(m)


def test_verified_must_be_bool(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name": "x", "family": "y", "summary": "s", "verified": "yes"}',
    )
    with pytest.raises(TechniqueError, match="verified"):
        parse_manual(m)


def test_tools_must_be_object(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name":"x","family":"y","summary":"s","verified":true,"tools":[]}',
    )
    with pytest.raises(TechniqueError, match="`tools`"):
        parse_manual(m)


def test_tool_recipe_must_be_object(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name":"x","family":"y","summary":"s","verified":true,'
        '"tools":{"generic":"nope"}}',
    )
    with pytest.raises(TechniqueError, match="tools"):
        parse_manual(m)


def test_description_must_be_string(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name":"x","family":"y","summary":"s","verified":true,"description":123}',
    )
    with pytest.raises(TechniqueError, match="description"):
        parse_manual(m)


def test_parameter_must_be_object(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name":"x","family":"y","summary":"s","verified":true,'
        '"parameters":["not-a-dict"]}',
    )
    with pytest.raises(TechniqueError, match="objeto"):
        parse_manual(m)


def test_parameter_name_must_be_nonempty(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name":"x","family":"y","summary":"s","verified":true,'
        '"parameters":[{"name":""}]}',
    )
    with pytest.raises(TechniqueError, match="parametro.name"):
        parse_manual(m)


def test_parameter_range_must_be_two_items(tmp_path: Path):
    m = _one_block_manual(
        tmp_path,
        '{"name":"x","family":"y","summary":"s","verified":true,'
        '"parameters":[{"name":"p","range":[1,2,3]}]}',
    )
    with pytest.raises(TechniqueError, match="range"):
        parse_manual(m)
