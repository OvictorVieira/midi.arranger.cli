"""Fixtures sinteticas de deteccao de afinacao (US-006, issue #35).

Reproduzem, uma a uma, as seis situacoes que o detector precisa lidar. O
MIDI multi-instrumento real do usuario nao esta versionado; as fixtures
vivem em `tests/fixtures/tuning/`, geradas por `generate.py` (script
determinístico versionado junto).

Todo teste aqui regera as fixtures em `tmp` a cada rodada, para pegar
imediatamente qualquer drift entre o script e os `.mid` versionados —
alem de conferir que os arquivos versionados casam byte-a-byte com o que
o script produz.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests.fixtures.tuning import generate as fixgen
from tools import tuning

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tuning"


@pytest.fixture(scope="module")
def fixtures_tmp():
    with tempfile.TemporaryDirectory() as tmp:
        fixgen.build_all(tmp)
        yield tmp


def _fix(fixtures_tmp: str, name: str) -> str:
    return os.path.join(fixtures_tmp, name)


def test_versioned_fixtures_match_generator_output(fixtures_tmp):
    """Os `.mid` versionados devem ser byte-identicos ao que o script
    produz. Se algum divergir, rode `python3 tests/fixtures/tuning/generate.py`
    para atualiza-los."""
    for filename in fixgen.FIXTURES:
        versioned = FIXTURE_DIR / filename
        regenerated = Path(fixtures_tmp) / filename
        assert versioned.is_file(), (
            f"fixture versionada ausente: {versioned.relative_to(FIXTURE_DIR.parent.parent.parent)}"
        )
        assert versioned.read_bytes() == regenerated.read_bytes(), (
            f"drift entre `{filename}` versionado e o gerador — regere as fixtures"
        )


def test_fixture_a_rhythm_guitar_matches_issue_distribution(fixtures_tmp):
    """Fixture A: 5 canais com ~28/37/29/3/3 %; TRAVA 2 elimina as duas
    agudas; top3 = 94%; classe drop, nome Drop G#."""
    p = _fix(fixtures_tmp, "fixture_a_rhythm_guitar.mid")

    dist = tuning.channel_distribution(p)
    assert len(dist) == 1
    channels = dist[0].channels
    assert [c.channel for c in channels] == [0, 1, 2, 3, 4]
    assert [c.pitch_min for c in channels] == [32, 39, 44, 52, 55]
    assert [c.note_count for c in channels] == [28, 37, 29, 3, 3]
    assert abs(sum(c.percentage for c in channels) - 100.0) < 1e-6

    ti = tuning.tuning_inference(p)[0]
    assert ti.is_stringed is True
    assert ti.stringed_source == tuning.STRINGED_SOURCE_GM_PROGRAM
    assert {c.channel for c in ti.candidate_channels} == {0, 1, 2}
    assert {d.reason for d in ti.discarded_channels} == {
        tuning.DISCARD_LOW_NOTE_COUNT,
    }
    assert ti.tuning_class == tuning.TUNING_CLASS_DROP
    assert ti.tuning_name == "Drop G#"
    assert ti.lowest_string_pitch == 32
    assert abs(ti.low_strings_top3_percentage - 94.0) < 1e-6


def test_fixture_b_bass_concentrates_on_lowest_string(fixtures_tmp):
    """Fixture B: baixo com ~91,5% em MIDI 21 (A0), corda unica."""
    p = _fix(fixtures_tmp, "fixture_b_bass_riff.mid")

    ti = tuning.tuning_inference(p)[0]
    assert ti.is_stringed is True
    assert ti.stringed_source == tuning.STRINGED_SOURCE_GM_PROGRAM
    assert ti.lowest_string_pitch == 21

    cons = ti.string_concentrations
    assert cons[0].string_index == 0
    assert cons[0].pitch_min == 21
    assert abs(cons[0].percentage - 91.5) < 1e-6

    # Canal 2 (7 notas) cai pela TRAVA 2.
    assert {d.reason for d in ti.discarded_channels} == {
        tuning.DISCARD_LOW_NOTE_COUNT,
    }
    assert {d.channel for d in ti.discarded_channels} == {2}


def test_fixture_c_voice_with_wind_patch_does_not_infer(fixtures_tmp):
    """Fixture C: 4 canais com intervalos [5,5,4] entre minimos, mas
    patch GM 73 (Flute). TRAVA 1 recusa a inferencia."""
    p = _fix(fixtures_tmp, "fixture_c_voice_wind_patch.mid")

    ti = tuning.tuning_inference(p)[0]
    assert ti.is_stringed is False
    assert ti.stringed_source is None
    assert ti.discard_reason == tuning.NOT_STRINGED
    assert ti.candidate_channels == ()
    assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
    assert ti.tuning_name is None
    assert ti.confidence == tuning.TUNING_CONFIDENCE_UNKNOWN
    assert 73 in ti.gm_programs


def test_fixture_d_lead_guitar_channels_fall_by_note_count(fixtures_tmp):
    """Fixture D: canais com 2 e 4 notas caem pela TRAVA 2. Passa TRAVA 1
    (guitarra), mas nenhum candidato sobra."""
    p = _fix(fixtures_tmp, "fixture_d_lead_guitar_low_count.mid")

    ti = tuning.tuning_inference(p)[0]
    assert ti.is_stringed is True
    assert ti.candidate_channels == ()
    assert {d.channel for d in ti.discarded_channels} == {0, 1}
    assert {d.reason for d in ti.discarded_channels} == {
        tuning.DISCARD_LOW_NOTE_COUNT,
    }
    assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
    assert ti.tuning_name is None
    assert ti.confidence == tuning.TUNING_CONFIDENCE_UNKNOWN


def test_fixture_e_standard_tuning_is_not_classified_as_drop(fixtures_tmp):
    """Fixture E: 6 canais com intervalos [5,5,5,4,5] => classe standard,
    Standard E, confianca high."""
    p = _fix(fixtures_tmp, "fixture_e_standard_tuning.mid")

    ti = tuning.tuning_inference(p)[0]
    assert ti.tuning_intervals == (5, 5, 5, 4, 5)
    assert ti.tuning_class == tuning.TUNING_CLASS_STANDARD
    assert ti.tuning_class != tuning.TUNING_CLASS_DROP
    assert ti.tuning_name == "Standard E"
    assert ti.confidence == tuning.TUNING_CONFIDENCE_HIGH
    assert len(ti.candidate_channels) == 6


def test_fixture_f_single_channel_reports_absence_without_error(fixtures_tmp):
    """Fixture F: track de corda com tudo no canal 0. Nao ha separacao
    por canal — o detector reporta ausencia de informacao (classe
    unknown, nome None) sem levantar erro."""
    p = _fix(fixtures_tmp, "fixture_f_single_channel_guitar.mid")

    ti = tuning.tuning_inference(p)[0]
    assert ti.is_stringed is True
    assert len(ti.candidate_channels) == 1
    assert ti.tuning_intervals == ()
    assert ti.tuning_class == tuning.TUNING_CLASS_UNKNOWN
    assert ti.tuning_name is None
    assert ti.confidence == tuning.TUNING_CONFIDENCE_UNKNOWN
    assert ti.lowest_string_pitch == 40
