"""Testes de `tools/learn.py` (issue #18).

O teste mais importante do arquivo e
`test_corpus_drums_declares_feel_not_measurable`: sobre o corpus real e
versionado (`tests/fixtures/corpus_drums/`), fortemente quantizado e com
velocity travada perto do maximo, `learn` PRECISA declarar que timing,
velocity, ghost notes e autocorrelacao nao sao mensuraveis com confianca —
nunca reportar "o estilo desta banda e robotico" como se fosse uma escolha
deliberada. Ver AGENTS.md e a docstring de `tools/learn.py`.
"""

from __future__ import annotations

import glob
import os

import mido
import pytest

from tools import learn as learn_mod
from tools.contract import _plan_family_style_schema
from tools.plan import STYLE_FAMILIES
from tools.registry import call, validate_output

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CORPUS_DRUMS_DIR = os.path.join(FIXTURES_DIR, "corpus_drums")
CORPUS_DRUMS_FILES = sorted(glob.glob(os.path.join(CORPUS_DRUMS_DIR, "*.mid")))


def _output_style_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            family: _plan_family_style_schema() for family in STYLE_FAMILIES
        },
        "additionalProperties": False,
    }


# --- corpus real: a prova de honestidade -----------------------------------


def test_corpus_drums_fixture_is_present() -> None:
    assert len(CORPUS_DRUMS_FILES) == 10, (
        "corpus_drums deve continuar com os dez MIDIs versionados; "
        "se este teste falhar, o fixture mudou de lugar/tamanho"
    )


def test_corpus_drums_declares_feel_not_measurable() -> None:
    """O teste mais importante da issue #18: sobre um corpus quantizado e
    com velocity travada, `learn` reporta as dimensoes de FEEL como
    NAO medidas (confidence='default'), nunca como 'zero jitter e o estilo'.
    """
    result = learn_mod.learn(
        CORPUS_DRUMS_FILES, "drums", researched_at="2026-09-03",
    )
    dims = result.measurements["dimensions"]

    for name in ("velocity", "timing_offset_ms", "ghost_notes", "lag1_autocorrelation"):
        dim = dims[name]
        assert dim["measured"] is False, f"{name} deveria vir measured=False"
        assert dim["confidence"] == "default", f"{name} deveria vir confidence=default"
        assert dim["reason"], f"{name} precisa de uma razao explicita"

    # Os numeros crus continuam calculaveis (para auditoria), so nao entram
    # em `style.parameters` como se fossem confiaveis.
    assert dims["velocity"]["value"]["mode_ratio"] >= 0.5
    assert dims["timing_offset_ms"]["value"]["median_ms"] < 2.0

    style_params = result.style["drums"]["parameters"]
    for leaked_key in style_params:
        assert "velocity" not in leaked_key
        assert "timing" not in leaked_key
        assert "ghost" not in leaked_key
        assert "autocorrelation" not in leaked_key

    # A declaracao tambem aparece como warning acionavel no envelope da tool.
    warned_dimensions = {
        w["path"].rsplit(".", 1)[-1] for w in result.warnings
        if w["code"] == "W_LEARN_NOT_MEASURABLE"
    }
    assert warned_dimensions == {
        "velocity", "timing_offset_ms", "ghost_notes", "lag1_autocorrelation",
    }


def test_corpus_drums_reports_vocabulary_and_fill_density_with_high_confidence() -> None:
    """As mesmas dimensoes que SAO reais neste corpus (vocabulario de
    articulacao, densidade de virada) saem com confianca alta, nao
    contaminadas pela degenerescencia das dimensoes de feel."""
    result = learn_mod.learn(
        CORPUS_DRUMS_FILES, "drums", researched_at="2026-09-03",
    )
    dims = result.measurements["dimensions"]

    vocab = dims["articulation_vocabulary"]
    assert vocab["measured"] is True
    assert vocab["confidence"] == "high"
    assert vocab["value"]["distinct_piece_count"] >= 5

    fills = dims["fill_density"]
    assert fills["measured"] is True
    assert fills["confidence"] == "high"
    assert fills["value"]["fills_total"] > 0

    style_params = result.style["drums"]["parameters"]
    assert "drums_articulation_vocabulary_size" in style_params
    assert "drums_fill_density_per_bar" in style_params
    assert style_params["drums_fill_density_per_bar"] > 0


