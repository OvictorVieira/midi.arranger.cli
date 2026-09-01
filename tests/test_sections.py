"""Testes de leitura de secoes por marcadores + fallback heuristico."""

from __future__ import annotations

import os
import tempfile

import mido
import pretty_midi
import pytest

from tools import primitives, sections

ANCORA_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _ancora_path() -> str:
    if os.path.exists(ANCORA_FIXTURE):
        return ANCORA_FIXTURE
    pytest.skip("ANCORA reference MIDI not available in this environment")


# --- normalize_kind -------------------------------------------------------

def test_normalize_kind_verse_variants():
    assert sections.normalize_kind("VERSE 1") == "verse"
    assert sections.normalize_kind("Verse") == "verse"
    assert sections.normalize_kind("verso 1") == "verse"


def test_normalize_kind_chorus_variants():
    assert sections.normalize_kind("CHORUS 2") == "chorus"
    assert sections.normalize_kind("chorus") == "chorus"
    assert sections.normalize_kind("Refrão") == "chorus"


def test_normalize_kind_pre_chorus_beats_chorus():
    # 'PRE-CHORUS' contem 'chorus'; a regra precisa dar 'pre' e nao 'chorus'.
    assert sections.normalize_kind("PRE-CHORUS") == "pre"
    assert sections.normalize_kind("pre chorus") == "pre"
    assert sections.normalize_kind("prechorus") == "pre"


def test_normalize_kind_other_families():
    assert sections.normalize_kind("INTRO A") == "intro"
    assert sections.normalize_kind("INTRO B") == "intro"
    assert sections.normalize_kind("INTERLUDE") == "interlude"
    assert sections.normalize_kind("BREAKDOWN") == "breakdown"
    assert sections.normalize_kind("Bridge") == "bridge"
    assert sections.normalize_kind("OUTRO") == "outro"


def test_normalize_kind_build_and_hold_conventions():
    # 'build'/'build-up' e 'hold' sao cues de producao comuns (nao
    # especificos de uma musica) — build tem a mesma funcao formal de um
    # pre-chorus (tensao crescente); hold e pausa/fermata, mapeada como
    # interlude (transicao/textura, nao conteudo principal).
    assert sections.normalize_kind("BUILD") == "pre"
    assert sections.normalize_kind("Build-Up") == "pre"
    assert sections.normalize_kind("build up") == "pre"
    assert sections.normalize_kind("HOLD") == "interlude"
    assert sections.normalize_kind("Hold") == "interlude"


def test_normalize_kind_wider_production_cue_vocabulary():
    # Vocabulario adicional de cue comum (EN/PT), mesma funcao formal dos
    # oito kinds canonicos — nenhum e especifico de uma musica.
    assert sections.normalize_kind("Riser") == "pre"
    assert sections.normalize_kind("Pre-Drop") == "pre"
    assert sections.normalize_kind("predrop") == "pre"
    assert sections.normalize_kind("Transition") == "interlude"
    assert sections.normalize_kind("Transição") == "interlude"
    assert sections.normalize_kind("Turnaround") == "interlude"
    assert sections.normalize_kind("Vamp") == "interlude"
    assert sections.normalize_kind("Quebra") == "breakdown"
    assert sections.normalize_kind("Drop") == "breakdown"
    assert sections.normalize_kind("Solo") == "bridge"
    assert sections.normalize_kind("Hook") == "chorus"
    assert sections.normalize_kind("Gancho") == "chorus"
    assert sections.normalize_kind("Refrain") == "chorus"
    assert sections.normalize_kind("Estrofe") == "verse"
    assert sections.normalize_kind("Introdução") == "intro"
    assert sections.normalize_kind("Coda") == "outro"
    assert sections.normalize_kind("Tag") == "outro"


