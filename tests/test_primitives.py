"""Testes de comportamento dos primitivos vendorizados em tools/primitives.py.

Reencarnam os antigos "testes de contrato" contra logicpro (que checavam
`callable(logicpro_humanize._xxx)`): agora provamos que as funcoes vivem em
casa e devolvem o valor esperado para entradas concretas.
"""

from __future__ import annotations

import os
import tempfile

import pretty_midi

from tools import primitives

# --- constantes de percussao ---------------------------------------------

def test_kicks_and_snares_expose_expected_gm_pitches():
    assert {35, 36} == primitives.KICKS
    assert {37, 38, 40} == primitives.SNARES


def test_hats_toms_and_cymbals_cover_expected_gm_pitches():
    # HATS_CLOSED / HATS_OPEN / TOMS / CYMBALS sao usados por
    # fill_bar_features — a copia fiel exige os mesmos conjuntos da origem.
    assert {42, 44} == primitives.HATS_CLOSED
    assert {46} == primitives.HATS_OPEN
    assert {41, 43, 45, 47, 48, 50} == primitives.TOMS
    assert {49, 51, 52, 53, 55, 57, 59} == primitives.CYMBALS


# --- chord_root ----------------------------------------------------------

def test_chord_root_picks_c_from_c_major_triad():
    # C E G -> pitch classes 0, 4, 7. C tem terca (4) e quinta (7) acima.
    assert primitives.chord_root([0, 4, 7]) == 0


def test_chord_root_picks_a_from_a_minor_triad():
    # A C E -> pitch classes 9, 0, 4. A tem terca menor (0) e quinta (4).
    assert primitives.chord_root([9, 0, 4]) == 9


def test_chord_root_falls_back_to_first_pc_when_no_triad():
    # Power chord: E B -> 4 e 11. Sem terca, cai no primeiro pc da lista.
    assert primitives.chord_root([4, 11]) == 4


def test_chord_root_returns_zero_for_empty_input():
    assert primitives.chord_root([]) == 0


# --- key_root ------------------------------------------------------------

def _build_pm_with_pitches(pitches: list[int], length: float = 4.0) -> pretty_midi.PrettyMIDI:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    inst = pretty_midi.Instrument(program=0, is_drum=False, name="Piano")
    for p in pitches:
        inst.notes.append(pretty_midi.Note(velocity=90, pitch=p, start=0.0, end=length))
    pm.instruments.append(inst)
    return pm


def test_key_root_returns_zero_when_only_drums_present():
    # Sem nota melodica -> histograma zero -> devolve 0.
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    drums.notes.append(pretty_midi.Note(velocity=100, pitch=36, start=0.0, end=0.1))
    pm.instruments.append(drums)
    assert primitives.key_root(pm) == 0


def test_key_root_returns_pitch_class_in_zero_to_eleven():
    pm = _build_pm_with_pitches([60, 64, 67, 60, 67])  # C major
    assert 0 <= primitives.key_root(pm) <= 11


def test_key_root_detects_c_major_from_c_major_scale_material():
    # Escala e triade em C -> Krumhansl deve escolher C (pc=0).
    pm = _build_pm_with_pitches([60, 62, 64, 65, 67, 69, 71, 60, 64, 67], length=1.0)
    assert primitives.key_root(pm) == 0


# --- Bar dataclass -------------------------------------------------------

def test_bar_defaults_match_vendored_layout():
    b = primitives.Bar(idx=0, start=0.0, end=2.0)
    assert b.kicks == 0
    assert b.snares == 0
    assert b.hats == 0
    assert b.cymbals == 0
    assert b.toms == 0
    assert b.guitar_notes == 0
    assert b.guitar_avg_pitch == 0.0
    assert b.guitar_min_pitch == 127  # sentinela para "nunca visto"
    assert b.bass_notes == 0
    assert b.label == ""


# --- bars_from -----------------------------------------------------------

