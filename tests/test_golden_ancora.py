"""Golden test: valores congelados da analise + secoes do fixture ancora_arranjo_atual.mid.

Serve de canario contra regressao silenciosa em analyze/sections/primitives.
Se qualquer valor abaixo mudar sem que o fixture seja regerado, a suite grita.
"""

from __future__ import annotations

import os

import pytest

from tools import analyze, sections

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _require_fixture() -> str:
    if not os.path.exists(FIXTURE):
        pytest.skip(f"fixture not present: {FIXTURE}")
    return FIXTURE


# --- tom global -----------------------------------------------------------

def test_ancora_key_root_is_d_sharp():
    path = _require_fixture()
    a = analyze.analyze(path)
    assert a.key_root == 3  # D# / Eb


# --- secoes ----------------------------------------------------------------

EXPECTED_SECTIONS = [
    ("INTRO A",     "intro",       4,  14),
    ("INTRO B",     "intro",      14,  30),
    ("VERSE 1",     "verse",      30,  46),
    ("CHORUS 1",    "chorus",     46,  62),
    ("VERSE 2",     "verse",      62,  78),
    ("PRE-CHORUS",  "pre",        78,  87),
    ("CHORUS 2",    "chorus",     87, 103),
    ("INTERLUDE",   "interlude", 103, 119),
    ("CHORUS 3",    "chorus",    119, 135),
    ("OUTRO",       "outro",     135, 162),
]


def test_ancora_sections_match_expected():
    path = _require_fixture()
    secs = sections.read_sections(path)
    got = [(s.label, s.kind, s.start_bar, s.end_bar) for s in secs]
    assert got == EXPECTED_SECTIONS


def test_ancora_all_sections_come_from_markers():
    path = _require_fixture()
    secs = sections.read_sections(path)
    assert all(s.source == "marker" for s in secs)


# --- acordes ---------------------------------------------------------------

# Selecao intencional: primeiro acorde de cada secao + um outlier ('unknown').
EXPECTED_CHORDS = [
    (4,   (1,  "major")),
    (8,   (11, "major")),
    (14,  (0,  "minor")),
    (46,  (3,  "minor")),
    (78,  (1,  "major")),
    (87,  (3,  "minor")),
    (103, (3,  "minor")),
    (119, (3,  "minor")),
    (135, (1,  "major")),
    (160, (3,  "unknown")),
]


def test_ancora_bar_chords_match_expected():
    path = _require_fixture()
    a = analyze.analyze(path)
    for idx, expected in EXPECTED_CHORDS:
        b = a.bars[idx]
        assert b.chord is not None, f"bar {idx} sem acorde"
        assert (b.chord.root, b.chord.quality) == expected, (
            f"bar {idx}: esperado {expected}, obtido "
            f"({b.chord.root}, {b.chord.quality!r})"
        )


def test_ancora_total_bar_count():
    path = _require_fixture()
    a = analyze.analyze(path)
    assert len(a.bars) == 163
