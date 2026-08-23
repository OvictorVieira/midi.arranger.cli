"""Testes da analise musical (analyze.py)."""

from __future__ import annotations

import os
import tempfile

import pretty_midi
import pytest

from tools import analyze as analysis
from tools import primitives

ANCORA_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _ancora_path() -> str:
    if os.path.exists(ANCORA_FIXTURE):
        return ANCORA_FIXTURE
    pytest.skip("ANCORA reference MIDI not available in this environment")


# --- ANCORA: tom global e ocupacao do interludio -------------------------

def test_ancora_detected_key_matches_primitives():
    path = _ancora_path()
    a = analysis.analyze(path)
    pm = pretty_midi.PrettyMIDI(path)
    # Contrato: o motor delega para primitives.key_root — o valor tem que bater
    # com o que a propria primitive devolveria em cima do mesmo PrettyMIDI.
    assert a.key_root == primitives.key_root(pm)
    assert 0 <= a.key_root <= 11


def test_ancora_interlude_occupies_mid_and_high_bands():
    path = _ancora_path()
    a = analysis.analyze(path)
    # Interludio: spec.md secao 7 -> 140.681s ate 162.624s.
    interlude_bars = [b for b in a.bars if b.start >= 140.0 and b.end <= 163.0]
    assert interlude_bars, "interlude window contained no bars"

    hit_mid = False
    hit_high = False
    for b in interlude_bars:
        occ = analysis.register_occupancy(b)
        if occ["mid"]:
            hit_mid = True
        if occ["high"]:
            hit_high = True
    assert hit_mid, "no track active in mid band during interlude"
    assert hit_high, "no track active in high band during interlude"


def test_drum_anchors_extracted_when_name_contains_drum():
    """Logic Drummer exporta a track de bateria com is_drum=False. O motor
    tem que reconhecer pelo nome — caso contrario kick/snare sumiriam."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "drummer.mid")
        pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        # Note: is_drum=False de proposito — reproduz o comportamento do Logic.
        drums = pretty_midi.Instrument(program=0, is_drum=False, name="Drummer")
        for i, t in enumerate((0.0, 0.5, 1.0, 1.5)):
            # kicks em beats 1 e 3 (t=0.0, 1.0), snare em 2 e 4 (t=0.5, 1.5).
            pitch = 36 if i % 2 == 0 else 38
            drums.notes.append(pretty_midi.Note(velocity=100, pitch=pitch,
                                                 start=t, end=t + 0.05))
        pm.instruments.append(drums)
        pm.write(p)
        a = analysis.analyze(p)
        assert len(a.kick_positions) == 2
        assert len(a.snare_positions) == 2


# --- compasso vazio -------------------------------------------------------

def _build_midi_with_empty_bar(path: str) -> None:
    """4 compassos a 120bpm: hits regulares no compasso 0 (garantem que o
    estimate_tempo do pretty_midi tenha varias onsets), compassos 1 e 2
    vazios (edge case) e um hit no compasso 3 (empurra o end_time)."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    piano = pretty_midi.Instrument(program=0, is_drum=False, name="Piano")
    for t in (0.0, 0.5, 1.0, 1.5):
        piano.notes.append(pretty_midi.Note(velocity=90, pitch=60, start=t, end=t + 0.4))
    piano.notes.append(pretty_midi.Note(velocity=90, pitch=60, start=7.5, end=7.9))
    pm.instruments.append(piano)
    pm.write(path)


def test_empty_bar_returns_empty_occupancy_without_raising():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "empty_bar.mid")
        _build_midi_with_empty_bar(p)
        a = analysis.analyze(p)

        empty_bars = [b for b in a.bars if not b.notes_per_track]
        assert empty_bars, "expected at least one empty bar in the fixture"

        occ = analysis.register_occupancy(empty_bars[0])
        # Todas as bandas presentes, cada uma vazia.
        assert set(occ.keys()) == {"sub", "low", "mid", "high"}
        assert all(v == set() for v in occ.values())


# --- register_occupancy: nota alta cai na banda high ---------------------

