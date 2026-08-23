"""Testes do gerador de piano/Rhodes (US-003)."""

from __future__ import annotations

import random

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.constants import GATE_RATIOS, REGISTER_BANDS, VELOCITY_RANGES
from tools.palette.harmonic import (
    DEFAULT_KEYBOARD_REGISTER,
    KEYBOARD_BASE_VELOCITY_BUCKET,
    KEYBOARD_LAYER_ONSET_STAGGER_MS,
    KEYBOARD_ROLES,
    KEYBOARD_TIMING_JITTER_MS,
    MIN_NOTE_DURATION_S,
    PIANO_GAP_MS,
    RHODES_LEGATO_RATIO_RANGE,
    KeyboardLayer,
    KeyboardNote,
    _enforce_same_pitch_contract,
    _keyboard_base_velocity,
    _keyboard_gate,
    _legato_pair_count,
    _pedal_events_for_layer,
    _voice_keyboard_chord,
    generate_keyboard,
)
from tools.plan import PlanSection

BAR_S = 2.0


def _bar(index: int, chord: Chord | None) -> BarAnalysis:
    return BarAnalysis(
        index=index,
        start=index * BAR_S,
        end=(index + 1) * BAR_S,
        chord=chord,
    )


def _analysis_with_chord_run(
    chord: Chord,
    start_bar: int = 0,
    n_bars: int = 8,
) -> Analysis:
    bars = [_bar(i, chord) for i in range(start_bar, start_bar + n_bars)]
    return Analysis(
        key_root=chord.root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(
    label: str = "VERSE 1",
    kind: str = "verse",
    start_bar: int = 0,
    end_bar: int = 8,
) -> PlanSection:
    return PlanSection(
        label=label,
        kind=kind,
        start_bar=start_bar,
        end_bar=end_bar,
        source="marker",
        protagonist="piano_motif",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 5},
    )


# --- helpers puros ----------------------------------------------------------

def test_keyboard_base_velocity_is_normal_midpoint():
    lo, hi = VELOCITY_RANGES[KEYBOARD_BASE_VELOCITY_BUCKET]
    assert _keyboard_base_velocity() == (lo + hi) // 2


def test_keyboard_gate_falls_back_to_tight_for_unmapped_articulation():
    """let_ring nao esta em GATE_RATIOS — deve cair para tight sem levantar."""
    default = _keyboard_gate("let_ring")
    lo, hi = GATE_RATIOS["tight"]
    assert default == pytest.approx((lo + hi) / 2.0)


def test_keyboard_gate_uses_declared_articulation():
    lo, hi = GATE_RATIOS["open"]
    assert _keyboard_gate("open") == pytest.approx((lo + hi) / 2.0)


def test_voice_keyboard_chord_root_at_bottom_no_octave_double():
    """Teclado nao usa o dobramento de oitava do pad — raiz na base, corpo
    acima, sem cabeca C+12."""
    pitches = _voice_keyboard_chord(Chord(root=0, quality="major"), (48, 71))
    assert pitches[0] == 48        # C4 na base
    assert set(pitches) == {48, 52, 55}


def test_voice_keyboard_chord_stays_within_register():
    pitches = _voice_keyboard_chord(Chord(root=9, quality="minor"), (60, 84))
    assert all(60 <= p <= 84 for p in pitches)


def test_voice_keyboard_chord_below_c2_reduces_to_root_or_fifth():
    pitches = _voice_keyboard_chord(Chord(root=0, quality="major"), (0, 35))
    assert len(pitches) <= 2
    assert all(p < 36 for p in pitches)


def test_default_keyboard_register_is_mid_band():
    assert REGISTER_BANDS["mid"] == DEFAULT_KEYBOARD_REGISTER


# --- legato math ------------------------------------------------------------

def test_legato_pair_count_stays_inside_range():
    """Toda ratio sorteada dentro de RHODES_LEGATO_RATIO_RANGE => contagem
    fica em [floor(0.12*N), floor(0.47*N)] com piso 1."""
    for n_pairs in (10, 25, 100, 3):
        n_min = max(1, int(RHODES_LEGATO_RATIO_RANGE[0] * n_pairs))
        n_max = max(n_min, int(RHODES_LEGATO_RATIO_RANGE[1] * n_pairs))
        lo, hi = RHODES_LEGATO_RATIO_RANGE
        for ratio in (lo, hi, (lo + hi) / 2, lo - 0.05, hi + 0.05):
            n = _legato_pair_count(n_pairs, ratio)
            assert n_min <= n <= n_max


