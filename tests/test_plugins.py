"""Testes do inventario de plugins (plugins.py)."""

from __future__ import annotations

import os
import plistlib
import time
from pathlib import Path

from tools import plugins

# --- helpers ----------------------------------------------------------------

def _make_bundle(root: Path, name: str, ext: str, *, bundle_id: str | None = None, cf_name: str | None = None) -> Path:
    """Cria um bundle sintetico `<name><ext>/Contents/Info.plist`.

    Sem Info.plist se `bundle_id` e `cf_name` sao None.
    """
    bundle = root / f"{name}{ext}"
    (bundle / "Contents").mkdir(parents=True, exist_ok=True)
    if bundle_id or cf_name:
        payload: dict = {}
        if bundle_id:
            payload["CFBundleIdentifier"] = bundle_id
        if cf_name:
            payload["CFBundleName"] = cf_name
        with (bundle / "Contents" / "Info.plist").open("wb") as fp:
            plistlib.dump(payload, fp)
    return bundle


# --- classificacao ----------------------------------------------------------

def test_classify_known_plugin_returns_declared_roles():
    roles = plugins.classify("Omnisphere")
    assert "pad" in roles
    assert "arp" in roles
    assert "strings" in roles


def test_classify_is_case_insensitive():
    assert plugins.classify("ALCHEMY") == plugins.classify("alchemy")


def test_classify_falls_back_to_keyword():
    assert "piano" in plugins.classify("SuperGrand Piano XL")
    assert "strings" in plugins.classify("Session Strings Pro")
    assert "drums" in plugins.classify("MegaDrumKit")


def test_classify_unknown_returns_empty_tuple():
    assert plugins.classify("Zzz Unknown Widget 9000") == ()


def test_classify_generic_keyword_uses_first_match_only():
    roles = plugins.classify("Piano Strings Combo")
    # 'piano' aparece antes de 'string' em GENERIC_KEYWORD_ROLES
    assert roles == ("piano",)


def test_classify_known_takes_precedence_over_generic():
    # 'Alchemy' bate na tabela conhecida; nao deve rodar generico
    roles = plugins.classify("Alchemy")
    assert set(roles) == {"pad", "arp", "fx", "sampler"}


# --- varredura --------------------------------------------------------------

def test_scan_reads_bundles_and_extracts_metadata(tmp_path):
    _make_bundle(
        tmp_path, "Omnisphere", ".component",
        bundle_id="com.spectrasonics.Omnisphere",
        cf_name="Omnisphere",
    )
    _make_bundle(
        tmp_path, "Serum", ".vst3",
        bundle_id="com.xferrecords.Serum",
        cf_name="Serum",
    )
    inventory = plugins.scan([tmp_path])
    scanned = [p for p in inventory if p.format != "stock"]
    by_name = {p.name: p for p in scanned}
    assert "Omnisphere" in by_name
    assert by_name["Omnisphere"].format == "AU"
    assert by_name["Omnisphere"].manufacturer == "Spectrasonics"
    assert "pad" in by_name["Omnisphere"].roles
    assert by_name["Serum"].format == "VST3"
    assert by_name["Serum"].manufacturer == "Xferrecords"


def test_scan_ignores_files_without_plugin_extension(tmp_path):
    (tmp_path / "not-a-plugin.txt").write_text("nope", encoding="utf-8")
    (tmp_path / "AnotherThing").mkdir()  # dir sem extensao valida
    _make_bundle(tmp_path, "Alchemy", ".component")
    scanned = [p for p in plugins.scan([tmp_path]) if p.format != "stock"]
    assert [p.name for p in scanned] == ["Alchemy"]


def test_scan_handles_missing_directories(tmp_path):
    # nenhum diretorio existe -> scan devolve so os stock (nao levanta)
    missing = tmp_path / "ghost"
    inventory = plugins.scan([missing])
    assert all(p.format == "stock" for p in inventory)
    assert len(inventory) == len(plugins.LOGIC_STOCK)


def test_scan_bundle_without_info_plist_uses_stem_as_name(tmp_path):
    _make_bundle(tmp_path, "MyCustomSynth", ".vst")
    inventory = plugins.scan([tmp_path])
    scanned = [p for p in inventory if p.format != "stock"]
    assert len(scanned) == 1
    assert scanned[0].name == "MyCustomSynth"
    assert scanned[0].manufacturer is None
    assert scanned[0].format == "VST"


