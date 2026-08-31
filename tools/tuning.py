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
import unicodedata
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

# Faixa 32-37: baixo de corda (acustico, eletrico dedo, eletrico palheta,
# fretless, slap 1, slap 2). Mesma convencao "um canal por corda" no export
# Guitar Pro / Songsterr.
#
# GM 38/39 (`Synth Bass 1/2`) ficam DE FORA de proposito: sintetizador nao
# tem corda, entao nao tem afinacao de corda para inferir. Manter 38/39 aqui
# contradiria `_BASS_DISQUALIFIERS`, que ja tira `Bass Synth` do casamento
# por nome — o patch nao pode autorizar o que o nome proibe.
GM_BASS_PROGRAMS = frozenset(range(32, 38))

GM_STRINGED_PROGRAMS = GM_GUITAR_PROGRAMS | GM_BASS_PROGRAMS
"""Uniao dos programas GM tratados como corda dedilhada para efeito da
TRAVA 1. Sopros, teclados, drums e strings orquestrais ficam de fora."""

_STRINGED_NAME_HINTS = ("guitar", "bass", "guitarra", "baixo")

# Palavras que, vindo logo DEPOIS de `bass` como proxima palavra do nome,
# tiram a track de corda. Convencao: `bass` sozinho ou junto de um
# qualificador de corda/baixo (`Bass Guitar`, `bass 2`, `Electric Bass`)
# e corda; `bass` seguido de instrumento de sopro (clarinet, trombone,
# flute, sax/saxophone, tuba, oboe, bassoon), percussao (drum), voz
# (choir, voice) ou sintetizador (synth) NAO e — `Bass Clarinet` e
# clarone, `Bass Drum` e bumbo, `Bass Synth` e sintetizador com timbre
# grave. Sem essa lista, o casamento por palavra ainda deixava passar
# `Bass Clarinet` como corda porque `bass` bate como palavra completa.
_BASS_DISQUALIFIERS = frozenset({
    "clarinet",
    "trombone",
    "flute",
    "drum",
    "sax",
    "saxophone",
    "tuba",
    "oboe",
    "bassoon",
    "choir",
    "voice",
    "synth",
})

# Separadores tratados como fronteira de palavra em nomes de track.
# DAW sanitiza espacos trocando por `_`, `-` ou `.` no export (Guitar Pro,
# Songsterr, Reaper); sem tratar esses caracteres, `Guitar_1` nunca
# casaria porque `\b` do regex considera `_` como caractere de palavra.
_NAME_TOKEN_SPLIT = re.compile(r"[\s_\-.]+")

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

MIN_INTERVALS_FOR_CLASSIFICATION = 3
"""Numero minimo de intervalos observados para classificar por prefixo. Dois
intervalos como `[5, 5]` cabem tanto no inicio de uma afinacao padrao quanto
nas cordas de cima de um drop (onde a mais grave foi discardada) — sem um
prefixo maior nao da para desambiguar, e o detector devolve `unknown` em vez
de chutar. A unica excecao e o prefixo com a assinatura de drop na base
(interval `7` na posicao 0): esse valor nao aparece em afinacao padrao
nenhuma, entao mesmo com dois intervalos ja classifica como drop. Ver
`DROP_SIGNATURE_INTERVAL`."""

DROP_SIGNATURE_INTERVAL = 7
"""Primeiro intervalo (grave -> agudo) de toda afinacao drop conhecida:
a corda mais grave desce um tom inteiro em relacao a padrao. Nenhuma
afinacao padrao comeca com `7`, entao esse valor no inicio do prefixo e
assinatura suficiente para classificar como drop sem exigir o
`MIN_INTERVALS_FOR_CLASSIFICATION` completo."""

_PITCH_CLASS_NAMES = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
)

_TUNING_PATTERNS_CACHE: tuple[
    frozenset[tuple[int, ...]], frozenset[tuple[int, ...]],
] | None = None

