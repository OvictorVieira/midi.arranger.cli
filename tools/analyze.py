"""Analise musical do MIDI de origem.

Extrai, por compasso, acorde detectado (raiz + qualidade quando determinavel),
contagem de notas por track e faixa de pitch ocupada por track. Devolve tambem
o tom global (via tools.primitives.key_root), ancoras ritmicas (kick, snare,
unisono de guitarra) e uma consulta de ocupacao de registro por banda.

Delega intencionalmente para tools.primitives:
  - key_root (tom global)
  - chord_root (raiz do acorde a partir de pitch classes)
  - chordal_bars (contrato — usado no teste de contrato desta rodada)
  - KICKS / SNARES (mesmo mapa GM usado pelo humanize)

## Anotacoes textuais (issue #32)

O MIDI de origem pode trazer meta-eventos de texto (`text` 0x01, `cue_point`
0x07) e marcadores nao-secao (`marker` 0x06) que descrevem como o arranjo deve
soar naquele ponto. `analyze` extrai esses eventos como `Annotation`, com
texto, tick, compasso, segundo, track de origem, tipo de evento, secao em que
caem e escopo (do tick da propria anotacao ate a proxima anotacao dentro da
mesma secao OU ate o fim da secao, o que vier primeiro — empate vai para o fim
da secao).

Regras de filtragem:
- Marcador cujo texto casa `sections.normalize_kind` (INTRO A, VERSE 1, etc.)
  continua indo para `sections`, NAO vira anotacao — sao coisas diferentes.
- Ruido de DAW e descartado por padrao com listagem explicita na saida
  (`discarded_annotations`), com a razao textual do descarte. Nunca em
  silencio — filtro silencioso esconde anotacao real classificada errado.
- Padroes de ruido default cobrem `END_OF_VOICE` e `MEASURE_\\d+` (Logic e
  Songsterr geram em volume industrial). Texto que se repete acima do limiar
  (default 5 posicoes distintas) tambem e descartado como ruido — repeticao em
  massa e assinatura de ruido, nao de intencao.
- A interpretacao do texto e da IA, NAO do maquinario. Esta tool entrega
  texto e posicao estruturados; nao existe parser de linguagem natural aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import mido
import pretty_midi

from .constants import REGISTER_BANDS
from .primitives import KICKS, SNARES
from .primitives import bars_from as _bars_from
from .primitives import chord_root as _chord_root
from .primitives import key_root as _key_root
from .sections import Section, normalize_kind, read_sections
from .tuning import TrackChannelDistribution, TrackTuningInference, fallback_track_name
from .tuning import channel_distribution as _channel_distribution
from .tuning import tuning_inference as _tuning_inference

UNISON_WINDOW_S = 0.010  # 10 ms — mesmo limiar de cluster de strum do humanize

# Ruido de DAW: default cobrindo o que ja conhecemos por medicao de fixtures.
# `END_OF_VOICE` vem do export de MIDI da Songsterr; `MEASURE_\d+` do Logic
# Drummer e afins. Ambos aparecem em volume industrial (984 e 6 respectivamente
# em `tests/fixtures/corpus_drums/ENTRE NÓS.mid`).
DEFAULT_ANNOTATION_NOISE_PATTERNS: tuple[str, ...] = (
    r"^END_OF_VOICE$",
    r"^MEASURE_\d+$",
)

# Acima deste numero de posicoes distintas para o mesmo texto, o marcador e
# considerado ruido — repeticao em massa e assinatura de DAW, nao de intencao.
# Default estrito o suficiente para pegar automation mas permissivo para o
# usuario que quer marcar 3-5 pontos com a mesma legenda ("attack", "hit").
DEFAULT_ANNOTATION_REPETITION_THRESHOLD = 5

# Eventos meta que carregam texto de anotacao. `marker` (0x06) e triado pela
# `sections.normalize_kind`: os que sao rotulo de secao ficam de fora daqui.
# `cue_marker` e o nome que o `mido` da para o meta-evento 0x07 (MIDI standard
# "cue point") — usamos o mesmo string na saida para nao ter duas verdades.
_ANNOTATION_META_TYPES = frozenset({"text", "cue_marker", "marker"})
ANNOTATION_EVENT_TYPES = ("marker", "text", "cue_marker")


@dataclass
class Chord:
    """Acorde detectado em um compasso.

    root: pitch class 0-11 (C=0, C#=1, ...).
    quality: 'major' | 'minor' | 'power' | 'unknown'.
    """
    root: int
    quality: str


@dataclass
class BarAnalysis:
    index: int              # 0-based
    start: float            # segundos
    end: float              # segundos
    chord: Chord | None
    notes_per_track: dict[str, int] = field(default_factory=dict)
    pitch_range_per_track: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass
class GuitarNote:
    """Nota disparada em uma track de guitarra do MIDI de origem.

    Consumida pelo validador harmonico no modo `unison_guitar` — precisa
    do pitch por nota, nao apenas do onset agregado em
    `guitar_unison_positions`.
    """
    start: float
    pitch: int
    track: str


@dataclass
class Annotation:
    """Anotacao textual encontrada no MIDI (issue #32).

    - `text`: literal do meta-evento, preservado como veio (sem interpretacao).
    - `tick`: posicao absoluta em ticks.
    - `bar`: compasso 1-based em que a anotacao cai.
    - `time_s`: instante em segundos (tick_to_time).
    - `track`: nome da track SMF em que o evento apareceu (primeira track
      observada quando (tick, texto, tipo) aparece em varias).
    - `event_type`: `marker` | `text` | `cue_point`.
    - `section_label`: rotulo da secao que contem a anotacao (None quando
      cai fora do range de qualquer secao).
    - `end_tick`, `end_bar`, `end_time_s`: fim do escopo da anotacao.
    - `scope_end_source`: `next_annotation` | `section_end` | `file_end`.
      Regra: escopo vai ate a proxima anotacao dentro da mesma secao OU ate
      o fim da secao, o que vier PRIMEIRO. Empate (proxima anotacao no mesmo
      tick que o fim da secao) vai para `section_end` — anotacao que cai na
      fronteira pertence a secao que comeca ali, entao nao e "proxima na
      mesma secao".
    """
    text: str
    tick: int
    bar: int
    time_s: float
    track: str
    event_type: str
    section_label: str | None
    end_tick: int
    end_bar: int
    end_time_s: float
    scope_end_source: str


@dataclass
class DiscardedAnnotation:
    """Anotacao que o filtro de ruido descartou, com a razao textual.

    Nunca omitir — filtro silencioso esconde anotacao real classificada errado.
    A razao carrega o padrao literal do filtro (`pattern:^END_OF_VOICE$`) ou o
    limiar de repeticao (`repetition:12>5`). O relatorio da tool agrega
    contagens por razao.
    """
    text: str
    tick: int
    track: str
    event_type: str
    reason: str


@dataclass
class Analysis:
    key_root: int                              # 0-11
    bars: list[BarAnalysis]
    kick_positions: list[float]                # segundos
    snare_positions: list[float]               # segundos
    guitar_unison_positions: list[float]       # segundos
    track_names: list[str]                     # ordem estavel
    guitar_notes: list[GuitarNote] = field(default_factory=list)
    channel_distribution: list[TrackChannelDistribution] = field(default_factory=list)
    tuning_inference: list[TrackTuningInference] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    discarded_annotations: list[DiscardedAnnotation] = field(default_factory=list)


def find_bar(analysis: Analysis, onset_s: float) -> BarAnalysis | None:
    """Compasso cujo intervalo contem `onset_s`. None se fora do range.

    Helper compartilhado por harmony_validator/placement/artifice — a busca
    linear e barata (musicas cabem em dezenas/centenas de bars) e o codigo
    fica em um lugar so."""
    for bar in analysis.bars:
        if bar.start <= onset_s < bar.end:
            return bar
    return None


def bar_number(bar: BarAnalysis | None) -> int:
    """Numero 1-based do compasso; 0 quando `bar` e None (onset fora do range
    da analise). Convencao usada por todos os validadores no relatorio."""
    return (bar.index + 1) if bar is not None else 0


def _track_name(inst: pretty_midi.Instrument, idx: int) -> str:
    """Nome estavel para uma track — reusa `fallback_track_name` de
    `tools.tuning` para nao divergir do texto que a inferencia de afinacao
    reporta na mesma posicao."""
    name = (inst.name or "").strip()
    if name:
        return name
    return fallback_track_name(idx)


def _is_guitar_track(inst: pretty_midi.Instrument) -> bool:
    """Guitarra = track nao-drum cujo nome contem 'guitar' e NAO 'bass'.

    Espelha a heuristica usada em tools.primitives para separar guitarra
    de baixo pelo nome — evita divergencia entre analise e humanize.
    """
    if inst.is_drum:
        return False
    name = (inst.name or "").lower()
    return "guitar" in name and "bass" not in name


def _is_drum_track(inst: pretty_midi.Instrument) -> bool:
    """Drum track = is_drum True OU nome contem 'drum'/'drummer'.

    Logic Drummer exporta faixa de bateria sem is_drum=True; o nome
    'Drummer' e a unica pista. Espelha o comportamento observado no
    fixture ANCORA (secao 7 do spec).
    """
    if inst.is_drum:
        return True
    name = (inst.name or "").lower()
    return "drum" in name


def _classify_quality(pcs: set[int], root: int) -> str:
    """Classifica qualidade a partir de pitch classes e raiz.

    Regras (ordem importa):
      - major:  raiz + 4 (terca maior) + 7 (quinta justa)
      - minor:  raiz + 3 (terca menor) + 7
      - power:  raiz + 7 sem terca detectavel
      - unknown: nenhum dos padroes acima
    """
    has_maj_third = (root + 4) % 12 in pcs
    has_min_third = (root + 3) % 12 in pcs
    has_fifth = (root + 7) % 12 in pcs
    if has_fifth and has_maj_third:
        return "major"
    if has_fifth and has_min_third:
        return "minor"
    if has_fifth:
        return "power"
    return "unknown"


def _detect_chord(pm: pretty_midi.PrettyMIDI, start: float, end: float) -> Chord | None:
    """Detecta acorde predominante em [start, end) usando notas nao-drum
    que soam no intervalo. Devolve None quando nao ha nota suficiente."""
    pcs: list[int] = []
    for inst in pm.instruments:
        if _is_drum_track(inst):
            continue
        for n in inst.notes:
            if n.start < end and n.end > start:
                pcs.append(n.pitch % 12)
    if not pcs:
        return None
    pcs_unique = sorted(set(pcs))
    root = _chord_root(pcs_unique)
    quality = _classify_quality(set(pcs_unique), root)
    return Chord(root=root, quality=quality)


def _find_unisons(
    guitar_tracks: list[tuple[str, pretty_midi.Instrument]],
    window: float = UNISON_WINDOW_S,
) -> list[float]:
    """Encontra instantes em que 2+ tracks distintas de guitarra atacam juntas
    dentro de `window` segundos. Cada instante e reportado uma unica vez
    (tempo do ataque mais cedo do cluster)."""
    if len(guitar_tracks) < 2:
        return []
    events: list[tuple[float, str]] = []
    for name, inst in guitar_tracks:
        for n in inst.notes:
            events.append((n.start, name))
    events.sort(key=lambda x: x[0])

    unisons: list[float] = []
    i = 0
    while i < len(events):
        t0, _ = events[i]
        j = i
        names: set[str] = set()
        while j < len(events) and events[j][0] - t0 <= window:
            names.add(events[j][1])
            j += 1
        if len(names) >= 2:
            unisons.append(t0)
            i = j
        else:
            i += 1
    return unisons


def _collect_raw_annotation_events(mid: mido.MidiFile) -> list[dict]:
    """Coleta candidatos brutos a anotacao de todas as tracks.

    Devolve dict com keys `type`, `text`, `tick`, `track` (nome ou fallback).
    Marker cujo texto casa `sections.normalize_kind` fica FORA — sao rotulos de
    secao e vao para `sections`, nao viram anotacao.
    """
    raw: list[dict] = []
    for track_idx, track in enumerate(mid.tracks):
        name = ""
        for msg in track:
            if msg.type == "track_name" and not name:
                name = (msg.name or "").strip()
                break
        if not name:
            name = fallback_track_name(track_idx)
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type not in _ANNOTATION_META_TYPES:
                continue
            text = getattr(msg, "text", None)
            if text is None:
                continue
            if msg.type == "marker" and normalize_kind(text) is not None:
                # Rotulo de secao — sai por `sections`, nao por `annotations`.
                continue
            raw.append({
                "type": msg.type,
                "text": text,
                "tick": abs_tick,
                "track": name,
            })
    return raw


def _dedupe_annotation_events(raw: list[dict]) -> list[dict]:
    """Colapsa eventos identicos (tick, texto, tipo) em diferentes tracks.

    DAW as vezes replica a mesma anotacao em varias tracks; contar cada uma
    como evento distinto inflaria falsamente a repeticao e criaria duplicatas
    na saida. Mantem a primeira track observada como origem.
    """
    seen: dict[tuple[int, str, str], dict] = {}
    for event in raw:
        key = (event["tick"], event["text"], event["type"])
        if key not in seen:
            seen[key] = event
    return list(seen.values())


def _classify_annotation_noise(
    events: list[dict],
    noise_patterns: tuple[str, ...],
    repetition_threshold: int,
) -> dict[int, str]:
    """Marca eventos como ruido; devolve {index_in_events: reason}.

    Duas regras, aplicadas nesta ordem:
      1. Texto casa qualquer padrao em `noise_patterns` (regex ancorado).
      2. Texto aparece em > `repetition_threshold` posicoes (ticks) distintas.
    """
    compiled = [(pat, re.compile(pat)) for pat in noise_patterns]

    # Repeticao por texto: quantos TICKS distintos usam esse texto.
    ticks_per_text: dict[str, set[int]] = {}
    for event in events:
        ticks_per_text.setdefault(event["text"], set()).add(event["tick"])

    reasons: dict[int, str] = {}
    for i, event in enumerate(events):
        text = event["text"]
        matched_pattern: str | None = None
        for raw_pat, regex in compiled:
            if regex.search(text):
                matched_pattern = raw_pat
                break
        if matched_pattern is not None:
            reasons[i] = f"pattern:{matched_pattern}"
            continue
        occurrences = len(ticks_per_text.get(text, ()))
        if occurrences > repetition_threshold:
            reasons[i] = f"repetition:{occurrences}>{repetition_threshold}"
    return reasons


def _section_for_tick(sections_list: list[Section], tick: int) -> Section | None:
    """Secao que contem `tick` — inclusive no start_tick, exclusiva no end_tick.

    Anotacao exatamente na fronteira pertence a secao que COMECA ali (regra
    do issue #32).
    """
    for s in sections_list:
        if s.start_tick <= tick < s.end_tick:
            return s
    return None


def _tick_to_bar(pm: pretty_midi.PrettyMIDI, tick: int, downbeats: list[float]) -> int:
    """Compasso 1-based que contem o tick informado.

    Retorna 0 quando fora do range (comportamento igual ao `bar_number`
    helper). Usa a mesma tabela de downbeats do `_bar_index_at_time` de
    `sections.py`.
    """
    if not downbeats:
        return 0
    t = float(pm.tick_to_time(tick))
    # busca linear em segundos (quantidade cabe em centenas)
    idx = 0
    for i, db in enumerate(downbeats):
        if db > t + 1e-6:
            break
        idx = i
    return idx + 1


def _extract_annotations(
    midi_path: str,
    pm: pretty_midi.PrettyMIDI,
    sections_list: list[Section],
    noise_patterns: tuple[str, ...] = DEFAULT_ANNOTATION_NOISE_PATTERNS,
    repetition_threshold: int = DEFAULT_ANNOTATION_REPETITION_THRESHOLD,
) -> tuple[list[Annotation], list[DiscardedAnnotation]]:
    """Pipeline completo de extracao de anotacoes.

    1. Coleta candidatos brutos (marker nao-secao, text, cue_point).
    2. Deduplica por (tick, texto, tipo) para o pool KEPT — DAW as vezes
       replica a mesma anotacao em varias tracks e contar cada uma inflaria
       a repeticao.
    3. Classifica ruido pelos EVENTOS UNICOS (padroes + repeticao acima do
       limiar) — a repeticao mede posicoes distintas, nao instancias.
    4. Descarta EM BRUTO: cada evento raw considerado ruido vira uma entrada
       em `discarded_annotations`, para que a contagem do relatorio bata com
       o que o usuario ve ao inspecionar o arquivo (984 MEASURE_* + 6
       END_OF_VOICE em ENTRE NOS, nao 165).
    5. Calcula bar/tempo/secao/escopo de cada anotacao mantida.
    """
    mid = mido.MidiFile(midi_path)
    raw = _collect_raw_annotation_events(mid)
    events = _dedupe_annotation_events(raw)
    # Ordem estavel: por tick, depois por texto — a saida da tool nao pode
    # depender da ordem de leitura das tracks.
    events.sort(key=lambda e: (e["tick"], e["text"], e["type"]))

    noise_reasons_by_key: dict[tuple[int, str, str], str] = {}
    idx_noise = _classify_annotation_noise(
        events, noise_patterns, repetition_threshold,
    )
    for i, reason in idx_noise.items():
        e = events[i]
        noise_reasons_by_key[(e["tick"], e["text"], e["type"])] = reason

    downbeats = list(pm.get_downbeats())
    end_time = pm.get_end_time()
    file_end_tick = int(round(pm.time_to_tick(end_time)))

    # Descarta cada evento raw individualmente para relatar a poluicao real.
    discarded: list[DiscardedAnnotation] = []
    for event in raw:
        key = (event["tick"], event["text"], event["type"])
        reason = noise_reasons_by_key.get(key)
        if reason is None:
            continue
        discarded.append(DiscardedAnnotation(
            text=event["text"],
            tick=int(event["tick"]),
            track=event["track"],
            event_type=event["type"],
            reason=reason,
        ))

    kept_events: list[dict] = [
        e for e in events
        if (e["tick"], e["text"], e["type"]) not in noise_reasons_by_key
    ]

    kept_events.sort(key=lambda e: e["tick"])

    annotations: list[Annotation] = []
    for idx, event in enumerate(kept_events):
        section = _section_for_tick(sections_list, event["tick"])
        # Escopo: proxima anotacao mantida DENTRO da mesma secao OU fim da
        # secao — o que vier primeiro. Empate vai para `section_end`.
        section_end_tick = section.end_tick if section is not None else file_end_tick
        section_label = section.label if section is not None else None

        next_tick_in_section: int | None = None
        for future in kept_events[idx + 1:]:
            if future["tick"] <= event["tick"]:
                continue
            if section is not None and future["tick"] >= section.end_tick:
                # Ja saiu da secao — proxima anotacao pertence a outra secao.
                break
            next_tick_in_section = future["tick"]
            break

        if next_tick_in_section is None:
            end_tick = section_end_tick
            scope_end_source = "section_end" if section is not None else "file_end"
        elif next_tick_in_section < section_end_tick:
            end_tick = next_tick_in_section
            scope_end_source = "next_annotation"
        else:
            # empate: proxima anotacao no mesmo tick que o fim da secao
            end_tick = section_end_tick
            scope_end_source = "section_end"

        annotations.append(Annotation(
            text=event["text"],
            tick=int(event["tick"]),
            bar=_tick_to_bar(pm, event["tick"], downbeats),
            time_s=float(pm.tick_to_time(event["tick"])),
            track=event["track"],
            event_type=event["type"],
            section_label=section_label,
            end_tick=int(end_tick),
            end_bar=_tick_to_bar(pm, end_tick, downbeats),
            end_time_s=float(pm.tick_to_time(end_tick)),
            scope_end_source=scope_end_source,
        ))
    return annotations, discarded


def analyze(
    midi_path: str,
    declared_stringed_tracks: list[str] | None = None,
) -> Analysis:
    """Analisa `midi_path` e devolve uma `Analysis` pronta para o planejador.

    `declared_stringed_tracks` propaga a declaracao explicita do usuario
    para a TRAVA 1 da inferencia de afinacao (US-002)."""
    pm = pretty_midi.PrettyMIDI(midi_path)
    key_root = _key_root(pm)

    named: list[tuple[str, pretty_midi.Instrument]] = [
        (_track_name(inst, i), inst) for i, inst in enumerate(pm.instruments)
    ]
    track_names = [n for n, _ in named]

    bars_raw = _bars_from(pm)
    bars: list[BarAnalysis] = []
    for b in bars_raw:
        notes_per_track: dict[str, int] = {}
        pitch_range_per_track: dict[str, tuple[int, int]] = {}
        for name, inst in named:
            in_bar = [n for n in inst.notes if b.start <= n.start < b.end]
            if not in_bar:
                continue
            notes_per_track[name] = len(in_bar)
            if not _is_drum_track(inst):
                pitches = [n.pitch for n in in_bar]
                pitch_range_per_track[name] = (min(pitches), max(pitches))
        chord = _detect_chord(pm, b.start, b.end)
        bars.append(BarAnalysis(
            index=b.idx,
            start=b.start,
            end=b.end,
            chord=chord,
            notes_per_track=notes_per_track,
            pitch_range_per_track=pitch_range_per_track,
        ))

    kick_positions: list[float] = []
    snare_positions: list[float] = []
    for _, inst in named:
        if not _is_drum_track(inst):
            continue
        for n in inst.notes:
            if n.pitch in KICKS:
                kick_positions.append(n.start)
            elif n.pitch in SNARES:
                snare_positions.append(n.start)
    kick_positions.sort()
    snare_positions.sort()

    guitar_tracks = [(n, i) for n, i in named if _is_guitar_track(i)]
    unisons = _find_unisons(guitar_tracks)

    guitar_notes: list[GuitarNote] = []
    for name, inst in guitar_tracks:
        for n in inst.notes:
            guitar_notes.append(GuitarNote(start=n.start, pitch=n.pitch, track=name))
    guitar_notes.sort(key=lambda g: (g.start, g.pitch))

    all_sections = read_sections(midi_path)
    # Escopo de anotacao ancora nas secoes CANONICAS (normalize_kind casou),
    # nunca em marker de texto livre que o `read_sections` tambem promove a
    # secao — misturar as duas quebraria a semantica do issue #32: "marcador
    # que delimita secao continua indo para sections, nao vira anotacao".
    # Reconstroi as extensoes canonicas: cada secao canonica vai ate a
    # PROXIMA secao canonica (ignorando markers de texto livre no meio),
    # ou ate o fim do arquivo se for a ultima.
    end_tick_file = int(round(pm.time_to_tick(pm.get_end_time())))
    canonical = [s for s in all_sections if s.kind]
    canonical_sections: list[Section] = []
    for i, s in enumerate(canonical):
        next_start = (
            canonical[i + 1].start_tick if i + 1 < len(canonical) else end_tick_file
        )
        canonical_sections.append(Section(
            label=s.label,
            kind=s.kind,
            start_tick=s.start_tick,
            end_tick=next_start,
            start_bar=s.start_bar,
            end_bar=s.end_bar,
            source=s.source,
        ))
    annotations, discarded_annotations = _extract_annotations(
        midi_path, pm, canonical_sections,
    )

    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=kick_positions,
        snare_positions=snare_positions,
        guitar_unison_positions=unisons,
        track_names=track_names,
        guitar_notes=guitar_notes,
        channel_distribution=_channel_distribution(midi_path),
        tuning_inference=_tuning_inference(midi_path, declared_stringed_tracks),
        annotations=annotations,
        discarded_annotations=discarded_annotations,
    )


def register_occupancy(bar: BarAnalysis) -> dict[str, set[str]]:
    """Devolve, por banda de REGISTER_BANDS, o conjunto de tracks ativas no
    compasso. Track e considerada ativa em uma banda quando pelo menos uma
    nota do compasso cai dentro do intervalo (inclusivo) da banda.

    Compasso vazio devolve dict com todas as bandas presentes, cada uma
    associada a um conjunto vazio — a chave existe para o consumidor iterar
    sem checar KeyError.
    """
    occupancy: dict[str, set[str]] = {band: set() for band in REGISTER_BANDS}
    for name, (lo, hi) in bar.pitch_range_per_track.items():
        for band, (blo, bhi) in REGISTER_BANDS.items():
            if hi >= blo and lo <= bhi:
                occupancy[band].add(name)
    return occupancy