# --- stock ------------------------------------------------------------------

def test_stock_instruments_always_included(tmp_path):
    inventory = plugins.scan([tmp_path])
    stock_names = {p.name for p in inventory if p.format == "stock"}
    for expected in ("Alchemy", "ES2", "Retro Synth", "Sampler",
                     "Studio Strings", "Vintage Keys", "Drum Machine Designer"):
        assert expected in stock_names, expected


def test_stock_dedup_when_plugin_bundle_shares_name(tmp_path):
    # bundle 'Alchemy.component' presente: nao deve gerar stock duplicado
    _make_bundle(tmp_path, "Alchemy", ".component", cf_name="Alchemy")
    inventory = plugins.scan([tmp_path])
    alchemy_entries = [p for p in inventory if p.name.lower() == "alchemy"]
    assert len(alchemy_entries) == 1
    assert alchemy_entries[0].format == "AU"


def test_stock_manufacturer_is_apple(tmp_path):
    inventory = plugins.scan([tmp_path])
    for p in inventory:
        if p.format == "stock":
            assert p.manufacturer == "Apple"
            assert p.path is None


def test_uninstalled_third_party_plugin_never_appears(tmp_path):
    # nenhum bundle real -> nada de terceiros no inventario
    inventory = plugins.scan([tmp_path])
    third_party = [p for p in inventory if p.format != "stock"]
    assert third_party == []


# --- cache ------------------------------------------------------------------

def test_cache_is_written_on_first_scan(tmp_path):
    cache = tmp_path / "cache.json"
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _make_bundle(plugin_dir, "Alchemy", ".component")
    plugins.load_or_scan(cache, [plugin_dir])
    assert cache.is_file()


def test_cache_hit_avoids_rescan(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _make_bundle(plugin_dir, "Omnisphere", ".component", cf_name="Omnisphere")
    plugins.load_or_scan(cache, [plugin_dir])

    calls: list[int] = []
    real_scan = plugins.scan
    def spy(dirs):
        calls.append(1)
        return real_scan(dirs)
    monkeypatch.setattr(plugins, "scan", spy)
    plugins.load_or_scan(cache, [plugin_dir])
    assert calls == []


def test_cache_invalidates_when_directory_changes(tmp_path):
    cache = tmp_path / "cache.json"
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _make_bundle(plugin_dir, "Omnisphere", ".component", cf_name="Omnisphere")
    first = plugins.load_or_scan(cache, [plugin_dir])
    first_names = sorted(p.name for p in first if p.format != "stock")

    # forca mtime distinto: adiciona bundle e bumpa mtime do diretorio
    time.sleep(0.01)
    _make_bundle(plugin_dir, "Serum", ".vst3", cf_name="Serum")
    new_mtime = plugin_dir.stat().st_mtime + 1
    os.utime(plugin_dir, (new_mtime, new_mtime))

    second = plugins.load_or_scan(cache, [plugin_dir])
    second_names = sorted(p.name for p in second if p.format != "stock")
    assert "Serum" in second_names
    assert second_names != first_names


def test_cache_survives_missing_directory(tmp_path):
    cache = tmp_path / "cache.json"
    missing = tmp_path / "ghost"
    inventory = plugins.load_or_scan(cache, [missing])
    # deve funcionar (retornar so stock) e escrever cache
    assert cache.is_file()
    assert all(p.format == "stock" for p in inventory)
    # segunda chamada deve bater cache
    again = plugins.load_or_scan(cache, [missing])
    assert [p.name for p in again] == [p.name for p in inventory]


def test_cache_corrupted_falls_back_to_scan(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("{not json", encoding="utf-8")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _make_bundle(plugin_dir, "Alchemy", ".component")
    inventory = plugins.load_or_scan(cache, [plugin_dir])
    assert any(p.name == "Alchemy" and p.format == "AU" for p in inventory)


# --- dataclass round-trip ---------------------------------------------------

def test_plugin_to_dict_and_from_dict_roundtrip():
    p = plugins.Plugin(
        name="Serum",
        manufacturer="Xfer",
        format="VST3",
        path="/x/y/Serum.vst3",
        roles=("arp", "fx"),
    )
    restored = plugins.Plugin.from_dict(p.to_dict())
    assert restored == p