# ---------------------------------------------------------------------------
# issue #44 — resolver nome de afinacao declarado (brief) para as notas das
# cordas soltas, contra o MESMO manual `guitar.drop_tuning` que
# `_load_tuning_patterns` ja le. Nao existe tabela paralela: cada entrada de
# `tools.generic.afinacoes` vira uma entrada aqui, indexada por
# (numero_de_cordas, nome_canonico) — o nome canonico usa a MESMA convencao
# de `_tuning_name` (`Drop <classe de altura>` / `Standard <classe de
# altura>`), calculada a partir da corda mais grave da entrada. Nome que o
# usuario digitar so resolve quando existe entrada no manual para aquele
# nome E aquele numero de cordas exatos — "Drop G#" para 7 cordas nao
# resolve so porque "Drop G#" existe para 6, porque sao instrumentos
# diferentes com pisos diferentes.
# ---------------------------------------------------------------------------

_NAMED_TUNINGS_CACHE: dict[tuple[int, str], tuple[int, ...]] | None = None

_FLAT_TO_SHARP_LETTER = {
    "cb": "B", "db": "C#", "eb": "D#", "fb": "E", "gb": "F#",
    "ab": "G#", "bb": "A#",
}

_TUNING_NAME_NOTE = r"([a-g])\s*(#|b|s|sharp)?"

_TUNING_NAME_DROP_RE = re.compile(rf"^drop\s+{_TUNING_NAME_NOTE}$")
_TUNING_NAME_STD_PREFIX_RE = re.compile(
    rf"^(?:standard|padrao)\s+{_TUNING_NAME_NOTE}$",
)
_TUNING_NAME_STD_SUFFIX_RE = re.compile(
    rf"^{_TUNING_NAME_NOTE}\s+(?:standard|padrao)$",
)


