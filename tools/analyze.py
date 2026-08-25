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
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pretty_midi

from .constants import REGISTER_BANDS
from .primitives import KICKS, SNARES
from .primitives import bars_from as _bars_from
from .primitives import chord_root as _chord_root
from .primitives import key_root as _key_root
from .tuning import TrackChannelDistribution, TrackTuningInference, fallback_track_name
from .tuning import channel_distribution as _channel_distribution
from .tuning import tuning_inference as _tuning_inference

UNISON_WINDOW_S = 0.010  # 10 ms — mesmo limiar de cluster de strum do humanize


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
