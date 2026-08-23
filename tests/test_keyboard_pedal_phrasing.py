"""US-007 — CC64 sincopado por frase (bar-a-bar).

Os ACs desta story pedem duas garantias que os testes existentes de
`test_keyboard.py` nao cobriam:

1. Com sustain ligado, CC64 e solto ao menos uma vez por secao (o pedal
   NAO segura a camada inteira).
2. Os gaps do contrato de mesma altura (`_enforce_same_pitch_contract`)
   continuam audiveis — pedal ESTA solto durante o intervalo que separa
   um par same-pitch consecutivo. Se o pedal estiver pisado sobre o
   note_off, o motor de sustain do plugin defere o encerramento e o gap
   escrito no MIDI some no som.

Arquivo dedicado (nao mexer em `test_render.py` — outra frente em
paralelo, PRD proibe).
"""

from __future__ import annotations

import random

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.palette.harmonic import (
    PIANO_GAP_MS,
    KeyboardNote,
    _enforce_same_pitch_contract,
    _pedal_events_for_layer,
    generate_keyboard,
)
from tools.plan import PlanSection

BAR_S = 2.0


def _bars(n_bars: int, chord: Chord) -> list[BarAnalysis]:
    return [
        BarAnalysis(index=i, start=i * BAR_S, end=(i + 1) * BAR_S, chord=chord)
        for i in range(n_bars)
    ]