def _strip_accents(text: str) -> str:
    """Remove acento (NFKD + descarta combining marks). `padrão` -> `padrao`,
    para o parser de nome de afinacao aceitar `E padrão` e `E padrao` igual."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def _canonicalize_note_letter(letter: str, accidental: str | None) -> str | None:
    """`(letra, acidente)` do regex do parser -> nome de classe de altura
    de `_PITCH_CLASS_NAMES`, ou None se nao for uma nota valida."""
    letter = letter.upper()
    if letter not in "ABCDEFG":
        return None
    if not accidental:
        return letter if letter in _PITCH_CLASS_NAMES else None
    if accidental in ("#", "s", "sharp"):
        name = f"{letter}#"
        return name if name in _PITCH_CLASS_NAMES else None
    if accidental == "b":
        return _FLAT_TO_SHARP_LETTER.get(f"{letter.lower()}b")
    return None


def _parse_tuning_name_query(raw: str) -> tuple[str, str] | None:
    """Reconhece `drop <nota>`, `<nota> padrao`/`standard` e
    `padrao`/`standard <nota>` (com ou sem acento, case-insensitive).
    Devolve `(classe, classe_de_altura)` ou None quando o formato nao e
    nenhum dos reconhecidos — nesse caso o chamador NAO deve chutar, so
    pedir as notas das cordas soltas."""
    text = re.sub(r"\s+", " ", _strip_accents(raw).strip().lower())
    if not text:
        return None
    m = _TUNING_NAME_DROP_RE.match(text)
    if m:
        pc = _canonicalize_note_letter(m.group(1), m.group(2))
        return (TUNING_CLASS_DROP, pc) if pc else None
    m = _TUNING_NAME_STD_PREFIX_RE.match(text) or _TUNING_NAME_STD_SUFFIX_RE.match(text)
    if m:
        pc = _canonicalize_note_letter(m.group(1), m.group(2))
        return (TUNING_CLASS_STANDARD, pc) if pc else None
    return None


def _load_named_tunings() -> dict[tuple[int, str], tuple[int, ...]]:
    """Constroi, uma vez, `(numero_de_cordas, nome_canonico) -> midis` a
    partir de `guitar.drop_tuning.tools.generic.afinacoes` — a mesma fonte
    de `_load_tuning_patterns`, so que preservando os MIDIs (nao so os
    intervalos) e o numero de cordas de cada entrada."""
    global _NAMED_TUNINGS_CACHE
    if _NAMED_TUNINGS_CACHE is not None:
        return _NAMED_TUNINGS_CACHE

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

    result: dict[tuple[int, str], tuple[int, ...]] = {}
    for name, midis in afinacoes.items():
        if not isinstance(midis, list) or len(midis) < 2:
            continue
        midis_t = tuple(int(m) for m in midis)
        cls = TUNING_CLASS_DROP if "drop" in name else TUNING_CLASS_STANDARD
        display = _tuning_name(cls, midis_t[0])
        if display is None:
            continue
        result[(len(midis_t), display)] = midis_t

    _NAMED_TUNINGS_CACHE = result
    return result


def resolve_tuning_name(name: str, strings: int) -> tuple[int, ...] | None:
    """Resolve um nome de afinacao declarado (`Drop G#`, `E padrão`,
    `standard E`...) para as notas MIDI das cordas soltas, grave -> agudo,
    contra o manual `guitar.drop_tuning` — NUNCA uma tabela paralela.

    Devolve `None` quando o nome nao casa com um formato reconhecido OU
    quando o manual nao tem entrada para aquela classe/nota E aquele
    numero de cordas exatos. Nos dois casos o chamador (brief_schema, a
    skill) tem que pedir as notas das cordas soltas explicitamente — nome
    desconhecido nunca vira chute.

    O manual `guitar.drop_tuning` so documenta afinacoes de guitarra (6,
    7 e 8 cordas) — nao tem entrada para baixo de 4/5 cordas. Chamar isto
    para baixo por nome, portanto, sempre devolve `None` HOJE: e o
    comportamento correto (pede as notas), nao um bug — inventar uma
    conversao "guitarra menos uma oitava" seria uma tabela hardcoded
    disfarcada. Se um manual de afinacao de baixo for adicionado depois,
    esta funcao passa a enxergar as entradas dele automaticamente, sem
    mudanca de codigo.
    """
    parsed = _parse_tuning_name_query(name)
    if parsed is None:
        return None
    cls, pitch_class = parsed
    display = f"{'Drop' if cls == TUNING_CLASS_DROP else 'Standard'} {pitch_class}"
    named = _load_named_tunings()
    return named.get((strings, display))


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


def fallback_track_name(index: int) -> str:
    """Fallback estavel de nome de track sem meta `track_name`.

    Formato unico usado pela fachada `analyze` e pela inferencia de afinacao —
    para o usuario declarar em `declared_stringed_tracks` exatamente o mesmo
    texto que ve no relatorio. Convive com nomes reais preservados; so entra
    quando o MIDI nao trouxe (ou trouxe vazio) o meta `track_name`.

    `index` e a POSICAO na lista filtrada de tracks-com-notas, nao o indice
    bruto do SMF. Isso mantem `tracks[i].name` e `tuning_inference[i].track_name`
    alinhados mesmo em Format 1, em que `mido` enxerga o tempo track vazio na
    posicao 0 e `pretty_midi` nao."""
    return f"Track {index}"


def _track_name(track: mido.MidiTrack, position: int) -> str:
    """Nome estavel para uma SMF track. Preserva o meta `track_name` quando
    presente; cai em `fallback_track_name(position)` quando ausente ou vazio.

    `position` e a posicao na lista de tracks-com-notas, nao o indice bruto do
    SMF — ver docstring de `fallback_track_name` para o motivo."""
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            name = (msg.name or "").strip()
            if name:
                return name
            break
    return fallback_track_name(position)


def _normalize_declared_name(name: str) -> str:
    """Normaliza nome declarado em `declared_stringed_tracks` para casamento
    tolerante: `strip` nas pontas e `casefold` para caixa. O usuario declara
    o texto que ve no relatorio; um espaco extra ou caixa trocada nao pode
    quebrar o casamento e derrubar a inferencia inteira."""
    return name.strip().casefold()


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
    position = 0
    for idx, track in enumerate(mid.tracks):
        stats = _channel_stats_from_track(track)
        if not stats:
            continue
        result.append(TrackChannelDistribution(
            track_index=idx,
            track_name=_track_name(track, position),
            channels=tuple(stats),
        ))
        position += 1

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
    """Devolve `drop`, `standard` ou `unknown`.

    Regras:
      - Prefixo que casa com mais de UMA CLASSE (drop e standard ao mesmo
        tempo) -> `unknown`: nao da para decidir sem chutar. Hoje as duas
        classes sao disjuntas pelo primeiro intervalo (drop = 7, standard
        = 5), mas a checagem existe como trava estrutural para o dia em que
        o manual crescer.
      - Prefixo curto (`< MIN_INTERVALS_FOR_CLASSIFICATION`) so classifica
        quando carrega a assinatura de drop no inicio (`DROP_SIGNATURE_INTERVAL`).
        Sem essa assinatura, `[5, 5]` sozinho cabe tanto no inicio de uma
        afinacao padrao quanto nas cordas de cima de um drop com a mais
        grave descartada — e ambiguo, entao devolve `unknown`.
      - Com `MIN_INTERVALS_FOR_CLASSIFICATION` intervalos ou mais, o prefixo
        que casa com uma unica classe classifica como aquela classe.
    """
    if not intervals:
        return TUNING_CLASS_UNKNOWN
    drop_patterns, standard_patterns = _load_tuning_patterns()
    matches_drop = _is_prefix_of_any(intervals, drop_patterns)
    matches_standard = _is_prefix_of_any(intervals, standard_patterns)
    if matches_drop and matches_standard:
        return TUNING_CLASS_UNKNOWN
    if matches_drop:
        if intervals[0] == DROP_SIGNATURE_INTERVAL:
            return TUNING_CLASS_DROP
        if len(intervals) >= MIN_INTERVALS_FOR_CLASSIFICATION:
            return TUNING_CLASS_DROP
        return TUNING_CLASS_UNKNOWN
    if matches_standard:
        if len(intervals) >= MIN_INTERVALS_FOR_CLASSIFICATION:
            return TUNING_CLASS_STANDARD
        return TUNING_CLASS_UNKNOWN
    return TUNING_CLASS_UNKNOWN


def _pitch_class_name(midi: int) -> str:
    return _PITCH_CLASS_NAMES[midi % 12]


def _classify_confidence(
    tuning_class: str,
    candidate_count: int,
    has_discards: bool = False,
) -> str:
    """US-004 + US-003 (rodada 2). `unknown` sempre que a classe for
    `unknown`; caso contrario `high` quando ha amostra suficiente e
    NENHUM canal foi descartado, `low` quando falta amostra OU quando
    algum canal caiu em qualquer trava.

    O descarte rebaixa a confianca porque a inferencia pode estar
    incompleta: um dos canais descartados podia ser justamente a corda que
    fecharia o padrao (ex.: DropD com a corda grave abaixo do limiar da
    TRAVA 2 sobra `[5, 5, 4, 5]` — visualmente parece padrao, mas nao e).
    Enquanto o descarte estiver sinalizado no relatorio, a confianca nunca
    sobe para `high`."""
    if tuning_class == TUNING_CLASS_UNKNOWN:
        return TUNING_CONFIDENCE_UNKNOWN
    if has_discards:
        return TUNING_CONFIDENCE_LOW
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
    - `gm_programs`: TODOS os programas GM observados na track (historico
      completo de `program_change`, qualquer canal, qualquer momento), em
      ordem crescente — existe para o relatorio expor tudo que a track
      declarou, mesmo o que nunca regeu nota nenhuma.
    - `governing_programs`: SO os programas GM que regem pelo menos uma
      nota (`_governing_programs_by_channel`), em ordem crescente — e o
      que a classificacao de familia (guitarra vs baixo) tem que usar, NUNCA
      `gm_programs` bruto: track com `program_change` de baixo seguido de
      guitarra antes da primeira nota tem `gm_programs=(24,32)` mas
      `governing_programs=(24,)` — so a guitarra soa (achado do Codex no
      PR #64, issue #44).
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
    - `inference_incomplete`: True quando pelo menos um canal foi descartado
      por qualquer motivo (`DISCARD_*`). Sinaliza que a assinatura de
      intervalos observada pode nao ser a assinatura real da afinacao —
      alguma corda ficou de fora. Enquanto essa flag estiver ligada, a
      `confidence` nunca sobe para `high`. Sempre False para tracks que nao
      passam na TRAVA 1 (nao ha candidato pra descartar).
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
    inference_incomplete: bool = False
    governing_programs: tuple[int, ...] = ()


def _iter_track_programs(track: mido.MidiTrack) -> dict[int, list[int]]:
    """Programas GM declarados em `program_change` agrupados por canal.

    `program_change` no MIDI e SEMPRE por canal — o mesmo evento nao afeta
    outros canais da mesma track. Cada canal 0-15 mantem a lista de programas
    observados na ordem em que aparecem. Track sem `program_change` volta
    dict vazio; nesse caso, TRAVA 1 tem que se apoiar em nome ou declaracao
    explicita.

    A agregacao por canal e o que permite a TRAVA 1 exigir que o patch de
    corda governe um canal que REALMENTE tem notas — patch num canal vazio
    nao autoriza inferencia sobre notas de outro canal.

    Esta funcao e so para RELATO (`gm_programs` na saida). A classificacao
    usa `_governing_programs_by_channel`, que respeita a ordem temporal."""
    per_channel: dict[int, list[int]] = {}
    for msg in track:
        if msg.is_meta:
            continue
        if msg.type == "program_change":
            per_channel.setdefault(int(msg.channel), []).append(int(msg.program))
    return per_channel


def _governing_programs_by_channel(
    track: mido.MidiTrack,
) -> dict[int, list[int]]:
    """Programas GM que REGEM pelo menos uma nota, por canal.

    `program_change` vale do ponto em que aparece ate o proximo do mesmo
    canal — quem determina o timbre de uma nota e o patch vigente no
    `note_on`, nao qualquer patch que a track tenha declarado em algum
    momento. Um canal com `program_change 30` (guitarra) seguido de
    `program_change 73` (flauta) ANTES de qualquer nota toca flauta; olhar a
    lista historica com `any()` classificaria a track como corda e inventaria
    afinacao.

    Nota antes de qualquer `program_change` no canal nao tem patch declarado
    e nao contribui programa nenhum — o canal cai no fallback por nome.
    Programa declarado depois da ultima nota do canal tambem nao entra: nao
    rege nota alguma."""
    current: dict[int, int] = {}
    governing: dict[int, list[int]] = {}
    for msg in track:
        if msg.is_meta:
            continue
        channel = int(getattr(msg, "channel", -1))
        if channel < 0:
            continue
        if msg.type == "program_change":
            current[channel] = int(msg.program)
        elif msg.type == "note_on" and msg.velocity > 0:
            program = current.get(channel)
            if program is None:
                continue
            seen = governing.setdefault(channel, [])
            if program not in seen:
                seen.append(program)
    return governing


def _tokenize_track_name(track_name: str) -> list[str]:
    """Tokeniza nome de track em palavras minusculas, tratando `_`, `-` e
    `.` como separador alem de whitespace. Ver `_NAME_TOKEN_SPLIT` para a
    razao (DAW sanitiza espaco por esses caracteres)."""
    return [tok.lower() for tok in _NAME_TOKEN_SPLIT.split(track_name) if tok]


def _matched_name_hint(track_name: str) -> str | None:
    """Palavra de `_STRINGED_NAME_HINTS` que casa por PALAVRA no nome da
    track (case-insensitive), ou None.

    Fronteira de palavra e o que impede `Bassoon`, `Brass` ou
    `Contrabassoon` virarem `bass` por substring solta; os separadores
    aceitos incluem whitespace, `_`, `-` e `.` (DAW troca espaco por
    esses no export).

    `bass` seguido imediatamente de qualificador de sopro, percussao,
    voz ou synth (`_BASS_DISQUALIFIERS`) NAO casa: `Bass Clarinet` e
    clarone, `Bass Drum` e bumbo, `Bass Synth` e sintetizador. Outros
    hints (`guitar`, `guitarra`, `baixo`) casam direto — nao ha
    ambiguidade equivalente para eles."""
    tokens = _tokenize_track_name(track_name)
    for i, tok in enumerate(tokens):
        if tok not in _STRINGED_NAME_HINTS:
            continue
        if tok == "bass":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt in _BASS_DISQUALIFIERS:
                continue
        return tok
    return None


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
    (fallback `STRINGED_SOURCE_NAME`).

    `programs_by_channel` aqui e o mapa de programas que REGEM nota
    (`_governing_programs_by_channel`), nao a lista historica: patch trocado
    antes da primeira nota do canal nao vale como evidencia de corda. Canal
    regido por um patch de corda E um de nao-corda e ambiguo e fica de fora
    da inferencia — nao da para dizer qual afinacao descreve as notas."""
    if _normalize_declared_name(track_name) in declared_names:
        return True, STRINGED_SOURCE_DECLARED, None, None, None
    active_programs_by_channel = {
        ch: progs for ch, progs in programs_by_channel.items()
        if ch in channels_with_notes
    }
    stringed_channels = frozenset(
        ch for ch, progs in active_programs_by_channel.items()
        if progs and all(p in GM_STRINGED_PROGRAMS for p in progs)
    )
    non_stringed_only_channels = frozenset(
        ch for ch, progs in active_programs_by_channel.items()
        if progs and not all(p in GM_STRINGED_PROGRAMS for p in progs)
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
    declared = frozenset(
        _normalize_declared_name(n) for n in (declared_stringed_tracks or ())
    )
    mid = mido.MidiFile(midi_path)

    result: list[TrackTuningInference] = []
    position = 0
    for idx, track in enumerate(mid.tracks):
        all_stats = _channel_stats_from_track(track)
        if not all_stats:
            continue

        name = _track_name(track, position)
        position += 1
        # `gm_programs` na saida relata TUDO que a track declarou; a
        # classificacao usa so o que rege nota. Ver
        # `_governing_programs_by_channel`.
        all_programs = [
            p for progs in _iter_track_programs(track).values() for p in progs
        ]
        governing_by_channel = _governing_programs_by_channel(track)
        governing_programs = tuple(sorted({
            p for progs in governing_by_channel.values() for p in progs
        }))
        channels_with_notes = frozenset(s.channel for s in all_stats)
        is_stringed, source, discard_reason, conflict, gm_channels = (
            _classify_stringed(
                name, governing_by_channel, channels_with_notes, declared,
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
                governing_programs=governing_programs,
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
        confidence = _classify_confidence(
            tuning_class,
            len(candidates),
            has_discards=bool(discarded),
        )

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
            inference_incomplete=bool(discarded),
            governing_programs=governing_programs,
        ))

    return result


def unmatched_declared_tracks(
    inferences: Iterable[TrackTuningInference],
    declared_stringed_tracks: Iterable[str] | None,
) -> tuple[str, ...]:
    """Nomes de `declared_stringed_tracks` que nao casaram com track nenhuma.

    A declaracao explicita e a TRAVA 1 de maior precedencia, mas ela casa por
    NOME. Nome errado — ou nome lido de `analyze.tracks[]`, que e indexado por
    `Instrument` do pretty_midi e nao por SMF track — nao casa com nada e a
    declaracao vira no-op silencioso: o usuario acha que forcou a track para
    corda e a saida continua `unknown`. Quem chama deve virar isso em warning
    visivel.

    Preserva a grafia original do usuario na saida, para o aviso citar
    exatamente o que ele digitou.
    """
    known = {
        _normalize_declared_name(inf.track_name) for inf in inferences
    }
    return tuple(
        str(raw) for raw in (declared_stringed_tracks or ())
        if _normalize_declared_name(str(raw)) not in known
    )


__all__ = [
    "DISCARD_LOW_NOTE_COUNT",
    "DISCARD_NON_STRINGED_PATCH",
    "DISCARD_SPAN_TOO_WIDE",
    "DROP_SIGNATURE_INTERVAL",
    "GM_BASS_PROGRAMS",
    "GM_GUITAR_PROGRAMS",
    "GM_STRINGED_PROGRAMS",
    "MAX_STRING_SPAN_SEMITONES",
    "MIN_CANDIDATES_FOR_HIGH_CONFIDENCE",
    "MIN_INTERVALS_FOR_CLASSIFICATION",
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
    "fallback_track_name",
    "tuning_inference",
    "unmatched_declared_tracks",
]