def test_normalize_kind_numbered_variants_of_new_cue_vocabulary():
    # Achado do Codex na PR: os padroes ancorados em string inteira
    # ("^hook$") nao reconheciam variante numerada/com letra, ao contrario
    # do resto do arquivo ("CHORUS 2" ja casava com "chorus" por substring).
    assert sections.normalize_kind("HOOK 1") == "chorus"
    assert sections.normalize_kind("GANCHO A") == "chorus"
    assert sections.normalize_kind("SOLO 2") == "bridge"
    assert sections.normalize_kind("CODA FINAL") == "outro"
    assert sections.normalize_kind("TAG 1") == "outro"
    assert sections.normalize_kind("BUILD 1") == "pre"
    assert sections.normalize_kind("HOLD 2") == "interlude"
    assert sections.normalize_kind("PRE-DROP 2") == "pre"
    # Palavra composta que so acidentalmente comeca com o mesmo prefixo nao
    # deve casar — a fronteira de palavra (\b) protege isso.
    assert sections.normalize_kind("Hooked") is None


def test_normalize_kind_explicit_section_beats_coexisting_modifier():
    # Achados do Codex na PR: quando um marcador combina o nome explicito da
    # secao com um cue de producao secundario de OUTRA familia, o explicito
    # tem que vencer — a ordem de primeiro-match da tupla nao pode deixar o
    # modificador generico sobrepor o nome literal da secao.
    assert sections.normalize_kind("CHORUS DROP") == "chorus"
    assert sections.normalize_kind("INTRO RISER") == "intro"
    assert sections.normalize_kind("BRIDGE VAMP") == "bridge"


def test_normalize_kind_cue_alias_accepts_qualifier_prefix():
    # Achado do Codex na PR: "^solo$"/"^hook$"/"^gancho$" (virados "\bsolo\b"
    # etc.) precisam casar em qualquer posicao do rotulo, nao so no inicio —
    # marcador comum qualifica o cue por instrumento/voz.
    assert sections.normalize_kind("GUITAR SOLO") == "bridge"
    assert sections.normalize_kind("VOCAL HOOK") == "chorus"


def test_normalize_kind_new_modifiers_do_not_match_as_substring():
    # Achado do Codex na PR: "vamp" e "drop" sem fronteira de palavra casavam
    # como substring de qualquer palavra ("Revamped" contem "vamp", "Backdrop"
    # contem "drop"), classificando errado rotulo sem nenhuma intencao de cue.
    # "Revamped Chorus" ja e protegido pela precedencia canonical (chorus
    # explicito vence antes do modificador ser sequer checado); "Backdrop"
    # nao tem canonical nenhum, entao so a fronteira de palavra em "drop"
    # evita o falso positivo.
    assert sections.normalize_kind("Revamped Chorus") == "chorus"
    assert sections.normalize_kind("Backdrop") is None
    # Sufixo numerado continua casando normalmente.
    assert sections.normalize_kind("DROP 1") == "breakdown"
    assert sections.normalize_kind("VAMP 2") == "interlude"


def test_normalize_kind_pre_drop_beats_breakdown_drop():
    # 'PRE-DROP' contem 'drop'; a regra precisa dar 'pre' e nao 'breakdown',
    # mesma logica que ja protege 'pre-chorus' vs 'chorus'.
    assert sections.normalize_kind("PRE-DROP") == "pre"
    assert sections.normalize_kind("pre drop") == "pre"


def test_normalize_kind_unknown_returns_none():
    assert sections.normalize_kind("mystery") is None
    assert sections.normalize_kind("") is None


# --- ANCORA fixture -------------------------------------------------------

EXPECTED_ANCORA = [
    # (label, kind, start_tick, expected_seconds)
    ("INTRO A",     "intro",     7680,   5.517),
    ("INTRO B",     "intro",     26880, 19.310),
    ("VERSE 1",     "verse",     57600, 41.253),
    ("CHORUS 1",    "chorus",    88320, 63.196),
    ("VERSE 2",     "verse",     119040, 85.139),
    ("PRE-CHORUS",  "pre",       149760, 107.082),
    ("CHORUS 2",    "chorus",    166080, 118.739),
    ("INTERLUDE",   "interlude", 196800, 140.681),
    ("CHORUS 3",    "chorus",    227520, 162.624),
    ("OUTRO",       "outro",     258240, 184.567),
]


