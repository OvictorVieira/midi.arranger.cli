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


def test_unverified_for_nexus_still_marks_verified_false():
    # `unverified` continua sendo a fabrica correta pra sugestao vinda de
    # conhecimento do modelo, incluindo Nexus.
    suggestion = presets.unverified(presets.NEXUS_PLUGIN_NAME, "Pluck Modern")
    assert suggestion.verified is False
    assert suggestion.plugin == presets.NEXUS_PLUGIN_NAME


def test_scan_all_default_paths_do_not_raise(monkeypatch, tmp_path):
    # Rodar sem overrides bate os DEFAULT_* que talvez nao existam. Nao pode levantar.
    # Aponta env pra tmp_path pra nao varrer o Mac real durante o teste.
    monkeypatch.setenv("MIDI_ARRANGER_PRESET_ROOTS", "")
    monkeypatch.setenv("SPECTRASONICS_STEAM_ROOT", "")
    result = presets.scan_all()
    assert "Omnisphere" in result
    assert isinstance(result["Serum"], list)


# --- sweep generico ---------------------------------------------------------

def test_sweep_finds_nexus_fxp_in_system_library(tmp_path):
    # Estrutura como `/Library/Audio/Presets/reFX/NEXUS library/Presets/<Pack>/*.fxp`
    root = tmp_path / "Audio" / "Presets"
    pack = root / "reFX" / "NEXUS library" / "Presets" / "XP Artist Series Steve Aoki"
    _touch(pack / "LD Wack.fxp", "binary")
    _touch(pack / "SQ Caked SC.fxp", "binary")
    _touch(pack / "cover.png", "not a preset")  # ignorado
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    found, opaque = presets.sweep(pr)
    names = sorted(p.name for p in found)
    assert names == ["LD Wack", "SQ Caked SC"]
    # Alias `NEXUS library` -> `Nexus`
    assert all(p.plugin == "Nexus" for p in found)
    assert all(p.vendor == "reFX" for p in found)
    assert all(p.verified for p in found)
    assert opaque == []


def test_sweep_finds_fabfilter_ffp_in_documents(tmp_path):
    root = tmp_path / "Documents"
    _touch(root / "FabFilter" / "Pro-Q 3" / "Presets" / "Vocal Boost.ffp", "b")
    _touch(root / "FabFilter" / "Saturn 2" / "Warm.ffp", "b")
    _touch(root / "MyProjects" / "notes.ffp", "should be ignored — vendor not whitelisted")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    found, _ = presets.sweep(pr)
    by_plugin = {p.plugin: p for p in found}
    assert set(by_plugin) == {"Pro-Q 3", "Saturn 2"}
    assert all(p.vendor == "FabFilter" for p in found)


def test_sweep_finds_neural_dsp_xml_only_under_neural_dsp_marker(tmp_path):
    root = tmp_path / "Library" / "Audio" / "Presets"
    _touch(root / "Neural DSP" / "Archetype Gojira" / "Djent.xml", "<xml/>")
    _touch(root / "SomeoneElse" / "random.xml", "not a preset")  # sem marcador
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    found, _ = presets.sweep(pr)
    assert [p.name for p in found] == ["Djent"]
    assert found[0].plugin == "Archetype Gojira"
    assert found[0].vendor == "Neural DSP"


def test_sweep_recognizes_ik_amplitube_extensions(tmp_path):
    root = tmp_path / "Documents"
    ik = root / "IK Multimedia" / "AmpliTube 5" / "Presets"
    _touch(ik / "Modern Metal.at5p", "b")
    _touch(ik / "Legacy Tone.at4p", "b")
    _touch(ik / "Bass Rig.mbp", "b")
    _touch(ik / "sample.wav", "wav should be ignored")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    found, _ = presets.sweep(pr)
    names = sorted(p.name for p in found)
    assert names == ["Bass Rig", "Legacy Tone", "Modern Metal"]
    assert all(p.plugin == "AmpliTube 5" for p in found)


def test_sweep_ignores_appledouble_metadata(tmp_path):
    # macOS gera `._name.ext` em filesystems non-HFS. Nao e preset —
    # apareceria como duplicata do arquivo real.
    root = tmp_path / "Library" / "Audio" / "Presets"
    _touch(root / "reFX" / "NEXUS library" / "Pack" / "Cool.fxp", "binary")
    _touch(root / "reFX" / "NEXUS library" / "Pack" / "._Cool.fxp", "metadata")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    found, _ = presets.sweep(pr)
    assert [p.name for p in found] == ["Cool"]