def test_legato_pair_count_zero_pairs_returns_zero():
    assert _legato_pair_count(0, 0.3) == 0


# --- generator: shape e escopo ---------------------------------------------

def test_generate_keyboard_produces_exactly_n_layers():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)
    layers = generate_keyboard(ana, sec, role="piano", layers=3, seed=0)
    assert [layer.index for layer in layers] == [0, 1, 2]
    for layer in layers:
        assert isinstance(layer, KeyboardLayer)
        assert len(layer.notes) > 0


def test_generate_keyboard_layers_zero_raises():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=2)
    sec = _section(end_bar=2)
    with pytest.raises(ValueError):
        generate_keyboard(ana, sec, layers=0)


def test_generate_keyboard_role_outside_vocabulary_raises():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=2)
    sec = _section(end_bar=2)
    with pytest.raises(ValueError):
        generate_keyboard(ana, sec, role="pad", layers=1)


def test_generate_keyboard_empty_section_returns_empty_layers():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=2)
    sec = _section(start_bar=10, end_bar=12)
    layers = generate_keyboard(ana, sec, role="piano", layers=2, seed=0)
    assert len(layers) == 2
    assert all(layer.notes == () for layer in layers)
    assert all(layer.pedal_events == () for layer in layers)


def test_generate_keyboard_skips_bars_without_chord():
    bars = [
        _bar(0, Chord(root=0, quality="major")),
        _bar(1, None),
        _bar(2, Chord(root=0, quality="major")),
    ]
    ana = Analysis(
        key_root=0,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )
    sec = _section(end_bar=3)
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    # Nenhuma nota comeca no intervalo do bar 1 (segundos [2.0, 4.0)).
    for n in layers[0].notes:
        assert not (2.0 <= n.start_s < 4.0)


# --- timing / velocity ------------------------------------------------------

def test_generate_keyboard_timing_jitter_within_spec():
    """AC: 'Timing +-5-15ms'. Cada onset de acorde cai dentro dessa janela
    apos o downbeat do bar (layer 0, sem stagger)."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=6)
    sec = _section(end_bar=6)
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=7)
    # Onset do acorde = onset da primeira nota do bar (menor pitch).
    lo_ms, hi_ms = KEYBOARD_TIMING_JITTER_MS
    by_bar: dict[int, float] = {}
    for n in layers[0].notes:
        bar_idx = int(n.start_s // BAR_S)
        by_bar.setdefault(bar_idx, n.start_s)
        by_bar[bar_idx] = min(by_bar[bar_idx], n.start_s)
    for bar_idx, onset in by_bar.items():
        offset_ms = (onset - bar_idx * BAR_S) * 1000.0
        assert lo_ms <= offset_ms <= hi_ms + 0.001


def test_generate_keyboard_notes_of_chord_are_not_simultaneous():
    """AC: 'Notas do acorde nao simultaneas — espalhamento de alguns ms'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=1)
    sec = _section(end_bar=1)
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    # Todos os onsets do bar 0 sao distintos (spread do voicing engine).
    starts = sorted(n.start_s for n in layers[0].notes)
    assert len(starts) >= 3
    for a, b in zip(starts, starts[1:], strict=False):
        assert b > a


def test_generate_keyboard_velocity_varies_by_spread():
    """AC: 'Velocity segue a forma da frase via motor da rodada 1'.

    Voicing engine aplica CLOSED_TRIAD_TOP_DELTA=+20 no topo. Portanto, a
    nota mais aguda de um acorde fechado tem velocity > a da base."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=1)
    sec = _section(end_bar=1)
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    notes = sorted(layers[0].notes, key=lambda n: n.pitch)
    assert notes[-1].velocity > notes[0].velocity


def test_generate_keyboard_stagger_between_layers():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=1)
    sec = _section(end_bar=1)
    layers = generate_keyboard(ana, sec, role="piano", layers=3, seed=1)
    firsts = [min(n.start_s for n in layer.notes) for layer in layers]
    assert firsts[0] < firsts[1] < firsts[2]
    # Stagger dentro da faixa configurada (soma de 2 sorteios ate 25ms cada).
    _, hi_ms = KEYBOARD_LAYER_ONSET_STAGGER_MS
    assert (firsts[-1] - firsts[0]) * 1000.0 <= hi_ms * 3


# --- Rhodes x Piano ---------------------------------------------------------

def _same_pitch_pair_stats(notes: tuple[KeyboardNote, ...]) -> tuple[int, int]:
    """Devolve (n_pairs, n_overlap_or_touching)."""
    by_pitch: dict[int, list[KeyboardNote]] = {}
    for n in notes:
        by_pitch.setdefault(n.pitch, []).append(n)
    for lst in by_pitch.values():
        lst.sort(key=lambda n: n.start_s)
    pairs = 0
    legato = 0
    for lst in by_pitch.values():
        for a, b in zip(lst, lst[1:], strict=False):
            pairs += 1
            if a.end_s >= b.start_s - 1e-9:
                legato += 1
    return pairs, legato


def test_piano_has_no_same_pitch_legato():
    """AC: 'modo piano e 100% gapped (release limpo)'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=8)
    sec = _section(end_bar=8)
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    pairs, legato = _same_pitch_pair_stats(layers[0].notes)
    assert pairs > 0
    assert legato == 0


