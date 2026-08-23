"""Testes do scanner de presets (presets.py, US-012)."""

from __future__ import annotations

import json
from pathlib import Path

from tools import presets

# --- helpers ----------------------------------------------------------------

def _touch(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- Preset dataclass -------------------------------------------------------

def test_preset_roundtrip():
    p = presets.Preset(
        name="Desert Wind",
        plugin="Omnisphere",
        format="prt_a",
        path="/x/y/Desert Wind.prt_a",
        verified=True,
    )
    assert presets.Preset.from_dict(p.to_dict()) == p


def test_unverified_marks_verified_false():
    p = presets.unverified("Serum", "What The Pluck long")
    assert p.plugin == "Serum"
    assert p.name == "What The Pluck long"
    assert p.verified is False
    assert p.path is None
    assert p.format == "suggested"


# --- Omnisphere -------------------------------------------------------------

def test_scan_omnisphere_finds_patch_files(tmp_path):
    _touch(tmp_path / "Pads" / "Desert Wind.prt_a", "binary")
    _touch(tmp_path / "Pads" / "Ocean Deep.prt_b", "binary")
    _touch(tmp_path / "readme.txt", "ignore me")  # nao e patch
    found = presets.scan_omnisphere(tmp_path)
    names = sorted(p.name for p in found)
    assert names == ["Desert Wind", "Ocean Deep"]
    assert all(p.plugin == "Omnisphere" for p in found)
    assert all(p.verified for p in found)


def test_scan_omnisphere_missing_dir_returns_empty(tmp_path):
    assert presets.scan_omnisphere(tmp_path / "ghost") == []


# --- Logic ------------------------------------------------------------------

def test_scan_logic_scans_declared_subplugins(tmp_path):
    _touch(tmp_path / "Alchemy" / "Cinematic Bell.aupreset", "<plist/>")
    _touch(tmp_path / "ES2" / "Sub 808.aupreset", "<plist/>")
    _touch(tmp_path / "Sampler" / "Impact.exs", "<plist/>")
    _touch(tmp_path / "Retro Synth" / "Analog Pad.aupreset", "<plist/>")
    _touch(tmp_path / "Unknown Plugin" / "should-be-ignored.aupreset", "<plist/>")
    found = presets.scan_logic(tmp_path)
    by_plugin = {p.plugin: p for p in found}
    assert set(by_plugin) == {"Alchemy", "ES2", "Sampler", "Retro Synth"}
    assert by_plugin["Sampler"].format == "exs"
    assert all(p.verified for p in found)


def test_scan_logic_missing_dir_returns_empty(tmp_path):
    assert presets.scan_logic(tmp_path / "ghost") == []


# --- Kontakt ----------------------------------------------------------------

def test_scan_kontakt_finds_nki(tmp_path):
    _touch(tmp_path / "Nord Piano 3" / "Grand.nki", "binary")
    _touch(tmp_path / "Nord Piano 3" / "manual.pdf", "ignore")
    found = presets.scan_kontakt(tmp_path)
    assert [p.name for p in found] == ["Grand"]
    assert found[0].plugin == "Kontakt"
    assert found[0].format == "nki"


# --- Serum ------------------------------------------------------------------

def test_scan_serum_finds_fxp(tmp_path):
    _touch(tmp_path / "Pluck" / "What The Pluck long.fxp", "binary")
    found = presets.scan_serum(tmp_path)
    assert [p.name for p in found] == ["What The Pluck long"]
    assert found[0].plugin == "Serum"


def test_scan_serum_absent_does_not_raise(tmp_path):
    # Serum ainda nao esta instalado na maquina do usuario. Isso e o comportamento
    # que a skill precisa suportar sem quebrar.
    assert presets.scan_serum(tmp_path / "nao-existe") == []


def test_scan_serum_default_path_never_raises():
    # Sem passar root, cai no DEFAULT_SERUM. Ausencia real do disco nao pode
    # levantar excecao, so devolver lista vazia.
    result = presets.scan_serum()
    assert isinstance(result, list)


# --- Vital ------------------------------------------------------------------

def test_scan_vital_finds_json_presets(tmp_path):
    _touch(tmp_path / "Bass" / "Sub Growl.vital", json.dumps({"preset_name": "Sub Growl"}))
    found = presets.scan_vital(tmp_path)
    assert [p.name for p in found] == ["Sub Growl"]
    assert found[0].plugin == "Vital"


def test_scan_vital_skips_corrupted_json(tmp_path):
    _touch(tmp_path / "ok.vital", json.dumps({"preset_name": "ok"}))
    _touch(tmp_path / "broken.vital", "{not json")
    found = presets.scan_vital(tmp_path)
    assert [p.name for p in found] == ["ok"]


# --- Addictive Drums 2 ------------------------------------------------------

def test_scan_addictive_drums_walks_multiple_roots(tmp_path):
    r1 = tmp_path / "app-support"
    r2 = tmp_path / "audio-presets"
    _touch(r1 / "Kits" / "Modern Metal.adkit", "binary")
    _touch(r2 / "Presets" / "Ambient.adpak", "binary")
    found = presets.scan_addictive_drums([r1, r2])
    names = sorted(p.name for p in found)
    assert names == ["Ambient", "Modern Metal"]
    assert all(p.plugin == "Addictive Drums 2" for p in found)


def test_scan_addictive_drums_skips_missing_roots(tmp_path):
    r_ok = tmp_path / "ok"
    _touch(r_ok / "Modern Metal.adkit", "binary")
    result = presets.scan_addictive_drums([tmp_path / "ghost", r_ok])
    assert [p.name for p in result] == ["Modern Metal"]


# --- scan_all ---------------------------------------------------------------

def test_scan_all_populates_every_plugin_key(tmp_path):
    omni = tmp_path / "omni"
    logic = tmp_path / "logic"
    kontakt = tmp_path / "kontakt"
    vital = tmp_path / "vital"
    ad2 = tmp_path / "ad2"
    _touch(omni / "Desert Wind.prt_a", "binary")
    _touch(logic / "Alchemy" / "Cinematic Bell.aupreset", "<plist/>")
    _touch(kontakt / "Grand.nki", "binary")
    _touch(vital / "Sub.vital", json.dumps({"preset_name": "Sub"}))
    _touch(ad2 / "Kit.adkit", "binary")

    roots = presets.PresetRoots(
        omnisphere=omni,
        logic=logic,
        kontakt=kontakt,
        serum=tmp_path / "serum-missing",  # ausencia proposital
        vital=vital,
        addictive=(ad2,),
    )
    result = presets.scan_all(roots)

    expected_keys = {
        "Omnisphere", "Alchemy", "ES2", "Sampler", "Retro Synth",
        "Kontakt", "Serum", "Vital", "Addictive Drums 2", "Nexus",
    }
    assert set(result) == expected_keys
    assert [p.name for p in result["Omnisphere"]] == ["Desert Wind"]
    assert [p.name for p in result["Alchemy"]] == ["Cinematic Bell"]
    assert [p.name for p in result["Kontakt"]] == ["Grand"]
    assert [p.name for p in result["Vital"]] == ["Sub"]
    assert [p.name for p in result["Addictive Drums 2"]] == ["Kit"]
    # Serum e Nexus vazios, mas presentes.
    assert result["Serum"] == []
    assert result["Nexus"] == []


def test_scan_all_never_returns_verified_for_nexus():
    # A chave 'Nexus' NUNCA carrega preset verificado: nao ha como escanear
    # a base binaria fechada. Sugestoes devem vir por `unverified`.
    result = presets.scan_all()
    assert result[presets.NEXUS_PLUGIN_NAME] == []
    suggestion = presets.unverified(presets.NEXUS_PLUGIN_NAME, "Pluck Modern")
    assert suggestion.verified is False


def test_scan_all_default_paths_do_not_raise():
    # Rodar sem overrides bate os DEFAULT_* que talvez nao existam. Nao pode levantar.
    result = presets.scan_all()
    assert "Omnisphere" in result
    assert isinstance(result["Serum"], list)