def test_corpus_drums_aggregates_across_all_files_not_just_first() -> None:
    """Rodar so o primeiro arquivo tem que dar numero de amostra bem menor
    do que rodar o corpus inteiro — prova de que `learn` agrega, nao le so
    `midi_paths[0]`."""
    single = learn_mod.learn(
        CORPUS_DRUMS_FILES[:1], "drums", researched_at="2026-09-03",
    )
    full = learn_mod.learn(
        CORPUS_DRUMS_FILES, "drums", researched_at="2026-09-03",
    )
    single_n = single.measurements["dimensions"]["velocity"]["n_samples"]
    full_n = full.measurements["dimensions"]["velocity"]["n_samples"]
    assert full_n > single_n * 5
    assert full.measurements["dimensions"]["velocity"]["n_files"] == 10
    assert single.measurements["dimensions"]["velocity"]["n_files"] == 1


# --- schema: a saida entra direto em plan.style -----------------------------


def test_corpus_drums_style_output_validates_against_plan_style_schema() -> None:
    result = learn_mod.learn(
        CORPUS_DRUMS_FILES, "drums", researched_at="2026-09-03",
    )
    validate_output(result.style, _output_style_schema())


def test_learn_tool_call_round_trip_via_registry() -> None:
    """Chamada de ponta a ponta pela mesma fachada que o agente usa
    (`tools.registry.call`), incluindo validacao de output_schema."""
    envelope = call("learn", {
        "midi_paths": CORPUS_DRUMS_FILES,
        "family": "drums",
        "researched_at": "2026-09-03",
    })
    assert envelope["ok"] is True
    assert "drums" in envelope["data"]["style"]
    assert envelope["data"]["measurements"]["dimensions"]["velocity"]["measured"] is False


# --- familia nao implementada: erro explicito, nunca perfil vazio ----------


@pytest.mark.parametrize("family", ["bass", "guitar", "keys"])
def test_unsupported_family_raises_explicit_error(family: str) -> None:
    with pytest.raises(learn_mod.LearnFamilyNotSupportedError):
        learn_mod.learn(
            CORPUS_DRUMS_FILES[:1], family, researched_at="2026-09-03",
        )


def test_empty_corpus_raises_explicit_error() -> None:
    with pytest.raises(learn_mod.LearnEmptyCorpusError):
        learn_mod.learn([], "drums", researched_at="2026-09-03")


# --- corpus sintetico com feel injetado -------------------------------------


_SIXTEENTH = 120  # ppq=480 -> sixteenth=120
_KICK, _SNARE, _HAT = 36, 38, 42

# Padrao deterministico de offsets de grade (ms equivalentes variam com o
# tempo) e de velocity, cobrindo groove + ghost notes + uma virada de tom no
# fim de cada compasso de 4 tempos (16 semicolcheias). Sem `random`: os
# valores ciclam por listas fixas, preservando o determinismo exigido pelas
# tools (AGENTS.md).
_TIMING_JITTER_TICKS = [-9, -5, 3, 7, -3, 9, -7, 4]  # laid-back e ahead alternando
_VELOCITY_CYCLE = [42, 68, 95, 110, 55, 88, 120, 75, 30, 100]  # dinamica real
_GHOST_VELOCITY = 28


