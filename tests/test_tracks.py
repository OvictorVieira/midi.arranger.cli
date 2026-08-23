"""Testes de convencao de nome de track (US-013)."""

from __future__ import annotations

import pytest

from tools.tracks import (
    FORBIDDEN_PLUGINS,
    MIDI_TRACK_NAME_MAX_LEN,
    PRESET_JOINER,
    SAMPLER_ROUTING,
    SEPARATOR,
    SERUM_ALLOWED_ROLES,
    TRUNCATION_MARK,
    UNVERIFIED_MARK,
    VERIFIED_MARK,
    TrackNameError,
    default_plugin_for_role,
    format_track_name,
    is_ascii_safe,
    is_forbidden_plugin,
    is_sampler_role,
    is_serum_allowed_for_role,
    name_for_element,
)

# --- vocabulario ------------------------------------------------------------

def test_marks_match_spec():
    assert VERIFIED_MARK == "*"
    assert UNVERIFIED_MARK == "?"


def test_fr24_routing_table_matches_spec():
    assert SAMPLER_ROUTING == {
        "perc_elec": "Addictive Drums 2",
        "impact": "Logic Sampler",
        "snare_bomb": "Logic Sampler",
        "sub_drop": "Logic Sampler",
        "vox_chop": "Logic Sampler",
    }


def test_forbidden_plugins_cover_trigger():
    assert "Trigger 2" in FORBIDDEN_PLUGINS
    assert "Trigger_2" in FORBIDDEN_PLUGINS
    assert "Addictive Trigger" in FORBIDDEN_PLUGINS


def test_serum_allowed_roles_match_fr14():
    assert frozenset({
        "pluck",
        "arp_gated",
        "riser",
        "growl_bass",
        "lead_agressivo",
    }) == SERUM_ALLOWED_ROLES


def test_is_sampler_role_covers_fr24_and_only_fr24():
    for role in SAMPLER_ROUTING:
        assert is_sampler_role(role) is True
    for role in ("pad", "piano", "arp", "strings", "sub"):
        assert is_sampler_role(role) is False


def test_default_plugin_for_role_returns_none_for_free_choice():
    assert default_plugin_for_role("pad") is None
    assert default_plugin_for_role("arp") is None
    assert default_plugin_for_role("piano") is None


def test_default_plugin_for_role_returns_fr24_default():
    assert default_plugin_for_role("perc_elec") == "Addictive Drums 2"
    assert default_plugin_for_role("impact") == "Logic Sampler"
    assert default_plugin_for_role("vox_chop") == "Logic Sampler"


def test_is_serum_allowed_for_role():
    for role in SERUM_ALLOWED_ROLES:
        assert is_serum_allowed_for_role(role) is True
    for role in ("pad", "arp", "piano", "strings", "sustain_pad"):
        assert is_serum_allowed_for_role(role) is False


def test_is_forbidden_plugin_true_for_trigger_only():
    assert is_forbidden_plugin("Trigger 2") is True
    assert is_forbidden_plugin("Addictive Trigger") is True
    assert is_forbidden_plugin("Omnisphere") is False
    assert is_forbidden_plugin("Serum") is False


# --- format_track_name ------------------------------------------------------

def test_format_verified_matches_spec_example():
    assert (
        format_track_name("Pad Atmos", "Omnisphere", "Desert Wind", verified=True)
        == "Pad Atmos - Omnisphere / Desert Wind *"
    )


def test_format_unverified_matches_spec_example():
    assert (
        format_track_name("Arp 16th", "Nexus", "Pluck Modern", verified=False)
        == "Arp 16th - Nexus / Pluck Modern ?"
    )


def test_format_empty_field_raises():
    with pytest.raises(TrackNameError):
        format_track_name("", "Omnisphere", "Desert Wind", verified=True)
    with pytest.raises(TrackNameError):
        format_track_name("Pad", "", "Desert Wind", verified=True)
    with pytest.raises(TrackNameError):
        format_track_name("Pad", "Omnisphere", "", verified=True)


def test_format_whitespace_only_field_raises():
    with pytest.raises(TrackNameError):
        format_track_name("   ", "Omnisphere", "Desert Wind", verified=True)


def test_format_non_string_field_raises():
    with pytest.raises(TrackNameError):
        format_track_name(42, "Omnisphere", "Desert Wind", verified=True)


def test_format_invalid_max_len_raises():
    with pytest.raises(TrackNameError):
        format_track_name("Pad", "Omni", "Wind", verified=True, max_len=0)
    with pytest.raises(TrackNameError):
        format_track_name("Pad", "Omni", "Wind", verified=True, max_len=-5)


# --- truncamento ------------------------------------------------------------

def test_truncation_when_preset_is_long():
    long_preset = "A" * 200
    result = format_track_name("Pad Atmos", "Omnisphere", long_preset, verified=True)
    assert len(result) <= MIDI_TRACK_NAME_MAX_LEN
    assert result.startswith("Pad Atmos - Omnisphere / ")
    assert result.endswith(f"{TRUNCATION_MARK} {VERIFIED_MARK}")


