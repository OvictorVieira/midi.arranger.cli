"""Deteccao de corda e afinacao a partir da distribuicao de canais MIDI.

O MIDI nao carrega metadado de afinacao, mas Guitar Pro e Songsterr
exportam **um canal por corda**. Este modulo le a distribuicao de notas
por canal em cada SMF track — o dado bruto de onde qualquer inferencia
de afinacao vai partir em rodadas seguintes.

Le o arquivo com `mido`, porque `pretty_midi.Instrument` funde notas
por (channel, program) e perde a nocao de SMF track. O que importa aqui
e a track fisica exportada pela DAW, e cada canal dentro dela.

Escopo:
  - US-001: distribuicao por canal (`channel_distribution`).
  - US-002: as tres travas que impedem inferir afinacao onde nao ha corda
    (`tuning_inference`).
  - US-003: a partir dos minimos dos canais confiaveis, calcular os intervalos
    entre cordas adjacentes e classificar a afinacao contra o manual
    `guitar.drop_tuning` (padrao vs drop). A tabela vem do indice de tecnicas
    — NUNCA e hardcoded aqui.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import mido

# ---------------------------------------------------------------------------
# US-002 — as tres travas
#
# TRAVA 1: o instrumento precisa ser de corda dedilhada. Aceita evidencia
# pelo nome da track, pelo patch General MIDI (program change) OU por
# declaracao explicita do usuario. Sem nenhuma das tres, NAO infere — e
# assim que se evita o modelo inventar afinacao para linha de voz.
#
# TRAVA 2: canal com contagem de notas abaixo do limiar tem minimo que e
# nota casada, nao corda solta. O limiar e constante nomeada e
# documentada — nunca numero solto no meio do codigo.
#
# TRAVA 3: span por canal como sanidade. Corda vai de 0 a cerca de 24
# casas (dois oitavos); span maior desmente a hipotese de canal-igual-corda.
# ---------------------------------------------------------------------------

MIN_NOTES_PER_CHANNEL_FOR_INFERENCE = 8
"""TRAVA 2 — canal precisa ter no minimo este numero de notas para entrar
na inferencia de afinacao. Meia duzia de notas (=6) e territorio de nota
casada; oito ja indica uso repetido de uma corda solta candidata."""

MAX_STRING_SPAN_SEMITONES = 24
"""TRAVA 3 — span maximo aceito por canal candidato, em semitons. Duas
oitavas cobrem a extensao pratica de uma corda em guitarra/baixo de 22-24
casas. Canal com span acima disso nao e uma corda so."""

# General MIDI — programa 0-indexado como aparece no `mido.Message.program`.
# Faixa 24-31: guitarra dedilhada (nylon, steel, jazz, clean, muted,
# overdriven, distortion, harmonics).
GM_GUITAR_PROGRAMS = frozenset(range(24, 32))

# Faixa 32-39: baixo (acustico, eletrico dedo, eletrico palheta, fretless,
# slap 1, slap 2, synth 1, synth 2). Todos sao instrumentos de corda com a
# mesma convencao "um canal por corda" no export Guitar Pro / Songsterr.
GM_BASS_PROGRAMS = frozenset(range(32, 40))

GM_STRINGED_PROGRAMS = GM_GUITAR_PROGRAMS | GM_BASS_PROGRAMS
"""Uniao dos programas GM tratados como corda dedilhada para efeito da
TRAVA 1. Sopros, teclados, drums e strings orquestrais ficam de fora."""

_STRINGED_NAME_HINTS = ("guitar", "bass", "guitarra", "baixo")

DISCARD_LOW_NOTE_COUNT = "low_note_count"
"""Motivo de descarte pelo limiar da TRAVA 2."""

DISCARD_SPAN_TOO_WIDE = "span_too_wide"
"""Motivo de descarte pelo teto da TRAVA 3."""

NOT_STRINGED = "not_stringed"
"""Motivo de descarte no nivel da track (TRAVA 1): nenhuma das tres
evidencias de instrumento de corda."""

STRINGED_SOURCE_NAME = "track_name"
STRINGED_SOURCE_GM_PROGRAM = "gm_program"
STRINGED_SOURCE_DECLARED = "declared"

# ---------------------------------------------------------------------------
# US-003 — classificacao da afinacao
#
# O 7 na base e a assinatura do drop: `[7, 5, 5, 4, 5]` bate com Drop D,
# Drop C, Drop B... etc (a corda mais grave desce um tom inteiro em relacao
# a padrao). Ja `[5, 5, 5, 4, 5]` e a assinatura da afinacao padrao (E, D,
# meio-tons a baixo). A tabela concreta com todos os nomes e as MIDI das
# cordas soltas vem do manual `guitar.drop_tuning`, lida pelo indice de
# tecnicas — nao hardcode aqui, para nao termos dois lugares para atualizar.
# ---------------------------------------------------------------------------

TUNING_CLASS_DROP = "drop"
TUNING_CLASS_STANDARD = "standard"
TUNING_CLASS_UNKNOWN = "unknown"

_PITCH_CLASS_NAMES = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
)

_TUNING_PATTERNS_CACHE: tuple[
    frozenset[tuple[int, ...]], frozenset[tuple[int, ...]],
] | None = None


class TuningKnowledgeError(RuntimeError):
    """Manual de afinacoes ausente ou incompleto — sem tabela, nao classifica."""


@dataclass(frozen=True)
class ChannelStats:
    """Distribuicao de uma corda-candidata dentro de uma SMF track.

    - `channel`: 0-15, o numero do canal MIDI.
    - `note_count`: quantas notas foram disparadas nesse canal.
    - `pitch_min` / `pitch_max`: menor e maior nota vista.
    - `span`: `pitch_max - pitch_min`, em semitons.
    - `percentage`: fracao das notas da track que caem nesse canal,
      em porcentagem (0.0-100.0). A soma dentro de uma track e 100.0.
    """
    channel: int
    note_count: int
    pitch_min: int
    pitch_max: int
    span: int
    percentage: float


@dataclass(frozen=True)
class TrackChannelDistribution:
    """Distribuicao de canais de uma SMF track (arquivo bruto)."""
    track_index: int
    track_name: str
    channels: tuple[ChannelStats, ...]


def _track_name(track: mido.MidiTrack, index: int) -> str:
    """Nome estavel para uma SMF track. Cai em `Track {index}` quando o
    arquivo nao declarou meta `track_name` ou o valor veio vazio."""
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            name = (msg.name or "").strip()
            if name:
                return name
            break
    return f"Track {index}"


def _iter_note_ons(track: mido.MidiTrack):
    """Itera apenas note_on de fato (velocity > 0). `note_on vel=0` e o
    `note_off` embutido do MIDI running status e nao inicia nota."""
    for msg in track:
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            yield msg


def channel_distribution(midi_path: str) -> list[TrackChannelDistribution]:
    """Distribuicao de notas por canal, por SMF track, em `midi_path`.

    Ordem estavel: tracks na ordem em que aparecem no arquivo; canais
    dentro de uma track ordenados por numero de canal ascendente. Track
    sem nenhuma nota nao entra no resultado — a secao de distribuicao
    reporta apenas o que existe.

    Track com todas as notas num canal so devolve uma unica entrada de
    canal, sem erro.
    """
    mid = mido.MidiFile(midi_path)

    result: list[TrackChannelDistribution] = []
    for idx, track in enumerate(mid.tracks):
        per_channel: dict[int, list[int]] = {}
        for msg in _iter_note_ons(track):
            per_channel.setdefault(msg.channel, []).append(msg.note)
        if not per_channel:
            continue

        total = sum(len(pitches) for pitches in per_channel.values())
        stats: list[ChannelStats] = []
        for ch in sorted(per_channel):
            pitches = per_channel[ch]
            lo = min(pitches)
            hi = max(pitches)
            stats.append(ChannelStats(
                channel=int(ch),
                note_count=len(pitches),
                pitch_min=int(lo),
                pitch_max=int(hi),
                span=int(hi - lo),
                percentage=100.0 * len(pitches) / total,
            ))
        result.append(TrackChannelDistribution(
            track_index=idx,
            track_name=_track_name(track, idx),
            channels=tuple(stats),
        ))

    return result


@dataclass(frozen=True)
class DiscardedChannel:
    """Canal descartado pela inferencia de afinacao — motivo declarado.

    Nao sai do relatorio em silencio: o motivo aparece sempre. O
    vocabulario de motivos e fechado (`DISCARD_*`).
    """
    channel: int
    reason: str
    note_count: int
    span: int


def _load_tuning_patterns() -> tuple[
    frozenset[tuple[int, ...]], frozenset[tuple[int, ...]],
]:
    """Constroi, uma vez, os conjuntos de padroes de drop e padrao a partir
    de `guitar.drop_tuning.tools.generic.afinacoes`. Nome contendo "drop"
    vira padrao de drop; qualquer outro vira padrao. Isso preserva a fonte
    unica de verdade (o manual) e nao duplica a tabela em Python."""
    global _TUNING_PATTERNS_CACHE
    if _TUNING_PATTERNS_CACHE is not None:
        return _TUNING_PATTERNS_CACHE

    from .techniques import build_index

    idx = build_index()
    tech = idx.get("guitar.drop_tuning")
    if tech is None:
        raise TuningKnowledgeError(
            "tecnica `guitar.drop_tuning` nao encontrada no indice de tecnicas",
        )
    generic = tech.tools.get("generic") or {}
    afinacoes = generic.get("afinacoes") or {}
    if not isinstance(afinacoes, dict) or not afinacoes:
        raise TuningKnowledgeError(
            "guitar.drop_tuning.tools.generic.afinacoes ausente ou vazio",
        )

    drop_patterns: set[tuple[int, ...]] = set()
    standard_patterns: set[tuple[int, ...]] = set()
    for name, midis in afinacoes.items():
        if not isinstance(midis, list) or len(midis) < 2:
            continue
        intervals = tuple(int(midis[i + 1] - midis[i]) for i in range(len(midis) - 1))
        if "drop" in name:
            drop_patterns.add(intervals)
        else:
            standard_patterns.add(intervals)

    _TUNING_PATTERNS_CACHE = (frozenset(drop_patterns), frozenset(standard_patterns))
    return _TUNING_PATTERNS_CACHE


def _is_prefix_of_any(
    observed: tuple[int, ...],
    patterns: frozenset[tuple[int, ...]],
) -> bool:
    """`observed` casa quando e prefixo (ou igual) de algum padrao conhecido.
    Isso e o que permite classificar drop a partir das 3 cordas graves
    quando o riff nao usa as agudas."""
    if not observed:
        return False
    obs_len = len(observed)
    return any(
        obs_len <= len(p) and p[:obs_len] == observed
        for p in patterns
    )


def _classify_tuning(intervals: tuple[int, ...]) -> str:
    """Devolve `drop`, `standard` ou `unknown`. Drop tem prioridade porque
    a assinatura `7` na base e disjunta da assinatura `5` do padrao — nao
    ha ambiguidade real, so consistencia de ordem."""
    if not intervals:
        return TUNING_CLASS_UNKNOWN
    drop_patterns, standard_patterns = _load_tuning_patterns()
    if _is_prefix_of_any(intervals, drop_patterns):
        return TUNING_CLASS_DROP
    if _is_prefix_of_any(intervals, standard_patterns):
        return TUNING_CLASS_STANDARD
    return TUNING_CLASS_UNKNOWN


def _pitch_class_name(midi: int) -> str:
    return _PITCH_CLASS_NAMES[midi % 12]


def _tuning_name(cls: str, lowest_pitch: int | None) -> str | None:
    """Nome da afinacao a partir da corda mais grave. Ex.: drop + MIDI 32
    (G#1) => `Drop G#`. Fora dos padroes conhecidos, retorna None — nome
    de afinacao NUNCA acompanha `unknown`."""
    if lowest_pitch is None:
        return None
    if cls == TUNING_CLASS_DROP:
        return f"Drop {_pitch_class_name(lowest_pitch)}"
    if cls == TUNING_CLASS_STANDARD:
        return f"Standard {_pitch_class_name(lowest_pitch)}"
    return None


@dataclass(frozen=True)
class TrackTuningInference:
    """Resultado da TRAVA 1 + TRAVA 2 + TRAVA 3 para uma SMF track.

    - `is_stringed`: True quando ha evidencia de instrumento de corda.
    - `stringed_source`: origem da evidencia (`track_name`, `gm_program`
      ou `declared`); None quando a track nao passa na TRAVA 1.
    - `gm_programs`: programas GM observados na track, em ordem crescente.
      Existe para o relatorio expor por que a TRAVA 1 disparou (ou nao).
    - `candidate_channels`: canais que sobraram apos aplicar as tres travas.
    - `discarded_channels`: canais que caiam nas travas 2 ou 3 e o motivo.
      Track que nao passou na TRAVA 1 tem `candidate_channels` vazio e
      NENHUM `DiscardedChannel` — o descarte da TRAVA 1 e da track inteira,
      exposto por `is_stringed=False` e `discard_reason='not_stringed'`.
    - `discard_reason`: None quando a track passa na TRAVA 1;
      `NOT_STRINGED` quando nao passa. Explicita o "nao infere" para o
      leitor do relatorio.
    - `tuning_intervals`: intervalos em semitons entre minimos de canais
      confiaveis, ordenados do grave para o agudo. Vazio quando nao ha
      candidato suficiente para dois minimos (< 2 canais).
    - `tuning_class`: `drop`, `standard` ou `unknown` (US-003). Sempre
      preenchido — a classificacao e feita sobre `tuning_intervals`.
    - `tuning_name`: nome derivado da corda mais grave (`Drop G#`,
      `Standard E`). None quando `tuning_class == unknown`.
    - `lowest_string_pitch`: MIDI da corda solta mais grave detectada
      (min pitch do canal mais grave em `candidate_channels`). None
      quando nao ha canal confiavel.
    """
    track_index: int
    track_name: str
    is_stringed: bool
    stringed_source: str | None
    gm_programs: tuple[int, ...]
    candidate_channels: tuple[ChannelStats, ...]
    discarded_channels: tuple[DiscardedChannel, ...]
    discard_reason: str | None
    tuning_intervals: tuple[int, ...] = ()
    tuning_class: str = TUNING_CLASS_UNKNOWN
    tuning_name: str | None = None
    lowest_string_pitch: int | None = None


def _iter_track_programs(track: mido.MidiTrack) -> list[int]:
    """Programas GM (0-127) declarados em `program_change` na track, em ordem
    de aparicao. Track sem `program_change` volta lista vazia — para essas,
    a TRAVA 1 tem que se apoiar em nome ou declaracao explicita."""
    programs: list[int] = []
    for msg in track:
        if msg.is_meta:
            continue
        if msg.type == "program_change":
            programs.append(int(msg.program))
    return programs


def _name_hints_stringed(track_name: str) -> bool:
    """Nome da track sugere instrumento de corda. Case-insensitive,
    tolerante a portugues (`guitarra`, `baixo`) e ingles."""
    lower = track_name.lower()
    return any(hint in lower for hint in _STRINGED_NAME_HINTS)


def _classify_stringed(
    track_name: str,
    programs: Iterable[int],
    declared_names: frozenset[str],
) -> tuple[bool, str | None]:
    """TRAVA 1. Devolve (is_stringed, source_or_None).

    Precedencia: declaracao explicita > patch GM > nome da track. A ordem
    espelha a confianca da evidencia — declaracao vem do usuario, patch e
    metadado de exportacao, nome da track e o mais fraco (nomes podem ser
    apelidos de mixer)."""
    if track_name in declared_names:
        return True, STRINGED_SOURCE_DECLARED
    for prog in programs:
        if prog in GM_STRINGED_PROGRAMS:
            return True, STRINGED_SOURCE_GM_PROGRAM
    if _name_hints_stringed(track_name):
        return True, STRINGED_SOURCE_NAME
    return False, None


def _apply_channel_locks(
    channels: Iterable[ChannelStats],
) -> tuple[tuple[ChannelStats, ...], tuple[DiscardedChannel, ...]]:
    """TRAVA 2 + TRAVA 3 sobre uma sequencia de `ChannelStats`. Devolve os
    canais que passaram e os que caiam, com o motivo. A ordem de teste e
    contagem antes de span — canal com poucas notas nem entra na aritmetica
    de span como candidato a corda."""
    candidates: list[ChannelStats] = []
    discarded: list[DiscardedChannel] = []
    for stat in channels:
        if stat.note_count < MIN_NOTES_PER_CHANNEL_FOR_INFERENCE:
            discarded.append(DiscardedChannel(
                channel=stat.channel,
                reason=DISCARD_LOW_NOTE_COUNT,
                note_count=stat.note_count,
                span=stat.span,
            ))
            continue
        if stat.span > MAX_STRING_SPAN_SEMITONES:
            discarded.append(DiscardedChannel(
                channel=stat.channel,
                reason=DISCARD_SPAN_TOO_WIDE,
                note_count=stat.note_count,
                span=stat.span,
            ))
            continue
        candidates.append(stat)
    return tuple(candidates), tuple(discarded)


def tuning_inference(
    midi_path: str,
    declared_stringed_tracks: Iterable[str] | None = None,
) -> list[TrackTuningInference]:
    """Aplica as tres travas de US-002 e devolve, por SMF track, quais
    canais podem entrar na inferencia de afinacao (rodadas seguintes).

    - `declared_stringed_tracks`: nomes de tracks que o usuario declarou
      explicitamente como de corda. Tem precedencia sobre patch e nome.
      Casamento e por nome exato (mesma convencao de `plan.edits.track`).

    Tracks sem notas nao entram no resultado — espelha `channel_distribution`.
    """
    declared = frozenset(declared_stringed_tracks or ())
    mid = mido.MidiFile(midi_path)

    result: list[TrackTuningInference] = []
    for idx, track in enumerate(mid.tracks):
        per_channel: dict[int, list[int]] = {}
        for msg in _iter_note_ons(track):
            per_channel.setdefault(msg.channel, []).append(msg.note)
        if not per_channel:
            continue

        total = sum(len(pitches) for pitches in per_channel.values())
        all_stats: list[ChannelStats] = []
        for ch in sorted(per_channel):
            pitches = per_channel[ch]
            lo, hi = min(pitches), max(pitches)
            all_stats.append(ChannelStats(
                channel=int(ch),
                note_count=len(pitches),
                pitch_min=int(lo),
                pitch_max=int(hi),
                span=int(hi - lo),
                percentage=100.0 * len(pitches) / total,
            ))

        name = _track_name(track, idx)
        programs = _iter_track_programs(track)
        is_stringed, source = _classify_stringed(name, programs, declared)

        if not is_stringed:
            result.append(TrackTuningInference(
                track_index=idx,
                track_name=name,
                is_stringed=False,
                stringed_source=None,
                gm_programs=tuple(sorted(set(programs))),
                candidate_channels=(),
                discarded_channels=(),
                discard_reason=NOT_STRINGED,
            ))
            continue

        candidates, discarded = _apply_channel_locks(all_stats)

        # US-003: ordenar candidatos pelo minimo (do grave para o agudo),
        # calcular intervalos e classificar.
        by_pitch = sorted(candidates, key=lambda c: c.pitch_min)
        intervals = tuple(
            by_pitch[i + 1].pitch_min - by_pitch[i].pitch_min
            for i in range(len(by_pitch) - 1)
        )
        tuning_class = _classify_tuning(intervals)
        lowest_pitch = by_pitch[0].pitch_min if by_pitch else None
        tuning_name = _tuning_name(tuning_class, lowest_pitch)

        result.append(TrackTuningInference(
            track_index=idx,
            track_name=name,
            is_stringed=True,
            stringed_source=source,
            gm_programs=tuple(sorted(set(programs))),
            candidate_channels=candidates,
            discarded_channels=discarded,
            discard_reason=None,
            tuning_intervals=intervals,
            tuning_class=tuning_class,
            tuning_name=tuning_name,
            lowest_string_pitch=lowest_pitch,
        ))

    return result


__all__ = [
    "DISCARD_LOW_NOTE_COUNT",
    "DISCARD_SPAN_TOO_WIDE",
    "GM_BASS_PROGRAMS",
    "GM_GUITAR_PROGRAMS",
    "GM_STRINGED_PROGRAMS",
    "MAX_STRING_SPAN_SEMITONES",
    "MIN_NOTES_PER_CHANNEL_FOR_INFERENCE",
    "NOT_STRINGED",
    "STRINGED_SOURCE_DECLARED",
    "STRINGED_SOURCE_GM_PROGRAM",
    "STRINGED_SOURCE_NAME",
    "TUNING_CLASS_DROP",
    "TUNING_CLASS_STANDARD",
    "TUNING_CLASS_UNKNOWN",
    "ChannelStats",
    "DiscardedChannel",
    "TrackChannelDistribution",
    "TrackTuningInference",
    "TuningKnowledgeError",
    "channel_distribution",
    "tuning_inference",
]
