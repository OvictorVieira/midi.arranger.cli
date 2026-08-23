"""Elementos harmonicos — pad, piano, Rhodes (US-014, US-003).

Strings e drone virao em rodadas seguintes.

Pad em uma frase: notas sustentadas seguindo os acordes detectados pela
analise, com voicing sem fundamental no grave (a fundamental sobe para o
topo, uma oitava acima do voicing natural), ataques desencontrados ENTRE
camadas (nao entre notas do mesmo voicing), release alinhado a limite de
compasso e forma dinamica configuravel.

Piano e Rhodes em uma frase: acorde por bar seguindo os acordes
detectados, voicing calculado por `VoicingEngine` da rodada 1 (spread
deltas + espalhamento de ataque entre notas do acorde), timing +-5-15ms
por onset, release controlado por articulacao e diferenciados por
contrato de pares de mesma altura — piano sempre com gap, Rhodes com
12-47% dos pares em overlap ou touching. Sustain CC64 opcional, nunca
default.

O gerador nao renderiza MIDI diretamente — devolve `PadLayer`s ou
`KeyboardLayer`s que o renderer transforma em tracks. Isso mantem o motor
puro e testavel sem depender de escrita em disco.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from ..analyze import Analysis, BarAnalysis, Chord
from ..constants import GATE_RATIOS, REGISTER_BANDS, VELOCITY_RANGES
from ..humanize import DurationEngine, DurationRequest
from ..plan import ArrangementPlan, PlanSection
from ..voicing import (
    C2_PITCH,
    STRINGS_GHOST_MAX_ABS_VELOCITY,
    VoicingEngine,
    VoicingRequest,
    _apply_register_constraint,
)

# --- constantes -------------------------------------------------------------

DEFAULT_PAD_REGISTER: tuple[int, int] = REGISTER_BANDS["mid"]
"""Registro default do pad: banda mid (C3-C5). Configuravel por elemento."""

PAD_LAYER_ONSET_STAGGER_MS: tuple[float, float] = (8.0, 25.0)
"""Desencontro de ataque entre camadas de pad. Cada camada > 0 acumula um
sorteio nesta faixa por camada anterior — camada 2 entra ~10-25ms depois
da 1, camada 3 mais uns 10-25ms depois da 2, e por ai vai. Dentro do
voicing (mesma camada) o ataque e simultaneo por regra do voicing engine.
"""

PAD_BASE_VELOCITY_BUCKET: str = "tied_soft"
"""Bucket de VELOCITY_RANGES para a base do pad. tied_soft = (50, 75) —
pad sustentado suave, nunca dominante."""

FADE_IN_FLOOR: int = VELOCITY_RANGES["ghost"][0]
"""Piso do fade-in — pad comeca no chao (ghost) e sobe ate a base."""

OPEN_AT_CHORUS_BONUS: int = 8
"""Ganho de velocity quando shape='open_at_chorus' e a secao e refrao."""

CHORUS_KIND: str = "chorus"
"""Vocabulario fechado do plan.SECTION_KINDS."""


# --- schema de saida --------------------------------------------------------

@dataclass(frozen=True)
class PadNote:
    """Uma nota de pad. `end_s` alinhado a limite de compasso por definicao
    do gerador — o pad sustenta por bar inteiro."""
    pitch: int
    velocity: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class PadLayer:
    """Uma camada (track) de pad. `layers=N` no plano gera N camadas em
    unisono; cada camada e uma track independente no MIDI final."""
    index: int
    notes: tuple[PadNote, ...]


# --- helpers de voicing -----------------------------------------------------

def _pad_base_velocity() -> int:
    lo, hi = VELOCITY_RANGES[PAD_BASE_VELOCITY_BUCKET]
    return (lo + hi) // 2


def _chord_degrees(chord: Chord) -> list[int]:
    """Semitons acima da raiz para o corpo do acorde."""
    if chord.quality == "major":
        return [0, 4, 7]
    if chord.quality == "minor":
        return [0, 3, 7]
    # power e unknown: sem terca. Mais seguro que assumir maior ou menor.
    return [0, 7]


def _voice_pad_chord(chord: Chord, register: tuple[int, int]) -> list[int]:
    """Constroi voicing de pad para o acorde dentro de `register`.

    Regra da base de conhecimento: pad SEM fundamental no grave. Duas
    formas equivalentes: (a) omitir a raiz; (b) mover a raiz uma oitava
    acima. Este gerador usa (b) — a raiz vira o topo do voicing e o corpo
    (terca/quinta) fica logo abaixo.

    Se todo o voicing sair abaixo de C2, a restricao de registro do
    `voicing` reduz a nota unica ou quinta — pad denso no sub vira lama.
    """
    lo_reg, hi_reg = register
    root_pc = chord.root
    degs = _chord_degrees(chord)

    # Menor pitch >= lo_reg com pitch-class == root.
    root_base = lo_reg + ((root_pc - lo_reg) % 12)
    if root_base < lo_reg:
        root_base += 12

    non_root = [d for d in degs if d != 0]
    body = sorted(root_base + d for d in non_root)
    # Fundamental uma oitava acima do corpo (topo do voicing).
    top = root_base + 12
    pitches = sorted([*body, top])

    # Se o topo estourou o registro, transpoe TUDO para baixo em oitavas
    # ate caber — desde que a base continue dentro do registro.
    while pitches[-1] > hi_reg and pitches[0] - 12 >= lo_reg:
        pitches = [p - 12 for p in pitches]

    # Restricao de registro sub (abaixo de C2): reduz a nota unica ou quinta.
    return _apply_register_constraint(pitches)


# --- helpers de secao -------------------------------------------------------

def _bars_in_section(section: PlanSection, analysis: Analysis) -> list[BarAnalysis]:
    return [
        b for b in analysis.bars
        if section.start_bar <= b.index < section.end_bar
    ]


# --- forma dinamica ---------------------------------------------------------

def _parse_fade_in_bars(entry: object) -> int:
    """Devolve N de 'fade_in_Nbars', ou 0 se o entry nao for reconhecido."""
    if not isinstance(entry, str):
        return 0
    prefix, suffix = "fade_in_", "bars"
    if not entry.startswith(prefix) or not entry.endswith(suffix):
        return 0
    try:
        n = int(entry[len(prefix):-len(suffix)])
    except ValueError:
        return 0
    return max(0, n)


def _velocity_for_bar(
    bar_index_in_section: int,
    dynamics: dict,
    section_kind: str,
    base: int,
) -> int:
    """Velocity do pad para uma nota comecando na barra bar_index_in_section
    (0-based dentro da secao).

    Suporta forma dinamica:
      - entry='fade_in_Nbars': ramp linear de FADE_IN_FLOOR ate base nos
        primeiros N bars; a partir do bar N a velocity fica no base.
      - shape='hold': velocity constante em base.
      - shape='open_at_chorus': base + OPEN_AT_CHORUS_BONUS quando a secao
        for do kind 'chorus'; caso contrario comporta-se como hold.
    """
    entry = dynamics.get("entry")
    shape = dynamics.get("shape", "hold")

    v = base

    fade_bars = _parse_fade_in_bars(entry)
    if fade_bars > 0 and bar_index_in_section < fade_bars:
        # Ramp linear: bar 0 -> 1/N do caminho, bar N-1 -> N/N (base).
        frac = (bar_index_in_section + 1) / fade_bars
        v = round(FADE_IN_FLOOR + (base - FADE_IN_FLOOR) * frac)

    if shape == "open_at_chorus" and section_kind == CHORUS_KIND:
        v += OPEN_AT_CHORUS_BONUS

    return max(1, min(127, v))


# --- gerador ----------------------------------------------------------------

def generate_pad(
    analysis: Analysis,
    section: PlanSection,
    *,
    register: tuple[int, int] = DEFAULT_PAD_REGISTER,
    layers: int = 1,
    dynamics: dict | None = None,
    seed: int = 0,
) -> list[PadLayer]:
    """Gera as camadas de pad para uma secao.

    Cada camada e uma track em unisono; o desencontro de ataque entre elas
    e o unico ruido temporal aplicado ao pad. Notas duram um bar inteiro
    (release em cada bar boundary), o que da controle de dinamica bar-a-bar
    para fade_in e mantem a intencao de 'sustained' do pad.

    Args:
      analysis: saida de `midiarranger.analysis.analyze(midi_path)`.
      section: secao do plano onde o pad vai tocar.
      register: par (low, high) MIDI dentro do qual o voicing deve caber.
        Default: banda mid (C3-C5). Passar (0, 35) forca sub e a restricao
        de registro reduz o voicing.
      layers: quantas camadas em unisono. Cada layer e uma track do MIDI
        final. FR-23: sempre explicito no plano.
      dynamics: dict opcional com 'entry' e 'shape'. Vazio equivale a
        {'shape': 'hold'}.
      seed: seed do RNG para reprodutibilidade do stagger entre camadas.

    Raises:
      ValueError: se layers < 1.
    """
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")

    dyn = dynamics or {}
    rng = random.Random(seed)

    bars = _bars_in_section(section, analysis)
    if not bars:
        return [PadLayer(index=i, notes=()) for i in range(layers)]

    base = _pad_base_velocity()

    # Pre-calcula o offset (segundos) de cada camada. Camada 0 no onset;
    # cada camada seguinte acumula um sorteio na faixa PAD_LAYER_ONSET_STAGGER_MS.
    layer_offsets_s: list[float] = [0.0]
    for _ in range(1, layers):
        step_ms = rng.uniform(*PAD_LAYER_ONSET_STAGGER_MS)
        layer_offsets_s.append(layer_offsets_s[-1] + step_ms / 1000.0)

    result: list[PadLayer] = []
    for layer_idx in range(layers):
        stagger_s = layer_offsets_s[layer_idx]
        notes: list[PadNote] = []
        for bar_pos, bar in enumerate(bars):
            if bar.chord is None:
                continue
            v = _velocity_for_bar(bar_pos, dyn, section.kind, base)
            pitches = _voice_pad_chord(bar.chord, register)
            start_s = bar.start + stagger_s
            end_s = bar.end
            if end_s <= start_s:
                # Camada muito atrasada em bar curtissimo — nao emite nota
                # em vez de emitir nota invertida.
                continue
            for p in pitches:
                notes.append(PadNote(
                    pitch=p,
                    velocity=v,
                    start_s=start_s,
                    end_s=end_s,
                ))
        result.append(PadLayer(index=layer_idx, notes=tuple(notes)))

    return result


# --- keyboard (piano / rhodes) ---------------------------------------------

DEFAULT_KEYBOARD_REGISTER: tuple[int, int] = REGISTER_BANDS["mid"]
"""Registro default do piano/Rhodes: banda mid (C3-C5)."""

KEYBOARD_BASE_VELOCITY_BUCKET: str = "normal"
"""Bucket de VELOCITY_RANGES para a base do teclado. normal = (82, 105) —
teclado canta acima do pad, abaixo de acentos de guitarra/lead."""

KEYBOARD_TIMING_JITTER_MS: tuple[float, float] = (5.0, 15.0)
"""Jitter aplicado ao onset do acorde a cada bar (spec: 'Timing +-5-15ms').
Aplicado como atraso positivo em relacao ao downbeat — teclado atrasado do
grid soa humano, adiantado soa nervoso."""

KEYBOARD_LAYER_ONSET_STAGGER_MS: tuple[float, float] = (8.0, 25.0)
"""Desencontro de ataque entre camadas de teclado, na mesma escala do pad —
duas maos/dois instrumentos em unisono nunca atacam no mesmo tick."""

RHODES_LEGATO_RATIO_RANGE: tuple[float, float] = (0.12, 0.47)
"""AC: 'modo Rhodes: 12-47% dos pares de mesma altura com overlap ou
touching'. Sorteio uniforme na faixa; a contagem final e arredondada e
travada dentro de [12%, 47%] via `_legato_pair_count` para o teste
determinstico nunca escapar do intervalo."""

RHODES_OVERLAP_MS: tuple[float, float] = (5.0, 25.0)
"""LEGATO_OVERLAP_MS espelhado da rodada 1 (constants.LEGATO_OVERLAP_MS).
Duplicado como constante local para nao amarrar o comportamento de
teclado a uma constante compartilhada entre motores — o Rhodes pode
divergir dessa faixa em rodadas futuras sem quebrar guitar/bass."""

PIANO_GAP_MS: tuple[float, float] = (10.0, 40.0)
"""AC: 'modo piano e 100% gapped (release limpo)'. Release cai pelo menos
10 ms antes do proximo onset da mesma altura. Faixa larga o suficiente
para o ouvido perceber a articulacao sem cortar a fundamental."""

DEFAULT_KEYBOARD_ARTICULATION: str = "tight"
"""Gate default quando o elemento nao declara articulacao suportada aqui —
tight (0.60-0.82) mantem release claro sem sufocar a nota. Rhodes tipico
usa 'open' (0.78-0.95) para deixar as caudas se tocarem."""

KEYBOARD_ROLES: tuple[str, ...] = ("piano", "rhodes")
"""Roles atendidos por `generate_keyboard`. Voicing engine trata os dois
como KEYBOARD_SPREAD_ROLES — mesmo espalhamento de ataque; a diferenca
esta no pos-processamento de pares de mesma altura."""

MIN_NOTE_DURATION_S: float = 0.001
"""Piso absoluto de duracao de nota — 1 ms garante que note_off cai depois
de note_on mesmo apos jitter e gate agressivos, sem virar nota-fantasma."""


@dataclass(frozen=True)
class KeyboardNote:
    """Nota individual de teclado. Diferente de `PadNote` no proposito —
    piano/Rhodes tem release controlado por articulacao e por contrato de
    par de mesma altura, nao pelo bar boundary."""
    pitch: int
    velocity: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class KeyboardPedal:
    """Evento de pedal (CC64 sustain).

    - `time_s`: instante do evento em segundos absolutos.
    - `value`: 0-127, MIDI padrao. 127 = pedal apertado (sustain on), 0 =
      solto. Nao expomos limiar 64: alguns plugins interpretam 63 como off
      e 64 como on, outros usam 127/0 puro; ficar nos extremos evita
      surpresa entre plugins.
    """
    time_s: float
    value: int


@dataclass(frozen=True)
class KeyboardLayer:
    """Uma camada (track) de teclado. Cada camada e uma track independente
    no MIDI final — mesmo padrao do pad. `pedal_events` vazio quando o
    elemento nao pede sustain."""
    index: int
    notes: tuple[KeyboardNote, ...]
    pedal_events: tuple[KeyboardPedal, ...] = ()


def _keyboard_base_velocity() -> int:
    lo, hi = VELOCITY_RANGES[KEYBOARD_BASE_VELOCITY_BUCKET]
    return (lo + hi) // 2


def _keyboard_gate(articulation: str) -> float:
    """Fator de gate (0-1) para articulacao. Cai para tight quando o
    vocabulario da rodada 1 nao tem a articulacao mapeada — 'let_ring',
    por exemplo, nao esta em GATE_RATIOS e vira 'tight'."""
    ratios = GATE_RATIOS.get(articulation)
    if ratios is None:
        ratios = GATE_RATIOS[DEFAULT_KEYBOARD_ARTICULATION]
    lo, hi = ratios
    return (lo + hi) / 2.0


def _voice_keyboard_chord(chord: Chord, register: tuple[int, int]) -> list[int]:
    """Voicing de teclado: raiz na base, corpo (terca + quinta) acima.

    Sem oitava dobrada no topo (esse e o padrao do pad, nao do teclado —
    piano da mao esquerda tocando root+fifth+octave-root soa a preset).
    root_base fica sempre no primeiro octavo dentro do registro, entao o
    voicing pode ultrapassar `hi` quando o registro tem menos de 8 semitons
    de folga — assumimos que o plano declara register largo o suficiente
    para o corpo do acorde caber; validador de colisao pega o resto.
    """
    lo, _hi = register
    root_base = lo + ((chord.root - lo) % 12)
    pitches = sorted(root_base + d for d in _chord_degrees(chord))
    return _apply_register_constraint(pitches)


def _legato_pair_count(n_pairs: int, ratio: float) -> int:
    """Numero de pares que viram legato para uma dada `ratio` sorteada.

    Trava o resultado dentro de [floor(0.12*N), floor(0.47*N)] para nao
    escapar do AC por conta de arredondamento — quando `ratio` cai perto
    da borda, o `round` classico pode devolver um valor 1 fora da faixa
    permitida.
    """
    if n_pairs <= 0:
        return 0
    n_min = max(1, int(RHODES_LEGATO_RATIO_RANGE[0] * n_pairs))
    n_max = max(n_min, int(RHODES_LEGATO_RATIO_RANGE[1] * n_pairs))
    n_target = int(round(ratio * n_pairs))
    return max(n_min, min(n_target, n_max))


def _enforce_same_pitch_contract(
    notes: list[KeyboardNote],
    *,
    role: str,
    rng: random.Random,
) -> list[KeyboardNote]:
    """Piano: 100% dos pares de mesma altura com gap. Rhodes: 12-47% com
    overlap ou touching, o restante com gap.

    Par = duas notas consecutivas no tempo com o mesmo pitch (independente
    de bar ou secao). Notas nao pareadas (unica ocorrencia do pitch) nao
    sao tocadas.
    """
    if not notes:
        return notes

    by_pitch: dict[int, list[int]] = {}
    for idx, n in enumerate(notes):
        by_pitch.setdefault(n.pitch, []).append(idx)
    for idxs in by_pitch.values():
        idxs.sort(key=lambda i: notes[i].start_s)

    pairs: list[tuple[int, int]] = []
    for idxs in by_pitch.values():
        for a, b in zip(idxs, idxs[1:], strict=False):
            pairs.append((a, b))

    if role == "rhodes" and pairs:
        ratio = rng.uniform(*RHODES_LEGATO_RATIO_RANGE)
        n_legato = _legato_pair_count(len(pairs), ratio)
        legato_indices = set(rng.sample(range(len(pairs)), n_legato))
    else:
        legato_indices = set()

    out = list(notes)
    for pair_idx, (a, b) in enumerate(pairs):
        start_b = out[b].start_s
        if pair_idx in legato_indices:
            # start_b + overlap_s > start_b, e overlap_s > 0 sempre — se a nota
            # ja invade o proximo onset, esticamos ate `start_b + overlap`;
            # se estava com gap, o mesmo new_end garante touching-com-overlap.
            new_end = start_b + rng.uniform(*RHODES_OVERLAP_MS) / 1000.0
            if new_end > out[a].end_s:
                out[a] = replace(out[a], end_s=new_end)
            continue
        # Gap obrigatorio (piano OU rhodes fora da cota de legato).
        if out[a].end_s >= start_b:
            gap_s = rng.uniform(*PIANO_GAP_MS) / 1000.0
            new_end = start_b - gap_s
            floor = out[a].start_s + MIN_NOTE_DURATION_S
            if new_end < floor:
                new_end = floor
            out[a] = replace(out[a], end_s=new_end)
    return out


def _pedal_events_for_layer(
    notes: tuple[KeyboardNote, ...],
) -> tuple[KeyboardPedal, ...]:
    """CC64 apertado no primeiro onset da camada, solto apos a ultima nota.

    Uma unica ativacao por camada casa com o AC ('referencia usa CC64 uma
    unica vez'): quem quiser sustain multi-secao rica edita o plano para
    outra camada com use_sustain_cc64=True em outra faixa de secoes.
    """
    if not notes:
        return ()
    first = min(n.start_s for n in notes)
    last = max(n.end_s for n in notes)
    return (
        KeyboardPedal(time_s=first, value=127),
        KeyboardPedal(time_s=last, value=0),
    )


def generate_keyboard(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "piano",
    register: tuple[int, int] = DEFAULT_KEYBOARD_REGISTER,
    layers: int = 1,
    articulation: str = DEFAULT_KEYBOARD_ARTICULATION,
    dynamics: dict | None = None,
    use_sustain_cc64: bool = False,
    seed: int = 0,
) -> list[KeyboardLayer]:
    """Gera camadas de teclado (piano ou Rhodes) para uma secao.

    Uma nota por acorde por bar por voz do voicing. Voicing engine da
    rodada 1 aplica velocity delta por spread e espalhamento de ataque
    entre as notas do acorde. Timing jitter aplica-se ao onset do acorde
    inteiro; o release depende da articulacao + contrato de par de mesma
    altura (piano gapped, Rhodes 12-47% legato).

    Args:
      analysis: saida de `midiarranger.analysis.analyze(midi_path)`.
      section: secao do plano onde o elemento toca.
      role: 'piano' ou 'rhodes'. Diferencas: pos-processamento de pares.
      register: par (low, high) MIDI para o voicing. Default: banda mid.
      layers: camadas em unisono, cada uma vira uma track. FR-23 explicito.
      articulation: rotulo de ARTICULATIONS. Mapeado para gate via GATE_RATIOS;
        articulacoes fora do mapa (ex. let_ring) caem para o gate de tight.
      dynamics: reaproveita 'entry' e 'shape' do pad — fade_in_Nbars,
        open_at_chorus, hold.
      use_sustain_cc64: quando True, emite CC64 127 no primeiro onset e
        CC64 0 apos a ultima nota. Default False — spec 'usa CC64 uma
        unica vez, nao e default'.
      seed: seed determinstica.

    Raises:
      ValueError: se layers < 1 ou role fora de KEYBOARD_ROLES.
    """
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in KEYBOARD_ROLES:
        raise ValueError(f"role must be one of {list(KEYBOARD_ROLES)}; got {role!r}")

    dyn = dynamics or {}
    rng = random.Random(seed)
    bars = _bars_in_section(section, analysis)
    if not bars:
        empty = tuple()
        return [
            KeyboardLayer(index=i, notes=empty, pedal_events=empty)
            for i in range(layers)
        ]

    base = _keyboard_base_velocity()
    # Articulacao normalizada — motor de duracao usa GATE_RATIOS diretamente e
    # exige articulacao do vocabulario (`let_ring` etc caem para tight, igual
    # a `_keyboard_gate`).
    duration_art = (
        articulation if articulation in GATE_RATIOS
        else DEFAULT_KEYBOARD_ARTICULATION
    )

    layer_offsets_s: list[float] = [0.0]
    for _ in range(1, layers):
        step_ms = rng.uniform(*KEYBOARD_LAYER_ONSET_STAGGER_MS)
        layer_offsets_s.append(layer_offsets_s[-1] + step_ms / 1000.0)

    result: list[KeyboardLayer] = []
    for layer_idx in range(layers):
        stagger_s = layer_offsets_s[layer_idx]
        # Engine independente por camada — evita correlacao de onset spread
        # entre camadas quando duas dividem o mesmo bar/acorde.
        engine = VoicingEngine(seed=seed + layer_idx * 1_000_003)
        # Motor de duracao independente por camada; sem isso o `note_dur`
        # ficaria constante e `_check_duration_uniform` certificaria a track
        # como duracao chapada.
        dur_engine = DurationEngine(seed=seed + layer_idx * 1_000_009)

        # Passo 1: por bar, resolve onset base do acorde e pitches.
        bar_chords: list[tuple[float, list[int], int] | None] = []
        for bar_pos, bar in enumerate(bars):
            if bar.chord is None:
                bar_chords.append(None)
                continue
            jitter_s = rng.uniform(*KEYBOARD_TIMING_JITTER_MS) / 1000.0
            onset = bar.start + stagger_s + jitter_s
            if onset >= bar.end:
                # Bar curtissimo ou stagger acumulado grande — pula em vez de
                # emitir onset invertido. Casa com a mesma protecao do pad.
                bar_chords.append(None)
                continue
            pitches = _voice_keyboard_chord(bar.chord, register)
            bar_chords.append((onset, pitches, bar_pos))

        # Passo 2: emite notas com duracao sorteada por voz via motor de
        # duracao — cada voz recebe uma proporcao independente de
        # GATE_RATIOS[articulation] * gap ate o proximo acorde, quebrando a
        # duracao chapada que originava `duration_uniform`.
        raw: list[KeyboardNote] = []
        for i, item in enumerate(bar_chords):
            if item is None:
                continue
            chord_onset, pitches, bar_pos = item
            next_onset: float | None = None
            for j in range(i + 1, len(bar_chords)):
                if bar_chords[j] is not None:
                    next_onset = bar_chords[j][0]
                    break
            boundary = next_onset if next_onset is not None else bars[-1].end
            # window > 0 por construcao: onset < bar.end <= boundary (proximo
            # bar comeca em bar.end, e o guard de linha 562 assegura o resto).
            v_bar = _velocity_for_bar(bar_pos, dyn, section.kind, base)
            voiced = engine.voice(VoicingRequest(pitches=tuple(pitches), role=role))
            for vn in voiced:
                note_onset = chord_onset + vn.onset_offset_ms / 1000.0
                velocity = max(1, min(127, v_bar + vn.velocity_delta))
                gap_s = boundary - note_onset
                gap_ms = gap_s * 1000.0 if gap_s > 0 else None
                dur_ms = dur_engine.compute(
                    DurationRequest(articulation=duration_art, gap_ms=gap_ms),
                )
                dur_s = max(MIN_NOTE_DURATION_S, dur_ms / 1000.0)
                raw.append(KeyboardNote(
                    pitch=vn.pitch,
                    velocity=velocity,
                    start_s=note_onset,
                    end_s=note_onset + dur_s,
                ))

        # Passo 3: contrato de par de mesma altura por role.
        finalized = _enforce_same_pitch_contract(raw, role=role, rng=rng)
        finalized.sort(key=lambda n: (n.start_s, n.pitch))

        pedal: tuple[KeyboardPedal, ...] = ()
        if use_sustain_cc64:
            pedal = _pedal_events_for_layer(tuple(finalized))

        result.append(KeyboardLayer(
            index=layer_idx,
            notes=tuple(finalized),
            pedal_events=pedal,
        ))
    return result


# --- strings / choir --------------------------------------------------------

DEFAULT_STRINGS_REGISTER: tuple[int, int] = REGISTER_BANDS["mid"]
"""Registro default de strings/choir: banda mid. tutti pode subir para
banda mais alta pelo `register` do elemento."""

STRINGS_BASE_VELOCITY_BUCKET: str = "mid"
"""Bucket 'mid' (70-90) — strings sustentam abaixo do piano/lead."""

STRINGS_ROLES: tuple[str, ...] = ("strings", "choir")
"""Roles atendidos por `generate_strings`. Voice-leading + expression CC11
identica; a diferenca de timbre fica no plugin/preset."""

STRINGS_TUTTI_MAX_VOICES: int = 8
"""AC: 'Stack de ate 8 notas em ~40 semitons quando a secao pedir tutti'.
Mesmo se `element.layers` (voices) declara mais, tutti trava em 8."""

STRINGS_TUTTI_SPREAD_ST: int = 40
"""~40 semitons de spread entre voz mais grave e mais aguda no tutti,
distribuidas em pitches do acorde."""

STRINGS_VOICE_ATTACK_JITTER_MS: tuple[float, float] = (10.0, 45.0)
"""Attack diferente por voz. Faixa maior que teclado — strings tem ataque
mais lento e mais variavel. Aplicado como atraso positivo em relacao ao
downbeat, acumulado voz a voz."""

STRINGS_STEP_SEARCH_ST: int = 7
"""Busca de voice-leading: para achar a proxima nota de uma voz, procura
tom do acorde a no maximo 7 semitons (uma quinta) do pitch anterior —
preserva 'movimento por grau conjunto'."""

STRINGS_GHOST_RATIO: float = 0.20
"""AC: 'Vozes internas em ghost (velocity < 40) em ~20% das notas'."""

STRINGS_EXPRESSION_STEPS: int = 4
"""Numero de pontos de CC11 por voz por secao (rampa de expression).
Poucos pontos deixam a rampa suave sem inflar arquivo — DAWs interpolam."""

STRINGS_EXPRESSION_LO: int = 40
STRINGS_EXPRESSION_HI: int = 110
"""Faixa de CC11 usada pelas curvas de expression por voz. Cada voz sorteia
uma fase distinta na senoide para que as curvas se cruzem — o efeito
auditivo pedido pela AC ('curva de expression por voz')."""

STRINGS_LOW_PITCH_CEILING: int = 48
"""'Low strings' = notas abaixo de C3 (< 48). Suprimidas em bar com chug
grave ativo."""

STRINGS_CHUG_PITCH_CEILING: int = 48
"""Nota de guitarra abaixo de C3 conta como 'chug grave'."""

STRINGS_CHUG_MIN_NOTES: int = 2
"""Precisa 2+ notas graves de guitarra no bar para caracterizar chug (uma
nota solta grave nao e chug)."""


@dataclass(frozen=True)
class StringsNote:
    """Nota individual de strings/choir. Voz-alvo do voice-leading;
    `is_ghost=True` marca voz interna do tutti que recebeu velocity ghost."""
    pitch: int
    velocity: int
    start_s: float
    end_s: float
    is_ghost: bool = False


@dataclass(frozen=True)
class StringsExpressionEvent:
    """Evento CC11 (expression) por voz. Emitido em pontos discretos —
    DAW interpola. Valor 0-127."""
    time_s: float
    value: int


@dataclass(frozen=True)
class StringsVoice:
    """Uma voz da linha de strings/choir. Cada voz vira uma track no MIDI
    final — attack e curva CC11 por voz."""
    index: int
    notes: tuple[StringsNote, ...]
    expression_events: tuple[StringsExpressionEvent, ...] = ()


def _strings_base_velocity() -> int:
    lo, hi = VELOCITY_RANGES[STRINGS_BASE_VELOCITY_BUCKET]
    return (lo + hi) // 2


def _strings_ghost_velocity() -> int:
    """Velocity ghost para vozes internas. STRINGS_GHOST_MAX_ABS_VELOCITY
    (=39) e o piso do 'ghost <40'; usamos meio do bucket ghost mas trava
    em <40 para respeitar o AC literal."""
    lo, _hi = VELOCITY_RANGES["ghost"]
    return min(STRINGS_GHOST_MAX_ABS_VELOCITY, (lo + STRINGS_GHOST_MAX_ABS_VELOCITY) // 2)


def _pitch_class_within(pc: int, lo: int, hi: int) -> int:
    """Menor pitch com pitch class `pc` que caiba em [lo, hi]. Se a classe
    nao ocorre dentro do registro, devolve `lo` — fallback rigido que
    preserva determinismo e mantem a nota dentro do registro declarado."""
    base = lo + ((pc - lo) % 12)
    return base if base <= hi else lo


def _tutti_pitches(chord: Chord, register: tuple[int, int]) -> list[int]:
    """Constroi ate STRINGS_TUTTI_MAX_VOICES notas do acorde espalhadas em
    ~STRINGS_TUTTI_SPREAD_ST semitons. Comeca no minimo do registro e
    empilha pitches do acorde (raiz+3a/7a) em oitavas ate cobrir o span.

    Se o registro for estreito demais para caber qualquer grau do acorde
    dentro do span do tutti (ex.: register=(60,63) com raiz F, todos os
    graus caem acima do teto), degrada para uma unica voz na tonica
    snapped ao registro. Decisao: degradar em vez de erro — o path
    nao-tutti (`_initial_voice_pitches`) ja segue essa politica, e o
    plano continua renderizavel. O operador ouve tutti anemico e ajusta
    o register."""
    lo, hi = register
    span = min(STRINGS_TUTTI_SPREAD_ST, hi - lo)
    ceiling = lo + span
    degrees = _chord_degrees(chord)
    root_base = lo + ((chord.root - lo) % 12)
    # root_base >= lo, d >= 0, octave >= 0 => p >= lo por construcao;
    # so o teto (`p > ceiling`) pode filtrar. Quando a iteracao completa
    # nao emite nada, ja passamos do teto — break.
    pitches: list[int] = []
    octave = 0
    while len(pitches) < STRINGS_TUTTI_MAX_VOICES:
        emitted = False
        for d in degrees:
            p = root_base + d + octave * 12
            if p > ceiling:
                continue
            pitches.append(p)
            emitted = True
            if len(pitches) >= STRINGS_TUTTI_MAX_VOICES:
                break
        if not emitted:
            break
        octave += 1
    pitches = sorted(set(pitches))
    if not pitches:
        pitches = [_pitch_class_within(chord.root, lo, hi)]
    return pitches[:STRINGS_TUTTI_MAX_VOICES]


def _initial_voice_pitches(
    chord: Chord, register: tuple[int, int], voices: int,
) -> list[int]:
    """Distribui `voices` pitches iniciais pelo registro, seguindo o acorde.
    Voz 0 = grave, voz N-1 = aguda. Espalha em ordens de oitava conforme
    o registro comporta — evita tudo no mesmo octavo."""
    lo, hi = register
    degrees = _chord_degrees(chord)
    root_base = lo + ((chord.root - lo) % 12)
    # p >= lo por construcao (mesmo argumento de `_tutti_pitches`); o
    # unico corte e `p > hi`.
    candidates: list[int] = []
    octave = 0
    while True:
        added = False
        for d in degrees:
            p = root_base + d + octave * 12
            if p > hi:
                continue
            candidates.append(p)
            added = True
        if not added or len(candidates) >= voices * 3:
            break
        octave += 1
    candidates = sorted(set(candidates))
    if not candidates:
        # Registro estreito demais para caber qualquer grau do acorde:
        # degrada para a tonica snapped em [lo, hi] em vez de devolver
        # `root_base` — que pode cair acima de `hi` e violar o registro.
        return [_pitch_class_within(chord.root, lo, hi)] * voices
    if len(candidates) >= voices:
        # Amostragem uniforme de N pitches distintos.
        step = (len(candidates) - 1) / max(1, voices - 1) if voices > 1 else 0
        return [candidates[int(round(i * step))] for i in range(voices)]
    # Menos candidatos que vozes: repete o mais grave/mais agudo nos extremos.
    filled = list(candidates)
    while len(filled) < voices:
        filled.append(candidates[-1])
    return sorted(filled)


def _next_voice_pitch(
    prev_pitch: int, chord: Chord, register: tuple[int, int],
    inner: bool,
) -> int:
    """Voice-leading: prefere tom comum (mesmo pitch class) mais proximo do
    anterior; se nao houver dentro de STRINGS_STEP_SEARCH_ST, escolhe o tom
    do acorde mais proximo (grau conjunto). `inner=True` (vozes internas)
    da preferencia a movimento por grau conjunto — o AC pede 'outra voz
    move por grau conjunto'."""
    lo, hi = register
    degrees = _chord_degrees(chord)
    root_base = lo + ((chord.root - lo) % 12)
    # Todas as ocorrencias dos degrees dentro do registro.
    candidates: list[int] = []
    octave = -1
    while True:
        p_min = root_base + (octave + 1) * 12
        if p_min > hi + 12:
            break
        for d in degrees:
            p = root_base + d + octave * 12
            if lo <= p <= hi:
                candidates.append(p)
        octave += 1
    if not candidates:
        return prev_pitch
    same_pc = [p for p in candidates if p % 12 == prev_pitch % 12]
    if same_pc and not inner:
        return min(same_pc, key=lambda p: abs(p - prev_pitch))
    # Voz interna: prefere movimento por grau conjunto (mais proxima nao-igual).
    non_same = [p for p in candidates if p != prev_pitch]
    if inner and non_same:
        near = [p for p in non_same if abs(p - prev_pitch) <= STRINGS_STEP_SEARCH_ST]
        pool = near or non_same
        return min(pool, key=lambda p: abs(p - prev_pitch))
    return min(candidates, key=lambda p: abs(p - prev_pitch))


def _expression_curve(
    start_s: float, end_s: float, phase: float,
) -> tuple[StringsExpressionEvent, ...]:
    """Curva CC11 senoidal por voz. `phase` em [0, 1) define offset dentro
    da senoide — vozes distintas devem receber phases distintas para as
    curvas se cruzarem. Chamador so invoca com notas nao-vazias, entao
    start < end por construcao (min start < max end quando notes ja estao
    ordenadas por start)."""
    events: list[StringsExpressionEvent] = []
    span = end_s - start_s
    amp = (STRINGS_EXPRESSION_HI - STRINGS_EXPRESSION_LO) / 2.0
    center = (STRINGS_EXPRESSION_HI + STRINGS_EXPRESSION_LO) / 2.0
    for i in range(STRINGS_EXPRESSION_STEPS):
        frac = i / max(1, STRINGS_EXPRESSION_STEPS - 1)
        t = start_s + frac * span
        value = int(round(center + amp * math.sin(2 * math.pi * (frac + phase))))
        value = max(0, min(127, value))
        events.append(StringsExpressionEvent(time_s=t, value=value))
    return tuple(events)


def _bar_has_chug(bar: BarAnalysis, analysis: Analysis) -> bool:
    """Chug grave = >= STRINGS_CHUG_MIN_NOTES notas de guitarra abaixo de
    STRINGS_CHUG_PITCH_CEILING (C3) no bar. Usa `guitar_notes` da analise
    da rodada 1 — nao ha necessidade de re-heuristica."""
    count = 0
    for gn in analysis.guitar_notes:
        if gn.start < bar.start:
            continue
        if gn.start >= bar.end:
            break
        if gn.pitch < STRINGS_CHUG_PITCH_CEILING:
            count += 1
            if count >= STRINGS_CHUG_MIN_NOTES:
                return True
    return False


def generate_strings(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "strings",
    register: tuple[int, int] = DEFAULT_STRINGS_REGISTER,
    voices: int = 3,
    articulation: str = "sustained",
    dynamics: dict | None = None,
    tutti: bool = False,
    ghost_ratio: float = STRINGS_GHOST_RATIO,
    seed: int = 0,
) -> list[StringsVoice]:
    """Gera vozes de strings ou choir para uma secao.

    Nao emite blocos de acorde — escreve LINHAS: uma voz mantem nota comum
    entre acordes consecutivos, outras vozes movem por grau conjunto. Cada
    voz recebe attack proprio (jitter em STRINGS_VOICE_ATTACK_JITTER_MS) e
    uma curva CC11 (fase distinta por voz).

    Args:
      analysis: saida de analise da rodada 1.
      section: secao alvo.
      role: 'strings' ou 'choir'.
      register: (low, high) MIDI onde as vozes vivem.
      voices: numero de vozes. Tutti sobe ate STRINGS_TUTTI_MAX_VOICES.
      articulation: mapeado para gate via GATE_RATIOS; default 'sustained'.
      dynamics: 'entry' e 'shape' (fade_in_Nbars, open_at_chorus, hold).
      tutti: quando True, gera stack de ate 8 notas em ~40 st para o pico.
      ghost_ratio: fracao das NOTAS totais que recebem velocity ghost.
        Aplicado apenas as vozes internas — extremos nunca sao ghost.
      seed: seed determinstica.

    Raises:
      ValueError: voices < 1 ou role fora de STRINGS_ROLES.
    """
    if voices < 1:
        raise ValueError(f"voices must be >= 1; got {voices}")
    if role not in STRINGS_ROLES:
        raise ValueError(f"role must be one of {list(STRINGS_ROLES)}; got {role!r}")
    if tutti and voices > STRINGS_TUTTI_MAX_VOICES:
        voices = STRINGS_TUTTI_MAX_VOICES

    dyn = dynamics or {}
    rng = random.Random(seed)
    bars = _bars_in_section(section, analysis)

    if not bars:
        return [
            StringsVoice(index=i, notes=(), expression_events=())
            for i in range(voices)
        ]

    base = _strings_base_velocity()
    ghost_vel = _strings_ghost_velocity()
    gate_ratios = GATE_RATIOS.get(articulation, GATE_RATIOS["sustained"])
    gate = (gate_ratios[0] + gate_ratios[1]) / 2.0

    # Attack offset por voz — acumulado, para diferenciar entrada de cada voz.
    voice_offsets_s: list[float] = [0.0]
    for _ in range(1, voices):
        step_ms = rng.uniform(*STRINGS_VOICE_ATTACK_JITTER_MS)
        voice_offsets_s.append(voice_offsets_s[-1] + step_ms / 1000.0)

    # Resolucao de pitches por bar via voice-leading. Bars sem acorde herdam
    # o pitch do bar anterior (voz sustenta) — se o primeiro bar for sem
    # acorde, procura o primeiro bar com acorde e usa como semente.
    first_chord_bar: Chord | None = None
    for b in bars:
        if b.chord is not None:
            first_chord_bar = b.chord
            break
    if first_chord_bar is None:
        return [
            StringsVoice(index=i, notes=(), expression_events=())
            for i in range(voices)
        ]

    if tutti:
        voice_pitches_per_bar: list[list[int]] = []
        for b in bars:
            if b.chord is None:
                voice_pitches_per_bar.append(voice_pitches_per_bar[-1] if voice_pitches_per_bar else [])
                continue
            pitches = _tutti_pitches(b.chord, register)
            # Alinha comprimento com `voices` (tutti sobreescreve voices).
            while len(pitches) < voices:
                pitches.append(pitches[-1])
            voice_pitches_per_bar.append(pitches[:voices])
    else:
        prev: list[int] = _initial_voice_pitches(first_chord_bar, register, voices)
        voice_pitches_per_bar = []
        for b in bars:
            if b.chord is None:
                voice_pitches_per_bar.append(list(prev))
                continue
            next_pitches: list[int] = []
            for vi in range(voices):
                inner = 0 < vi < voices - 1
                next_pitches.append(_next_voice_pitch(prev[vi], b.chord, register, inner))
            voice_pitches_per_bar.append(next_pitches)
            prev = next_pitches

    chug_map: list[bool] = [_bar_has_chug(b, analysis) for b in bars]

    voices_notes_raw: list[list[StringsNote]] = [[] for _ in range(voices)]
    voices_bar_pos: list[list[int]] = [[] for _ in range(voices)]
    for bar_pos, bar in enumerate(bars):
        if bar.chord is None:
            continue
        pitches = voice_pitches_per_bar[bar_pos]
        bar_ends = bar.end
        boundary = bar_ends
        # Encontra proximo bar com acorde para calcular fim natural.
        for j in range(bar_pos + 1, len(bars)):
            if bars[j].chord is not None:
                boundary = bars[j].start
                break
        note_dur = (boundary - bar.start) * gate
        v_bar = _velocity_for_bar(bar_pos, dyn, section.kind, base)
        chug_active = chug_map[bar_pos]
        for vi in range(voices):
            pitch = pitches[vi]
            if chug_active and pitch < STRINGS_LOW_PITCH_CEILING:
                # Suprime low strings em bar com chug grave (AC).
                continue
            start = bar.start + voice_offsets_s[vi]
            if start >= boundary:
                continue
            end = start + note_dur
            n = StringsNote(
                pitch=pitch, velocity=v_bar,
                start_s=start, end_s=end, is_ghost=False,
            )
            voices_notes_raw[vi].append(n)
            voices_bar_pos[vi].append(bar_pos)

    # Consolida `nota comum sustentada` — knowledge/persona/persona_produtor_
    # metal_moderno.md linhas 524 ("nota comum: uma voz permanece enquanto o
    # acorde muda") e 699 ("uma voz sustenta nota comum") descrevem UM arco
    # segurado, nao um ataque por compasso. Ataques repetidos com mesmo
    # pitch/velocity/duracao/espacamento sao literalmente o padrao que o
    # artifice `_check_repeated_notes` denuncia como robotico — o conserto
    # e no gerador, colapsando corridas de bars contiguos no mesmo pitch
    # numa unica nota longa.
    for vi in range(voices):
        raw_notes = voices_notes_raw[vi]
        raw_bp = voices_bar_pos[vi]
        if len(raw_notes) < 2:
            continue
        merged_notes: list[StringsNote] = []
        merged_bp: list[int] = []
        cur = raw_notes[0]
        cur_bp = raw_bp[0]
        for i in range(1, len(raw_notes)):
            nxt = raw_notes[i]
            nxt_bp = raw_bp[i]
            if (
                nxt.pitch == cur.pitch
                and nxt.velocity == cur.velocity
                and nxt_bp == cur_bp + 1
            ):
                cur = StringsNote(
                    pitch=cur.pitch, velocity=cur.velocity,
                    start_s=cur.start_s, end_s=nxt.end_s,
                    is_ghost=cur.is_ghost,
                )
                cur_bp = nxt_bp
            else:
                merged_notes.append(cur)
                merged_bp.append(cur_bp)
                cur = nxt
                cur_bp = nxt_bp
        merged_notes.append(cur)
        merged_bp.append(cur_bp)
        voices_notes_raw[vi] = merged_notes
        voices_bar_pos[vi] = merged_bp

    # Ghost roda DEPOIS do merge — a fracao de ghost e sobre notas MUSICAIS
    # (arcos), nao sobre re-ataques que nem existem musicalmente. Vozes
    # extremas nunca sao ghost (grave sustenta o arco, aguda entrega a nota
    # focal).
    total_notes = sum(len(v) for v in voices_notes_raw)
    inner_note_indices: list[tuple[int, int]] = []
    for vi in range(1, voices - 1):
        for ni in range(len(voices_notes_raw[vi])):
            inner_note_indices.append((vi, ni))
    target_ghost = int(round(ghost_ratio * total_notes))
    ghost_pool = list(inner_note_indices)
    rng.shuffle(ghost_pool)
    selected = ghost_pool[:min(target_ghost, len(ghost_pool))]
    for vi, ni in selected:
        old = voices_notes_raw[vi][ni]
        voices_notes_raw[vi][ni] = StringsNote(
            pitch=old.pitch, velocity=ghost_vel,
            start_s=old.start_s, end_s=old.end_s, is_ghost=True,
        )

    result: list[StringsVoice] = []
    for vi in range(voices):
        notes = tuple(sorted(voices_notes_raw[vi], key=lambda n: (n.start_s, n.pitch)))
        if notes:
            phase = vi / max(1, voices)
            exp = _expression_curve(notes[0].start_s, notes[-1].end_s, phase)
        else:
            exp = ()
        result.append(StringsVoice(
            index=vi, notes=notes, expression_events=exp,
        ))
    return result


# --- drone / nota-pedal -----------------------------------------------------

DEFAULT_DRONE_REGISTER: tuple[int, int] = REGISTER_BANDS["mid"]
"""Registro default do drone: banda mid. Drone estreito por regra — o
validador de largura rejeita registros que cruzam mais de uma banda."""

DRONE_ROLES: tuple[str, ...] = ("drone",)
"""Role atendido por `generate_drone`. Nota-pedal e o mesmo role no modo
pedal=True — distincao esta no comportamento de CC, nao no nome."""

DRONE_FILTER_CC: int = 74
"""CC74 (Brightness/Filter Cutoff) — o eixo de 'filtro respirando' da
persona (secao 7.4)."""

DRONE_EXPRESSION_CC: int = 11
"""CC11 (Expression) — eixo do 'pitch drift lento' interpretado como
modulacao ampla de expressao ao longo do ciclo macro. Nao emitimos
pitch_bend porque a integracao do seam de render so aceita CC events
nesta rodada."""

DRONE_FILTER_CYCLE_BARS_ALLOWED: tuple[int, ...] = (2, 4, 8)
"""Ciclos permitidos para CC74 (filtro respirando). AC: 'ciclo de 2, 4 ou
8 compassos'; qualquer outro valor levanta ValueError no gerador."""

DRONE_DEFAULT_FILTER_CYCLE_BARS: int = 4
"""Default de ciclo de filtro: 4 bars — cai no meio das opcoes."""

DRONE_MODULATION_MIN_BARS: int = 16
"""AC: 'ciclo de modulacao com periodo minimo de 16 compassos'. Ciclo
mais curto = loop percebido; validamos no gerador (ValueError)."""

DRONE_DEFAULT_MODULATION_BARS: int = 16
"""Default do ciclo macro de expressao/pitch drift: 16 bars — piso do AC."""

DRONE_CC_STEPS_PER_CYCLE: int = 16
"""Pontos de CC por ciclo — DAW interpola entre eles. 16 pontos e o suficiente
para uma senoide suave sem inflar arquivo (mesma economia do CC11 de strings)."""

DRONE_FILTER_LO: int = 30
DRONE_FILTER_HI: int = 110
"""Faixa de valores do CC74. Nao vai a 0/127 para o filtro nao fechar
completamente (drone sumir) nem abrir a ponto de perder o carater."""

DRONE_EXPRESSION_LO: int = 40
DRONE_EXPRESSION_HI: int = 110
"""Faixa de valores do CC11. Igual as strings — coerencia entre elementos
sustentados na mesma mix."""

DRONE_BASE_VELOCITY_BUCKET: str = "tied_soft"
"""Bucket 'tied_soft' (50-75) — drone sustenta abaixo do pad para nao
mascarar a fundamental."""

DRONE_FIFTH_SEMITONES: int = 7
"""Quinta justa acima da tonica. Usado quando pedal_pitch='fifth'."""


@dataclass(frozen=True)
class DroneNote:
    """Nota unica sustentada de um drone/nota-pedal."""
    pitch: int
    velocity: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class DroneCCEvent:
    """Evento CC do drone. `cc` = DRONE_FILTER_CC ou DRONE_EXPRESSION_CC
    (ou outro CC no futuro)."""
    time_s: float
    cc: int
    value: int


@dataclass(frozen=True)
class DroneLayer:
    """Uma camada de drone. Cada camada e uma track independente — mesma
    convencao das outras camadas harmonicas. `cc_events` vazio no modo
    pedal (nota pura sem modulacao)."""
    index: int
    notes: tuple[DroneNote, ...]
    cc_events: tuple[DroneCCEvent, ...] = ()


def validate_drone_register(register: tuple[int, int]) -> None:
    """Levanta ValueError se `register` cruza mais de uma banda de
    REGISTER_BANDS. AC: 'drone nao ocupa faixa larga — rejeita drone que
    cubra mais de uma banda de REGISTER_BANDS'."""
    lo, hi = register
    if lo > hi:
        raise ValueError(f"drone register invalid: low > high in {register}")
    touched = [name for name, (blo, bhi) in REGISTER_BANDS.items()
               if lo <= bhi and hi >= blo]
    if len(touched) > 1:
        raise ValueError(
            f"drone register {register} spans multiple bands {touched} "
            f"— drone must stay within a single REGISTER_BANDS band"
        )


def _drone_base_velocity() -> int:
    lo, hi = VELOCITY_RANGES[DRONE_BASE_VELOCITY_BUCKET]
    return (lo + hi) // 2


def _drone_pitch(key_root: int, register: tuple[int, int], pitch_choice: str) -> int:
    """Escolhe pitch do drone dentro do registro. 'tonic' = raiz do tom;
    'fifth' = quinta justa. Ambos alinham com `harmony: pedal` do
    validador harmonico (PEDAL_INTERVALS = {0, 7})."""
    lo, _hi = register
    offset = DRONE_FIFTH_SEMITONES if pitch_choice == "fifth" else 0
    pc = (key_root + offset) % 12
    return lo + ((pc - lo) % 12)


def _sine_lfo_points(
    start_s: float,
    end_s: float,
    cycle_dur_s: float,
    steps_per_cycle: int,
    lo_val: int,
    hi_val: int,
    phase: float,
) -> list[tuple[float, int]]:
    """Amostras de um LFO senoidal ao longo de [start_s, end_s].

    Ciclo em segundos = `cycle_dur_s`. Emite `steps_per_cycle` pontos por
    ciclo, ajustado pela razao span/ciclo. `phase` em [0,1). Retorna
    `(time_s, value)` com value em [0, 127]. Chamador embrulha em seu
    proprio dataclass de evento (DroneCCEvent, RhythmicCCEvent, ...)."""
    span = end_s - start_s
    cycles = span / cycle_dur_s
    n_steps = max(2, int(round(cycles * steps_per_cycle)))
    amp = (hi_val - lo_val) / 2.0
    center = (hi_val + lo_val) / 2.0
    points: list[tuple[float, int]] = []
    for i in range(n_steps):
        frac = i / (n_steps - 1)
        t = start_s + span * frac
        theta = 2 * math.pi * (cycles * frac + phase)
        value = int(round(center + amp * math.sin(theta)))
        value = max(0, min(127, value))
        points.append((t, value))
    return points


def _drone_cc_curve(
    start_s: float,
    end_s: float,
    cycle_bars: int,
    bar_dur_s: float,
    cc: int,
    lo_val: int,
    hi_val: int,
    phase: float,
) -> tuple[DroneCCEvent, ...]:
    """LFO senoidal para um CC ao longo de [start_s, end_s]. Ciclo em
    segundos = `cycle_bars * bar_dur_s`. `phase` em [0,1) — permite duas
    CCs no mesmo drone com fases distintas."""
    points = _sine_lfo_points(
        start_s, end_s, cycle_bars * bar_dur_s,
        DRONE_CC_STEPS_PER_CYCLE, lo_val, hi_val, phase,
    )
    return tuple(DroneCCEvent(time_s=t, cc=cc, value=v) for t, v in points)


def generate_drone(
    analysis: Analysis,
    section: PlanSection,
    *,
    register: tuple[int, int] = DEFAULT_DRONE_REGISTER,
    layers: int = 1,
    pedal: bool = False,
    pedal_pitch: str = "tonic",
    filter_cycle_bars: int = DRONE_DEFAULT_FILTER_CYCLE_BARS,
    modulation_bars: int = DRONE_DEFAULT_MODULATION_BARS,
    seed: int = 0,
) -> list[DroneLayer]:
    """Gera camadas de drone / nota-pedal para uma secao.

    Nota unica sustentada na tonica (ou quinta em `pedal_pitch='fifth'`)
    do tom global (`analysis.key_root`) — casa com `harmony: pedal` do
    validador harmonico. `pedal=False` acompanha a nota com CC74 (filtro
    respirando) em ciclo de `filter_cycle_bars` bars E CC11 (expressao)
    em ciclo macro de `modulation_bars` bars. `pedal=True` desativa a
    modulacao — nota-pedal e por definicao 'sem drift'.

    Args:
      analysis: saida de analise da rodada 1.
      section: secao alvo.
      register: (low, high) MIDI. Rejeita registros que cruzam mais de
        uma banda de REGISTER_BANDS (via `validate_drone_register`).
      layers: camadas em unisono. Cada layer vira uma track no MIDI final.
      pedal: True desativa CCs — nota-pedal pura.
      pedal_pitch: 'tonic' ou 'fifth'. Casa com PEDAL_INTERVALS = {0, 7}
        do validador harmonico.
      filter_cycle_bars: ciclo de CC74 em bars. Deve estar em
        DRONE_FILTER_CYCLE_BARS_ALLOWED = (2, 4, 8).
      modulation_bars: ciclo de CC11 em bars. Precisa ser >=
        DRONE_MODULATION_MIN_BARS = 16.
      seed: seed determinstica.

    Raises:
      ValueError: layers < 1, pedal_pitch fora de {tonic, fifth},
        filter_cycle_bars fora de DRONE_FILTER_CYCLE_BARS_ALLOWED,
        modulation_bars < DRONE_MODULATION_MIN_BARS, ou register cruza
        mais de uma banda (via `validate_drone_register`).
    """
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if pedal_pitch not in ("tonic", "fifth"):
        raise ValueError(f"pedal_pitch must be 'tonic' or 'fifth'; got {pedal_pitch!r}")
    if filter_cycle_bars not in DRONE_FILTER_CYCLE_BARS_ALLOWED:
        raise ValueError(
            f"filter_cycle_bars must be one of "
            f"{list(DRONE_FILTER_CYCLE_BARS_ALLOWED)}; got {filter_cycle_bars}"
        )
    if modulation_bars < DRONE_MODULATION_MIN_BARS:
        raise ValueError(
            f"modulation_bars must be >= {DRONE_MODULATION_MIN_BARS}; "
            f"got {modulation_bars}"
        )
    validate_drone_register(register)

    bars = _bars_in_section(section, analysis)
    if not bars:
        return [
            DroneLayer(index=i, notes=(), cc_events=())
            for i in range(layers)
        ]

    rng = random.Random(seed)
    pitch = _drone_pitch(analysis.key_root, register, pedal_pitch)
    velocity = _drone_base_velocity()
    start_s = bars[0].start
    end_s = bars[-1].end
    bar_dur_s = bars[0].end - bars[0].start

    result: list[DroneLayer] = []
    for layer_idx in range(layers):
        note = DroneNote(pitch=pitch, velocity=velocity, start_s=start_s, end_s=end_s)
        if pedal:
            cc_events: tuple[DroneCCEvent, ...] = ()
        else:
            filter_phase = rng.random()
            expression_phase = rng.random()
            filter_events = _drone_cc_curve(
                start_s, end_s, filter_cycle_bars, bar_dur_s,
                DRONE_FILTER_CC, DRONE_FILTER_LO, DRONE_FILTER_HI,
                filter_phase,
            )
            expression_events = _drone_cc_curve(
                start_s, end_s, modulation_bars, bar_dur_s,
                DRONE_EXPRESSION_CC, DRONE_EXPRESSION_LO, DRONE_EXPRESSION_HI,
                expression_phase,
            )
            merged = sorted(
                [*filter_events, *expression_events],
                key=lambda ev: (ev.time_s, ev.cc, ev.value),
            )
            cc_events = tuple(merged)
        result.append(DroneLayer(
            index=layer_idx, notes=(note,), cc_events=cc_events,
        ))
    return result


def check_tutti_uniqueness(plan: ArrangementPlan) -> list[str]:
    """AC: 'tutti reservado a um unico pico da musica — aviso se aparecer em
    mais de uma secao'.

    Percorre `plan.elements` procurando pattern['tutti']=True e conta em
    quantas secoes cada elemento tutti aparece; alem disso, quantos
    elementos tutti aparecem em cada secao. Devolve lista de avisos
    string (nao levanta) — chamador anexa a RenderReport.warnings."""
    warnings: list[str] = []
    tutti_sections: set[str] = set()
    for e in plan.elements:
        pattern = e.pattern or {}
        if not pattern.get("tutti", False):
            continue
        for label in e.sections:
            tutti_sections.add(label)
    if len(tutti_sections) > 1:
        warnings.append(
            "strings tutti declared in more than one section: "
            f"{sorted(tutti_sections)} — tutti should be reserved for a single peak"
        )
    return warnings


# Documentacao viva: registrar que C2_PITCH e o corte da restricao de sub,
# reusado do modulo de voicing (garante consistencia entre motores).
__all__ = [
    "C2_PITCH",
    "CHORUS_KIND",
    "DEFAULT_DRONE_REGISTER",
    "DEFAULT_KEYBOARD_ARTICULATION",
    "DEFAULT_KEYBOARD_REGISTER",
    "DEFAULT_PAD_REGISTER",
    "DEFAULT_STRINGS_REGISTER",
    "DRONE_BASE_VELOCITY_BUCKET",
    "DRONE_CC_STEPS_PER_CYCLE",
    "DRONE_DEFAULT_FILTER_CYCLE_BARS",
    "DRONE_DEFAULT_MODULATION_BARS",
    "DRONE_EXPRESSION_CC",
    "DRONE_EXPRESSION_HI",
    "DRONE_EXPRESSION_LO",
    "DRONE_FIFTH_SEMITONES",
    "DRONE_FILTER_CC",
    "DRONE_FILTER_CYCLE_BARS_ALLOWED",
    "DRONE_FILTER_HI",
    "DRONE_FILTER_LO",
    "DRONE_MODULATION_MIN_BARS",
    "DRONE_ROLES",
    "DroneCCEvent",
    "DroneLayer",
    "DroneNote",
    "FADE_IN_FLOOR",
    "KEYBOARD_BASE_VELOCITY_BUCKET",
    "KEYBOARD_LAYER_ONSET_STAGGER_MS",
    "KEYBOARD_ROLES",
    "KEYBOARD_TIMING_JITTER_MS",
    "KeyboardLayer",
    "KeyboardNote",
    "KeyboardPedal",
    "MIN_NOTE_DURATION_S",
    "OPEN_AT_CHORUS_BONUS",
    "PAD_BASE_VELOCITY_BUCKET",
    "PAD_LAYER_ONSET_STAGGER_MS",
    "PIANO_GAP_MS",
    "PadLayer",
    "PadNote",
    "RHODES_LEGATO_RATIO_RANGE",
    "RHODES_OVERLAP_MS",
    "STRINGS_BASE_VELOCITY_BUCKET",
    "STRINGS_CHUG_MIN_NOTES",
    "STRINGS_CHUG_PITCH_CEILING",
    "STRINGS_EXPRESSION_HI",
    "STRINGS_EXPRESSION_LO",
    "STRINGS_EXPRESSION_STEPS",
    "STRINGS_GHOST_RATIO",
    "STRINGS_LOW_PITCH_CEILING",
    "STRINGS_ROLES",
    "STRINGS_STEP_SEARCH_ST",
    "STRINGS_TUTTI_MAX_VOICES",
    "STRINGS_TUTTI_SPREAD_ST",
    "STRINGS_VOICE_ATTACK_JITTER_MS",
    "StringsExpressionEvent",
    "StringsNote",
    "StringsVoice",
    "check_tutti_uniqueness",
    "generate_drone",
    "generate_keyboard",
    "generate_pad",
    "generate_strings",
    "validate_drone_register",
]