def test_sweep_ignores_audio_samples_and_backups(tmp_path):
    root = tmp_path / "Library" / "Audio" / "Presets"
    _touch(root / "Vendor" / "Plugin" / "Cool.aupreset", "<plist/>")
    _touch(root / "Vendor" / "Plugin" / "sample.wav", "sample")
    _touch(root / "Vendor" / "Plugin" / "manual.pdf", "doc")
    _touch(root / "Vendor" / "Plugin" / "Cool.bak", "backup")
    _touch(root / "Vendor" / "Plugin" / "Samples" / "kick.wav", "in Samples dir")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    found, _ = presets.sweep(pr)
    assert [p.name for p in found] == ["Cool"]


def test_sweep_vendor_whitelist_blocks_unknown_documents(tmp_path):
    # `~/Documents/<Vendor>` so entra em vendors da whitelist. Vendor
    # desconhecido nao e varrido — protege privacidade e evita ruido.
    root = tmp_path / "Documents"
    _touch(root / "MyPersonalStuff" / "secret.aupreset", "<plist/>")
    _touch(root / "FabFilter" / "Pro-Q 3" / "Ok.ffp", "b")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    # simula que root e detectado como precisando de filtro (nome 'Documents')
    found, _ = presets.sweep(pr)
    plugins_found = {p.plugin for p in found}
    assert "Pro-Q 3" in plugins_found
    # secret.aupreset em vendor nao-whitelisted NAO aparece
    assert not any(p.name == "secret" for p in found)


def test_sweep_registers_toontrack_superior3_as_opaque_db(tmp_path):
    root = tmp_path / "Library" / "Application Support"
    (root / "Toontrack" / "Superior3").mkdir(parents=True)
    (root / "Toontrack" / "Superior3" / "SoundDB").write_bytes(b"\x00\x01")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    _, opaque = presets.sweep(pr)
    assert len(opaque) == 1
    op = opaque[0]
    assert op.plugin == "Superior Drummer 3"
    assert op.vendor == "Toontrack"
    assert op.reason == "proprietary_db"


def test_sweep_deduplicates_when_same_path_seen_via_two_roots(tmp_path):
    shared_file = tmp_path / "shared" / "Vendor" / "Plugin" / "Cool.aupreset"
    _touch(shared_file, "<plist/>")
    root_a = tmp_path / "shared"
    root_b = tmp_path / "shared"  # deliberadamente igual
    pr = presets.PresetRoots(
        extra_roots=(root_a, root_b), disable_defaults=True,
    )
    found, _ = presets.sweep(pr)
    assert len(found) == 1


def test_env_var_extra_roots_are_swept(tmp_path, monkeypatch):
    root = tmp_path / "extra"
    _touch(root / "Vendor" / "Plugin" / "Fromenv.aupreset", "<plist/>")
    monkeypatch.setenv("MIDI_ARRANGER_PRESET_ROOTS", str(root))
    pr = presets.PresetRoots(disable_defaults=True)
    found, _ = presets.sweep(pr)
    assert [p.name for p in found] == ["Fromenv"]


def test_spectrasonics_steam_root_env_is_swept(tmp_path, monkeypatch):
    steam = tmp_path / "SharedSteam"
    _touch(steam / "Spectrasonics" / "STEAM" / "Omnisphere" / "Patches" / "Deep Pad.prt_a", "b")
    monkeypatch.setenv("SPECTRASONICS_STEAM_ROOT", str(steam))
    monkeypatch.setenv("MIDI_ARRANGER_PRESET_ROOTS", "")
    pr = presets.PresetRoots(disable_defaults=True)
    found, _ = presets.sweep(pr)
    names = [p.name for p in found]
    assert "Deep Pad" in names
    # STEAM alias mapeia pra Omnisphere
    assert any(p.plugin == "Omnisphere" for p in found)


