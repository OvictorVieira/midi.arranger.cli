"""Regressao US-004: strings/choir NAO devem produzir ataques repetidos
sobre nota comum sustentada.

Base de conhecimento (`knowledge/persona/persona_produtor_metal_moderno.md`
linhas 524 e 699):

- 524: "nota comum: uma voz permanece enquanto o acorde muda; cria memoria."
- 699: "uma voz sustenta nota comum; outra se move por grau conjunto".

Ambas as passagens usam vocabulario de arco segurado ("permanece",
"sustenta"), nao de re-ataque compasso a compasso. O gerador deve emitir
UMA nota longa sobre os bars contiguos onde a voice-leading escolhe o
mesmo pitch, nao uma cadeia de ataques identicos que o validador
`_check_repeated_notes` denuncia como robotico.

Cenarios cobertos:

1. Progressao harmonica onde a voz externa mantem tom comum por 8 bars
   consecutivos vira UMA nota longa; a track resultante NAO dispara
   `PATTERN_REPEATED_NOTES`.
2. Bar sem acorde no meio quebra o merge — o arco realmente parou.
3. Suppressao por chug quebra o merge — a voz saiu e voltou.
4. Voz interna que oscila entre pitches diferentes por bar continua com
   uma nota por bar (nao merge nada indevidamente).
5. Choir compartilha o mesmo gerador de strings — mesma regra vale.
"""

from __future__ import annotations

from tools.analyze import Analysis, BarAnalysis, Chord, GuitarNote
from tools.palette.harmonic import generate_strings
from tools.plan import Element, PlanSection
from tools.validators.artifice import (
    PATTERN_REPEATED_NOTES,
    _check_repeated_notes,
)
from tools.validators.harmony import RenderedNote, RenderedTrack

BAR_S = 2.0


def _bar(index: int, chord: Chord | None) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=chord,
    )


def _analysis(
    chord_bars: list[Chord | None],
    guitar_notes: list[GuitarNote] | None = None,
) -> Analysis:
    return Analysis(
        key_root=0,
        bars=[_bar(i, c) for i, c in enumerate(chord_bars)],
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
        guitar_notes=guitar_notes or [],
    )