def test_truncation_preserves_verified_mark():
    long_preset = "Very Long Preset Name That Definitely Exceeds The Limit Twice"
    result = format_track_name("Pad Atmos", "Omnisphere", long_preset, verified=True, max_len=50)
    assert len(result) <= 50
    assert result.endswith(f" {VERIFIED_MARK}")
    assert TRUNCATION_MARK in result


def test_truncation_preserves_unverified_mark():
    long_preset = "Very Long Preset Name That Definitely Exceeds The Limit Twice"
    result = format_track_name("Pad Atmos", "Omnisphere", long_preset, verified=False, max_len=50)
    assert len(result) <= 50
    assert result.endswith(f" {UNVERIFIED_MARK}")
    assert TRUNCATION_MARK in result


def test_truncation_within_boundary_leaves_string_untouched():
    # exemplo do spec tem 38 chars, cabem em max_len=38
    exact = "Pad Atmos - Omnisphere / Desert Wind *"
    assert (
        format_track_name(
            "Pad Atmos", "Omnisphere", "Desert Wind", verified=True, max_len=len(exact)
        )
        == exact
    )


def test_truncation_when_prefix_alone_exceeds_max_raises():
    with pytest.raises(TrackNameError):
        format_track_name(
            "PadPadPadPadPadPadPad",
            "OmniOmniOmniOmni",
            "Preset",
            verified=True,
            max_len=20,
        )


def test_truncation_room_of_exactly_one_preset_char_still_ok():
    # prefix "El - Pl / " tem 10 chars; " *" tem 2; TRUNCATION_MARK "..." tem 3.
    # room precisa ser >=1, entao max_len minimo = 10+2+3+1 = 16.
    # (Era 14 quando a marca de corte era o caractere unico "…"; virou ASCII
    # para sobreviver ao meta-evento 0x03 do SMF.)
    floor = 10 + 2 + len(TRUNCATION_MARK) + 1
    assert floor == 16
    result = format_track_name("El", "Pl", "abcdefghij", verified=True, max_len=floor)
    assert result == f"El - Pl / a{TRUNCATION_MARK} {VERIFIED_MARK}"
    assert len(result) == floor
    # Um caractere abaixo do piso nao cabe e tem que falhar alto.
    with pytest.raises(TrackNameError):
        format_track_name("El", "Pl", "abcdefghij", verified=True, max_len=floor - 1)


# --- name_for_element: FR-24 routing ----------------------------------------

def test_perc_elec_must_use_addictive_drums_2():
    ok = name_for_element(
        "Perc Clap", "perc_elec", "Addictive Drums 2", "Clap Punchy", verified=True
    )
    assert "Addictive Drums 2" in ok
    with pytest.raises(TrackNameError):
        name_for_element(
            "Perc Clap", "perc_elec", "Omnisphere", "Clap", verified=True
        )


def test_impact_snare_bomb_sub_drop_vox_chop_must_use_logic_sampler():
    for role in ("impact", "snare_bomb", "sub_drop", "vox_chop"):
        ok = name_for_element(
            f"El {role}", role, "Logic Sampler", "sample.wav", verified=True
        )
        assert "Logic Sampler" in ok
        with pytest.raises(TrackNameError):
            name_for_element(f"El {role}", role, "Kontakt", "sample.wav", verified=True)


def test_trigger_2_never_suggested():
    with pytest.raises(TrackNameError):
        name_for_element("Snare", "snare_bomb", "Trigger 2", "sample.wav", verified=True)


def test_trigger_2_underscore_variant_also_forbidden():
    with pytest.raises(TrackNameError):
        name_for_element("Kick", "impact", "Trigger_2", "sample.wav", verified=True)


def test_addictive_trigger_never_suggested():
    with pytest.raises(TrackNameError):
        name_for_element(
            "Kick", "perc_elec", "Addictive Trigger", "kick.wav", verified=True
        )


def test_sampler_role_accepts_sample_filename_as_preset():
    ok = name_for_element(
        "Impact",
        "impact",
        "Logic Sampler",
        "cinematic_boom.wav",
        verified=True,
    )
    assert ok == "Impact - Logic Sampler / cinematic_boom.wav *"


# --- name_for_element: FR-14 Serum -----------------------------------------

def test_serum_allowed_for_pluck():
    ok = name_for_element("Pluck 16", "pluck", "Serum", "Pluck Ice", verified=False)
    assert ok == "Pluck 16 - Serum / Pluck Ice ?"


def test_serum_allowed_for_arp_gated_riser_growl_lead():
    for role in ("arp_gated", "riser", "growl_bass", "lead_agressivo"):
        ok = name_for_element(f"El {role}", role, "Serum", "Preset", verified=False)
        assert "Serum" in ok


def test_serum_rejected_for_pad():
    with pytest.raises(TrackNameError) as exc:
        name_for_element("Pad Atmos", "pad", "Serum", "Bright Wash", verified=False)
    assert "Serum" in str(exc.value)
    assert "pad" in str(exc.value)