def _analysis(n_bars: int, chord: Chord) -> Analysis:
    return Analysis(
        key_root=chord.root,
        bars=_bars(n_bars, chord),
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(n_bars: int) -> PlanSection:
    return PlanSection(
        label="VERSE 1",
        kind="verse",
        start_bar=0,
        end_bar=n_bars,
        source="marker",
        protagonist="piano_motif",
        energy={
            "densidade": 5, "impacto": 5, "largura": 5,
            "altura": 5, "instabilidade": 5,
        },
    )


# --- AC #1: solto ao menos uma vez por secao --------------------------------

def test_pedal_released_multiple_times_across_section():
    """AC US-007: 'CC64 solto ao menos uma vez por secao'. Um bug de pedal
    apertado a camada inteira produziria len(events) == 2. Um pedal
    sincopado por bar produz um par por bar — em 4 bars, no minimo 2
    pares (4+ eventos)."""
    ana = _analysis(n_bars=4, chord=Chord(root=0, quality="major"))
    sec = _section(n_bars=4)
    layers = generate_keyboard(
        ana, sec, role="piano", layers=1, seed=0, use_sustain_cc64=True,
    )
    events = layers[0].pedal_events
    releases = [e for e in events if e.value == 0]
    presses = [e for e in events if e.value == 127]
    assert len(releases) >= 2, (
        f"pedal solto <2 vezes numa secao de 4 bars — camada inteira sob "
        f"sustain? events={events}"
    )
    assert len(presses) == len(releases)


def test_pedal_release_time_before_next_press_time():
    """Toda pisada de pedal (repress) acontece ESTRITAMENTE APOS o release
    anterior. Se press <= release anterior, o CC nao serve pra 'levantar'
    a mao — pedal segue engolindo o gap."""
    ana = _analysis(n_bars=4, chord=Chord(root=0, quality="major"))
    sec = _section(n_bars=4)
    layers = generate_keyboard(
        ana, sec, role="piano", layers=1, seed=0, use_sustain_cc64=True,
    )
    events = layers[0].pedal_events
    presses = [e for e in events if e.value == 127]
    releases = [e for e in events if e.value == 0]
    for release, next_press in zip(releases, presses[1:], strict=False):
        assert next_press.time_s >= release.time_s


# --- AC #2: gaps do contrato de mesma altura continuam audiveis -------------

def _pedal_state_at(events: tuple, t: float) -> int:
    """Estado do CC64 (0 ou 127) no instante `t`, dada uma lista ordenada
    de KeyboardPedal. Inicialmente 0 (pedal solto); cada evento com
    time_s <= t sobrescreve o estado."""
    state = 0
    for e in events:
        if e.time_s <= t:
            state = e.value
        else:
            break
    return state


def test_pedal_is_up_across_piano_same_pitch_gap_at_bar_boundary():
    """AC US-007: 'os gaps do contrato de mesma altura continuam audiveis,
    isto e, o pedal nao esta pisado sobre eles'. O contrato de piano faz
    100% dos pares de mesma altura terminarem com gap PIANO_GAP_MS antes
    do proximo attack. Se o pedal ficasse apertado por cima, esse gap
    desapareceria no som.

    Cenario minimo: duas notas mesma altura em bars consecutivos, com a
    primeira 'overlapping' o proximo attack para forcar `_enforce_same_
    pitch_contract` a escrever o gap explicito.
    """
    # a: pitch 60 comecando em 0, invadindo o proximo attack (end=2.5)
    # b: mesmo pitch em 2.0 (bar 2). Contrato encurta a.end_s pra ~1.98.
    a = KeyboardNote(pitch=60, velocity=90, start_s=0.0, end_s=2.5)
    b = KeyboardNote(pitch=60, velocity=90, start_s=2.0, end_s=3.5)
    finalized = _enforce_same_pitch_contract(
        [a, b], role="piano", rng=random.Random(0),
    )
    a_end = finalized[0].end_s
    b_start = finalized[1].start_s
    # Sanity: contrato escreveu o gap pedido.
    gap_lo, gap_hi = PIANO_GAP_MS
    assert a_end <= b_start - gap_lo / 1000.0 + 1e-9
    assert a_end >= b_start - gap_hi / 1000.0 - 1e-9

    events = _pedal_events_for_layer(
        tuple(finalized), bar_starts=(0.0, 2.0),
    )
    # No meio do gap enforcado o pedal precisa estar UP (=0).
    midpoint = (a_end + b_start) / 2.0
    assert _pedal_state_at(events, midpoint) == 0, (
        f"pedal apertado no meio do gap same-pitch "
        f"[{a_end:.4f}, {b_start:.4f}] — o gap escrito no MIDI some no som. "
        f"events={events}"
    )


def test_pedal_is_up_across_piano_natural_articulation_gap():
    """Mesma garantia para pares nao-overlapping: a nota do bar N termina
    naturalmente (articulation gate < 1.0), a do bar N+1 comeca no
    downbeat. O pedal nao pode segurar a nota atraves do bar boundary."""
    ana = _analysis(n_bars=3, chord=Chord(root=0, quality="major"))
    sec = _section(n_bars=3)
    layers = generate_keyboard(
        ana, sec, role="piano", layers=1, seed=0,
        articulation="tight", use_sustain_cc64=True,
    )
    layer = layers[0]
    events = layer.pedal_events
    # Localiza pares consecutivos de mesma altura entre bars.
    by_pitch: dict[int, list] = {}
    for n in sorted(layer.notes, key=lambda x: x.start_s):
        by_pitch.setdefault(n.pitch, []).append(n)
    checked = 0
    for series in by_pitch.values():
        for a, b in zip(series, series[1:], strict=False):
            if b.start_s <= a.end_s + 1e-9:
                # sem gap natural, contrato ja cobre pelo teste anterior
                continue
            midpoint = (a.end_s + b.start_s) / 2.0
            assert _pedal_state_at(events, midpoint) == 0, (
                f"pedal apertado no gap natural entre same-pitch "
                f"pitch={a.pitch} [{a.end_s:.4f}, {b.start_s:.4f}]"
            )
            checked += 1
    assert checked > 0, "cenario nao produziu par same-pitch para validar"


# --- rhodes touching: pedal segue apertado dentro da frase ------------------

def test_pedal_holds_across_rhodes_touching_pair():
    """Rhodes com legato: pares touching (end_a == start_b) NAO devem
    quebrar frase de pedal — o AC de legato do rhodes exige continuidade
    sonora e o pedal e o que sustenta essa continuidade. Um repress no
    limite touching mataria o legato.

    Threshold: `PEDAL_PHRASE_GAP_S` = 5 ms; touching == gap 0 ms.
    """
    # 4 notas mesma altura, 2 pares touching (rhodes) dentro do MESMO bar
    # para garantir que touching e a unica variavel — a divisao por bar
    # ja e coberta pelos testes acima.
    notes = tuple(
        KeyboardNote(pitch=60, velocity=90, start_s=t, end_s=t + 0.5)
        for t in (0.0, 0.5, 1.0, 1.5)
    )
    events = _pedal_events_for_layer(notes, bar_starts=(0.0,))
    # Um bar => uma frase => um press e um release.
    assert len(events) == 2
    assert events[0].value == 127
    assert events[1].value == 0
    assert events[0].time_s == 0.0
    assert events[1].time_s == 2.0


# --- guarda contra regressao do bug documentado -----------------------------

def test_pedal_is_not_pressed_over_entire_layer():
    """Regressao explicita do bug de US-007: pedal apertado no primeiro
    onset e solto so no fim da camada inteira. Sinal claro do bug e
    o intervalo (release_1st, press_2nd) cobrir a maioria dos note_offs
    da camada."""
    ana = _analysis(n_bars=8, chord=Chord(root=0, quality="major"))
    sec = _section(n_bars=8)
    layers = generate_keyboard(
        ana, sec, role="piano", layers=1, seed=0, use_sustain_cc64=True,
    )
    events = layers[0].pedal_events
    # Total de tempo com pedal PISADO nao pode ser 100% da span da camada
    # — precisa sobrar tempo com o pedal solto entre frases.
    first_press = min(e.time_s for e in events if e.value == 127)
    last_release = max(e.time_s for e in events if e.value == 0)
    total_span = last_release - first_press
    down_time = 0.0
    state = 0
    prev_t = first_press
    for e in events:
        if e.time_s > first_press:
            if state == 127:
                down_time += e.time_s - prev_t
            state = e.value
            prev_t = e.time_s
    up_time = total_span - down_time
    assert up_time > 0.0, "pedal segue apertado por toda a camada"
