"""Testes do motor de voicing (US-008)."""

from __future__ import annotations

from tools.voicing import (
    C2_PITCH,
    CLOSED_TRIAD_TOP_DELTA,
    OCTAVE_BASS_ACCENT_DELTA,
    OCTAVE_TOP_DELTA,
    STRINGS_GHOST_MAX_ABS_VELOCITY,
    STRINGS_GHOST_RATIO,
    WIDE_STACK_BASS_ACCENT_DELTA,
    WIDE_STACK_TOP_DELTA,
    VoicedNote,
    VoicingEngine,
    VoicingRequest,
    _apply_register_constraint,
    _spread_deltas,
)

# --- funcoes puras de spread ------------------------------------------------

def test_spread_deltas_octave_gives_bass_accent_and_top_minus_14():
    """AC: 'spread exato de 12 semitons resulta em nota grave acentuada
    e nota aguda com velocity 14 unidades menor'."""
    deltas = _spread_deltas(spread=12, n=2)
    assert deltas[0] == OCTAVE_BASS_ACCENT_DELTA
    assert deltas[0] > 0
    assert deltas[-1] == OCTAVE_TOP_DELTA
    assert deltas[-1] == -14


def test_spread_deltas_closed_triad_gives_top_plus_20():
    """AC: 'spread entre 7 e 11 semitons resulta em nota do topo com
    velocity 20 unidades maior'."""
    for spread in range(7, 12):
        deltas = _spread_deltas(spread=spread, n=3)
        assert deltas[-1] == CLOSED_TRIAD_TOP_DELTA
        assert deltas[-1] == +20
        # topo canta; base nao acentua em triade fechada.
        assert deltas[0] == 0


def test_spread_deltas_wide_stack_gives_bass_accent_and_top_minus_14():
    """AC: 'spread entre 19 e 24 semitons resulta em grave acentuado e
    topo com velocity 14 unidades menor'."""
    for spread in range(19, 25):
        deltas = _spread_deltas(spread=spread, n=3)
        assert deltas[0] == WIDE_STACK_BASS_ACCENT_DELTA
        assert deltas[0] > 0
        assert deltas[-1] == WIDE_STACK_TOP_DELTA
        assert deltas[-1] == -14


def test_spread_deltas_neutral_spread_yields_zeros():
    """Spreads fora das faixas conhecidas (0, 5, 15, 30) nao alteram."""
    for spread in (0, 5, 15, 30):
        deltas = _spread_deltas(spread=spread, n=3)
        assert deltas == [0, 0, 0]


def test_spread_deltas_single_note_returns_zero():
    assert _spread_deltas(spread=0, n=1) == [0]


# --- restricao de registro (abaixo de C2) -----------------------------------

def test_below_c2_dense_chord_reduced_to_fifth():
    """AC: 'Registro abaixo de C2 nunca recebe acorde denso, apenas nota
    unica ou quinta'."""
    # Todas abaixo de C2 (36). Existe quinta a partir da raiz (24 + 7 = 31).
    reduced = _apply_register_constraint([24, 28, 31, 34])
    assert reduced == [24, 31]


def test_below_c2_no_fifth_reduces_to_single_note():
    reduced = _apply_register_constraint([24, 28, 32, 34])
    assert reduced == [24]


def test_at_or_above_c2_untouched():
    pitches = [30, 36, 40]  # topo em C2 exato — voicing sai do registro sub
    assert _apply_register_constraint(pitches) == pitches


def test_engine_below_c2_yields_at_most_two_notes():
    """AC: 'Teste verifica que voicing gerado abaixo de C2 tem no maximo
    2 notas'."""
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(24, 27, 31, 33), role="pad"))
    assert len(result) <= 2
    assert all(n.pitch < C2_PITCH for n in result)


# --- integracao: regras de spread via engine --------------------------------

def test_engine_octave_voicing_returns_expected_deltas():
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(60, 72), role="pad"))
    assert [n.pitch for n in result] == [60, 72]
    assert result[0].velocity_delta == OCTAVE_BASS_ACCENT_DELTA
    assert result[-1].velocity_delta == OCTAVE_TOP_DELTA


