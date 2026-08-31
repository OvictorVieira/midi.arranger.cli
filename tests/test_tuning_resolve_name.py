"""Resolucao de nome de afinacao declarado -> notas das cordas soltas
(issue #44). `tools.tuning.resolve_tuning_name` le SO o manual
`guitar.drop_tuning` (via `techniques.build_index`) — nunca uma tabela
paralela hardcoded no codigo.
"""

from __future__ import annotations

from tools import tuning


def test_drop_c_six_strings_resolves_to_manual_midis():
    """`Drop C` de 6 cordas bate com `tools.generic.afinacoes.drop_c` do
    manual `guitar.drop_tuning`: C1 G1 C2 F2 A2 D3."""
    assert tuning.resolve_tuning_name("Drop C", 6) == (36, 43, 48, 53, 57, 62)


def test_standard_e_accepts_portuguese_form_with_accent():
    """`E padrão` (com acento) e `E padrao` (sem) resolvem igual — mesma
    entrada `e_padrao` do manual."""
    expected = (40, 45, 50, 55, 59, 64)
    assert tuning.resolve_tuning_name("E padrão", 6) == expected
    assert tuning.resolve_tuning_name("E padrao", 6) == expected
    assert tuning.resolve_tuning_name("standard E", 6) == expected


def test_seven_string_drop_a_resolves_and_differs_from_six_string():
    """`Drop A` de 7 cordas e `Drop A` de 6 cordas sao instrumentos
    diferentes — o numero de cordas desambigua qual entrada do manual
    vale, mesmo com o mesmo nome."""
    seven = tuning.resolve_tuning_name("Drop A", 7)
    six = tuning.resolve_tuning_name("Drop A", 6)
    assert seven == (33, 40, 45, 50, 55, 59, 64)
    assert six == (33, 40, 45, 50, 54, 59)
    assert seven != six


def test_drop_g_sharp_seven_strings_is_unknown_to_the_manual():
    """Caso real da issue #44 (`DEIXE IR`): guitarra de 7 cordas Drop G#
    com corda mais grave em MIDI 32 (ver `tests/fixtures/tuning/
    fixture_a_rhythm_guitar.mid`, gerado a partir da distribuicao real).
    O manual `guitar.drop_tuning` NAO documenta esse afinacao para 7
    cordas (so `sete_cordas_b` e `sete_cordas_drop_a`) — resolver por
    nome tem que devolver None, nunca chutar um valor proximo."""
    assert tuning.resolve_tuning_name("Drop G#", 7) is None
    # nem para 6 cordas (que tambem nao tem entrada Drop G# no manual).
    assert tuning.resolve_tuning_name("Drop G#", 6) is None


def test_unrecognized_format_returns_none():
    assert tuning.resolve_tuning_name("meia entrada estranha", 6) is None
    assert tuning.resolve_tuning_name("", 6) is None


def test_bass_has_no_manual_entries_so_name_never_resolves():
    """O manual `guitar.drop_tuning` so documenta guitarra (6/7/8 cordas).
    Baixo de 4 cordas por nome tem que devolver None sempre — pedir as
    notas explicitas, nunca inventar 'guitarra menos uma oitava'."""
    assert tuning.resolve_tuning_name("E padrao", 4) is None
    assert tuning.resolve_tuning_name("Drop D", 4) is None