def test_rhodes_legato_ratio_within_range():
    """AC: 'modo Rhodes: 12-47% dos pares de mesma altura com overlap ou touching'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=8)
    sec = _section(end_bar=8)
    # Testa varios seeds — a fracao final tem que ficar dentro do intervalo
    # em todo caso, nao em media (o generator trava a contagem).
    for seed in range(10):
        layers = generate_keyboard(ana, sec, role="rhodes", layers=1, seed=seed)
        pairs, legato = _same_pitch_pair_stats(layers[0].notes)
        assert pairs > 0
        ratio = legato / pairs
        assert RHODES_LEGATO_RATIO_RANGE[0] <= ratio + 1e-9
        assert ratio <= RHODES_LEGATO_RATIO_RANGE[1] + 1e-9


def test_piano_and_rhodes_differ_on_same_seed():
    """Sanidade: piano vs rhodes com mesma seed produz saidas diferentes."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=8)
    sec = _section(end_bar=8)
    piano = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    rhodes = generate_keyboard(ana, sec, role="rhodes", layers=1, seed=0)
    _, legato_p = _same_pitch_pair_stats(piano[0].notes)
    _, legato_r = _same_pitch_pair_stats(rhodes[0].notes)
    assert legato_p == 0
    assert legato_r > 0


# --- CC64 -------------------------------------------------------------------

def test_default_generation_emits_no_pedal_events():
    """AC: 'Teste verifica que nenhum CC64 e emitido quando o elemento nao pede'."""
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    for layer in layers:
        assert layer.pedal_events == ()


def test_use_sustain_cc64_emits_paired_events():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)
    layers = generate_keyboard(
        ana, sec, role="piano", layers=1, seed=0, use_sustain_cc64=True,
    )
    pedals = layers[0].pedal_events
    assert len(pedals) == 2
    assert pedals[0].value == 127
    assert pedals[1].value == 0
    assert pedals[0].time_s < pedals[1].time_s


def test_pedal_events_for_layer_empty_notes_returns_empty():
    assert _pedal_events_for_layer(()) == ()


# --- determinismo -----------------------------------------------------------

def test_same_seed_produces_same_output():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=4)
    sec = _section(end_bar=4)
    a = generate_keyboard(ana, sec, role="rhodes", layers=2, seed=42)
    b = generate_keyboard(ana, sec, role="rhodes", layers=2, seed=42)
    assert a == b


def test_different_seeds_diverge_on_stagger():
    ana = _analysis_with_chord_run(Chord(root=0, quality="major"), n_bars=1)
    sec = _section(end_bar=1)
    a = generate_keyboard(ana, sec, role="piano", layers=3, seed=1)
    b = generate_keyboard(ana, sec, role="piano", layers=3, seed=999)
    # Layer 0 nao tem stagger; ainda assim jitter individual difere entre seeds.
    l0_a = min(n.start_s for n in a[0].notes)
    l0_b = min(n.start_s for n in b[0].notes)
    assert l0_a != l0_b


# --- contrato de par (unit puro) -------------------------------------------

def test_enforce_same_pitch_contract_piano_forces_gap():
    """Nota que termina depois do proximo onset e cortada com gap PIANO_GAP."""
    notes = [
        KeyboardNote(pitch=60, velocity=90, start_s=0.0, end_s=1.5),
        KeyboardNote(pitch=60, velocity=90, start_s=1.0, end_s=2.0),
    ]
    rng = random.Random(0)
    out = _enforce_same_pitch_contract(notes, role="piano", rng=rng)
    gap_ms_min, gap_ms_max = PIANO_GAP_MS
    # End da primeira agora e antes do start da segunda por pelo menos gap_min.
    assert out[0].end_s <= 1.0 - gap_ms_min / 1000.0 + 1e-9
    assert out[0].end_s >= 1.0 - gap_ms_max / 1000.0 - 1e-9