def test_engine_closed_triad_voicing_returns_top_boost():
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(60, 64, 67), role="pad"))
    assert result[-1].velocity_delta == CLOSED_TRIAD_TOP_DELTA
    assert result[0].velocity_delta == 0


def test_engine_wide_stack_voicing_returns_bass_accent_and_top_minus_14():
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(48, 60, 68), role="pad"))
    assert result[0].velocity_delta == WIDE_STACK_BASS_ACCENT_DELTA
    assert result[-1].velocity_delta == WIDE_STACK_TOP_DELTA


# --- strings tutti ----------------------------------------------------------

def _strings_stack_8_notes_spanning_40st() -> tuple[int, ...]:
    # 8 pitches distribuidos em ~40 semitons a partir de C3 (48).
    return (48, 55, 60, 64, 67, 72, 79, 88)


def test_strings_marks_approximately_20_percent_ghost_internal():
    """AC: 'stack de 8 notas em aproximadamente 40 semitons com vozes
    internas em ghost (velocity abaixo de 40) em aproximadamente 20% das
    notas'."""
    engine = VoicingEngine(seed=42)
    pitches = _strings_stack_8_notes_spanning_40st()
    assert pitches[-1] - pitches[0] == 40
    result = engine.voice(VoicingRequest(pitches=pitches, role="strings"))

    ghost_count = sum(1 for n in result if n.is_ghost)
    # round(0.20 * 8) == 2 — aproximadamente 20% das notas.
    assert ghost_count == round(STRINGS_GHOST_RATIO * len(pitches)) == 2

    # Vozes ghost so acontecem em posicoes INTERNAS: nunca a mais grave
    # nem a mais aguda.
    ghost_indices = [i for i, n in enumerate(result) if n.is_ghost]
    for idx in ghost_indices:
        assert 0 < idx < len(result) - 1

    # Todas as 8 notas foram vozeadas (nada perdido).
    assert [n.pitch for n in result] == list(pitches)


def test_strings_ghost_ceiling_matches_spec_less_than_40():
    """A regra de strings define 'ghost' como velocity < 40. O motor de
    voicing so tagueia; a velocidade final entra pelo bucket 'ghost' do
    velocity engine. O teto documentado deve casar com a regra do spec.
    """
    assert STRINGS_GHOST_MAX_ABS_VELOCITY < 40


# --- espalhamento de ataque -------------------------------------------------

def test_piano_chord_notes_not_simultaneous():
    """AC: 'Notas de um mesmo acorde de piano nao sao simultaneas, com
    espalhamento de alguns milissegundos entre elas'."""
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(60, 64, 67), role="piano"))
    offsets = [n.onset_offset_ms for n in result]
    # Primeira nota entra no onset do acorde; as demais em ms crescentes.
    assert offsets[0] == 0.0
    assert offsets[1] > offsets[0]
    assert offsets[2] > offsets[1]
    # "alguns milissegundos" — total do espalhamento em ordem de dezenas de ms.
    assert 0 < offsets[-1] < 50


def test_rhodes_also_spreads_attack_like_piano():
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(60, 64, 67), role="rhodes"))
    offsets = [n.onset_offset_ms for n in result]
    assert offsets[0] == 0.0
    assert offsets[-1] > 0.0


def test_pad_voicing_notes_are_simultaneous():
    """AC: 'Para pad, o desencontro de ataque acontece entre camadas e
    nao entre notas do mesmo voicing'."""
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(60, 64, 67, 72), role="pad"))
    assert all(n.onset_offset_ms == 0.0 for n in result)


# --- determinismo -----------------------------------------------------------

def test_same_seed_same_output():
    pitches = _strings_stack_8_notes_spanning_40st()
    a = VoicingEngine(seed=7).voice(VoicingRequest(pitches=pitches, role="strings"))
    b = VoicingEngine(seed=7).voice(VoicingRequest(pitches=pitches, role="strings"))
    assert a == b


def test_empty_request_returns_empty():
    engine = VoicingEngine(seed=0)
    assert engine.voice(VoicingRequest(pitches=(), role="pad")) == []


def test_single_pitch_returns_single_note_with_zero_delta():
    engine = VoicingEngine(seed=0)
    result = engine.voice(VoicingRequest(pitches=(60,), role="pad"))
    assert result == [VoicedNote(pitch=60, velocity_delta=0)]
