"""Testes de regressao para US-005: registro estreito demais em strings.

`_tutti_pitches` estourava `IndexError` quando o registro nao comportava
nenhum grau do acorde dentro do span do tutti. O caminho nao-tutti
(`_initial_voice_pitches`) ja nao crashava, mas devolvia pitch acima de
`hi`, violando o registro declarado. Ambos passam a degradar para a
tonica snapped em [lo, hi]."""

from __future__ import annotations

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.palette.harmonic import (
    STRINGS_TUTTI_MAX_VOICES,
    _initial_voice_pitches,
    _tutti_pitches,
    generate_strings,
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


def _analysis(chord_bars: list[Chord | None]) -> Analysis:
    return Analysis(
        key_root=chord_bars[0].root if chord_bars and chord_bars[0] else 0,
        bars=[_bar(i, c) for i, c in enumerate(chord_bars)],
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=[],
    )


def _section(end_bar: int = 2) -> PlanSection:
    return PlanSection(
        label="S1",
        kind="chorus",
        start_bar=0,
        end_bar=end_bar,
        source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


# --- _tutti_pitches: caso do PRD ---------------------------------------------


def test_tutti_pitches_narrow_register_returns_non_empty():
    """Reproducao literal do AC: `_tutti_pitches(Chord(root=5, quality='minor'),
    (60, 63))` estourava IndexError no chamador porque devolvia lista vazia."""
    pitches = _tutti_pitches(Chord(root=5, quality="minor"), (60, 63))
    assert pitches, "narrow register deve degradar para 1+ pitch, nao lista vazia"


def test_tutti_pitches_narrow_register_stays_within_register():
    lo, hi = 60, 63
    pitches = _tutti_pitches(Chord(root=5, quality="minor"), (lo, hi))
    for p in pitches:
        assert lo <= p <= hi, f"pitch {p} fora do registro [{lo}, {hi}]"


def test_tutti_pitches_narrow_register_snaps_to_root_when_no_degree_fits():
    """Registro (60, 63) sem nota da classe F: fallback usa a tonica
    snapped em [lo, hi] — como F=65 nao cabe, cai para `lo`=60."""
    pitches = _tutti_pitches(Chord(root=5, quality="minor"), (60, 63))
    assert pitches == [60]


def test_tutti_pitches_narrow_register_uses_root_when_pitch_class_fits():
    """Registro estreito mas contem a tonica: fallback pega esse pitch."""
    # Register (62, 63): F root (pc=5) → snap = 62 + ((5-62)%12) = 62+3 = 65
    # que nao cabe → fallback lo=62.
    # Para exercitar o ramo "cabe": C root (pc=0), register (60, 61) — 60 cabe.
    pitches = _tutti_pitches(Chord(root=0, quality="major"), (60, 61))
    assert pitches == [60]


# --- _initial_voice_pitches: mesmo cenario -----------------------------------


def test_initial_voice_pitches_narrow_register_does_not_crash():
    """Nao-tutti ja nao crashava, mas o teste blinda contra regressoes."""
    pitches = _initial_voice_pitches(Chord(root=5, quality="minor"), (60, 63), voices=3)
    assert len(pitches) == 3


def test_initial_voice_pitches_narrow_register_stays_within_register():
    """Antes do fix o fallback devolvia `root_base` que podia cair fora de
    [lo, hi]. Agora snap para dentro."""
    lo, hi = 60, 63
    pitches = _initial_voice_pitches(Chord(root=5, quality="minor"), (lo, hi), voices=3)
    for p in pitches:
        assert lo <= p <= hi, f"pitch {p} fora do registro [{lo}, {hi}]"


# --- generate_strings: ponta a ponta -----------------------------------------


def test_generate_strings_tutti_narrow_register_does_not_raise():
    """Reproducao ponta a ponta: antes do fix, IndexError vazava do
    `_tutti_pitches` para `generate_strings`."""
    ana = _analysis([Chord(root=5, quality="minor"), Chord(root=5, quality="minor")])
    voices = generate_strings(
        ana, _section(end_bar=2), voices=4, tutti=True, register=(60, 63), seed=0,
    )
    assert len(voices) == 4
    active = [n for v in voices for n in v.notes]
    assert active, "cada voz deveria emitir ao menos uma nota"
    for n in active:
        assert 60 <= n.pitch <= 63


def test_generate_strings_non_tutti_narrow_register_stays_within_register():
    """Nao-tutti tambem respeita o registro no cenario estreito."""
    ana = _analysis([Chord(root=5, quality="minor"), Chord(root=5, quality="minor")])
    voices = generate_strings(
        ana, _section(end_bar=2), voices=3, tutti=False, register=(60, 63), seed=0,
    )
    for v in voices:
        for n in v.notes:
            assert 60 <= n.pitch <= 63


def test_generate_strings_tutti_narrow_register_respects_max_voices_cap():
    """Registro estreito nao invalida o cap de STRINGS_TUTTI_MAX_VOICES."""
    ana = _analysis([Chord(root=5, quality="minor")])
    voices = generate_strings(
        ana, _section(end_bar=1), voices=32, tutti=True, register=(60, 63), seed=0,
    )
    assert len(voices) == STRINGS_TUTTI_MAX_VOICES
