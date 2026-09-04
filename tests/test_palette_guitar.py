"""Testes do gerador de guitarra do zero (issue #19)."""

from __future__ import annotations

from pathlib import Path

import pretty_midi
import pytest

from tools.analyze import Analysis, analyze
from tools.palette.guitar import (
    DEFAULT_GUITAR_REGISTER,
    DEFAULT_GUITAR_TUNING,
    generate_guitar,
)
from tools.plan import Element, PlanSection
from tools.techniques.physical import guitar_voicing_is_playable
from tools.validators.harmony import RenderedNote, RenderedTrack, validate_harmony


def _build_chord_source(tmp_path: Path, name: str = "chords.mid") -> Path:
    """MIDI de 8 compassos, 120bpm 4/4: piano alterna E5/C#m a cada 2
    compassos, e bateria com kick em 1 e 3 — mesma estrutura de
    `tests/test_palette_bass.py::_build_chord_source`, so trocando o
    instrumento portador do acorde para nao acoplar aos testes de baixo."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    keys = pretty_midi.Instrument(program=0, name="Keys")
    drums = pretty_midi.Instrument(program=0, name="Drums", is_drum=True)
    bar_len = 2.0
    beat_len = bar_len / 4

    chords = [
        (4, 8, 11),   # E maior (raiz E=4, terca G#=8, quinta B=11)
        (4, 8, 11),
        (1, 4, 8),    # C# menor (raiz C#=1, terca E=4, quinta G#=8)
        (1, 4, 8),
        (4, 8, 11),
        (4, 8, 11),
        (1, 4, 8),
        (1, 4, 8),
    ]
    for bar, pcs in enumerate(chords):
        start = bar * bar_len
        for pc in pcs:
            pitch = 48 + pc
            keys.notes.append(pretty_midi.Note(
                velocity=80, pitch=pitch, start=start, end=start + bar_len,
            ))
        for beat in (0, 2):
            drums.notes.append(pretty_midi.Note(
                velocity=100, pitch=36, start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.append(keys)
    pm.instruments.append(drums)
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _analyze(src: Path) -> Analysis:
    return analyze(str(src))


def _section(*, start_bar=0, end_bar=8, **energy_overrides) -> PlanSection:
    energy = {
        "densidade": 5, "impacto": 5, "largura": 5,
        "altura": 5, "instabilidade": 5,
    }
    energy.update(energy_overrides)
    return PlanSection(
        label="MAIN", kind="verse", start_bar=start_bar, end_bar=end_bar,
        source="marker", energy=energy,
    )


def _guitar_element() -> Element:
    return Element(
        id="guitar_main",
        role="guitar",
        sections=["MAIN"],
        register=list(DEFAULT_GUITAR_REGISTER),
        layers=1,
        sync_role="kick_support",
        articulation="tight",
        harmony="follow_chords",
        rationale="Guitarra gerada do zero seguindo o campo harmonico.",
    )


class _FakePlan:
    def __init__(self, elements):
        self.elements = elements


# --- campo harmonico ---------------------------------------------------------

def test_generate_guitar_follows_harmonic_field(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8)
    layers = generate_guitar(analysis, section, seed=1)
    notes = layers[0].notes
    assert len(notes) > 4

    element = _guitar_element()
    track = RenderedTrack(
        element_id=element.id,
        track_name="Guitar",
        notes=tuple(
            RenderedNote(pitch=n.pitch, start_s=n.start_s, end_s=n.end_s, velocity=n.velocity)
            for n in notes
        ),
    )
    issues = validate_harmony([track], _FakePlan([element]), analysis)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"guitar notes must belong to the harmonic field: {errors}"


def test_generate_guitar_never_stacks_the_third_in_the_low_register():
    """`guitar.power_chord`: nunca terca empilhada no grave, so intervalos
    0/7/12 a partir da raiz — checa isso direto pela funcao de voicing,
    integrando com o campo harmonico real via `generate_guitar` acima."""
    from tools.analyze import Chord
    from tools.palette.guitar import _power_chord_pitches

    chord = Chord(root=4, quality="major")  # E maior: raiz E, terca G#, quinta B
    root, fifth, octave = _power_chord_pitches(chord, DEFAULT_GUITAR_REGISTER)
    for pitch in (fifth, octave):
        interval = (pitch - root) % 12
        assert interval in (0, 7), (
            f"power chord so pode conter raiz (0) ou quinta (7) acima da "
            f"raiz, nunca terca; got interval {interval} for pitch {pitch}"
        )


# --- ancoras de kick -----------------------------------------------------------

def test_generate_guitar_follows_kick_anchors(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    assert analysis.kick_positions, "fixture must carry kick anchors"
    section = _section(start_bar=0, end_bar=8, densidade=10)
    notes = generate_guitar(analysis, section, seed=2)[0].notes

    onsets = sorted({n.start_s for n in notes})
    matched = sum(
        1 for onset in onsets
        if any(abs(onset - k) < 0.02 for k in analysis.kick_positions)
    )
    assert matched / len(onsets) > 0.5, (
        "most guitar strum onsets must land within 20ms of a kick anchor "
        "when the section has kicks and density is high enough to follow them all"
    )


# --- plausibilidade fisica: voicing sempre tocavel ------------------------------

def test_generate_guitar_voicings_are_always_physically_playable(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=10)
    notes = generate_guitar(analysis, section, seed=6)[0].notes

    by_onset: dict[float, list[int]] = {}
    for note in notes:
        by_onset.setdefault(note.start_s, []).append(note.pitch)

    for onset, pitches in by_onset.items():
        assert guitar_voicing_is_playable(pitches, DEFAULT_GUITAR_TUNING), (
            f"voicing at t={onset} is not physically playable: {pitches}"
        )


def test_generate_guitar_strums_never_overlap(tmp_path):
    """Golpes (strums) sao homofonicos por construcao: cada onset distinto
    termina antes ou junto do proximo, nunca sobrepondo."""
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=10)
    notes = generate_guitar(analysis, section, seed=7)[0].notes

    by_onset: dict[float, tuple[float, float]] = {}
    for note in notes:
        prev = by_onset.get(note.start_s)
        end = note.end_s if prev is None else max(prev[1], note.end_s)
        by_onset[note.start_s] = (note.start_s, end)

    strums = sorted(by_onset.values(), key=lambda item: item[0])
    for prev, nxt in zip(strums, strums[1:], strict=False):
        assert prev[1] <= nxt[0] + 1e-9, (
            f"guitar strums must never overlap — {prev} overlaps {nxt}"
        )


def test_generate_guitar_never_goes_below_tuning_floor(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8)
    notes = generate_guitar(analysis, section, seed=8)[0].notes
    floor = min(DEFAULT_GUITAR_TUNING)
    assert all(n.pitch >= floor for n in notes)


def test_generate_guitar_respects_declared_drop_tuning(tmp_path):
    from tools.techniques.physical import _GUITAR_TUNINGS

    drop_c = _GUITAR_TUNINGS["drop_c"]
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8)
    notes = generate_guitar(
        analysis, section, seed=8, tuning=drop_c, register=(min(drop_c), 76),
    )[0].notes
    assert all(n.pitch >= min(drop_c) for n in notes)


# --- densidade acompanha o eixo energy.densidade --------------------------------

def test_guitar_note_count_grows_monotonically_with_density_axis(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)

    low = _section(densidade=1)
    high = _section(densidade=10)

    low_count = len(generate_guitar(analysis, low, seed=9)[0].notes)
    high_count = len(generate_guitar(analysis, high, seed=9)[0].notes)

    assert low_count < high_count


# --- validacoes de entrada -------------------------------------------------------

def test_generate_guitar_rejects_invalid_layers(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    with pytest.raises(ValueError):
        generate_guitar(analysis, _section(), layers=0)


def test_generate_guitar_rejects_invalid_role(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    with pytest.raises(ValueError):
        generate_guitar(analysis, _section(), role="bass")


def test_generate_guitar_empty_section_returns_empty_layers():
    empty_analysis = Analysis(
        key_root=0, bars=[], kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
    )
    layers = generate_guitar(empty_analysis, _section(start_bar=0, end_bar=8), seed=1)
    assert layers[0].notes == ()


def test_generate_guitar_is_deterministic_for_same_seed(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=7)
    notes_a = generate_guitar(analysis, section, seed=13)[0].notes
    notes_b = generate_guitar(analysis, section, seed=13)[0].notes
    assert notes_a == notes_b