def _build_synthetic_drum_midi(*, n_bars: int = 24, ticks_per_beat: int = 480) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums (synthetic)", time=0))

    sixteenth = ticks_per_beat // 4
    events: list[tuple[int, int, int]] = []  # (tick, pitch, velocity)
    idx = 0
    for bar in range(n_bars):
        bar_start = bar * 16 * sixteenth
        for step in range(16):
            base_tick = bar_start + step * sixteenth
            jitter = _TIMING_JITTER_TICKS[idx % len(_TIMING_JITTER_TICKS)]
            tick = max(0, base_tick + jitter)
            velocity = _VELOCITY_CYCLE[idx % len(_VELOCITY_CYCLE)]
            idx += 1
            # Hi-hat em toda semicolcheia (textura).
            events.append((tick, _HAT, velocity))
            if step in (0, 8):
                events.append((tick, _KICK, velocity))
            if step in (4, 12):
                events.append((tick, _SNARE, velocity))
            # Ghost notes de snare nos "e" das colcheias, fora do backbeat.
            if step in (2, 6, 10, 14):
                events.append((tick, _SNARE, _GHOST_VELOCITY))
        # Virada simples de toms no ultimo tempo do compasso a cada 4 bars.
        if bar % 4 == 3:
            fill_start = bar_start + 12 * sixteenth
            for i, pitch in enumerate((48, 45, 41, 38)):
                events.append((fill_start + i * (sixteenth // 2), pitch, 105 + i))

    events.sort(key=lambda e: e[0])
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    duration = sixteenth // 3
    for tick, pitch, velocity in events:
        absolute.append((
            tick, order,
            mido.Message("note_on", channel=9, note=pitch, velocity=velocity, time=0),
        ))
        order += 1
        absolute.append((
            tick + duration, order,
            mido.Message("note_off", channel=9, note=pitch, velocity=0, time=0),
        ))
        order += 1

    prev = 0
    for tick, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=max(0, tick - prev)))
        prev = tick
    mid.tracks.append(track)
    return mid


@pytest.fixture
def synthetic_drum_midi(tmp_path) -> str:
    mid = _build_synthetic_drum_midi()
    path = tmp_path / "synthetic_feel.mid"
    mid.save(str(path))
    return str(path)


def test_synthetic_feel_is_recovered_with_high_confidence(synthetic_drum_midi: str) -> None:
    result = learn_mod.learn(
        [synthetic_drum_midi], "drums", researched_at="2026-09-03",
    )
    dims = result.measurements["dimensions"]

    velocity = dims["velocity"]
    assert velocity["measured"] is True
    assert velocity["confidence"] in ("high", "medium")
    # A dinamica injetada cobre ghost (28) ate acento (120) — media longe
    # dos extremos, nada colado em um unico valor.
    assert 60 <= velocity["value"]["mean"] <= 95
    assert velocity["value"]["mode_ratio"] < 0.35

    timing = dims["timing_offset_ms"]
    assert timing["measured"] is True
    assert timing["confidence"] in ("high", "medium")
    assert timing["value"]["median_ms"] > 2.0

    ghosts = dims["ghost_notes"]
    assert ghosts["measured"] is True
    # 4 ghost hits em 20 golpes carne (kick+snare+ghost) por compasso ->
    # proporcao real, nao nula.
    assert ghosts["value"]["ratio"] > 0.05

    fills = dims["fill_density"]
    assert fills["measured"] is True
    assert fills["value"]["fills_total"] > 0

    vocab = dims["articulation_vocabulary"]
    assert vocab["measured"] is True
    assert vocab["value"]["distinct_group_count"] >= 3  # kick/snare/hat/tom


def test_synthetic_feel_style_output_validates_against_plan_style_schema(
    synthetic_drum_midi: str,
) -> None:
    result = learn_mod.learn(
        [synthetic_drum_midi], "drums", researched_at="2026-09-03",
    )
    validate_output(result.style, _output_style_schema())
    # Com feel real detectado, os parametros do plano tambem carregam os
    # numeros de feel (nao so vocabulario/estrutura, ao contrario do corpus
    # travado).
    params = result.style["drums"]["parameters"]
    assert any("velocity" in k or "timing" in k or "ghost" in k for k in params) or (
        result.style["drums"]["confidence"] != "default"
    )
