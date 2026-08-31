"""`instruments.<familia>` — configuracao de instrumento de corda por
musica (issue #44).

O problema real que motivou a issue: a ferramenta gerou humanizacao em
`DEIXE IR` sem nunca ter perguntado a afinacao. O usuario declarou depois
"guitarras 7 cordas Drop G#, baixo 4 cordas Drop G# finger" e a informacao
batia com o arquivo (guitarra ritmica com minimo em MIDI 32 = G#1, exato
piso do Drop G# de 7 cordas — ver `tests/fixtures/tuning/
fixture_a_rhythm_guitar.mid`, ja coberta por `test_tuning_fixtures.py`).

Cobrimos aqui:
- brief com `instruments` completo valida;
- afinacao por nome resolve para as notas certas via manual (delegado a
  `test_tuning_resolve_name.py`; aqui testamos que `brief.validate`
  aceita o resultado);
- nome desconhecido falha pedindo as notas;
- 7 cordas com 6 notas falha citando a divergencia;
- notas fora de ordem falham;
- brief sem `instruments` continua valido, com a ausencia declarada;
- `known=False` e ausencia declarada, nunca chute;
- baixo com `known=True` exige `playing_style` e `notation` (altura
  escrita vs soante — baixo e instrumento transpositor).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tests.test_contract_brief import _valid_brief
from tools import contract as _contract  # noqa: F401  # registra as tools
from tools.brief_schema import validate_brief
from tools.registry import ToolError


def _brief_with_instruments(instruments: dict[str, Any]) -> dict[str, Any]:
    brief = copy.deepcopy(_valid_brief())
    brief["instruments"] = instruments
    return brief


def _guitar_seven_strings_drop_g_sharp() -> dict[str, Any]:
    """Reproduz o caso real da issue: guitarra de 7 cordas Drop G#, nome
    desconhecido do manual (so 6 e 8 cordas tem Drop G#/F# documentado),
    entao declarada por notas — o mesmo minimo (MIDI 32) medido em
    `fixture_a_rhythm_guitar.mid`."""
    return {
        "known": True,
        "strings": 7,
        "tuning": {
            "name": "Drop G#",
            "notes": [32, 39, 44, 49, 54, 58, 63],
        },
    }


def _bass_four_strings_finger_written() -> dict[str, Any]:
    return {
        "known": True,
        "strings": 4,
        "tuning": {"name": None, "notes": [28, 33, 38, 43]},
        "playing_style": "finger",
        "notation": "written",
    }


# --- brief completo, brief ausente ----------------------------------------


def test_brief_with_complete_instruments_validates():
    brief = _brief_with_instruments({
        "guitar": _guitar_seven_strings_drop_g_sharp(),
        "bass": _bass_four_strings_finger_written(),
    })
    validate_brief(brief)  # nao levanta


def test_brief_without_instruments_key_still_validates():
    """Backward compat: brief antigo, sem `instruments`, continua valido
    — a ausencia da chave inteira e a forma mais simples de 'nao
    perguntei/nao sei'."""
    validate_brief(_valid_brief())


def test_fixture_a_real_lowest_pitch_matches_declared_tuning():
    """A declaracao do usuario ('Drop G#' 7 cordas) bate com o minimo
    real medido em `fixture_a_rhythm_guitar.mid` (issue #35 / #44):
    MIDI 32 = G#1, corda mais grave."""
    import os
    import tempfile

    from tests.fixtures.tuning import generate as fixgen
    from tools import tuning as tuning_mod

    with tempfile.TemporaryDirectory() as tmp:
        fixgen.build_all(tmp)
        p = os.path.join(tmp, "fixture_a_rhythm_guitar.mid")
        ti = tuning_mod.tuning_inference(p)[0]
        assert ti.lowest_string_pitch == 32
        assert ti.tuning_name == "Drop G#"

    declared = _guitar_seven_strings_drop_g_sharp()
    assert min(declared["tuning"]["notes"]) == 32


# --- nome desconhecido ------------------------------------------------------


def test_unknown_tuning_name_without_notes_fails_asking_for_notes():
    """`Drop G#` de 7 cordas nao existe no manual — sem `notes`, o
    validador recusa em vez de chutar."""
    brief = _brief_with_instruments({
        "guitar": {
            "known": True, "strings": 7,
            "tuning": {"name": "Drop G#", "notes": []},
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_TUNING_NAME_UNKNOWN"
    assert "instruments.guitar.tuning.name" in exc.value.path


def test_known_tuning_name_resolves_and_matches_manual():
    """`Drop C` de 6 cordas existe no manual — nome sozinho (sem notes)
    ja e suficiente."""
    brief = _brief_with_instruments({
        "guitar": {
            "known": True, "strings": 6,
            "tuning": {"name": "Drop C", "notes": []},
        },
    })
    validate_brief(brief)  # nao levanta


def test_tuning_name_that_disagrees_with_declared_notes_fails():
    brief = _brief_with_instruments({
        "guitar": {
            "known": True, "strings": 6,
            # Drop C resolve para (36,43,48,53,57,62); aqui declaramos
            # Drop A por engano.
            "tuning": {"name": "Drop C", "notes": [33, 40, 45, 50, 54, 59]},
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_TUNING_NAME_MISMATCH"


# --- contagem de cordas / ordem ---------------------------------------------


def test_seven_strings_with_six_notes_fails_citing_mismatch():
    brief = _brief_with_instruments({
        "guitar": {
            "known": True, "strings": 7,
            "tuning": {"name": None, "notes": [33, 40, 45, 50, 55, 59]},
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_STRING_COUNT_MISMATCH"
    assert "7" in exc.value.message and "6" in exc.value.message


def test_notes_out_of_order_fail():
    brief = _brief_with_instruments({
        "bass": {
            "known": True, "strings": 4,
            "tuning": {"name": None, "notes": [40, 33, 45, 50]},
            "playing_style": "finger", "notation": "written",
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_NOTES_NOT_ORDERED"


def test_duplicate_open_string_notes_are_not_strictly_ascending():
    brief = _brief_with_instruments({
        "bass": {
            "known": True, "strings": 4,
            "tuning": {"name": None, "notes": [28, 33, 33, 43]},
            "playing_style": "finger", "notation": "written",
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_NOTES_NOT_ORDERED"


def test_notes_outside_midi_range_fail_schema():
    brief = _brief_with_instruments({
        "guitar": {
            "known": True, "strings": 6,
            "tuning": {"name": None, "notes": [-1, 40, 45, 50, 54, 59]},
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INVALID"


# --- ausencia declarada ("nao sei") -----------------------------------------


def test_known_false_is_accepted_as_declared_absence():
    brief = _brief_with_instruments({
        "bass": {
            "known": False, "strings": None, "tuning": None,
            "playing_style": None, "notation": None,
        },
    })
    validate_brief(brief)  # nao levanta


def test_known_false_with_a_number_present_is_a_conflict():
    """'Nao sei' nao pode vir acompanhado de um numero — ou o usuario sabe
    (known=true, declara), ou nao sabe (known=false, tudo null)."""
    brief = _brief_with_instruments({
        "bass": {
            "known": False, "strings": 4, "tuning": None,
            "playing_style": None, "notation": None,
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_KNOWN_CONFLICT"


def test_known_false_with_playing_style_or_notation_present_is_a_conflict():
    """Regressao do achado P2#1 do PR #64: `known=false` com `strings` e
    `tuning` nulos mas `playing_style`/`notation` preenchidos passava direto
    (o `continue` disparava antes de olhar esses dois campos), deixando o
    brief logicamente inconsistente — 'nao sei' tem que zerar TODOS os
    campos de baixo, nao so numero/afinacao."""
    brief = _brief_with_instruments({
        "bass": {
            "known": False, "strings": None, "tuning": None,
            "playing_style": "slap", "notation": "written",
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_KNOWN_CONFLICT"


def test_known_false_with_only_notation_present_is_a_conflict():
    brief = _brief_with_instruments({
        "bass": {
            "known": False, "strings": None, "tuning": None,
            "playing_style": None, "notation": "sounding",
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_KNOWN_CONFLICT"


def test_known_true_without_tuning_fails():
    brief = _brief_with_instruments({
        "guitar": {"known": True, "strings": 6, "tuning": None},
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_MISSING_TUNING"


# --- baixo: altura escrita vs soante, estilo de execucao --------------------


def test_bass_known_true_requires_playing_style_and_notation():
    brief = _brief_with_instruments({
        "bass": {
            "known": True, "strings": 4,
            "tuning": {"name": None, "notes": [28, 33, 38, 43]},
            "playing_style": None, "notation": "written",
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INSTRUMENT_MISSING_BASS_FIELD"
    assert "playing_style" in exc.value.path


def test_bass_notation_written_means_sounds_an_octave_below():
    """82% dos ataques do baixo em `DEIXE IR` estavam em uníssono escrito
    com a guitarra — que soa uma oitava abaixo, porque baixo e
    transpositor. `notation: written` e o valor que descreve esse caso."""
    brief = _brief_with_instruments({
        "bass": _bass_four_strings_finger_written(),
    })
    validate_brief(brief)
    assert brief["instruments"]["bass"]["notation"] == "written"


def test_bass_playing_style_rejects_free_text():
    brief = _brief_with_instruments({
        "bass": {
            "known": True, "strings": 4,
            "tuning": {"name": None, "notes": [28, 33, 38, 43]},
            "playing_style": "com a boca", "notation": "written",
        },
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INVALID"


# --- so familias de corda ----------------------------------------------------


def test_drums_key_in_instruments_is_a_structural_error():
    """`instruments` so aceita `guitar`/`bass` — bateria nao tem corda."""
    brief = _brief_with_instruments({
        "drums": {"known": False, "strings": None, "tuning": None},
    })
    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_INVALID"