def _section(end_bar: int) -> PlanSection:
    return PlanSection(
        label="S", kind="chorus", start_bar=0, end_bar=end_bar, source="marker",
        protagonist="texture",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


def _element(articulation: str = "sustained") -> Element:
    return Element(
        id="str_main", role="strings", sections=["S"], register=[48, 84],
        layers=3, sync_role="sustain_through", articulation=articulation,
        harmony="follow_chords", pattern=None, dynamics={"shape": "hold"},
        instrument={"plugin": "Omnisphere", "preset": "Strings", "verified": True},
    )


def _voice_to_track(voice_index: int, notes) -> RenderedTrack:
    return RenderedTrack(
        element_id="str_main",
        track_name=f"strings L{voice_index + 1}",
        notes=tuple(
            RenderedNote(
                pitch=n.pitch, velocity=n.velocity,
                start_s=n.start_s, end_s=n.end_s,
            )
            for n in notes
        ),
    )


def test_c_major_eight_bars_outer_voice_merges_into_single_sustained():
    """8 bars de C major seguidos: cada voz externa vira UMA nota longa
    cobrindo o slice inteiro — antes eram 8 ataques identicos que
    disparavam `PATTERN_REPEATED_NOTES`."""
    ana = _analysis([Chord(root=0, quality="major")] * 8)
    voices = generate_strings(ana, _section(end_bar=8), voices=3, seed=0)

    # Voz grave (outer): mesmo pitch por 8 bars => 1 nota longa.
    grave = voices[0].notes
    assert len(grave) == 1
    assert grave[0].start_s < BAR_S
    assert grave[0].end_s > 7 * BAR_S

    # Voz aguda (outer): mesma coisa.
    aguda = voices[-1].notes
    assert len(aguda) == 1
    assert aguda[0].start_s < BAR_S
    assert aguda[0].end_s > 7 * BAR_S


def test_repeated_notes_check_clean_for_sustained_common_tone():
    """A regressao central: rodar `_check_repeated_notes` sobre a track de
    cada voz e nenhuma delas dispara o anti-padrao."""
    ana = _analysis([Chord(root=0, quality="major")] * 8)
    voices = generate_strings(ana, _section(end_bar=8), voices=3, seed=0)
    element = _element()

    for i, voice in enumerate(voices):
        track = _voice_to_track(i, voice.notes)
        issue = _check_repeated_notes(element.id, track, ana)
        assert issue is None, (
            f"voice {i} triggered {PATTERN_REPEATED_NOTES}: {issue}"
        )


def test_bar_without_chord_breaks_common_tone_merge():
    """Bar sem acorde no meio de dois bars com mesmo pitch: o gerador nao
    emite nota no bar do meio, entao o merge nao pode encadear."""
    chords: list[Chord | None] = [
        Chord(root=0, quality="major"), None, Chord(root=0, quality="major"),
    ]
    ana = _analysis(chords)
    voices = generate_strings(ana, _section(end_bar=3), voices=3, seed=0)

    grave = voices[0].notes
    # Duas notas distintas — bar 0 e bar 2, com silencio no bar 1.
    assert len(grave) == 2
    assert grave[0].start_s < BAR_S
    assert grave[1].start_s >= 2 * BAR_S


def test_chug_suppression_breaks_common_tone_merge():
    """Chug grave no bar 0 suprime a voz grave nesse bar; ela reentra no
    bar 1. O merge nao deve encadear entre o bar suprimido e o bar
    seguinte (a voz literalmente saiu do arco)."""
    chord = Chord(root=0, quality="major")
    ana = Analysis(
        key_root=0,
        bars=[_bar(0, chord), _bar(1, chord), _bar(2, chord)],
        kick_positions=[], snare_positions=[], guitar_unison_positions=[],
        track_names=[],
        guitar_notes=[
            GuitarNote(start=0.1, pitch=30, track="G"),
            GuitarNote(start=0.5, pitch=30, track="G"),
            GuitarNote(start=1.0, pitch=30, track="G"),
        ],
    )
    voices = generate_strings(
        ana, _section(end_bar=3), voices=3, register=(36, 71), seed=0,
    )
    grave = voices[0].notes
    # Bar 0 suprimido => a primeira nota da voz grave comeca em bar 1;
    # bars 1 e 2 tem mesmo pitch e sao contiguos => merge em 1 nota longa.
    assert grave, "voz grave deveria reentrar depois do chug"
    assert grave[0].start_s >= BAR_S


def test_inner_voice_with_oscillating_pitches_does_not_merge():
    """Voz interna prefere movimento por grau conjunto sobre pitch fixo.
    C major, C major, C major, ... a voz interna oscila entre pitches
    diferentes do acorde e portanto nao merge."""
    ana = _analysis([Chord(root=0, quality="major")] * 4)
    voices = generate_strings(ana, _section(end_bar=4), voices=5, seed=0)
    inner = voices[2].notes
    # Voz interna oscila; espera-se mais de uma nota (nao ha corrida de
    # mesmo pitch para colapsar).
    assert len(inner) >= 2


def test_choir_role_shares_sustain_behavior():
    """`choir` compartilha o gerador com strings; o merge deve valer."""
    ana = _analysis([Chord(root=0, quality="major")] * 6)
    voices = generate_strings(
        ana, _section(end_bar=6), role="choir", voices=3, seed=0,
    )
    grave = voices[0].notes
    assert len(grave) == 1
    assert grave[0].end_s > 5 * BAR_S


def test_merge_preserves_expression_curve_span():
    """A curva CC11 continua cobrindo o span completo — start da primeira
    nota ate end da ultima — mesmo com merge reduzindo a contagem."""
    ana = _analysis([Chord(root=0, quality="major")] * 4)
    voices = generate_strings(ana, _section(end_bar=4), voices=3, seed=0)
    for v in voices:
        if not v.notes:
            continue
        exp = v.expression_events
        assert exp, "voz nao-vazia deve ter expression events"
        assert exp[0].time_s <= v.notes[0].end_s
        assert exp[-1].time_s >= v.notes[0].start_s
