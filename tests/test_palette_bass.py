"""Testes do gerador de baixo do zero (issue #20)."""

from __future__ import annotations

from pathlib import Path

import pretty_midi
import pytest

from tools.analyze import Analysis, Chord, analyze
from tools.palette.bass import DEFAULT_BASS_REGISTER, _chord_tone_pitches, generate_bass
from tools.plan import Element, PlanSection
from tools.validators.harmony import RenderedNote, RenderedTrack, validate_harmony

# --- fixtures compartilhadas (tambem usadas por test_palette_drums.py) ------


def _build_chord_source(tmp_path: Path, name: str = "chords.mid") -> Path:
    """MIDI de 8 compassos, 120bpm 4/4: guitarra alterna C maior / A menor
    a cada 2 compassos (acorde detectavel por `analyze._detect_chord`), e
    uma track de bateria com kick em cada tempo forte (1 e 3) — ancora
    para o baixo seguir."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    guitar = pretty_midi.Instrument(program=27, name="Guitar")
    drums = pretty_midi.Instrument(program=0, name="Drums", is_drum=True)
    bar_len = 2.0
    beat_len = bar_len / 4

    chords = [
        (0, 4, 7),   # C maior
        (0, 4, 7),
        (9, 0, 4),   # A menor (raiz A=9, terca C=0, quinta E=4)
        (9, 0, 4),
        (0, 4, 7),
        (0, 4, 7),
        (9, 0, 4),
        (9, 0, 4),
    ]
    for bar, pcs in enumerate(chords):
        start = bar * bar_len
        for pc in pcs:
            pitch = 48 + pc
            guitar.notes.append(pretty_midi.Note(
                velocity=80, pitch=pitch, start=start, end=start + bar_len,
            ))
        for beat in (0, 2):
            drums.notes.append(pretty_midi.Note(
                velocity=100, pitch=36, start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.append(guitar)
    pm.instruments.append(drums)
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _build_b_major_chord_source(tmp_path: Path, name: str = "b_major.mid") -> Path:
    """MIDI de 8 compassos, 120bpm 4/4, alternando Si maior (root pc 11) /
    Mi menor a cada 2 compassos — reproduz o exemplo concreto do achado do
    Codex na PR #69: registro estreito `[28, 35]` sobre acorde de Si maior
    produzia candidato 39 (terca), que a subtracao unica de uma oitava
    levava a pitch 27 (abaixo do registro E do piso de afinacao E1=28)."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    guitar = pretty_midi.Instrument(program=27, name="Guitar")
    drums = pretty_midi.Instrument(program=0, name="Drums", is_drum=True)
    bar_len = 2.0
    beat_len = bar_len / 4

    chords = [
        (11, 3, 6),   # B maior (raiz B=11, terca D#=3, quinta F#=6)
        (11, 3, 6),
        (4, 7, 11),   # E menor (raiz E=4, terca G=7, quinta B=11)
        (4, 7, 11),
        (11, 3, 6),
        (11, 3, 6),
        (4, 7, 11),
        (4, 7, 11),
    ]
    for bar, pcs in enumerate(chords):
        start = bar * bar_len
        for pc in pcs:
            pitch = 48 + pc
            guitar.notes.append(pretty_midi.Note(
                velocity=80, pitch=pitch, start=start, end=start + bar_len,
            ))
        for beat in (0, 2):
            drums.notes.append(pretty_midi.Note(
                velocity=100, pitch=36, start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.append(guitar)
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


def _bass_element(section_label: str = "MAIN") -> Element:
    return Element(
        id="bass_main",
        role="bass",
        sections=[section_label],
        register=list(DEFAULT_BASS_REGISTER),
        layers=1,
        sync_role="kick_support",
        articulation="tight",
        harmony="follow_chords",
        rationale="Baixo gerado do zero seguindo o campo harmonico.",
    )


class _FakePlan:
    """Substituto minimo de ArrangementPlan — validate_harmony so le
    `plan.elements`."""

    def __init__(self, elements):
        self.elements = elements


# --- campo harmonico ----------------------------------------------------------

def test_generate_bass_follows_harmonic_field(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8)
    layers = generate_bass(analysis, section, seed=1)
    notes = layers[0].notes
    assert len(notes) > 4

    element = _bass_element()
    track = RenderedTrack(
        element_id=element.id,
        track_name="Bass",
        notes=tuple(
            RenderedNote(pitch=n.pitch, start_s=n.start_s, end_s=n.end_s, velocity=n.velocity)
            for n in notes
        ),
    )
    issues = validate_harmony([track], _FakePlan([element]), analysis)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"bass notes must belong to the harmonic field: {errors}"


# --- ancoras de kick -----------------------------------------------------------

def test_generate_bass_follows_kick_anchors(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    assert analysis.kick_positions, "fixture must carry kick anchors"
    section = _section(start_bar=0, end_bar=8, densidade=10)
    notes = generate_bass(analysis, section, seed=2)[0].notes

    matched = 0
    for note in notes:
        if any(abs(note.start_s - k) < 0.02 for k in analysis.kick_positions):
            matched += 1
    assert matched / len(notes) > 0.5, (
        "most bass onsets must land within 20ms of a kick anchor when the "
        "section has kicks and density is high enough to follow them all"
    )


# --- contorno proprio ----------------------------------------------------------

def test_generate_bass_contour_is_not_only_the_tonic(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=8)
    notes = generate_bass(analysis, section, seed=4)[0].notes
    distinct_pitch_classes = {n.pitch % 12 for n in notes}
    assert len(distinct_pitch_classes) > 1, (
        "bass must move beyond a single repeated pitch class (own contour, "
        "not just doubling the tonic)"
    )


# --- plausibilidade fisica: quase-monofonico ------------------------------------

def test_generate_bass_notes_never_overlap(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=10)
    notes = sorted(generate_bass(analysis, section, seed=6)[0].notes, key=lambda n: n.start_s)
    for prev, nxt in zip(notes, notes[1:], strict=False):
        assert prev.end_s <= nxt.start_s + 1e-9, (
            f"bass is monophonic by construction — {prev} overlaps {nxt}"
        )


def test_generate_bass_never_goes_below_tuning_floor(tmp_path):
    from tools.techniques.physical import _BASS_DEFAULT_TUNING

    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8)
    notes = generate_bass(analysis, section, seed=8)[0].notes
    floor = min(_BASS_DEFAULT_TUNING)
    assert all(n.pitch >= floor for n in notes)


# --- registro estreito: nunca produzir tom fora dos limites (achado Codex PR #69) ----

def test_chord_tone_pitches_stays_within_narrow_register():
    """Exemplo concreto do achado: registro `[28, 35]` sobre Si maior. A
    implementacao anterior subtraia uma oitava so quando o candidato
    excedia `hi`, sem checar `lo` depois — a terca (39) virava 27, abaixo
    do registro e do piso de afinacao E1 (28)."""
    b_major = Chord(root=11, quality="major")
    register = (28, 35)
    pitches = _chord_tone_pitches(b_major, register)
    assert pitches, "at least one chord tone must fit a 7-semitone register"
    assert all(register[0] <= p <= register[1] for p in pitches), (
        f"every returned tone must fall inside {register}, got {pitches}"
    )
    assert 27 not in pitches, "regression: tone must never leak below the register"


def test_chord_tone_pitches_rejects_impossible_register():
    """Registro mais estreito que qualquer classe de pitch do acorde nao
    tem tom nenhum que caiba — deve falhar explicito, nunca devolver pitch
    fora do intervalo pedido."""
    b_major = Chord(root=11, quality="major")
    with pytest.raises(ValueError):
        _chord_tone_pitches(b_major, (100, 100))


def test_generate_bass_never_leaves_narrow_register(tmp_path):
    """Integracao: `generate_bass` sobre uma progressao real com Si maior
    e registro estreito `[28, 35]` nunca emite nota fora do registro
    declarado — reproduz de ponta a ponta o cenario do achado."""
    src = _build_b_major_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=8)
    register = (28, 35)
    notes = generate_bass(analysis, section, register=register, seed=3)[0].notes
    assert notes, "fixture must actually produce bass notes"
    assert all(register[0] <= n.pitch <= register[1] for n in notes), (
        f"every bass note must stay within the declared register {register}: "
        f"{[n.pitch for n in notes if not (register[0] <= n.pitch <= register[1])]}"
    )


# --- densidade acompanha o eixo energy.densidade --------------------------------

def test_note_count_grows_monotonically_with_density_axis(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)

    low = _section(densidade=1)
    high = _section(densidade=10)

    low_count = len(generate_bass(analysis, low, seed=9)[0].notes)
    high_count = len(generate_bass(analysis, high, seed=9)[0].notes)

    assert low_count < high_count


# --- validacoes de entrada -------------------------------------------------------

def test_generate_bass_rejects_invalid_layers(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    with pytest.raises(ValueError):
        generate_bass(analysis, _section(), layers=0)


def test_generate_bass_rejects_invalid_role(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    with pytest.raises(ValueError):
        generate_bass(analysis, _section(), role="drums")


def test_generate_bass_empty_section_returns_empty_layers():
    empty_analysis = Analysis(
        key_root=0, bars=[], kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
    )
    layers = generate_bass(empty_analysis, _section(start_bar=0, end_bar=8), seed=1)
    assert len(layers) == 1
    assert layers[0].notes == ()