def test_ancora_markers_match_spec_section_7():
    path = _ancora_path()
    secs = sections.read_sections(path)
    assert len(secs) == len(EXPECTED_ANCORA), \
        f"expected {len(EXPECTED_ANCORA)} sections, got {len(secs)}"

    pm = pretty_midi.PrettyMIDI(path)
    for got, (label, kind, start_tick, exp_sec) in zip(secs, EXPECTED_ANCORA, strict=True):
        assert got.label == label, f"label mismatch: {got.label!r} != {label!r}"
        assert got.kind == kind, f"kind mismatch for {label}: {got.kind!r}"
        assert got.source == "marker"
        assert got.start_tick == start_tick, f"tick mismatch for {label}"
        # Sanity: tick to seconds bate com o spec dentro de 10ms.
        got_sec = pm.tick_to_time(got.start_tick)
        assert abs(got_sec - exp_sec) < 0.01, \
            f"seconds mismatch for {label}: {got_sec} vs {exp_sec}"


def test_ancora_end_tick_chains_correctly():
    path = _ancora_path()
    secs = sections.read_sections(path)
    for i in range(len(secs) - 1):
        assert secs[i].end_tick == secs[i + 1].start_tick, \
            f"section {secs[i].label} does not chain into {secs[i + 1].label}"


def test_ancora_bar_indices_are_monotonic_and_within_range():
    path = _ancora_path()
    secs = sections.read_sections(path)
    prev_end = -1
    for s in secs:
        assert 0 <= s.start_bar < s.end_bar
        assert s.start_bar >= prev_end - 1  # tolera fronteira ambigua
        prev_end = s.end_bar


# --- fallback quando MIDI nao tem marcador --------------------------------

def _build_synthetic_midi_without_markers(path: str) -> None:
    """Gera um MIDI com bateria + guitarra e SEM marcadores."""
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)

    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    guitar = pretty_midi.Instrument(program=29, is_drum=False, name="Guitar")

    bar_len = 4 * 60.0 / 120.0  # 2.0s por compasso
    for bar in range(16):
        t0 = bar * bar_len
        # Kick nos beats 1 e 3.
        for beat in (0, 2):
            drums.notes.append(pretty_midi.Note(
                velocity=100, pitch=36,
                start=t0 + beat * (bar_len / 4),
                end=t0 + beat * (bar_len / 4) + 0.05,
            ))
        # Snare nos beats 2 e 4.
        for beat in (1, 3):
            drums.notes.append(pretty_midi.Note(
                velocity=100, pitch=38,
                start=t0 + beat * (bar_len / 4),
                end=t0 + beat * (bar_len / 4) + 0.05,
            ))
        # Guitar power-chord no downbeat.
        for pitch in (40, 47):
            guitar.notes.append(pretty_midi.Note(
                velocity=90, pitch=pitch, start=t0, end=t0 + bar_len,
            ))

    pm.instruments.extend([drums, guitar])
    pm.write(path)


def test_fallback_inferred_source_when_no_markers():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "no_markers.mid")
        _build_synthetic_midi_without_markers(p)

        # Sanity: garante que nao existe marker no arquivo gerado.
        mid = mido.MidiFile(p)
        assert not any(m.type == "marker" for tr in mid.tracks for m in tr)

        secs = sections.read_sections(p)
        assert secs, "fallback should produce at least one section"
        assert all(s.source == "inferred" for s in secs), \
            f"expected all inferred, got sources {[s.source for s in secs]}"
        assert all(s.kind in sections.CANONICAL_KINDS for s in secs), \
            f"unknown kinds: {[s.kind for s in secs]}"
        # Cobertura contigua do arquivo.
        assert secs[0].start_bar == 0
        for i in range(len(secs) - 1):
            assert secs[i].end_bar == secs[i + 1].start_bar


# --- contrato com tools.primitives ----------------------------------------

def test_contract_label_sections_is_callable_from_primitives():
    """Se tools.primitives.label_sections sumir/renomear, quebra AQUI."""
    assert callable(primitives.label_sections)


def test_contract_bars_from_and_fill_bar_features_available():
    assert callable(primitives.bars_from)
    assert callable(primitives.fill_bar_features)


def test_sections_module_lives_inside_tools(tmp_path):
    """Sanity: sections.py mora dentro do pacote tools."""
    here = os.path.dirname(os.path.abspath(sections.__file__))
    assert os.path.basename(here) == "tools", here