def _write_four_bar_midi(path: str, tempo: float = 120.0) -> pretty_midi.PrettyMIDI:
    """4 compassos em 4/4 a 120bpm (2s cada) com hits regulares."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, is_drum=False, name="Piano")
    bar_len = 4 * 60.0 / tempo
    for bar in range(4):
        t0 = bar * bar_len
        piano.notes.append(pretty_midi.Note(velocity=90, pitch=60, start=t0, end=t0 + 0.5))
        piano.notes.append(pretty_midi.Note(velocity=90, pitch=64, start=t0 + 0.25, end=t0 + 0.5))
    pm.instruments.append(piano)
    pm.write(path)
    return pm


def test_bars_from_returns_bars_covering_full_duration():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "four_bars.mid")
        _write_four_bar_midi(p)
        pm = pretty_midi.PrettyMIDI(p)
        bars = primitives.bars_from(pm)
        assert len(bars) >= 4
        assert bars[0].start == 0.0
        assert abs(bars[-1].end - pm.get_end_time()) < 1e-6


def test_bars_from_bars_are_contiguous_and_indexed():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "contig.mid")
        _write_four_bar_midi(p)
        bars = primitives.bars_from(pretty_midi.PrettyMIDI(p))
        for i, b in enumerate(bars):
            assert b.idx == i
        for i in range(len(bars) - 1):
            assert bars[i].end == bars[i + 1].start


def test_bars_from_falls_back_when_no_downbeats():
    # Sem downbeat nem time signature — a funcao tem que construir uma
    # grade 4/4 sintetica sem crashar. Precisa de >= 2 onsets distintas
    # para o estimate_tempo do pretty_midi conseguir opinar.
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    inst = pretty_midi.Instrument(program=0, name="Piano")
    for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5):
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=t, end=t + 0.4))
    pm.instruments.append(inst)
    bars = primitives.bars_from(pm)
    assert bars, "fallback deveria devolver ao menos uma barra"
    assert bars[0].start == 0.0


# --- fill_bar_features ---------------------------------------------------

def test_fill_bar_features_counts_kicks_snares_hats_cymbals_and_toms():
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    # kick=36, snare=38, hat closed=42, cymbal=49, tom=41 no primeiro compasso.
    for pitch in (36, 38, 42, 49, 41):
        drums.notes.append(pretty_midi.Note(velocity=100, pitch=pitch, start=0.1, end=0.15))
    pm.instruments.append(drums)

    bars = [primitives.Bar(idx=0, start=0.0, end=2.0),
            primitives.Bar(idx=1, start=2.0, end=4.0)]
    primitives.fill_bar_features(bars, pm)

    assert bars[0].kicks == 1
    assert bars[0].snares == 1
    assert bars[0].hats == 1
    assert bars[0].cymbals == 1
    assert bars[0].toms == 1


def test_fill_bar_features_counts_guitar_and_bass_by_instrument_name():
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    guitar = pretty_midi.Instrument(program=29, is_drum=False, name="Rhythm Guitar")
    bass = pretty_midi.Instrument(program=33, is_drum=False, name="Bass")
    guitar.notes.append(pretty_midi.Note(velocity=90, pitch=52, start=0.1, end=1.0))
    guitar.notes.append(pretty_midi.Note(velocity=90, pitch=59, start=0.6, end=1.0))
    bass.notes.append(pretty_midi.Note(velocity=90, pitch=28, start=0.2, end=0.8))
    pm.instruments.extend([guitar, bass])

    bars = [primitives.Bar(idx=0, start=0.0, end=2.0)]
    primitives.fill_bar_features(bars, pm)

    assert bars[0].guitar_notes == 2
    assert bars[0].bass_notes == 1
    assert bars[0].guitar_min_pitch == 52
    # Media dos pitches da guitarra.
    assert abs(bars[0].guitar_avg_pitch - ((52 + 59) / 2)) < 1e-6


def test_fill_bar_features_resets_guitar_min_pitch_when_empty():
    # Sem notas de guitarra -> min_pitch deve virar 0 (nao a sentinela 127).
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    bass = pretty_midi.Instrument(program=33, is_drum=False, name="Bass")
    bass.notes.append(pretty_midi.Note(velocity=90, pitch=28, start=0.1, end=0.5))
    pm.instruments.append(bass)

    bars = [primitives.Bar(idx=0, start=0.0, end=2.0)]
    primitives.fill_bar_features(bars, pm)
    assert bars[0].guitar_min_pitch == 0
    assert bars[0].guitar_avg_pitch == 0
    assert bars[0].bass_notes == 1


# --- label_sections ------------------------------------------------------

def test_label_sections_handles_empty_input_without_raising():
    primitives.label_sections([])  # nao deve levantar


def _bar(idx: int, **kw) -> primitives.Bar:
    b = primitives.Bar(idx=idx, start=idx * 2.0, end=(idx + 1) * 2.0)
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_label_sections_marks_low_activity_ends_as_intro_and_outro():
    # 16 barras: 4 vazias, 8 densas, 4 vazias. O min_bars=4 do 3o passe
    # respeita runs de tamanho >= 4, entao os extremos sobrevivem.
    bars = [_bar(i) for i in range(4)]
    for i in range(4, 12):
        bars.append(_bar(i, kicks=3, snares=3, hats=6, cymbals=1, guitar_notes=10))
    bars += [_bar(i) for i in range(12, 16)]
    primitives.label_sections(bars)
    assert bars[0].label == "intro"
    assert bars[-1].label == "outro"


def test_label_sections_enforces_minimum_section_length_of_four_bars():
    # Sequencia intencionalmente picada — apos a passagem final, nenhum run
    # deve ter menos de 4 barras.
    bars = []
    for i in range(16):
        bars.append(_bar(i, kicks=3, snares=3, hats=6, cymbals=1, guitar_notes=10))
    primitives.label_sections(bars)
    # Runs finais >= 4 (garantia do terceiro passe).
    runs = []
    i = 0
    while i < len(bars):
        j = i
        while j < len(bars) and bars[j].label == bars[i].label:
            j += 1
        runs.append((bars[i].label, j - i))
        i = j
    for label, run_len in runs:
        assert run_len >= 4, f"run '{label}' com {run_len} barras violou min=4"


# --- chordal_bars --------------------------------------------------------

class _FakeNote:
    def __init__(self, start: float, end: float, pitch: int):
        self.start = start
        self.end = end
        self.pitch = pitch


class _FakeInst:
    def __init__(self, notes):
        self.notes = notes


def test_chordal_bars_detects_bar_with_sustained_triad():
    # Triade C E G soando durante toda a barra (0..2s) — cobertura total.
    triad = _FakeInst([
        _FakeNote(0.0, 2.0, 60),
        _FakeNote(0.0, 2.0, 64),
        _FakeNote(0.0, 2.0, 67),
    ])
    result = primitives.chordal_bars(triad, [(0.0, 2.0)])
    assert result == {0}


def test_chordal_bars_skips_bar_with_fewer_notes_than_chord_size():
    # So duas notas soando: nao alcanca chord_size=3.
    dyad = _FakeInst([
        _FakeNote(0.0, 2.0, 60),
        _FakeNote(0.0, 2.0, 67),
    ])
    assert primitives.chordal_bars(dyad, [(0.0, 2.0)]) == set()


def test_chordal_bars_skips_bar_with_insufficient_coverage():
    # Triade soando apenas 5% da barra — abaixo de coverage=0.15 default.
    brief = _FakeInst([
        _FakeNote(0.0, 0.1, 60),
        _FakeNote(0.0, 0.1, 64),
        _FakeNote(0.0, 0.1, 67),
    ])
    assert primitives.chordal_bars(brief, [(0.0, 2.0)]) == set()


def test_chordal_bars_honours_custom_chord_size_and_coverage():
    # Duas notas soando 100% da barra: com chord_size=2 e coverage=0.5, entra.
    dyad = _FakeInst([
        _FakeNote(0.0, 2.0, 60),
        _FakeNote(0.0, 2.0, 67),
    ])
    result = primitives.chordal_bars(dyad, [(0.0, 2.0)], chord_size=2, coverage=0.5)
    assert result == {0}