def test_sweep_discovers_spectrasonics_steam_symlink_without_configuration(tmp_path):
    # Instalacao real comum no macOS: Application Support guarda so o
    # ponteiro STEAM; library vive em volume externo. O usuario nao configura
    # env nem passa o destino ao scanner.
    app_support = tmp_path / "Library" / "Application Support"
    steam = tmp_path / "External" / "Spectrasonics" / "STEAM"
    _touch(
        steam / "Omnisphere" / "Settings Library" / "Patches" / "Deep Pad.prt_a",
        "binary",
    )
    pointer = app_support / "Spectrasonics" / "STEAM"
    pointer.parent.mkdir(parents=True)
    pointer.symlink_to(steam, target_is_directory=True)

    pr = presets.PresetRoots(extra_roots=(app_support,), disable_defaults=True)
    found, opaque = presets.sweep(pr)

    assert opaque == []
    assert [(p.plugin, p.name, p.vendor) for p in found] == [
        ("Omnisphere", "Deep Pad", "Spectrasonics"),
    ]
    roots, discovered, unresolved = presets.discover_roots(pr)
    assert steam in roots
    assert discovered == [presets.DiscoveredRoot(
        path=str(steam), source=str(pointer), method="symlink",
    )]
    assert unresolved == []


def test_discovery_reports_unmounted_external_library(tmp_path):
    app_support = tmp_path / "Library" / "Application Support"
    pointer = app_support / "Spectrasonics" / "STEAM"
    pointer.parent.mkdir(parents=True)
    missing = tmp_path / "Volumes" / "Library Offline" / "STEAM"
    pointer.symlink_to(missing, target_is_directory=True)

    pr = presets.PresetRoots(extra_roots=(app_support,), disable_defaults=True)
    _roots, discovered, unresolved = presets.discover_roots(pr)

    assert discovered == []
    assert unresolved == [presets.UnresolvedRoot(
        source=str(pointer), target=str(missing), reason="target_unavailable",
    )]


def test_sweep_reads_real_names_from_spectrasonics_db_manifest(tmp_path):
    steam = tmp_path / "STEAM"
    db = steam / "Omnisphere" / "Settings Library" / "Patches" / "Factory" / "Library.db"
    _touch(
        db,
        "<FileSystem>\n"
        '<DIR name="Pads &amp; Strings">\n'
        '<FILE name="Air &amp; Motion.prt_omn" offset="0" size="12"/>\n'
        '<FILE name="cover.jpg" offset="12" size="2"/>\n'
        "</DIR>\n"
        "</FileSystem>\n"
        "payload que nao deve ser interpretado\n",
    )

    pr = presets.PresetRoots(extra_roots=(steam,), disable_defaults=True)
    found, _opaque = presets.sweep(pr)

    assert len(found) == 1
    item = found[0]
    assert item.name == "Air & Motion"
    assert item.plugin == "Omnisphere"
    assert item.vendor == "Spectrasonics"
    assert item.format == "prt_omn_db"
    assert item.path == f"{db}#Pads & Strings/Air & Motion.prt_omn"
    assert item.verified is True


def test_scan_all_dynamic_keys_includes_discovered_plugins(tmp_path):
    root = tmp_path / "Library" / "Audio" / "Presets"
    _touch(root / "reFX" / "NEXUS library" / "Presets" / "Pack" / "Wack.fxp", "b")
    _touch(root / "FabFilter" / "Pro-Q 3" / "Ok.ffp", "b")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    grouped = presets.scan_all(pr)
    # Chaves fixas presentes mesmo vazias:
    for name in ("Omnisphere", "Alchemy", "ES2", "Sampler", "Retro Synth",
                 "Kontakt", "Serum", "Vital", "Addictive Drums 2", "Nexus"):
        assert name in grouped
    # Nexus agora tem preset real (mudanca vs. versao antiga do modulo):
    assert [p.name for p in grouped["Nexus"]] == ["Wack"]
    # Pro-Q 3 vira chave dinamica:
    assert "Pro-Q 3" in grouped
    assert [p.name for p in grouped["Pro-Q 3"]] == ["Ok"]


def test_scan_all_with_opaque_returns_both(tmp_path):
    root = tmp_path / "Library" / "Application Support"
    (root / "Toontrack" / "Superior3").mkdir(parents=True)
    (root / "Toontrack" / "Superior3" / "SoundDB").write_bytes(b"\x00")
    pr = presets.PresetRoots(extra_roots=(root,), disable_defaults=True)
    grouped, opaque = presets.scan_all_with_opaque(pr)
    assert "Omnisphere" in grouped  # sempre listado
    assert any(op.plugin == "Superior Drummer 3" for op in opaque)
