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
  - US-005: concentracao de notas por corda (`string_concentrations`) e
    percentual acumulado nas tres cordas mais graves
    (`low_strings_top3_percentage`) — e o dado que diz ONDE o riff mora.
"""

from __future__ import annotations

import re
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

DISCARD_NON_STRINGED_PATCH = "non_stringed_channel_patch"
"""Motivo de descarte da TRAVA 1 por canal: quando a track passa pelo GM
program porque outro canal tem patch de corda, os canais cujo `program_change`
observado nao e de corda ficam de fora da inferencia — mesmo tendo notas
suficientes. Patch nao-corda num canal com notas e evidencia direta de que
aquele canal nao carrega corda solta."""

NOT_STRINGED = "not_stringed"
"""Motivo de descarte no nivel da track (TRAVA 1): nenhuma das tres
evidencias de instrumento de corda."""

NAME_PATCH_CONFLICT = "name_patch_conflict"
"""Motivo de descarte no nivel da track: o nome sugere corda dedilhada
mas o(s) `program_change` observado(s) na track sao patches nao-corda.
O patch VENCE por ser metadado de exportacao — nome de track e apelido
de mixer, o General MIDI e a declaracao do instrumento."""

STRINGED_SOURCE_NAME = "track_name"
STRINGED_SOURCE_GM_PROGRAM = "gm_program"
STRINGED_SOURCE_DECLARED = "declared"

_STRINGED_NAME_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(h) for h in _STRINGED_NAME_HINTS) + r")\b",
    re.IGNORECASE,
)
"""Casamento por PALAVRA (com fronteira), case-insensitive. Evita que
`Bassoon`, `Brass` ou `Contrabassoon` sejam confundidos com `bass` por
substring solta — e o que fazia a inferencia sair errada em tracks de
sopro."""

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

# ---------------------------------------------------------------------------
# US-004 — confianca declarada, nunca maquiada
#
# Vocabulario fechado: `high`, `low`, `unknown`. Regra:
#   - `unknown` sempre que `tuning_class == unknown` (intervalos que nao batem
#     com padrao nenhum, ou nenhum canal candidato) — e AI que o detector
#     admite nao saber, em vez de chutar.
#   - `low` quando ha padrao mas com poucos canais confiaveis (abaixo do
#     limiar): a assinatura pode estar certa mas o riff nao exercitou cordas
#     suficientes para o detector afirmar com peso.
#   - `high` quando ha padrao e o numero de candidatos atinge o limiar.
# Afinacao `unknown` NUNCA vem com `tuning_name` (garantido em `_tuning_name`).
# ---------------------------------------------------------------------------

TUNING_CONFIDENCE_HIGH = "high"
TUNING_CONFIDENCE_LOW = "low"
TUNING_CONFIDENCE_UNKNOWN = "unknown"

MIN_CANDIDATES_FOR_HIGH_CONFIDENCE = 4
"""Confianca `high` exige pelo menos este numero de canais candidatos.
Guitarra de 6 cordas com 4 delas exercitadas ja da amostra suficiente para
firmar a assinatura de intervalos; abaixo disso, a inferencia sai `low`."""

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


def _channel_stats_from_track(track: mido.MidiTrack) -> list[ChannelStats]:
    """Agrupa `note_on` da track por canal e devolve `ChannelStats` ordenado
    por numero de canal. Denominador de `percentage` e o total de notas da
    track, o mesmo numero usado pelo relatorio bruto de `channel_distribution`
    — ponto unico de agrupamento, sem duas implementacoes divergindo."""
    per_channel: dict[int, list[int]] = {}
    for msg in _iter_note_ons(track):
        per_channel.setdefault(msg.channel, []).append(msg.note)
    if not per_channel:
        return []
    total = sum(len(pitches) for pitches in per_channel.values())
    stats: list[ChannelStats] = []
    for ch in sorted(per_channel):
        pitches = per_channel[ch]
        lo, hi = min(pitches), max(pitches)
        stats.append(ChannelStats(
            channel=int(ch),
            note_count=len(pitches),
            pitch_min=int(lo),
            pitch_max=int(hi),
            span=int(hi - lo),
            percentage=100.0 * len(pitches) / total,
        ))
    return stats


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
        stats = _channel_stats_from_track(track)
        if not stats:
            continue
        result.append(TrackChannelDistribution(
            track_index=idx,
            track_name=_track_name(track, idx),
            channels=tuple(stats),
        ))

    return result


@dataclass(frozen=True)
class NamePatchConflict:
    """Track em que o nome sugere corda mas o(s) patch(es) GM contradizem.

    - `hint`: a palavra do nome que casou com um `_STRINGED_NAME_HINTS`
      (ex.: `bass` para uma track chamada `Bass Solo` com patch de bassoon).
    - `programs`: os `program_change` observados na track, em ordem
      crescente. Nenhum deles esta em `GM_STRINGED_PROGRAMS` — se
      estivesse, a track passaria pela evidencia mais forte (`gm_program`)
      e nao haveria conflito.
    """
    hint: str
    programs: tuple[int, ...]


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


def _classify_confidence(tuning_class: str, candidate_count: int) -> str:
    """US-004. `unknown` sempre que a classe for `unknown`; `low` quando a
    amostra de candidatos e magra; `high` quando atinge o limiar."""
    if tuning_class == TUNING_CLASS_UNKNOWN:
        return TUNING_CONFIDENCE_UNKNOWN
    if candidate_count < MIN_CANDIDATES_FOR_HIGH_CONFIDENCE:
        return TUNING_CONFIDENCE_LOW
    return TUNING_CONFIDENCE_HIGH


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
class StringNoteConcentration:
    """US-005 — concentracao de notas de uma corda inferida.

    Uma entrada por canal candidato de uma track de corda. Ordenada do
    grave para o agudo, indexada por `string_index` (0 = corda mais grave
    usada de fato). O `percentage` e o mesmo denominador de
    `ChannelStats.percentage`: fracao das notas da track inteira, para o
    numero comparar direto com o que se vive lendo o relatorio bruto.
    """
    string_index: int
    channel: int
    pitch_min: int
    note_count: int
    percentage: float


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
      `NOT_STRINGED` quando nao passa; `NAME_PATCH_CONFLICT` quando o
      nome sugere corda mas o patch GM contradiz. Explicita o "nao
      infere" para o leitor do relatorio.
    - `name_patch_conflict`: preenchido apenas quando `discard_reason ==
      NAME_PATCH_CONFLICT`, com os dois valores em disputa (a palavra
      do nome e os patches da track).
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
    - `confidence`: vocabulario fechado (`high`, `low`, `unknown`).
      `unknown` toda vez que `tuning_class` for `unknown` — e assim que o
      detector admite nao saber. `low` quando ha padrao mas poucos canais
      candidatos; `high` quando ha padrao e amostra suficiente.
    - `string_concentrations`: US-005. Uma entrada por canal candidato,
      ordenada do grave para o agudo. Vazio quando a track nao passa TRAVA
      1 ou nao tem canal candidato — nesses casos o relatorio nao afirma
      ONDE o riff mora, porque nao sabe.
    - `low_strings_top3_percentage`: soma dos percentuais das ate 3 cordas
      mais graves em `string_concentrations`. `None` quando nao ha nenhuma
      corda candidata; caso contrario e a soma das primeiras `min(3, N)`
      entradas. E o resumo que responde "quanto do riff cai nas graves".
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
    confidence: str = TUNING_CONFIDENCE_UNKNOWN
    string_concentrations: tuple[StringNoteConcentration, ...] = ()
    low_strings_top3_percentage: float | None = None
    name_patch_conflict: NamePatchConflict | None = None


def _iter_track_programs(track: mido.MidiTrack) -> dict[int, list[int]]:
    """Programas GM declarados em `program_change` agrupados por canal.

    `program_change` no MIDI e SEMPRE por canal — o mesmo evento nao afeta
    outros canais da mesma track. Cada canal 0-15 mantem a lista de programas
    observados na ordem em que aparecem. Track sem `program_change` volta
    dict vazio; nesse caso, TRAVA 1 tem que se apoiar em nome ou declaracao
    explicita.

    A agregacao por canal e o que permite a TRAVA 1 exigir que o patch de
    corda governe um canal que REALMENTE tem notas — patch num canal vazio
    nao autoriza inferencia sobre notas de outro canal."""
    per_channel: dict[int, list[int]] = {}
    for msg in track:
        if msg.is_meta:
            continue
        if msg.type == "program_change":
            per_channel.setdefault(int(msg.channel), []).append(int(msg.program))
    return per_channel


def _matched_name_hint(track_name: str) -> str | None:
    """Palavra de `_STRINGED_NAME_HINTS` que casa por FRONTEIRA no nome da
    track (case-insensitive), ou None. Fronteira e o que impede `Bassoon`
    virar `bass` e um fagote virar baixo por acidente."""
    match = _STRINGED_NAME_PATTERN.search(track_name)
    return match.group(1).lower() if match else None


def _classify_stringed(
    track_name: str,
    programs_by_channel: dict[int, list[int]],
    channels_with_notes: frozenset[int],
    declared_names: frozenset[str],
) -> tuple[bool, str | None, str | None, NamePatchConflict | None, frozenset[int] | None]:
    """TRAVA 1. Devolve
    `(is_stringed, source, discard_reason, conflict, gm_channels)`.

    Precedencia: declaracao explicita > patch GM > nome da track. A ordem
    espelha a confianca da evidencia — declaracao vem do usuario, patch e
    metadado de exportacao, nome da track e o mais fraco (nomes podem ser
    apelidos de mixer).

    A evidencia por GM program so vale para canais que REALMENTE tem notas
    na track: `program_change` num canal sem nota nao autoriza inferencia
    sobre notas de outro canal (MIDI trata program change por canal). Para
    TRAVA 1 disparar por GM, pelo menos um canal com notas precisa ter
    patch de corda. Uma vez disparada, os canais que entram na inferencia
    sao todos os que tem notas, EXCETO os que carregam um `program_change`
    proprio explicitamente nao-corda — a convencao Songsterr/Guitar Pro e
    emitir um unico `program_change` por track, e todos os canais herdam
    esse contexto; so canal com patch proprio contradizendo a corda e
    excluido. `gm_channels` devolve o conjunto autorizado; para declarado
    ou nome, `gm_channels` e None, sinalizando "todos os canais entram".

    Quando o nome sugere corda MAS os patches observados em canais com
    notas nao contem nenhum de corda dedilhada, o patch VENCE: a track sai
    como nao-corda com `discard_reason=NAME_PATCH_CONFLICT` e um
    `NamePatchConflict` registrando a palavra do nome e os patches em
    conflito (apenas os observados em canais com notas — patch em canal
    vazio nao entra no conflito, porque nao esta contradizendo nota
    nenhuma). Sem `program_change` em canal com notas, o nome ainda vale
    (fallback `STRINGED_SOURCE_NAME`)."""
    if track_name in declared_names:
        return True, STRINGED_SOURCE_DECLARED, None, None, None
    active_programs_by_channel = {
        ch: progs for ch, progs in programs_by_channel.items()
        if ch in channels_with_notes
    }
    stringed_channels = frozenset(
        ch for ch, progs in active_programs_by_channel.items()
        if any(p in GM_STRINGED_PROGRAMS for p in progs)
    )
    non_stringed_only_channels = frozenset(
        ch for ch, progs in active_programs_by_channel.items()
        if progs and not any(p in GM_STRINGED_PROGRAMS for p in progs)
    )
    if stringed_channels:
        allowed = frozenset(channels_with_notes) - non_stringed_only_channels
        return True, STRINGED_SOURCE_GM_PROGRAM, None, None, allowed
    hint = _matched_name_hint(track_name)
    if hint is not None:
        active_programs = [
            p for progs in active_programs_by_channel.values() for p in progs
        ]
        if active_programs:
            conflict = NamePatchConflict(
                hint=hint,
                programs=tuple(sorted(set(active_programs))),
            )
            return False, None, NAME_PATCH_CONFLICT, conflict, None
        return True, STRINGED_SOURCE_NAME, None, None, None
    return False, None, NOT_STRINGED, None, None


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
        all_stats = _channel_stats_from_track(track)
        if not all_stats:
            continue

        name = _track_name(track, idx)
        programs_by_channel = _iter_track_programs(track)
        all_programs = [
            p for progs in programs_by_channel.values() for p in progs
        ]
        channels_with_notes = frozenset(s.channel for s in all_stats)
        is_stringed, source, discard_reason, conflict, gm_channels = (
            _classify_stringed(
                name, programs_by_channel, channels_with_notes, declared,
            )
        )

        if not is_stringed:
            result.append(TrackTuningInference(
                track_index=idx,
                track_name=name,
                is_stringed=False,
                stringed_source=None,
                gm_programs=tuple(sorted(set(all_programs))),
                candidate_channels=(),
                discarded_channels=(),
                discard_reason=discard_reason,
                name_patch_conflict=conflict,
            ))
            continue

        if gm_channels is not None:
            pre_discarded = tuple(
                DiscardedChannel(
                    channel=stat.channel,
                    reason=DISCARD_NON_STRINGED_PATCH,
                    note_count=stat.note_count,
                    span=stat.span,
                )
                for stat in all_stats
                if stat.channel not in gm_channels
            )
            candidate_stats = [s for s in all_stats if s.channel in gm_channels]
        else:
            pre_discarded = ()
            candidate_stats = list(all_stats)

        candidates, discarded = _apply_channel_locks(candidate_stats)
        discarded = pre_discarded + discarded

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
        confidence = _classify_confidence(tuning_class, len(candidates))

        # US-005 — concentracao por corda, na ordem grave -> agudo. Reusa
        # `by_pitch` porque essa e exatamente a ordem "corda 0 = mais grave"
        # que interessa ao arranjador; o percentual ja vem calculado sobre
        # o total da track (denominador do relatorio bruto).
        concentrations = tuple(
            StringNoteConcentration(
                string_index=i,
                channel=c.channel,
                pitch_min=c.pitch_min,
                note_count=c.note_count,
                percentage=c.percentage,
            )
            for i, c in enumerate(by_pitch)
        )
        top3 = (
            sum(s.percentage for s in concentrations[:3])
            if concentrations else None
        )

        result.append(TrackTuningInference(
            track_index=idx,
            track_name=name,
            is_stringed=True,
            stringed_source=source,
            gm_programs=tuple(sorted(set(all_programs))),
            candidate_channels=candidates,
            discarded_channels=discarded,
            discard_reason=None,
            tuning_intervals=intervals,
            tuning_class=tuning_class,
            tuning_name=tuning_name,
            lowest_string_pitch=lowest_pitch,
            confidence=confidence,
            string_concentrations=concentrations,
            low_strings_top3_percentage=top3,
        ))

    return result


__all__ = [
    "DISCARD_LOW_NOTE_COUNT",
    "DISCARD_NON_STRINGED_PATCH",
    "DISCARD_SPAN_TOO_WIDE",
    "GM_BASS_PROGRAMS",
    "GM_GUITAR_PROGRAMS",
    "GM_STRINGED_PROGRAMS",
    "MAX_STRING_SPAN_SEMITONES",
    "MIN_CANDIDATES_FOR_HIGH_CONFIDENCE",
    "MIN_NOTES_PER_CHANNEL_FOR_INFERENCE",
    "NAME_PATCH_CONFLICT",
    "NOT_STRINGED",
    "STRINGED_SOURCE_DECLARED",
    "STRINGED_SOURCE_GM_PROGRAM",
    "STRINGED_SOURCE_NAME",
    "TUNING_CLASS_DROP",
    "TUNING_CLASS_STANDARD",
    "TUNING_CLASS_UNKNOWN",
    "TUNING_CONFIDENCE_HIGH",
    "TUNING_CONFIDENCE_LOW",
    "TUNING_CONFIDENCE_UNKNOWN",
    "ChannelStats",
    "DiscardedChannel",
    "NamePatchConflict",
    "StringNoteConcentration",
    "TrackChannelDistribution",
    "TrackTuningInference",
    "TuningKnowledgeError",
    "channel_distribution",
    "tuning_inference",
]
