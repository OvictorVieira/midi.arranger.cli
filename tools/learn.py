"""`learn` — mede um corpus de MIDI e devolve um perfil de `style.<familia>`.

Existe para o caminho "no estilo das nossas musicas" da entrevista (issue
#18): em vez da IA pesquisar um artista na web, o usuario entrega MIDIs da
propria banda e esta tool MEDE o corpus, deterministicamente, sem rede e sem
relogio (AGENTS.md "Determinismo nas tools").

## A regra que organiza este modulo

Medir nao e a mesma coisa que ter uma opiniao sobre o que foi medido. Um
corpus de bateria fortemente quantizado/exportado com velocity travada perto
do maximo (o caso real de `tests/fixtures/corpus_drums/`, onde a humanizacao
acontece dentro do Superior Drummer, no estagio de audio, nunca no MIDI) NAO
significa "esta banda toca robotico" — significa que o MIDI de origem nao
carrega a informacao de feel, ponto. Reportar `timing_jitter≈0` e
`ghost_ratio≈0` como se fossem escolha estilistica deliberada seria o mesmo
erro que `_identity_apply` e o gerador de bateria de andaime ja cometeram
neste repositorio (ver AGENTS.md): apresentar ausencia de dado como fato.

Por isso este modulo separa DUAS categorias de dimensao:

- **Estrutural** (vocabulario de articulacao, densidade de virada): o que a
  partitura em si documenta. Mais amostra e mais arquivo aumentam a
  confianca; nao existe nocao de "dado degenerado" — silencio aqui e so
  silencio (corpus pequeno).
- **De feel** (velocity, desvio de grade, proporcao de ghost note,
  autocorrelacao lag-1): o que so existe se a performance carregar variacao
  real. Cada uma tem um teste de DEGENERESCENCIA proprio (grade travada,
  velocity concentrada num unico valor) que, quando acionado, forca
  `confidence="default"` e `measured=False` **independente de quantas
  milhares de notas existam** — quantidade nunca compensa a AUSENCIA de
  informacao. Essa e a unica maneira de a mesma formula que premia "mais
  dado, mais confianca" tambem acertar o caso oposto: dado abundante e
  vazio de conteudo.

`style.<familia>.parameters` (o bloco que de fato entra no plano, ver
`tools/contract.py::_plan_family_style_schema`) SEMPRE sai vazio: nenhuma
tecnica registrada em `tools/techniques/engine.py` declara ou le um
parametro com o nome das dimensoes medidas aqui, e `tools/render.py` so
repassa `style.parameters` para dentro de `context.parameters` de uma
tecnica de dentro do loop `for technique in style.techniques` — que fica
vazio, porque `learn` nao autoriza tecnica nenhuma (issue #18 nao aciona
`authorized_techniques`, ver AGENTS.md). Colocar esses numeros em
`parameters` seria exatamente o "parametro mentiroso" que este repositorio
ja rejeitou (`_identity_apply`): aceito/validado pelo schema, ignorado em
silencio pelo motor. O detalhe completo de toda dimensao — medida ou nao,
com a razao explicita — vive SO em `LearnResult.measurements`, a unica
fonte de verdade para esses numeros (o schema de `style` tambem so aceita
numero escalar ou par `[min, max]`; nao ha onde guardar vocabulario ali de
qualquer forma). `style.<familia>` ainda sai valido para entrar direto em
`plan.style.<familia>` — so nao promete efeito de render que nao existe.

## Derivacao de `confidence` (documentada, deterministica)

Duas formulas, ambas produzindo um escore em `[0, 1]` mapeado para o
vocabulario fechado `high >= 0.7 > medium >= 0.4 > low > 0 == default`
(`STYLE_CONFIDENCE_LEVELS` de `tools/plan.py`):

- `sample_component = min(1, n_amostras / 200)`
- `file_component  = min(1, n_arquivos / 5)`

Estrutural: `score = 0.6*sample_component + 0.4*file_component`.

De feel: primeiro o teste de degenerescencia especifico da dimensao (abaixo).
Se degenerada, `score = 0` e `confidence = "default"` — fim, sem excecao.
Caso contrario, `variance_component = min(1, metrica / alvo)` (metrica e alvo
documentados por dimensao) e `score = 0.4*sample_component +
0.2*file_component + 0.4*variance_component`. Constantes `200`/`5`/os alvos
de variancia sao CONVENCAO deste modulo (nao vem de manual/fonte externa) —
documentadas aqui, nao escondidas dentro da formula.

Testes de degenerescencia por dimensao:

- `velocity`: `mode_ratio` = fracao das notas no valor de velocity mais
  comum. `mode_ratio >= VELOCITY_LOCK_MODE_RATIO` (0.5) significa que mais
  da metade das notas compartilham um UNICO valor exato — nao ha dinamica
  real para medir. E exatamente o padrao do corpus real (65%-100% em
  velocity 127 nos dez arquivos).
- `timing_offset_ms`: `median_offset_ms < TIMING_LOCK_MEDIAN_MS` (2.0 ms).
  Mediana (nao media/desvio) porque um flam ocasional no fim de uma virada
  ja produz cauda longa sem que o groove tenha desvio de grade real — a
  mediana ignora essa cauda e reflete o comportamento tipico.
- `ghost_notes`: ghost note e definida por limiar de velocity
  (`VELOCITY_RANGES["ghost"][1]`); se a dimensao `velocity` esta
  degenerada, a proporcao de ghost herda a mesma degenerescencia — o numero
  calculado existe, mas nao carrega informacao (mesma razao pela qual 0%
  ghost num corpus com velocity travada em 127 nao e "a banda nao usa ghost
  notes").
- `lag1_autocorrelation`: degenerada quando `timing_offset_ms` esta
  degenerada (serie sem variancia real) OU quando a variancia da serie e
  literalmente zero (denominador da formula de autocorrelacao).
"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mido

from .constants import REGISTER_BANDS, VELOCITY_RANGES
from .plan import STYLE_FAMILIES
from .primitives import CYMBALS, HATS_CLOSED, HATS_OPEN, KICKS, SNARES, TOMS
from .techniques._fill_detection import fill_windows
from .techniques._helpers import iter_note_dicts

# --- vocabulario e limites --------------------------------------------------

LEARN_SUPPORTED_FAMILIES: tuple[str, ...] = ("drums",)
"""Familias que `learn` de fato mede nesta rodada. `STYLE_FAMILIES` (bass,
drums, guitar, keys) e o vocabulario aceito pelo INPUT (para o schema nao
travar o agente cedo demais), mas so `drums` tem extracao implementada —
igual ao padrao "documentado no manual, nao implementado no motor" do
AGENTS.md (`bass.slide`/`bass.vibrato`/`bass.harmonic`): pedir uma familia
fora deste tuple e erro explicito (`LearnError`), nunca um perfil vazio
apresentado como medicao real."""

DRUM_CHANNEL = 9
"""Canal MIDI (0-indexado) convencao GM para bateria (canal 10 na notacao
1-indexada) — mesma convencao de
`tools/techniques/_fill_detection.py::_drum_channel_notes`. So um SINAL
entre outros: o corpus real (`tests/fixtures/corpus_drums/`) tem tres dos
dez arquivos com a bateria inteira exportada no canal 0 (export de DAW sem
canal GM dedicado, faixa de bateria unica) — ver `_select_drum_channels`."""

_GM_PERCUSSION_PITCH_MIN = 27
_GM_PERCUSSION_PITCH_MAX = 87
"""Faixa do General MIDI Percussion Key Map (27-87) — mais ampla que os
grupos nomeados de `tools/primitives.py`, para qualificar canal por
CONTEUDO quando o export nao usa o canal 9."""

DRUM_CHANNEL_MIN_NOTES = 20
"""CONVENCAO: canal com menos de 20 `note_on` e ruido demais (CC/click/nota
avulsa) para qualificar como canal de bateria por conteudo."""

DRUM_CHANNEL_PERCUSSION_RATIO = 0.7
"""CONVENCAO: fracao minima de notas do canal dentro da faixa GM de
percussao para o canal qualificar como bateria quando nao e o canal 9. 0.7
(nao 0.9+) porque kits reais tem pecas customizadas fora da faixa GM
padrao (o corpus real tem pitches 20/22 abaixo de 27 — mesmo espirito dos
aliases documentados em `tools/techniques/_fill_detection.py`, kit real do
usuario != GM puro). Sozinho este ratio NAO basta — ver
`DRUM_CHANNEL_TOP_PITCH_CONCENTRATION` — porque a faixa GM de percussao
(27-87) cobre cinco oitavas e se sobrepoe a quase toda a tessitura pratica
de baixo/guitarra/teclas; uma linha melodica escrita nesse registro
tambem cruza 70% com folga."""

DRUM_CHANNEL_TOP_PITCH_CONCENTRATION = 0.5
"""CONVENCAO: fracao minima das notas do canal concentrada nas 3 pitches
mais frequentes, para canal (fora do canal 9) qualificar como bateria. Um
groove real e dominado por kick+snare(+hat) — nos dez arquivos do corpus
real (`tests/fixtures/corpus_drums/`), as 3 pitches mais tocadas cobrem
~65% das notas em todo arquivo com bateria fora do canal 9. Baixo,
guitarra ou teclas tocando uma linha dentro da MESMA faixa de pitch nao
concentra dessa forma — uma parte melodica tipica se distribui por mais
pitches distintos ao longo da musica, mesmo repetindo frases. E o segundo
sinal (alem do ratio acima) que restringe a canais que de fato repetem um
kit fixo pequeno, em vez de qualquer conteudo predominantemente melodico
escrito no mesmo registro."""


def _select_drum_channels(mid: mido.MidiFile) -> frozenset[int]:
    """Canais que carregam bateria neste arquivo.

    O canal 9 (convencao GM) sempre qualifica quando tem qualquer nota —
    mesma convencao usada no resto do motor. Qualquer OUTRO canal qualifica
    quando tem volume razoavel de notas (`DRUM_CHANNEL_MIN_NOTES`) E as
    duas condicoes de conteudo abaixo se sustentam juntas — nenhuma delas
    sozinha e evidencia suficiente de bateria:

    - a maior parte das notas cai na faixa GM de percussao
      (`DRUM_CHANNEL_PERCUSSION_RATIO`);
    - as 3 pitches mais frequentes do canal concentram a maior parte das
      notas (`DRUM_CHANNEL_TOP_PITCH_CONCENTRATION`) — o padrao kick/snare
      repetido de um kit fixo, que uma linha melodica (baixo/guitarra/
      teclas) escrita no mesmo registro nao reproduz.

    Cobre o export de DAW que poe a bateria inteira num unico canal sem
    usar o canal 9 (3 dos 10 arquivos do corpus real fazem isso), sem
    qualificar um canal predominantemente melodico/harmonico so porque a
    tessitura da parte cai dentro da faixa GM de percussao.
    """
    counts: dict[int, int] = {}
    perc_counts: dict[int, int] = {}
    pitch_counts: dict[int, dict[int, int]] = {}
    for track in mid.tracks:
        for msg in track:
            if msg.is_meta or msg.type != "note_on" or msg.velocity <= 0:
                continue
            counts[msg.channel] = counts.get(msg.channel, 0) + 1
            if _GM_PERCUSSION_PITCH_MIN <= msg.note <= _GM_PERCUSSION_PITCH_MAX:
                perc_counts[msg.channel] = perc_counts.get(msg.channel, 0) + 1
            channel_pitches = pitch_counts.setdefault(msg.channel, {})
            channel_pitches[msg.note] = channel_pitches.get(msg.note, 0) + 1

    selected: set[int] = set()
    if counts.get(DRUM_CHANNEL, 0) > 0:
        selected.add(DRUM_CHANNEL)
    for channel, n in counts.items():
        if channel in selected or n < DRUM_CHANNEL_MIN_NOTES:
            continue
        ratio = perc_counts.get(channel, 0) / n
        if ratio < DRUM_CHANNEL_PERCUSSION_RATIO:
            continue
        top3 = sorted(pitch_counts.get(channel, {}).values(), reverse=True)[:3]
        top3_concentration = sum(top3) / n
        if top3_concentration >= DRUM_CHANNEL_TOP_PITCH_CONCENTRATION:
            selected.add(channel)
    return frozenset(selected)

GHOST_VELOCITY_CEILING = VELOCITY_RANGES["ghost"][1]
"""Fonte: `tools/constants.py::VELOCITY_RANGES["ghost"]` — o mesmo limiar
que o resto do motor de humanizacao usa para o bucket 'ghost'."""

_GM_PERCUSSION_NAMES: dict[int, str] = {
    35: "acoustic_bass_drum", 36: "bass_drum_1", 37: "side_stick",
    38: "acoustic_snare", 39: "hand_clap", 40: "electric_snare",
    41: "low_floor_tom", 42: "closed_hi_hat", 43: "high_floor_tom",
    44: "pedal_hi_hat", 45: "low_tom", 46: "open_hi_hat",
    47: "low_mid_tom", 48: "hi_mid_tom", 49: "crash_cymbal_1",
    50: "high_tom", 51: "ride_cymbal_1", 52: "chinese_cymbal",
    53: "ride_bell", 54: "tambourine", 55: "splash_cymbal",
    56: "cowbell", 57: "crash_cymbal_2", 58: "vibraslap", 59: "ride_cymbal_2",
}
"""Mapa canonico General MIDI Level 1 Percussion Key Map (35-59) — vocabulario
padrao da especificacao, nao carece de fonte adicional."""


def _piece_name(pitch: int) -> str:
    return _GM_PERCUSSION_NAMES.get(pitch, f"note_{pitch}")


def _piece_group(pitch: int) -> str:
    """Agrupamento reusando os conjuntos ja vendorizados de
    `tools/primitives.py` — mesma classificacao usada pelo resto do motor."""
    if pitch in KICKS:
        return "kick"
    if pitch in SNARES:
        return "snare"
    if pitch in HATS_CLOSED:
        return "hat_closed"
    if pitch in HATS_OPEN:
        return "hat_open"
    if pitch in TOMS:
        return "tom"
    if pitch in CYMBALS:
        return "cymbal"
    return "other"


def _register_band(pitch: int) -> str:
    for band, (lo, hi) in REGISTER_BANDS.items():
        if lo <= pitch <= hi:
            return band
    return "high"


# --- escore de confianca -----------------------------------------------------

SAMPLE_SATURATION = 200
"""CONVENCAO: >=200 amostras satura o componente de quantidade da formula
de confianca deste modulo."""

FILE_SATURATION = 5
"""CONVENCAO: >=5 arquivos satura o componente de diversidade de fonte."""

CONFIDENCE_HIGH_SCORE = 0.7
CONFIDENCE_MEDIUM_SCORE = 0.4

VELOCITY_LOCK_MODE_RATIO = 0.5
"""CONVENCAO: metade ou mais das notas no MESMO valor exato de velocity
significa que o valor nao carrega dinamica real (ver docstring do modulo)."""

VELOCITY_SPREAD_TARGET = 0.6
"""CONVENCAO: alvo de `1 - mode_ratio` (fracao das notas fora do valor mais
comum) que satura o componente de variancia da formula de feel."""

TIMING_LOCK_MEDIAN_MS = 2.0
"""CONVENCAO: mediana de desvio de grade abaixo disto e tratada como grade
quantizada (sem feel real de microtiming)."""

TIMING_SPREAD_TARGET_MS = 8.0
"""CONVENCAO: alvo de mediana de desvio (em ms) que satura o componente de
variancia — o teto do bucket 'normal' de `TIMING_JITTER_MS` em
`tools/constants.py` (3, 8)."""

GHOST_RATIO_TARGET = 0.15
"""CONVENCAO: proporcao de ghost notes que satura o componente de
variancia — ordem de grandeza tipica de pocket com ghost notes discretas."""

AUTOCORRELATION_TARGET = 0.3
"""CONVENCAO: |lag-1 autocorrelacao| que satura o componente de variancia —
acima disso o groove tem tendencia clara (nao e ruido puro)."""


def _confidence_from_score(score: float) -> str:
    if score >= CONFIDENCE_HIGH_SCORE:
        return "high"
    if score >= CONFIDENCE_MEDIUM_SCORE:
        return "medium"
    if score > 0.0:
        return "low"
    return "default"


def _structural_confidence(n_samples: int, n_files: int) -> tuple[str, float]:
    if n_samples <= 0:
        return "default", 0.0
    sample_component = min(1.0, n_samples / SAMPLE_SATURATION)
    file_component = min(1.0, n_files / FILE_SATURATION)
    score = 0.6 * sample_component + 0.4 * file_component
    return _confidence_from_score(score), score


def _feel_confidence(
    n_samples: int,
    n_files: int,
    *,
    degenerate: bool,
    variance_metric: float,
    variance_target: float,
) -> tuple[str, float]:
    if n_samples <= 0 or degenerate:
        return "default", 0.0
    sample_component = min(1.0, n_samples / SAMPLE_SATURATION)
    file_component = min(1.0, n_files / FILE_SATURATION)
    variance_component = (
        min(1.0, variance_metric / variance_target) if variance_target > 0 else 0.0
    )
    score = 0.4 * sample_component + 0.2 * file_component + 0.4 * variance_component
    return _confidence_from_score(score), score


# --- erros -------------------------------------------------------------------


class LearnError(ValueError):
    """Falha de dominio de `learn` — a fachada em `tools/contract.py`
    traduz para `ToolError` com codigo/`path` estaveis."""


class LearnFamilyNotSupportedError(LearnError):
    """Familia valida em `STYLE_FAMILIES` mas sem extracao implementada
    nesta versao de `learn` (ver `LEARN_SUPPORTED_FAMILIES`)."""


class LearnEmptyCorpusError(LearnError):
    """`midi_paths` vazio."""


# --- estruturas de dados -----------------------------------------------------


@dataclass
class DimensionMeasurement:
    """Uma dimensao medida (ou explicitamente NAO medida) do corpus."""

    name: str
    kind: str  # "structural" | "feel"
    measured: bool
    confidence: str
    n_samples: int
    n_files: int
    reason: str
    value: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "measured": self.measured,
            "confidence": self.confidence,
            "n_samples": self.n_samples,
            "n_files": self.n_files,
            "reason": self.reason,
            "value": self.value,
        }


@dataclass
class LearnResult:
    style: dict[str, Any]
    measurements: dict[str, Any]
    warnings: list[dict[str, Any]]


# --- leitura de MIDI ----------------------------------------------------------


DEFAULT_TEMPO_US = 500_000
"""Tempo default MIDI (120 BPM) quando nenhum `set_tempo` ocorre antes do
primeiro evento — mesma convencao de `mido`/`first_tempo`
(`tools/techniques/_helpers.py`)."""

DEFAULT_TIME_SIGNATURE = (4, 4)
"""Formula de compasso default (4/4) quando nenhum `time_signature` ocorre
antes do primeiro evento — mesma convencao GM/`mido`."""


def _tempo_map(mid: mido.MidiFile) -> tuple[tuple[int, int], ...]:
    """Mapa `(tick_absoluto, tempo_us)` ordenado por tick, com um evento
    default em tick 0 quando o arquivo nao declara `set_tempo` antes da
    primeira mudanca (ou nao declara nenhuma).

    Todas as tracks de um SMF compartilham o mesmo relogio de ticks a
    partir de tick 0 (cada track acumula seu proprio delta-time, mas o
    tick absoluto resultante e comparavel entre tracks) — por isso o mapa
    e construido varrendo TODAS as tracks, nao so a track de metadados
    (formato 1 costuma isolar `set_tempo` na track 0, mas o SMF nao
    exige isso)."""
    events: list[tuple[int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta and msg.type == "set_tempo":
                events.append((tick, int(msg.tempo)))
    events.sort(key=lambda e: e[0])
    if not events or events[0][0] > 0:
        events.insert(0, (0, DEFAULT_TEMPO_US))
    return tuple(events)


def _tempo_at(tempo_ticks: tuple[int, ...], tempo_values: tuple[int, ...], tick: int) -> int:
    """Tempo (us/quarter) vigente em `tick`, via o mapa de `_tempo_map`."""
    index = bisect.bisect_right(tempo_ticks, tick) - 1
    return tempo_values[max(0, index)]


def _time_signature_map(mid: mido.MidiFile) -> tuple[tuple[int, int, int], ...]:
    """Mapa `(tick_absoluto, numerator, denominator)` ordenado por tick,
    com um evento default (4/4) em tick 0 quando o arquivo nao declara
    `time_signature`. Mesma logica de varredura de todas as tracks de
    `_tempo_map` (o SMF nao exige que `time_signature` viva so na track de
    metadados)."""
    events: list[tuple[int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta and msg.type == "time_signature":
                events.append((tick, int(msg.numerator), int(msg.denominator)))
    events.sort(key=lambda e: e[0])
    if not events or events[0][0] > 0:
        events.insert(0, (0, *DEFAULT_TIME_SIGNATURE))
    return tuple(events)


def _bars_before_tick(
    time_signatures: tuple[tuple[int, int, int], ...], ppq: int, end_tick: int,
) -> float:
    """Numero (fracionario) de compassos entre tick 0 e `end_tick`,
    respeitando mudancas de formula de compasso — em vez de assumir 4/4
    fixo (`ppq * 4`) para o arquivo inteiro. Um compasso em `num/den` mede
    `num * 4 / den` semínimas, e cada semínima mede `ppq` ticks."""
    total_bars = 0.0
    for i, (tick, num, den) in enumerate(time_signatures):
        if tick >= end_tick:
            break
        next_tick = (
            time_signatures[i + 1][0] if i + 1 < len(time_signatures) else end_tick
        )
        segment_end = min(next_tick, end_tick)
        segment_ticks = segment_end - tick
        if segment_ticks <= 0:
            continue
        bar_ticks = ppq * 4 * num / den
        if bar_ticks <= 0:
            continue
        total_bars += segment_ticks / bar_ticks
    return total_bars


@dataclass
class _FileNotes:
    path: str
    ppq: int
    notes: tuple[dict[str, int], ...]  # apenas canal de bateria, ordenado por start
    time_signatures: tuple[tuple[int, int, int], ...]


def _load_drum_file(path: Path) -> _FileNotes:
    try:
        mid = mido.MidiFile(str(path))
    except (OSError, ValueError, EOFError, KeyError) as exc:
        raise LearnError(f"nao foi possivel carregar o MIDI {path}: {exc}") from None
    ppq = mid.ticks_per_beat or 480
    tempo_map = _tempo_map(mid)
    tempo_ticks = tuple(t for t, _ in tempo_map)
    tempo_values = tuple(v for _, v in tempo_map)
    time_signatures = _time_signature_map(mid)
    drum_channels = _select_drum_channels(mid)
    all_notes: list[dict[str, int]] = []
    for track_index, track in enumerate(mid.tracks):
        for note in iter_note_dicts(track, track_index=track_index):
            if note["channel"] in drum_channels:
                tempo = _tempo_at(tempo_ticks, tempo_values, note["start"])
                all_notes.append(dict(note, tempo=tempo))
    all_notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return _FileNotes(
        path=str(path), ppq=ppq, notes=tuple(all_notes), time_signatures=time_signatures,
    )


def _offset_from_sixteenth_grid(start_tick: int, ppq: int) -> int:
    sixteenth = max(1, ppq // 4)
    remainder = start_tick % sixteenth
    return min(remainder, sixteenth - remainder)


def _ticks_to_ms(ticks: int, ppq: int, tempo_us: int) -> float:
    return ticks * tempo_us / (ppq * 1000.0)


# --- medicao das dimensoes ----------------------------------------------------


def _measure_velocity(files: list[_FileNotes]) -> DimensionMeasurement:
    velocities = [n["velocity"] for f in files for n in f.notes]
    n_files = sum(1 for f in files if f.notes)
    n = len(velocities)
    if n == 0:
        return DimensionMeasurement(
            name="velocity", kind="feel", measured=False, confidence="default",
            n_samples=0, n_files=0,
            reason="nenhuma nota de bateria encontrada no corpus.",
        )

    counts: dict[int, int] = {}
    for v in velocities:
        counts[v] = counts.get(v, 0) + 1
    mode_value, mode_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    mode_ratio = mode_count / n
    degenerate = mode_ratio >= VELOCITY_LOCK_MODE_RATIO

    by_piece: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[int]] = {}
    for f in files:
        for note in f.notes:
            groups.setdefault(_piece_group(note["pitch"]), []).append(note["velocity"])
    for group, values in sorted(groups.items()):
        by_piece[group] = _velocity_stats(values)

    by_register: dict[str, dict[str, Any]] = {}
    reg_groups: dict[str, list[int]] = {}
    for f in files:
        for note in f.notes:
            reg_groups.setdefault(_register_band(note["pitch"]), []).append(note["velocity"])
    for band, values in sorted(reg_groups.items()):
        by_register[band] = _velocity_stats(values)

    spread_ratio = 1.0 - mode_ratio
    confidence, score = _feel_confidence(
        n, n_files,
        degenerate=degenerate,
        variance_metric=spread_ratio,
        variance_target=VELOCITY_SPREAD_TARGET,
    )
    if degenerate:
        reason = (
            f"{mode_ratio:.0%} das notas compartilham o mesmo valor exato de "
            f"velocity ({mode_value}) — o dado nao carrega dinamica real "
            "(velocity travada/exportacao sem humanizacao no MIDI; a "
            "humanizacao provavelmente acontece a jusante, no plugin/audio)."
        )
    else:
        reason = (
            f"{n} notas em {n_files} arquivo(s); valor mais comum "
            f"{mode_value} cobre {mode_ratio:.0%} — variacao real detectada."
        )

    return DimensionMeasurement(
        name="velocity", kind="feel", measured=not degenerate,
        confidence=confidence, n_samples=n, n_files=n_files, reason=reason,
        value={
            "score": round(score, 3),
            "mode_value": mode_value,
            "mode_ratio": round(mode_ratio, 4),
            "mean": round(statistics.fmean(velocities), 2),
            "median": statistics.median(velocities),
            "stdev": round(statistics.pstdev(velocities), 2),
            "by_piece": by_piece,
            "by_register": by_register,
        },
    )


def _velocity_stats(values: list[int]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 2),
        "median": statistics.median(values),
        "stdev": round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0,
    }


def _measure_timing_offset(files: list[_FileNotes]) -> DimensionMeasurement:
    offsets_ms: list[float] = []
    for f in files:
        for note in f.notes:
            offset_ticks = _offset_from_sixteenth_grid(note["start"], f.ppq)
            offsets_ms.append(_ticks_to_ms(offset_ticks, f.ppq, note["tempo"]))

    n_files = sum(1 for f in files if f.notes)
    n = len(offsets_ms)
    if n == 0:
        return DimensionMeasurement(
            name="timing_offset_ms", kind="feel", measured=False, confidence="default",
            n_samples=0, n_files=0,
            reason="nenhuma nota de bateria encontrada no corpus.",
        )

    median_ms = statistics.median(offsets_ms)
    degenerate = median_ms < TIMING_LOCK_MEDIAN_MS

    confidence, score = _feel_confidence(
        n, n_files,
        degenerate=degenerate,
        variance_metric=median_ms,
        variance_target=TIMING_SPREAD_TARGET_MS,
    )
    if degenerate:
        reason = (
            f"desvio mediano de {median_ms:.2f} ms em relacao a grade de "
            "semicolcheia — grade quantizada; o desvio nao carrega feel de "
            "microtiming real (numero calculavel, nao apresentavel como "
            "escolha estilistica)."
        )
    else:
        reason = (
            f"{n} notas em {n_files} arquivo(s); desvio mediano de "
            f"{median_ms:.2f} ms em relacao a grade — variacao real detectada."
        )

    return DimensionMeasurement(
        name="timing_offset_ms", kind="feel", measured=not degenerate,
        confidence=confidence, n_samples=n, n_files=n_files, reason=reason,
        value={
            "score": round(score, 3),
            "median_ms": round(median_ms, 3),
            "mean_ms": round(statistics.fmean(offsets_ms), 3),
            "stdev_ms": round(statistics.pstdev(offsets_ms), 3) if n > 1 else 0.0,
        },
    )


def _measure_ghost_notes(
    files: list[_FileNotes], velocity_dim: DimensionMeasurement,
) -> DimensionMeasurement:
    all_notes = [n for f in files for n in f.notes]
    n_files = sum(1 for f in files if f.notes)
    n = len(all_notes)
    if n == 0:
        return DimensionMeasurement(
            name="ghost_notes", kind="feel", measured=False, confidence="default",
            n_samples=0, n_files=0,
            reason="nenhuma nota de bateria encontrada no corpus.",
        )

    ghost_count = sum(1 for note in all_notes if note["velocity"] <= GHOST_VELOCITY_CEILING)
    ratio = ghost_count / n

    velocity_degenerate = not velocity_dim.measured
    if velocity_degenerate:
        confidence = "default"
        score = 0.0
        reason = (
            f"proporcao calculada e {ratio:.1%} ({ghost_count}/{n}), mas a "
            "dimensao 'velocity' esta degenerada neste corpus (ver dimensao "
            "'velocity'); ghost note e definida por limiar de velocity, "
            "entao essa proporcao nao carrega informacao confiavel — nao "
            "e apresentavel como 'a banda nao usa ghost notes'."
        )
    else:
        per_file_ratios = [
            sum(1 for note in f.notes if note["velocity"] <= GHOST_VELOCITY_CEILING)
            / len(f.notes)
            for f in files
            if f.notes
        ]
        spread = statistics.pstdev(per_file_ratios) if len(per_file_ratios) > 1 else ratio
        confidence, score = _feel_confidence(
            n, n_files,
            degenerate=False,
            variance_metric=max(ratio, spread),
            variance_target=GHOST_RATIO_TARGET,
        )
        reason = (
            f"{ghost_count}/{n} notas ({ratio:.1%}) abaixo do teto de ghost "
            f"({GHOST_VELOCITY_CEILING}) em {n_files} arquivo(s); dimensao "
            "'velocity' carrega variacao real, entao a proporcao e confiavel."
        )

    return DimensionMeasurement(
        name="ghost_notes", kind="feel", measured=not velocity_degenerate,
        confidence=confidence, n_samples=n, n_files=n_files, reason=reason,
        value={
            "score": round(score, 3),
            "ghost_count": ghost_count,
            "ratio": round(ratio, 4),
            "velocity_ceiling": GHOST_VELOCITY_CEILING,
        },
    )


def _measure_lag1_autocorrelation(
    files: list[_FileNotes], timing_dim: DimensionMeasurement,
) -> DimensionMeasurement:
    series: list[float] = []
    for f in files:
        for note in f.notes:
            offset_ticks = _offset_from_sixteenth_grid(note["start"], f.ppq)
            series.append(_ticks_to_ms(offset_ticks, f.ppq, note["tempo"]))

    n_files = sum(1 for f in files if f.notes)
    n = len(series)
    if n < 3:
        return DimensionMeasurement(
            name="lag1_autocorrelation", kind="feel", measured=False,
            confidence="default", n_samples=n, n_files=n_files,
            reason="amostra pequena demais (<3 notas) para autocorrelacao.",
        )

    timing_degenerate = not timing_dim.measured
    mean = statistics.fmean(series)
    denominator = sum((x - mean) ** 2 for x in series)

    if timing_degenerate or denominator == 0.0:
        return DimensionMeasurement(
            name="lag1_autocorrelation", kind="feel", measured=False,
            confidence="default", n_samples=n, n_files=n_files,
            reason=(
                "serie de desvio de grade sem variancia real (ver dimensao "
                "'timing_offset_ms') — autocorrelacao indefinida ou sem "
                "significado; nao apresentavel como groove medido."
            ),
            value={},
        )

    numerator = sum((series[i] - mean) * (series[i + 1] - mean) for i in range(n - 1))
    r1 = numerator / denominator

    confidence, score = _feel_confidence(
        n, n_files,
        degenerate=False,
        variance_metric=abs(r1),
        variance_target=AUTOCORRELATION_TARGET,
    )
    return DimensionMeasurement(
        name="lag1_autocorrelation", kind="feel", measured=True,
        confidence=confidence, n_samples=n, n_files=n_files,
        reason=(
            f"lag-1 autocorrelacao {r1:.3f} sobre {n} desvios de grade em "
            f"{n_files} arquivo(s); serie carrega variancia real."
        ),
        value={"score": round(score, 3), "r1": round(r1, 4)},
    )


def _measure_articulation_vocabulary(files: list[_FileNotes]) -> DimensionMeasurement:
    all_notes = [n for f in files for n in f.notes]
    n_files = sum(1 for f in files if f.notes)
    n = len(all_notes)
    if n == 0:
        return DimensionMeasurement(
            name="articulation_vocabulary", kind="structural", measured=False,
            confidence="default", n_samples=0, n_files=0,
            reason="nenhuma nota de bateria encontrada no corpus.",
        )

    piece_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    for note in all_notes:
        piece_counts[_piece_name(note["pitch"])] = piece_counts.get(
            _piece_name(note["pitch"]), 0,
        ) + 1
        group_counts[_piece_group(note["pitch"])] = group_counts.get(
            _piece_group(note["pitch"]), 0,
        ) + 1

    confidence, score = _structural_confidence(n, n_files)
    return DimensionMeasurement(
        name="articulation_vocabulary", kind="structural", measured=True,
        confidence=confidence, n_samples=n, n_files=n_files,
        reason=(
            f"{len(piece_counts)} pecas distintas ({len(group_counts)} grupos) "
            f"em {n} notas de {n_files} arquivo(s)."
        ),
        value={
            "score": round(score, 3),
            "pieces": dict(sorted(piece_counts.items())),
            "groups": dict(sorted(group_counts.items())),
            "distinct_piece_count": len(piece_counts),
            "distinct_group_count": len(group_counts),
        },
    )


def _measure_fill_density(files: list[_FileNotes]) -> DimensionMeasurement:
    total_fills = 0
    total_bars = 0.0
    n_files = 0
    for f in files:
        if not f.notes:
            continue
        n_files += 1
        # `fill_windows` (tools/techniques/_fill_detection.py) so considera
        # canal 9 (convencao GM) — `f.notes` ja foi filtrado por
        # `_select_drum_channels` e pode vir de outro canal (ver
        # `DRUM_CHANNEL`); normalizamos para 9 so para este calculo.
        note_dicts = [dict(n, channel=DRUM_CHANNEL) for n in f.notes]
        windows = fill_windows(note_dicts, ticks_per_beat=f.ppq)
        total_fills += len(windows)
        last_tick = max(n["end"] for n in f.notes)
        total_bars += _bars_before_tick(f.time_signatures, f.ppq, last_tick)

    n_samples = sum(len(f.notes) for f in files)
    if n_files == 0:
        return DimensionMeasurement(
            name="fill_density", kind="structural", measured=False,
            confidence="default", n_samples=0, n_files=0,
            reason="nenhuma nota de bateria encontrada no corpus.",
        )

    density_per_bar = total_fills / total_bars if total_bars > 0 else 0.0
    confidence, score = _structural_confidence(n_samples, n_files)
    return DimensionMeasurement(
        name="fill_density", kind="structural", measured=True,
        confidence=confidence, n_samples=n_samples, n_files=n_files,
        reason=(
            f"{total_fills} virada(s)/turnaround(s) detectado(s) em "
            f"~{total_bars:.1f} compassos (formula de compasso real do "
            f"arquivo) de {n_files} arquivo(s)."
        ),
        value={
            "score": round(score, 3),
            "fills_total": total_fills,
            "bars_total": round(total_bars, 2),
            "fills_per_bar": round(density_per_bar, 4),
        },
    )


# --- montagem do style.<familia> ---------------------------------------------


def _build_family_style(
    dimensions: list[DimensionMeasurement],
    *,
    reference: str,
    researched_at: str,
    sources: list[str],
) -> dict[str, Any]:
    # `parameters` fica vazio de proposito: nenhuma tecnica registrada em
    # `tools/techniques/engine.py` declara/le um parametro chamado
    # `drums_articulation_vocabulary_size`, `drums_fill_density_per_bar`,
    # `drums_velocity_mode_ratio`, `drums_velocity_stdev`,
    # `drums_timing_offset_median_ms`, `drums_ghost_note_ratio` ou
    # `drums_lag1_autocorrelation` — e `style.<familia>.parameters` so
    # alcanca `context.parameters` de dentro do loop
    # `for technique in style.techniques` (`tools/render.py`), que fica
    # vazio quando `techniques=[]` (ver abaixo). Popular `parameters` com
    # esses numeros faria `render()` aceitar/validar o valor e ignora-lo
    # silenciosamente — o "parametro mentiroso" que o AGENTS.md ja rejeitou
    # (`_identity_apply`). O detalhe completo de toda dimensao medida
    # continua em `LearnResult.measurements`, a unica fonte de verdade para
    # esses numeros; nenhuma tecnica esta autorizada aqui (issue #18 nao
    # aciona `authorized_techniques`), entao `techniques` tambem fica
    # vazio — mesma regra de "tecnica so se aplica se o usuario autorizou".
    parameters: dict[str, float] = {}

    measured_confident = [
        dim.confidence for dim in dimensions
        if dim.measured and dim.confidence != "default"
    ]
    order = ("default", "low", "medium", "high")
    confidence = (
        min(measured_confident, key=order.index) if measured_confident else "default"
    )

    return {
        "reference": reference,
        "researched_at": researched_at,
        "sources": sources,
        "confidence": confidence,
        "techniques": [],
        "parameters": parameters,
    }


# --- entrada publica -----------------------------------------------------------


def learn(
    midi_paths: list[str],
    family: str,
    *,
    researched_at: str,
    reference: str | None = None,
) -> LearnResult:
    """Mede `midi_paths` (um corpus da mesma banda/musico) e devolve um
    `style.<family>` pronto para `plan.style`, mais o detalhe completo de
    cada dimensao medida (ou explicitamente nao medida) em `measurements`.

    Determinismo: le somente os arquivos informados, sem rede e sem relogio
    — `researched_at` e responsabilidade de quem chama (harness/skill), que
    tem a data real da sessao; a tool nunca inventa timestamp.
    """
    if family not in STYLE_FAMILIES:
        raise LearnError(
            f"familia {family!r} desconhecida; esperado uma de {list(STYLE_FAMILIES)}",
        )
    if family not in LEARN_SUPPORTED_FAMILIES:
        raise LearnFamilyNotSupportedError(
            f"learn ainda nao mede a familia {family!r} nesta versao; "
            f"implementadas: {list(LEARN_SUPPORTED_FAMILIES)}",
        )
    if not midi_paths:
        raise LearnEmptyCorpusError(
            "midi_paths vazio — informe ao menos um arquivo do corpus",
        )

    files = [_load_drum_file(Path(p)) for p in midi_paths]

    velocity_dim = _measure_velocity(files)
    timing_dim = _measure_timing_offset(files)
    ghost_dim = _measure_ghost_notes(files, velocity_dim)
    autocorr_dim = _measure_lag1_autocorrelation(files, timing_dim)
    vocabulary_dim = _measure_articulation_vocabulary(files)
    fill_dim = _measure_fill_density(files)

    dimensions = [
        velocity_dim, timing_dim, ghost_dim, autocorr_dim, vocabulary_dim, fill_dim,
    ]

    default_reference = f"corpus proprio ({len(midi_paths)} arquivo(s))"
    style_entry = _build_family_style(
        dimensions,
        reference=reference or default_reference,
        researched_at=researched_at,
        sources=list(midi_paths),
    )

    warnings: list[dict[str, Any]] = []
    for dim in dimensions:
        if dim.kind == "feel" and not dim.measured:
            warnings.append({
                "code": "W_LEARN_NOT_MEASURABLE",
                "message": (
                    f"dimensao '{dim.name}' nao pode ser medida com "
                    f"confianca deste corpus: {dim.reason}"
                ),
                "path": f"measurements.dimensions.{dim.name}",
            })

    measurements = {
        "family": family,
        "files": [
            {"path": f.path, "note_count": len(f.notes)} for f in files
        ],
        "dimensions": {dim.name: dim.to_dict() for dim in dimensions},
    }

    return LearnResult(
        style={family: style_entry},
        measurements=measurements,
        warnings=warnings,
    )