def test_serum_rejected_for_texture_and_strings():
    with pytest.raises(TrackNameError):
        name_for_element("Texture", "texture", "Serum", "Wash", verified=False)
    with pytest.raises(TrackNameError):
        name_for_element("Strings", "strings", "Serum", "Legato", verified=False)


def test_serum_rejected_for_arp_generic():
    # 'arp' generico nao esta em SERUM_ALLOWED_ROLES; so 'arp_gated' esta
    with pytest.raises(TrackNameError):
        name_for_element("Arp", "arp", "Serum", "Pluck", verified=False)


def test_name_for_element_free_choice_role_accepts_any_non_forbidden_plugin():
    for plugin in ("Omnisphere", "Alchemy", "Kontakt", "Nexus"):
        ok = name_for_element(
            "Pad Atmos", "pad", plugin, "Bright Wash", verified=True
        )
        assert plugin in ok


def test_name_for_element_truncates_when_too_long():
    # role livre + preset gigante -> trunca preservando marca
    long_preset = "Extremely Long Preset Name From Some Sample Pack Volume 99"
    result = name_for_element(
        "Perc Clap", "perc_elec", "Addictive Drums 2", long_preset, verified=True
    )
    assert len(result) <= MIDI_TRACK_NAME_MAX_LEN
    assert result.endswith(f" {VERIFIED_MARK}")


# --- ASCII-safety: round-trip pelo ARQUIVO, nao pela string -----------------
#
# Estes testes existem porque a suite original validava apenas a string Python
# devolvida por `format_track_name`. Isso nao detecta o bug real: o nome so
# quebra ao atravessar o meta-evento 0x03 do SMF, onde bytes >127 ficam a
# merce do decoder do leitor. Um nome com "—"/"✓" passava em todo teste de
# string e chegava corrompido no DAW.

def test_marks_and_separators_are_ascii():
    """Todo simbolo do vocabulario tem que ser ASCII (<0x80)."""
    for name, value in (
        ("VERIFIED_MARK", VERIFIED_MARK),
        ("UNVERIFIED_MARK", UNVERIFIED_MARK),
        ("SEPARATOR", SEPARATOR),
        ("PRESET_JOINER", PRESET_JOINER),
        ("TRUNCATION_MARK", TRUNCATION_MARK),
    ):
        assert is_ascii_safe(value), f"{name}={value!r} tem byte >127"


def test_format_track_name_output_is_ascii_for_ascii_input():
    for verified in (True, False):
        n = format_track_name("Pad Atmos", "Omnisphere", "Desert Wind", verified)
        assert is_ascii_safe(n), f"nome nao-ASCII: {n!r}"


def test_track_name_survives_smf_round_trip(tmp_path):
    """O nome tem que sair do arquivo IDENTICO sob qualquer decoder.

    Escreve o nome num meta 0x03 real e le de volta com latin-1 (default do
    mido, e o que a spec assume) e com utf-8. Os dois tem que devolver a
    mesma string — so verdade se o conteudo for ASCII puro.
    """
    import mido

    name = format_track_name("Pad Atmos", "Omnisphere", "Desert Wind", True)
    mid = mido.MidiFile()
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=name, time=0))
    mid.tracks.append(tr)
    path = tmp_path / "roundtrip.mid"
    mid.save(str(path))

    # Extrai exatamente o payload do meta 0x03: FF 03 <len> <bytes>.
    # Nao dá para escanear o arquivo inteiro: delta-time e comprimento de
    # track sao VLQ, que usa o bit alto como flag de continuacao.
    raw = path.read_bytes()
    i = raw.index(b"\xff\x03")
    payload = raw[i + 3 : i + 3 + raw[i + 2]]
    assert payload.decode("ascii") == name
    assert all(b < 0x80 for b in payload), (
        f"meta track_name contem byte >127: {payload!r} — "
        "o resultado passaria a depender do decoder do DAW"
    )

    for charset in ("latin-1", "utf-8"):
        back = mido.MidiFile(str(path), charset=charset)
        got = [m.name for t in back.tracks for m in t if m.type == "track_name"]
        assert got == [name], f"charset={charset} devolveu {got!r}, esperado {[name]!r}"


def test_truncated_name_also_survives_round_trip(tmp_path):
    """Truncamento nao pode introduzir byte >127 (a reticencia era '…')."""
    import mido

    name = format_track_name(
        "Pad Atmos Muito Longo", "Omnisphere", "Preset Com Nome Enorme " * 3, True
    )
    assert is_ascii_safe(name), f"nome truncado nao-ASCII: {name!r}"
    mid = mido.MidiFile()
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=name, time=0))
    mid.tracks.append(tr)
    path = tmp_path / "trunc.mid"
    mid.save(str(path))
    back = mido.MidiFile(str(path))          # decoder default
    got = [m.name for t in back.tracks for m in t if m.type == "track_name"]
    assert got == [name]