def _build_multiband_midi(path: str) -> None:
    """Piano com C4 (mid) + C6 (high) no compasso 0 e outra hit em 1.5s
    para satisfazer estimate_tempo. Bass em E1+B1 (sub) no compasso 0."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    piano = pretty_midi.Instrument(program=0, is_drum=False, name="Piano")
    piano.notes.append(pretty_midi.Note(velocity=90, pitch=60, start=0.0, end=1.0))
    piano.notes.append(pretty_midi.Note(velocity=90, pitch=84, start=0.0, end=1.0))
    piano.notes.append(pretty_midi.Note(velocity=90, pitch=60, start=1.5, end=1.9))
    bass = pretty_midi.Instrument(program=33, is_drum=False, name="Bass")
    bass.notes.append(pretty_midi.Note(velocity=90, pitch=28, start=0.0, end=1.0))
    bass.notes.append(pretty_midi.Note(velocity=90, pitch=35, start=0.0, end=1.0))
    bass.notes.append(pretty_midi.Note(velocity=90, pitch=28, start=1.5, end=1.9))
    pm.instruments.extend([piano, bass])
    pm.write(path)


def test_register_occupancy_reports_bands_correctly():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "multi.mid")
        _build_multiband_midi(p)
        a = analysis.analyze(p)
        occ = analysis.register_occupancy(a.bars[0])
        assert "Piano" in occ["mid"]
        assert "Piano" in occ["high"]
        assert "Bass" in occ["sub"]
        assert "Bass" not in occ["mid"]


# --- chord detection ------------------------------------------------------

def _build_chord_midi(path: str, pitches: list[int]) -> None:
    """Emite o acorde no compasso 0 e um breve reataque em 1.5s (necessario
    para o estimate_tempo do pretty_midi ter 2+ onsets distintas)."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    piano = pretty_midi.Instrument(program=0, is_drum=False, name="Piano")
    for p in pitches:
        piano.notes.append(pretty_midi.Note(velocity=90, pitch=p, start=0.0, end=4.0))
    piano.notes.append(pretty_midi.Note(velocity=60, pitch=pitches[0], start=1.5, end=1.6))
    pm.instruments.append(piano)
    pm.write(path)


def test_chord_detection_major_and_minor_and_power():
    with tempfile.TemporaryDirectory() as tmp:
        # C major: C E G.
        p_maj = os.path.join(tmp, "cmaj.mid")
        _build_chord_midi(p_maj, [60, 64, 67])
        a = analysis.analyze(p_maj)
        assert a.bars[0].chord is not None
        assert a.bars[0].chord.root == 0
        assert a.bars[0].chord.quality == "major"

        # A minor: A C E.
        p_min = os.path.join(tmp, "amin.mid")
        _build_chord_midi(p_min, [69, 72, 76])
        a = analysis.analyze(p_min)
        assert a.bars[0].chord is not None
        assert a.bars[0].chord.root == 9
        assert a.bars[0].chord.quality == "minor"

        # Power chord: E B (root + fifth, sem terca).
        p_pow = os.path.join(tmp, "epow.mid")
        _build_chord_midi(p_pow, [40, 47])
        a = analysis.analyze(p_pow)
        assert a.bars[0].chord is not None
        assert a.bars[0].chord.root == 4
        assert a.bars[0].chord.quality == "power"


# --- unisono de guitarras -------------------------------------------------

def _build_two_guitars_with_unison(path: str) -> None:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    g1 = pretty_midi.Instrument(program=29, is_drum=False, name="Rhythm Guitar")
    g2 = pretty_midi.Instrument(program=30, is_drum=False, name="Lead Guitar")
    # Ataque simultaneo em 0.5s.
    g1.notes.append(pretty_midi.Note(velocity=100, pitch=40, start=0.5, end=1.0))
    g2.notes.append(pretty_midi.Note(velocity=100, pitch=52, start=0.502, end=1.0))
    # E um ataque solo em 1.5s (nao deve virar unisono).
    g1.notes.append(pretty_midi.Note(velocity=100, pitch=40, start=1.5, end=2.0))
    pm.instruments.extend([g1, g2])
    pm.write(path)


def test_guitar_unison_detected_only_when_two_tracks_hit_together():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "unison.mid")
        _build_two_guitars_with_unison(p)
        a = analysis.analyze(p)
        assert len(a.guitar_unison_positions) == 1
        assert abs(a.guitar_unison_positions[0] - 0.5) < 0.01


def test_single_guitar_produces_no_unisons():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "solo.mid")
        pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        g1 = pretty_midi.Instrument(program=29, is_drum=False, name="Rhythm Guitar")
        g1.notes.append(pretty_midi.Note(velocity=100, pitch=40, start=0.0, end=1.0))
        g1.notes.append(pretty_midi.Note(velocity=100, pitch=47, start=0.0, end=1.0))
        g1.notes.append(pretty_midi.Note(velocity=100, pitch=40, start=1.5, end=2.0))
        pm.instruments.append(g1)
        pm.write(p)
        a = analysis.analyze(p)
        assert a.guitar_unison_positions == []


# --- contrato com tools.primitives ----------------------------------------

def test_contract_key_root_available():
    assert callable(primitives.key_root)


def test_contract_chord_root_available():
    assert callable(primitives.chord_root)


def test_contract_chordal_bars_available():
    assert callable(primitives.chordal_bars)


def test_analyze_module_lives_inside_tools():
    """analyze.py mora dentro do pacote tools."""
    here = os.path.dirname(os.path.abspath(analysis.__file__))
    assert os.path.basename(here) == "tools", here
