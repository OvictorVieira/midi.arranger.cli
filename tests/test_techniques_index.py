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
        "drums.accented_roll",
        "drums.articulation_diff",
        "drums.buzz_roll",
        "drums.cymbal_choke",
        "drums.flam",
        "drums.ghost_notes",
        "drums.microtiming",
        "guitar.bend",
        "guitar.chord_voicing",
        "guitar.dead_notes",
        "guitar.dive_bomb",
        "guitar.double_tracking",
        "guitar.drop_tuning",
        "guitar.hammer_pull",
        "guitar.natural_harmonics",
        "guitar.palm_mute",
        "guitar.pick_scrape",
        "guitar.picking_direction",
        "guitar.pinch_harmonic",
        "guitar.power_chord",
        "guitar.rake",
        "guitar.slide",
        "guitar.track_offset",
        "guitar.tremolo_picking",
        "guitar.vibrato",
        "keys.bass_anticipation",
        "keys.damper_pedal",
        "keys.expression",
        "keys.hammond_dynamics",
        "keys.human_articulation",
        "keys.modulation",
        "keys.pitch_bend",
        "keys.rhodes_touch",
        "keys.vibrato",
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


def test_accented_roll_velocities_agree_with_the_accent_hierarchy():
    """O manual afirma que os valores do rufo corroboram a hierarquia de acento.

    Se as duas fontes discordassem, a afirmacao de corroboracao seria falsa e
    o arranjador teria duas verdades sobre o que e acento e o que e suave.
    """
    idx = build_index(MANUALS_DIR)
    roll = {p.name: p for p in idx.get("drums.accented_roll").parameters}
    hierarquia = {p.name: p for p in idx.get("drums.accent_hierarchy").parameters}

    lo, hi = hierarquia["accent"].range
    assert lo <= roll["velocity_acento"].value <= hi
    lo, hi = hierarquia["soft"].range
    assert lo <= roll["velocity_suave"].value <= hi


def test_by_family_filters_correctly():
    idx = build_index(MANUALS_DIR)
    assert len(idx.by_family("drums")) == 8
    assert len(idx.by_family("bass")) == 10
    assert len(idx.by_family("keys")) == 14
    assert len(idx.by_family("guitar")) == 18
    assert idx.by_family("vocals") == ()


# --- manual de guitarra ----------------------------------------------------

def test_guitar_manual_declares_gaps_without_faking_verified():
    """Parametro so com `name` documenta uma lacuna sem inventar numero.

    Um parametro sem value e sem range nao tem numero para verificar, entao
    nao derruba `verified` — mas fica visivel no indice para quem for
    programar a tecnica saber que ali nao ha fonte."""
    idx = build_index(MANUALS_DIR)
    dive = idx.get("guitar.dive_bomb")
    assert dive is not None
    assert dive.verified is True
    lacunas = {p.name for p in dive.parameters if p.source is None}
    assert lacunas == {"profundidade_semitons", "duracao_ms", "formato_da_curva"}
    for name in lacunas:
        p = next(p for p in dive.parameters if p.name == name)
        assert p.value is None and p.range is None, (
            f"{name} declara lacuna mas carrega numero — ou tem fonte, ou nao tem numero"
        )


@pytest.mark.parametrize(
    "canonical",
    ["guitar.vibrato", "guitar.tremolo_picking"],
)
def test_guitar_convention_only_techniques_are_marked_unverified(canonical: str):
    """Rate de vibrato e taxa de tremolo so tem fonte de blog de ensino.

    O bloco declara `verified: false` de proposito: a URL existe, entao o
    parser nao derrubaria sozinho, mas convencao nao e medicao e quem consome
    precisa enxergar a diferenca."""
    idx = build_index(MANUALS_DIR)
    tech = idx.get(canonical)
    assert tech is not None
    assert tech.verified is False
    assert any("CONVENCAO" in (p.source or "") for p in tech.parameters)


def test_guitar_tunings_are_ordered_low_to_high_and_anchor_on_e_standard():
    """A afinacao e o piso absoluto de altura, entao a tabela precisa estar
    correta e ordenada — nota abaixo da corda mais grave nao existe."""
    idx = build_index(MANUALS_DIR)
    tuning = idx.get("guitar.drop_tuning")
    assert tuning is not None
    afinacoes = tuning.tools["generic"]["afinacoes"]

    assert afinacoes["e_padrao"] == [40, 45, 50, 55, 59, 64]
    # Drop D e E padrao com a 6a corda um tom abaixo, e so ela.
    assert afinacoes["drop_d"] == [38, *afinacoes["e_padrao"][1:]]

    for nome, cordas in afinacoes.items():
        assert cordas == sorted(cordas), f"{nome} nao esta do grave para o agudo"
        assert len(cordas) in (6, 7, 8), f"{nome} tem {len(cordas)} cordas"
        assert all(0 <= n <= 127 for n in cordas), f"{nome} sai da faixa MIDI"


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
