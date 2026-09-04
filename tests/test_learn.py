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

    # `style.drums.parameters` fica vazio mesmo com dimensoes de alta
    # confianca: nenhuma tecnica registrada em `tools/techniques/engine.py`
    # le um parametro chamado `drums_articulation_vocabulary_size` ou
    # `drums_fill_density_per_bar` — coloca-lo em `parameters` seria o
    # "parametro mentiroso" que o AGENTS.md ja rejeitou. O numero medido
    # continua disponivel em `measurements`, so nao finge ter efeito de
    # render.
    style_params = result.style["drums"]["parameters"]
    assert style_params == {}


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
    # Mesmo com feel real detectado (confidence != default), `parameters`
    # continua vazio: `learn` nunca escreve um numero medido em
    # `style.parameters` que nenhuma tecnica do motor consome (ver
    # `_build_family_style`) — o numero fica em `measurements`.
    assert result.style["drums"]["parameters"] == {}
    assert result.style["drums"]["confidence"] != "default"


# --- regressao: canal melodico nao vira "bateria" (achado do Codex, PR #110) -


def test_melodic_bass_channel_is_not_selected_as_drum_channel() -> None:
    """Uma linha de baixo tocada num canal != 9, dentro da MESMA faixa de
    pitch GM de percussao (27-87), nao pode ser tratada como bateria so
    porque a tessitura cai nessa faixa — a faixa GM (27-87) cobre 5 oitavas
    e se sobrepoe a quase toda a escrita pratica de baixo/guitarra/teclas.
    Antes do fix, `ratio >= 0.7` sozinho bastava e este canal qualificava
    (100% das notas cai em 27-87); a peca que falta e concentracao real
    num kit pequeno (`DRUM_CHANNEL_TOP_PITCH_CONCENTRATION`)."""
    ppq = 480
    mid = mido.MidiFile(ticks_per_beat=ppq)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))

    # Walking bass: 24 pitches distintos dentro de 27-87 (registro tipico
    # de baixo), cada um tocado so 2x — nada concentrado nas 3 pitches mais
    # comuns, ao contrario de um kit real (kick/snare/hat repetidos).
    pitches = [p for p in range(28, 28 + 24) for _ in range(2)]
    assert len(pitches) >= learn_mod.DRUM_CHANNEL_MIN_NOTES

    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    duration = ppq // 4
    tick = 0
    for pitch in pitches:
        absolute.append((
            tick, order,
            mido.Message("note_on", channel=0, note=pitch, velocity=90, time=0),
        ))
        order += 1
        absolute.append((
            tick + duration, order,
            mido.Message("note_off", channel=0, note=pitch, velocity=0, time=0),
        ))
        order += 1
        tick += ppq // 2

    prev = 0
    for t, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=max(0, t - prev)))
        prev = t
    mid.tracks.append(track)

    assert learn_mod._select_drum_channels(mid) == frozenset()


# --- regressao: densidade de virada usa a formula de compasso real ---------