def test_enforce_same_pitch_contract_no_pairs_returns_input():
    notes = [
        KeyboardNote(pitch=60, velocity=90, start_s=0.0, end_s=0.5),
        KeyboardNote(pitch=62, velocity=90, start_s=1.0, end_s=1.5),
    ]
    out = _enforce_same_pitch_contract(notes, role="piano", rng=random.Random(0))
    assert out == notes


def test_enforce_same_pitch_contract_empty():
    out = _enforce_same_pitch_contract([], role="piano", rng=random.Random(0))
    assert out == []


def test_enforce_same_pitch_contract_rhodes_legato_is_touching_not_overlap():
    """Legato do rhodes cria par TOUCHING (end_s == start_s), nunca overlap.

    MIDI mesma altura no mesmo canal nao expressa overlap — o primeiro
    note_off termina o P atualmente soando, truncando o segundo ataque.
    A ordem note_off-antes-de-note_on no mesmo tick garante o segundo
    ataque limpo quando a primeira nota termina exatamente no onset da
    segunda."""
    notes = [
        KeyboardNote(pitch=60, velocity=90, start_s=0.0, end_s=0.5),
        KeyboardNote(pitch=60, velocity=90, start_s=1.0, end_s=1.5),
    ]
    rng = random.Random(0)
    out = _enforce_same_pitch_contract(notes, role="rhodes", rng=rng)
    # Um unico par => n_min=max(1, int(0.12)) = 1 => vira legato.
    assert out[0].end_s == pytest.approx(1.0)
    # Segundo ataque permanece intocado — o legato so alonga o primeiro.
    assert out[1].start_s == pytest.approx(1.0)
    assert out[1].end_s == pytest.approx(1.5)


def test_enforce_same_pitch_contract_rhodes_legato_truncates_prior_overlap():
    """Se a primeira nota ja invade o onset da segunda, o legato TRUNCA
    para touching — nunca mantem overlap na saida."""
    notes = [
        KeyboardNote(pitch=60, velocity=90, start_s=0.0, end_s=1.05),
        KeyboardNote(pitch=60, velocity=90, start_s=1.0, end_s=1.5),
    ]
    rng = random.Random(0)
    out = _enforce_same_pitch_contract(notes, role="rhodes", rng=rng)
    assert out[0].end_s == pytest.approx(1.0)


def test_role_in_keyboard_roles():
    assert set(KEYBOARD_ROLES) == {"piano", "rhodes"}


def test_generate_keyboard_skips_bar_when_jitter_pushes_onset_past_end():
    """Bar tao curto que ate o menor jitter passa da fronteira — a nota do
    bar e pulada em silencio, sem levantar."""
    # Bar de duracao 3ms: qualquer jitter (>= 5ms) empurra onset >= bar.end.
    bars = [
        BarAnalysis(index=0, start=0.0, end=0.003, chord=Chord(root=0, quality="major")),
        BarAnalysis(index=1, start=1.0, end=3.0, chord=Chord(root=0, quality="major")),
    ]
    ana = Analysis(
        key_root=0,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )
    sec = PlanSection(
        label="MAIN", kind="verse", start_bar=0, end_bar=2, source="marker",
        protagonist="piano_motif",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 5},
    )
    layers = generate_keyboard(ana, sec, role="piano", layers=1, seed=0)
    # Nenhuma nota comeca dentro do bar 0 (intervalo [0, 0.003)).
    for n in layers[0].notes:
        assert n.start_s >= 1.0 - 1e-6


def test_min_note_duration_floor_enforced_when_gap_would_flip_note():
    """Nota que termina depois do proximo onset E ficaria com end < start apos
    o gap cai no MIN_NOTE_DURATION_S para nao virar nota invertida."""
    # start=0.99, next_start=1.0 -> gap 10-40ms empurraria end para 0.96-0.99;
    # ainda >= start + MIN. Vamos criar um caso patologico com start 0.9995.
    notes = [
        KeyboardNote(pitch=60, velocity=90, start_s=0.9995, end_s=1.5),
        KeyboardNote(pitch=60, velocity=90, start_s=1.0, end_s=2.0),
    ]
    out = _enforce_same_pitch_contract(notes, role="piano", rng=random.Random(0))
    assert out[0].end_s >= out[0].start_s + MIN_NOTE_DURATION_S - 1e-9