def test_fill_density_uses_real_time_signature_not_hardcoded_4_4(tmp_path) -> None:
    """Corpus em 3/4: um compasso tem `ppq * 3` ticks, nao `ppq * 4`. O
    calculo antigo (`last_tick / (ppq * 4)`) subestimava o numero de
    compassos em 3/4 (25% a menos) e por consequencia inflava
    `fills_per_bar`. Este teste prova que o numero de compassos relatado
    bate com o real (16 compassos de 3/4), nao com o que a formula errada
    de 4/4 teria dado (12 compassos)."""
    ppq = 480
    sixteenth = ppq // 4
    n_bars = 16
    steps_per_bar = 12  # 3 tempos * 4 semicolcheias, formula de compasso 3/4

    mid = mido.MidiFile(ticks_per_beat=ppq)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums 3/4", time=0))

    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    absolute.append((
        0, order,
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
    ))
    order += 1

    duration = sixteenth // 3
    for bar in range(n_bars):
        bar_start = bar * steps_per_bar * sixteenth
        for step in range(steps_per_bar):
            tick = bar_start + step * sixteenth
            pitch = _KICK if step == 0 else (_SNARE if step == 6 else _HAT)
            absolute.append((
                tick, order,
                mido.Message("note_on", channel=9, note=pitch, velocity=100, time=0),
            ))
            order += 1
            absolute.append((
                tick + duration, order,
                mido.Message("note_off", channel=9, note=pitch, velocity=0, time=0),
            ))
            order += 1

    prev = 0
    for t, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=max(0, t - prev)))
        prev = t
    mid.tracks.append(track)

    path = tmp_path / "synthetic_3_4.mid"
    mid.save(str(path))

    result = learn_mod.learn([str(path)], "drums", researched_at="2026-09-03")
    fills = result.measurements["dimensions"]["fill_density"]

    # Real: ~16 compassos de 3/4. Formula antiga (assume 4/4 sempre) teria
    # relatado ~16 * 3/4 = 12 compassos — uma diferenca de 25%, longe da
    # tolerancia usada abaixo.
    assert fills["value"]["bars_total"] == pytest.approx(n_bars, abs=0.5)
    wrong_4_4_bars = n_bars * 3 / 4
    assert fills["value"]["bars_total"] != pytest.approx(wrong_4_4_bars, abs=0.5)


# --- regressao: offset de grade usa o tempo vigente no tick da nota --------


def test_timing_offset_uses_tempo_active_at_each_note_not_first_tempo(
    tmp_path,
) -> None:
    """Arquivo com mudanca de tempo ANTES de qualquer nota: `first_tempo`
    (o tempo do primeiro `set_tempo` do arquivo) nao e o tempo vigente onde
    as notas de fato estao. O bug antigo convertia ticks->ms com o tempo
    errado (o primeiro, 500000us/120bpm) para notas que na verdade tocam
    sob 100000us/600bpm — um fator de 5x no offset em ms calculado."""
    ppq = 480
    sixteenth = ppq // 4
    offset_ticks = 15  # deslocamento fixo em relacao a grade de semicolcheia
    n_notes = 24

    mid = mido.MidiFile(ticks_per_beat=ppq)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums tempo change", time=0))

    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    absolute.append((
        0, order, mido.MetaMessage("set_tempo", tempo=500_000, time=0),
    ))
    order += 1
    # Tempo muda bem antes da primeira nota (tick 50 < tick 2000 abaixo).
    absolute.append((
        50, order, mido.MetaMessage("set_tempo", tempo=100_000, time=0),
    ))
    order += 1

    duration = sixteenth // 3
    for i in range(n_notes):
        # `20 + i` semicolcheias inteiras: base sempre um multiplo exato
        # de `sixteenth`, para o offset REAL em relacao a grade ser
        # exatamente `offset_ticks` (tick 2400+ esta bem depois da mudanca
        # de tempo no tick 50).
        grid_tick = (20 + i) * sixteenth
        note_tick = grid_tick + offset_ticks
        absolute.append((
            note_tick, order,
            mido.Message("note_on", channel=9, note=_SNARE, velocity=100, time=0),
        ))
        order += 1
        absolute.append((
            note_tick + duration, order,
            mido.Message("note_off", channel=9, note=_SNARE, velocity=0, time=0),
        ))
        order += 1

    prev = 0
    for t, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=max(0, t - prev)))
        prev = t
    mid.tracks.append(track)

    path = tmp_path / "synthetic_tempo_change.mid"
    mid.save(str(path))

    result = learn_mod.learn([str(path)], "drums", researched_at="2026-09-03")
    median_ms = (
        result.measurements["dimensions"]["timing_offset_ms"]["value"]["median_ms"]
    )

    correct_ms = offset_ticks * 100_000 / (ppq * 1000.0)  # tempo vigente: 100000us
    wrong_ms = offset_ticks * 500_000 / (ppq * 1000.0)  # bug antigo: first_tempo
    assert median_ms == pytest.approx(correct_ms, abs=0.05)
    assert median_ms != pytest.approx(wrong_ms, abs=0.05)
