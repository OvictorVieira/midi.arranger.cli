"""Validador de conformidade com o brief (issue #5).

Prova, mecanicamente, que o MIDI RENDERIZADO atende o que o
`arrangement-brief.json` pediu em `requisitos[]` — a risca, com evidencia
numerica, nao "o plano e valido" nem "a saida e musical".

## O mecanismo

Cada `requisito` do brief (`id`, `familia`, `tipo`, `alvo`, `descricao`) vira
um `RequisitoVerdict` com `status` de vocabulario FECHADO
(`atendido`/`parcial`/`nao_atendido`/`nao_verificavel`) e uma `evidencia`
numerica — nunca prosa. `nao_atendido` e `parcial` bloqueiam: `conforme`
sai `False` no relatorio, e a fachada (`tools/contract.py`) levanta
`ToolError` para o envelope sair `ok=false` e o CLI encerrar com codigo
diferente de zero (mesmo mecanismo de qualquer outra tool: "erro e dado,
nao excecao", `tools/registry.py`). `nao_verificavel` NUNCA bloqueia — e a
resposta honesta para requisito que nao vira metrica automatica.

## Os seis tipos e o que cada verificacao mede de verdade

- `tecnica`: casa `alvo`/`descricao` do requisito contra o nome curto de
  uma tecnica declarada em `plan.style.<familia>.techniques[]` (nao
  hardcoded — usa `tools.techniques.build_index()`); conta ocorrencias
  NOVAS (notas que nao existiam na origem, mais notas de elemento gerado
  da familia), confere velocity contra a faixa do PROPRIO manual da
  tecnica (`Technique.parameters[name="velocity"].range`) e posicao contra
  a lista de pitches permitidos da receita `generic` (`tools[...].notes`),
  quando o manual declara essas coisas.
- `reducao`: mede QUATRO formas de reducao, sempre origem-vs-renderizado, e
  o eixo sai das palavras de `alvo`/`descricao` DEPOIS do gate de familia —
  nenhum eixo responde por familia que nao e a do requisito:
  (a) DENSIDADE DE VIRADA de bateria (`virada`/`fill`), pela mesma deteccao
  do motor (`tools.techniques._fill_detection`), so quando a familia do
  requisito e `drums`: `fill_windows` le exclusivamente o canal 9, entao
  julgar um baixo por ela seria veredito sobre track nunca olhada;
  (b) CAMADAS/VOZES (`camada`, `voz`, `layer`, `polifonia`, ...), pela
  polifonia medida nos onsets — maxima e media;
  (c) FAIXA DINAMICA (`dinamica`, `volume`, `velocity`, ...), pela amplitude
  (max - min) e pelo desvio padrao das velocities;
  (d) INSTRUMENTACAO (`instrumentacao`, `tirar`, `cortar`, `track`, ...),
  por quantas tracks da familia continuam soando e quantas notas somam.
  Sem palavra-chave nenhuma, o eixo default e DENSIDADE DE NOTAS POR
  COMPASSO, com o numero de compassos derivado do mapa real de formula de
  compasso (`learn._time_signature_map`/`_bars_before_tick`) — nunca 4/4
  assumido. O denominador e o MESMO dos dois lados (o comprimento da
  origem, contra o qual o requisito do brief foi escrito): medir cada lado
  com o proprio comprimento faz uma track nova de `plan.elements[]` que
  termina depois da origem "reduzir" a densidade de uma familia que saiu
  nota a nota identica.
  Os quatro eixos exigem queda de pelo menos `REDUCAO_MIN_PCT`, virada
  incluida — uma nota atravessando a borda da janela nao e reducao.
  `dinamica` e `camadas` devolvem `nao_verificavel` (que nao bloqueia)
  quando a origem nao tem o que reduzir (amplitude ja zerada, familia ja
  monofonica), como `virada` e `instrumentacao` sempre fizeram: culpar o
  render por algo que nenhum render podia entregar e tao ruim quanto o
  falso `atendido`.
  Quando o texto do requisito nomeia uma secao de `plan.sections`, todas as
  medicoes (b)-(d) e a default sao restritas a janela daquela secao,
  convertida de compasso para segundos pelo mapa real de tempo.
  Continua `nao_verificavel`, com motivo concreto: requisito sem familia de
  `STYLE_FAMILIES` fora do eixo de virada (sem familia nao se sabe QUAIS
  tracks comparar), familia que nao aparece em `plan.edits[].profile` nem em
  `plan.elements[].role`, e origem com menos de `_MIN_REDUCTION_SAMPLES`
  notas na familia/secao.
- `criacao`: confere que a familia pedida tem `plan.elements[]` cujo role
  mapeia pra ela (`ROLE_STYLE_FAMILIES`), que essas tracks renderizaram
  nota, e reusa os `HarmonyIssue`/`PlacementIssue` JA CALCULADOS pelos
  validadores harmonico/placement (nunca reimplementa deteccao de campo
  harmonico) para confirmar que o conteudo criado fica dentro do campo
  harmonico e das secoes declaradas.
- `estilo`: mede DOIS eixos declarados em `plan.style.<familia>.parameters`.
  O eixo sai do TEXTO do requisito (`alvo`/`descricao`), como em `reducao`,
  nunca da ordem em que este arquivo testa os eixos: escolher por ordem de
  codigo fazia o `velocity` declarado sumir do veredito e da evidencia
  sempre que houvesse qualquer chave de timing escalar — parametro aceito,
  validado e depois ignorado e parametro mentiroso (AGENTS.md). O parametro
  que nao foi medido aparece em `evidencia.parametros_nao_medidos`.
  (a) VIES DE TIMING (`*timing*` com `bias`/`offset`, escalar): mediana do
  offset com sinal contra a grade de semicolcheia NO RENDER menos a mesma
  mediana NA ORIGEM — o DELTA que o render acrescentou, como o resto do
  modulo, nunca a distancia absoluta ate a grade (numa origem nao
  quantizada o feel do take domina a mediana e reprova um render correto).
  A conversao tick->ms usa o tempo VIGENTE no tick de cada nota (mapa de
  `learn._tempo_map`) — arquivo com mudanca de andamento media errado de
  qualquer outro jeito. A medicao roda so nas tracks de `plan.edits[]`: sao
  as unicas com contraparte na origem e as unicas que o `profile` desloca.
  Chave de DISPERSAO (`hihat_timing_sigma_ms` e afins) nao entra: sigma
  descreve sorteio de media zero, e cobrar mediana de sigma reprova para
  sempre um render que fez o que o manual manda.
  (b) VELOCITY (`*velocity*`/`*dinamica*`): faixa `[min, max]` vira
  percentual de notas dentro da faixa; escalar vira mediana medida contra
  tolerancia. Quando o parametro e declarado por uma tecnica citada em
  `style.<familia>.techniques[]` (`velocity` e nome de parametro DE TECNICA
  no manual: `drums.ghost_notes` -> [20, 45]), a medicao roda so sobre as
  notas ACRESCENTADAS — mesma leitura de `_verdict_tecnica`. O nivel
  `technique` tem contrato de nao mexer na velocity estrutural, entao
  cobrar a faixa da familia inteira e cobrar o estruturalmente impossivel.
  Parametro de timing de escopo de tecnica sai `nao_verificavel` citando a
  tecnica dona: o eixo mede a mediana da familia e nao sabe isolar o
  ornamento.
  Sobre a propagacao de `timing_bias_ms` ponta a ponta: NENHUMA tecnica de
  `tools/techniques/engine.py` consome vies direcional de timing —
  `drums.microtiming` (o unico aplicador de nivel `humanize` que mexe em
  timing) sorteia offset gaussiano de MEDIA ZERO com autocorrelacao
  (`hihat_timing_sigma_ms`), e nenhum outro aplicador registrado le
  parametro de offset direcional. O vies direcional que EXISTE de ponta a
  ponta vem do motor de humanizacao por profile, antes do motor de
  tecnicas: `tools/edits.py::PROFILE_PARAMS[<profile>].bias_ms` (baixo
  -3.5ms, demais 0.0) escalado por `plan.edits[].intensity`, sorteado por
  `random.gauss(bias_ms, sigma_ms)` em `tools/edits.py::apply_edit`. Por
  isso a medicao de (a) tem teste de ponta a ponta por `tools.render.render`
  sobre uma track de baixo editada — nao so uma medicao isolada.
- `restricao`: prova por AUSENCIA — nenhum `plan.elements[]` da familia
  vetada, confirmado contra a contagem real de notas renderizadas daqueles
  elementos (zero elemento -> zero nota, nunca confianca cega no plano).
- `intensidade`: compara a ORIGEM contra o RENDERIZADO na mesma track
  editada — quantas velocities distintas existiam antes/depois (hierarquia
  de acento) e quantas ghost notes existiam antes/depois — o mesmo par de
  numeros que provou o defeito historico do motor (`ENTRE NOS.mid`: 1
  velocity distinta, 0 ghost antes de qualquer tecnica).

## O que fica fora desta rodada, declarado explicitamente

TODOS os tipos classificam familia via `plan.elements[].role`
(`ROLE_STYLE_FAMILIES`) e `plan.edits[].profile` — nunca por heuristica de
nome de instrumento MIDI, porque o plano ja e a fonte de verdade estrutural
usada pelo resto do motor (render/plan.validate). Consequencia declarada:
track de origem que nao esta em `plan.edits` nao pertence a familia nenhuma
para efeito deste validador (ela sai byte-identica do render, por contrato),
entao reducao pedida sobre ela sai `nao_verificavel`, nao `nao_atendido`.
`estilo` mede timing e velocity; densidade de tecnica ja tem o tipo
`tecnica` dedicado, e parametro declarado fora desses dois eixos sai
`nao_verificavel` NOMEANDO os parametros que estavam declarados. `restricao`
de padrao especifico dentro de uma familia presente (ex.: "nada de double
kick") continua fora do escopo automatico. Um requisito fora dessas formas
concretas sai `nao_verificavel` — nunca inventa numero para nao ficar em
branco.
"""

from __future__ import annotations

import bisect
import statistics
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

import mido

from ..constants import VELOCITY_RANGES
from ..learn import (
    _bars_before_tick,
    _tempo_at,
    _tempo_map,
    _time_signature_map,
)
from ..plan import (
    ROLE_STYLE_FAMILIES,
    STYLE_FAMILIES,
    ArrangementPlan,
    PlanSection,
)
from ..techniques import build_index
from ..techniques._fill_detection import fill_windows
from ..techniques._helpers import iter_note_dicts
from .harmony import SEVERITY_ERROR, HarmonyIssue, RenderedNote, RenderedTrack
from .placement import PlacementIssue

# --- vocabulario fechado -----------------------------------------------------

STATUS_ATENDIDO = "atendido"
STATUS_PARCIAL = "parcial"
STATUS_NAO_ATENDIDO = "nao_atendido"
STATUS_NAO_VERIFICAVEL = "nao_verificavel"

STATUSES: tuple[str, ...] = (
    STATUS_ATENDIDO, STATUS_PARCIAL, STATUS_NAO_ATENDIDO, STATUS_NAO_VERIFICAVEL,
)

BLOCKING_STATUSES: frozenset[str] = frozenset({STATUS_NAO_ATENDIDO, STATUS_PARCIAL})
"""`nao_atendido` e `parcial` bloqueiam a entrega — `nao_verificavel` nunca
bloqueia, e o unico status honesto para requisito que nao vira metrica."""

GHOST_VELOCITY_CEILING: int = VELOCITY_RANGES["ghost"][1]
"""Mesmo teto de `tools/constants.py::VELOCITY_RANGES['ghost']` usado pelo
resto do motor de humanizacao (`tools/learn.py::GHOST_VELOCITY_CEILING`)."""

TIMING_BIAS_TOLERANCE_MS: float = 5.0
"""CONVENCAO do validador (nao do manual): tolerancia para o vies de timing
medido bater com `timing_bias_ms`/`*_bias_ms` declarado em
`plan.style.<familia>.parameters`. `estilo` sai `parcial` ate 3x essa
tolerancia e `nao_atendido` alem disso."""

_FILL_FAMILY: str = "drums"
"""Unica familia que o eixo de virada pode julgar: `fill_windows` le
exclusivamente o canal 9 (bateria GM)."""

_FILL_KEYWORDS: frozenset[str] = frozenset({"virada", "viradas", "fill", "fills"})
"""Palavras que precisam aparecer em `alvo`/`descricao` para o tipo
`reducao` rodar a medicao de densidade de virada. So valem quando a familia
do requisito e `_FILL_FAMILY` — ver a docstring do modulo."""

_MIN_TIMING_SAMPLES = 4
"""Amostra minima de notas pra `estilo` (vies de timing) e `intensidade`
declararem medicao — abaixo disso a mediana e ruido, nao evidencia."""

_MIN_REDUCTION_SAMPLES = 4
"""Amostra minima de notas NA ORIGEM para `reducao` declarar medicao —
abaixo disso "caiu de 3 para 2 notas" nao e evidencia de reducao."""

_MIN_SECTION_LABEL_LEN = 3
"""Rotulo de secao com menos caracteres que isso nao e casado no texto do
requisito: 'A'/'B' colidiriam com qualquer palavra."""

REDUCAO_MIN_PCT: float = 5.0
"""CONVENCAO do validador (nao do manual): queda menor que isso, em
percentual, nao conta como reducao efetiva — sai `parcial` com o numero
medido, nunca `atendido`."""

VELOCITY_RANGE_MIN_PCT: float = 95.0
VELOCITY_RANGE_PARTIAL_PCT: float = 60.0
"""CONVENCAO do validador: percentual de notas que precisa cair dentro da
faixa `[min, max]` de velocity declarada em `plan.style.<familia>.parameters`
para o requisito de `estilo` sair `atendido` (95%) ou `parcial` (60%)."""

VELOCITY_MEDIAN_TOLERANCE: float = 8.0
"""CONVENCAO do validador: tolerancia (em unidades de velocity MIDI) entre a
mediana medida e um parametro de velocity declarado como ESCALAR. `parcial`
ate 3x, `nao_atendido` alem — mesma escada de `TIMING_BIAS_TOLERANCE_MS`."""

_LAYER_KEYWORDS: frozenset[str] = frozenset({
    "camada", "camadas", "voz", "vozes", "layer", "layers", "polifonia",
    "simultane", "sobreposicao", "empilha",
})
_DYNAMIC_KEYWORDS: frozenset[str] = frozenset({
    "dinamica", "dinamico", "faixa dinamica", "volume", "velocity",
    "amplitude",
})
_INSTRUMENTATION_KEYWORDS: frozenset[str] = frozenset({
    "instrumentacao", "instrumento", "instrumentos", "track", "tracks",
    "tirar", "remover", "retirar", "cortar", "silenciar", "familia",
})
"""Palavras que escolhem o EIXO de medicao do tipo `reducao` (ver a secao
'tipo: reducao' abaixo). Sem casamento nenhum, o eixo default e densidade de
notas por compasso."""

_TIMING_TEXT_KEYWORDS: frozenset[str] = frozenset({
    "timing", "vies", "bias", "laid", "adiant", "atras", "grade", "feel",
    "swing", "push", "pull", "andamento",
})
_VELOCITY_TEXT_KEYWORDS: frozenset[str] = frozenset({
    "velocity", "dinamic", "dynamic", "volume", "acento", "intensidade",
    "ghost", "forte", "fraco",
})
"""Palavras que escolhem o EIXO de medicao do tipo `estilo`. O eixo sai do
TEXTO do requisito, como em `reducao` — nunca da ordem em que o codigo testa
os eixos, que faria o parametro do outro eixo sumir do veredito."""


# --- dataclasses de saida -----------------------------------------------------


@dataclass(frozen=True)
class RequisitoVerdict:
    """Veredito de UM requisito do brief, com evidencia numerica.

    `evidencia` e sempre um dict de numeros/strings/listas simples — nunca
    prosa solta; a prosa explicativa vive em `motivo`, que so aparece
    quando o status nao e `atendido`.
    """
    id: str
    descricao: str
    status: str
    evidencia: dict[str, Any] = field(default_factory=dict)
    motivo: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "descricao": self.descricao,
            "status": self.status,
            "evidencia": self.evidencia,
        }
        if self.motivo:
            d["motivo"] = self.motivo
        return d


@dataclass(frozen=True)
class ComplianceReport:
    """Veredito agregado — `conforme` e `False` se QUALQUER requisito
    bloqueia (`atendido`/`parcial`)."""
    conforme: bool
    requisitos: tuple[RequisitoVerdict, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conforme": self.conforme,
            "requisitos": [r.to_dict() for r in self.requisitos],
        }


def blocking_requisitos(report: ComplianceReport) -> tuple[RequisitoVerdict, ...]:
    """Requisitos com status `nao_atendido` ou `parcial` — os que bloqueiam."""
    return tuple(r for r in report.requisitos if r.status in BLOCKING_STATUSES)


def format_report(report: ComplianceReport) -> str:
    """Pretty-print do relatorio, mesmo padrao dos demais validadores."""
    lines = [f"Compliance: {'OK' if report.conforme else 'NAO CONFORME'}"]
    for r in report.requisitos:
        tag = {
            STATUS_ATENDIDO: "OK",
            STATUS_PARCIAL: "PARCIAL",
            STATUS_NAO_ATENDIDO: "FALHOU",
            STATUS_NAO_VERIFICAVEL: "N/V",
        }[r.status]
        line = f"  [{tag}] {r.id}: {r.descricao}"
        if r.motivo:
            line += f" — {r.motivo}"
        lines.append(line)
    return "\n".join(lines)


# --- helpers puros -------------------------------------------------------


def _normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_accents.lower()


def _tracks_by_name(tracks: Iterable[RenderedTrack]) -> dict[str, list[RenderedTrack]]:
    by_name: dict[str, list[RenderedTrack]] = {}
    for t in tracks:
        by_name.setdefault(t.track_name, []).append(t)
    return by_name


def _notes_for_names(
    by_name: dict[str, list[RenderedTrack]], names: Iterable[str],
) -> list[RenderedNote]:
    notes: list[RenderedNote] = []
    for name in names:
        for track in by_name.get(name, ()):
            notes.extend(track.notes)
    return notes


def _notes_for_element_ids(
    tracks: Iterable[RenderedTrack], element_ids: frozenset[str],
) -> list[RenderedNote]:
    notes: list[RenderedNote] = []
    for t in tracks:
        if t.element_id in element_ids:
            notes.extend(t.notes)
    return notes


def _note_signature(n: RenderedNote) -> tuple[int, int, int]:
    """Assinatura tolerante a jitter de ponto flutuante do round-trip
    pretty_midi — arredonda tempo em milissegundos."""
    return (n.pitch, round(n.start_s * 1000), round(n.end_s * 1000))


def _added_notes(
    source_notes: list[RenderedNote], rendered_notes: list[RenderedNote],
) -> list[RenderedNote]:
    """Notas em `rendered_notes` cuja assinatura NAO existia em
    `source_notes` — ornamento inserido pela tecnica, nunca a nota
    estrutural (que o motor preserva por contrato)."""
    source_sigs = {_note_signature(n) for n in source_notes}
    return [n for n in rendered_notes if _note_signature(n) not in source_sigs]


def _edit_track_names(plan: ArrangementPlan, family: str) -> list[str]:
    return [e.track for e in plan.edits if e.profile == family]


def _element_ids_for_family(plan: ArrangementPlan, family: str) -> list[str]:
    return [
        e.id for e in plan.elements
        if ROLE_STYLE_FAMILIES.get(e.role) == family
    ]


def _mido_track_name(track: mido.MidiTrack) -> str | None:
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            return msg.name
    return None


def _signed_grid_offset_ticks(start_tick: int, sixteenth: int) -> int:
    """Distancia COM SINAL ate a semicolcheia mais proxima — negativo
    quando a nota antecipa a grade, positivo quando atrasa."""
    remainder = start_tick % sixteenth
    if remainder > sixteenth / 2:
        return remainder - sixteenth
    return remainder


def _drum_note_dicts(mid: mido.MidiFile) -> tuple[dict[str, int], ...]:
    """Notas do canal 9 (bateria GM) de TODAS as tracks, ordenadas por
    onset — formato de `iter_note_dicts`, insumo de `fill_windows`."""
    notes: list[dict[str, int]] = []
    for track_index, track in enumerate(mid.tracks):
        for note in iter_note_dicts(track, track_index=track_index):
            if note["channel"] == 9:
                notes.append(note)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return tuple(notes)


def _atendido(req: dict[str, Any], evidencia: dict[str, Any]) -> RequisitoVerdict:
    return RequisitoVerdict(req["id"], req["descricao"], STATUS_ATENDIDO, evidencia)


def _parcial(
    req: dict[str, Any], evidencia: dict[str, Any], motivo: str,
) -> RequisitoVerdict:
    return RequisitoVerdict(req["id"], req["descricao"], STATUS_PARCIAL, evidencia, motivo)


def _nao_atendido(
    req: dict[str, Any], evidencia: dict[str, Any], motivo: str,
) -> RequisitoVerdict:
    return RequisitoVerdict(
        req["id"], req["descricao"], STATUS_NAO_ATENDIDO, evidencia, motivo,
    )


def _nao_verificavel(
    req: dict[str, Any], motivo: str, evidencia: dict[str, Any] | None = None,
) -> RequisitoVerdict:
    """`nao_verificavel` NUNCA bloqueia. Quando a medicao chegou a produzir
    numero antes de concluir que nao ha o que verificar, o numero vai junto
    — evidencia calculada e descartada e evidencia perdida."""
    return RequisitoVerdict(
        req["id"], req["descricao"], STATUS_NAO_VERIFICAVEL,
        dict(evidencia) if evidencia else {}, motivo,
    )


# --- tempo, formula de compasso e janela de secao ---------------------------
#
# Reusa os helpers de `tools/learn.py` (`_tempo_map`, `_time_signature_map`,
# `_bars_before_tick`) em vez de reimplementar leitura de mapa de tempo /
# formula de compasso — reimplementacao divergente ja foi fonte de bug nesta
# base. `_bar_boundary_tick` e o INVERSO de `_bars_before_tick` (compasso ->
# tick) e `_tick_to_seconds` integra o mapa de tempo; nenhum dos dois existe
# em `learn.py`, que so precisa da direcao contraria.


def _tempo_arrays(mid: mido.MidiFile) -> tuple[tuple[int, ...], tuple[int, ...]]:
    tempo_map = _tempo_map(mid)
    return tuple(t for t, _ in tempo_map), tuple(v for _, v in tempo_map)


def _tick_to_seconds(mid: mido.MidiFile, tick: int) -> float:
    """Segundos absolutos de `tick`, integrando o mapa de tempo real do
    arquivo — nunca "o primeiro set_tempo vale para a musica inteira"."""
    ppq = mid.ticks_per_beat or 0
    if ppq <= 0:
        return 0.0
    tempo_map = _tempo_map(mid)
    seconds = 0.0
    for i, (seg_tick, tempo_us) in enumerate(tempo_map):
        if seg_tick >= tick:
            break
        next_tick = tempo_map[i + 1][0] if i + 1 < len(tempo_map) else tick
        segment = min(next_tick, tick) - seg_tick
        if segment <= 0:
            continue
        seconds += segment * tempo_us / (ppq * 1_000_000.0)
    return seconds


def _bar_boundary_tick(
    time_signatures: tuple[tuple[int, int, int], ...], ppq: int, bar_index: int,
) -> int:
    """Tick absoluto do inicio do compasso `bar_index` (0-based), respeitando
    mudanca de formula de compasso — inverso de `learn._bars_before_tick`."""
    if bar_index <= 0 or ppq <= 0:
        return 0
    bars_so_far = 0.0
    for i, (seg_tick, num, den) in enumerate(time_signatures):
        bar_ticks = ppq * 4 * num / den
        if bar_ticks <= 0:
            continue
        if i + 1 >= len(time_signatures):
            return int(round(seg_tick + (bar_index - bars_so_far) * bar_ticks))
        seg_bars = (time_signatures[i + 1][0] - seg_tick) / bar_ticks
        if bars_so_far + seg_bars >= bar_index:
            return int(round(seg_tick + (bar_index - bars_so_far) * bar_ticks))
        bars_so_far += seg_bars
    return 0


def _total_bars(mid: mido.MidiFile) -> float:
    """Numero (fracionario) de compassos do arquivo inteiro, pela formula de
    compasso real — `learn._bars_before_tick` sobre o ultimo tick."""
    ppq = mid.ticks_per_beat or 0
    if ppq <= 0:
        return 0.0
    last_tick = 0
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
        last_tick = max(last_tick, tick)
    if last_tick <= 0:
        return 0.0
    return _bars_before_tick(_time_signature_map(mid), ppq, last_tick)


def _section_window(
    mid: mido.MidiFile, section: PlanSection,
) -> tuple[float, float]:
    """Janela `[inicio_s, fim_s)` da secao no arquivo, derivada dos compassos
    declarados no plano com o mapa REAL de formula de compasso e de tempo."""
    ppq = mid.ticks_per_beat or 0
    time_signatures = _time_signature_map(mid)
    start_tick = _bar_boundary_tick(time_signatures, ppq, section.start_bar)
    end_tick = _bar_boundary_tick(time_signatures, ppq, section.end_bar)
    return (_tick_to_seconds(mid, start_tick), _tick_to_seconds(mid, end_tick))


def _match_section(plan: ArrangementPlan, haystack: str) -> PlanSection | None:
    """Secao do plano cujo `label` aparece no texto do requisito. Rotulo
    curto demais (< 3 caracteres) nao casa — 'A'/'B' colidiriam com
    qualquer palavra. Primeira secao declarada que casa vence
    (deterministico, ordem do plano)."""
    for section in plan.sections:
        label = _normalize_text(section.label)
        if len(label) >= _MIN_SECTION_LABEL_LEN and label in haystack:
            return section
    return None


def _notes_in_window(
    notes: list[RenderedNote], window: tuple[float, float] | None,
) -> list[RenderedNote]:
    if window is None:
        return notes
    start_s, end_s = window
    return [n for n in notes if start_s <= n.start_s < end_s]


# --- estatistica de notas (origem vs renderizado) ---------------------------


def _polyphony_stats(notes: list[RenderedNote]) -> tuple[int, float]:
    """`(polifonia_maxima, polifonia_media)` medidas nos ONSETS: quantas
    notas estao soando quando cada nota entra. Nota que termina exatamente
    no onset de outra NAO conta como simultanea."""
    if not notes:
        return (0, 0.0)
    starts = sorted(n.start_s for n in notes)
    ends = sorted(max(n.end_s, n.start_s) for n in notes)
    counts = [
        bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t)
        for t in starts
    ]
    return (max(counts), round(statistics.fmean(counts), 3))


def _velocity_stats(notes: list[RenderedNote]) -> dict[str, float]:
    velocities = [n.velocity for n in notes]
    if not velocities:
        return {"min": 0, "max": 0, "amplitude": 0, "desvio": 0.0}
    lo, hi = min(velocities), max(velocities)
    desvio = statistics.pstdev(velocities) if len(velocities) > 1 else 0.0
    return {
        "min": lo, "max": hi, "amplitude": hi - lo, "desvio": round(desvio, 3),
    }


# --- tracks de uma familia (origem vs renderizado) --------------------------


def _family_source_tracks(
    plan: ArrangementPlan, family: str,
    source_by_name: dict[str, list[RenderedTrack]],
) -> list[RenderedTrack]:
    """Tracks do MIDI de ORIGEM que pertencem a familia — casadas por nome
    declarado em `plan.edits[].profile`, nunca por heuristica de nome de
    instrumento MIDI (mesma regra do resto deste modulo)."""
    tracks: list[RenderedTrack] = []
    for name in _edit_track_names(plan, family):
        tracks.extend(source_by_name.get(name, ()))
    return tracks


def _family_rendered_tracks(
    plan: ArrangementPlan, family: str,
    rendered_by_name: dict[str, list[RenderedTrack]],
    rendered_tracks: list[RenderedTrack],
) -> list[RenderedTrack]:
    """Tracks do MIDI RENDERIZADO da familia: as editadas (por nome) MAIS as
    geradas por `plan.elements[]` da familia (por `element_id`) — conteudo
    novo entra na conta, senao "reduzir" ficaria satisfeito por um render
    que corta de um lado e acrescenta do outro."""
    tracks: list[RenderedTrack] = []
    for name in _edit_track_names(plan, family):
        tracks.extend(rendered_by_name.get(name, ()))
    element_ids = frozenset(_element_ids_for_family(plan, family))
    tracks.extend(t for t in rendered_tracks if t.element_id in element_ids)
    return tracks


def _family_track_names(
    plan: ArrangementPlan, family: str, rendered_tracks: list[RenderedTrack],
) -> list[str]:
    """Nomes de track (meta `track_name`) da familia no MIDI renderizado —
    editadas e geradas. Usado nas medicoes feitas em TICKS (`mido`), onde
    so o nome amarra a track ao plano."""
    names = list(_edit_track_names(plan, family))
    element_ids = frozenset(_element_ids_for_family(plan, family))
    for t in rendered_tracks:
        if t.element_id in element_ids and t.track_name not in names:
            names.append(t.track_name)
    return names

def _family_added_notes(
    plan: ArrangementPlan,
    family: str,
    source_by_name: dict[str, list[RenderedTrack]],
    rendered_by_name: dict[str, list[RenderedTrack]],
    rendered_tracks: list[RenderedTrack],
) -> list[RenderedNote]:
    """Notas que o arranjador ACRESCENTOU na familia: ornamentos inseridos
    nas tracks editadas (assinatura que nao existia na origem) mais todas as
    notas dos elementos gerados da familia.

    Toda nota vinda do MIDI de origem e estrutural por definicao (AGENTS.md),
    e o nivel `technique` so pode acrescentar ornamento sobre ela — logo
    parametro de escopo de tecnica so pode ser cobrado destas notas.
    """
    edit_names = _edit_track_names(plan, family)
    added = _added_notes(
        _notes_for_names(source_by_name, edit_names),
        _notes_for_names(rendered_by_name, edit_names),
    )
    element_ids = frozenset(_element_ids_for_family(plan, family))
    added += _notes_for_element_ids(rendered_tracks, element_ids)
    return added


def _technique_scoped_parameter(family_style: Any, param_name: str) -> str | None:
    """Canonico da tecnica declarada em `style.<familia>.techniques[]` que
    declara `param_name` no MANUAL — `None` quando o parametro nao e de
    escopo de tecnica.

    Nunca hardcoded: le os parametros pelo indice (`build_index()`), a mesma
    fonte que `plan.validate` usa para checar faixa de parametro.
    """
    if family_style is None:
        return None
    declared = list(getattr(family_style, "techniques", ()) or ())
    if not declared:
        return None
    idx = build_index()
    needle = _normalize_text(param_name)
    for t in declared:
        resolved = idx.get(t.name)
        if resolved is None:
            continue
        for p in resolved.parameters:
            if _normalize_text(p.name) == needle:
                return resolved.canonical
    return None


# --- tipo: tecnica -------------------------------------------------------


def _match_declared_technique(
    req: dict[str, Any], declared: list[Any],
) -> tuple[str, Any | None] | None:
    """Casa `alvo`/`descricao` do requisito contra o nome curto de uma
    tecnica declarada em `plan.style.<familia>.techniques[]`.

    Nunca hardcoded: resolve cada nome declarado contra
    `tools.techniques.build_index()` para achar o canonico, e casa o texto
    do requisito pela ultima parte do canonico (`drums.ghost_notes` ->
    "ghost notes"). Primeira tecnica declarada (ordem do plano) que casar
    vence — deterministico, sem ambiguidade de escolha.
    """
    idx = build_index()
    haystack = _normalize_text(f"{req['alvo']} {req['descricao']}")
    for t in declared:
        resolved = idx.get(t.name)
        canonical = resolved.canonical if resolved is not None else t.name
        short = canonical.split(".", 1)[-1]
        needle = _normalize_text(short.replace("_", " "))
        if needle and needle in haystack:
            return canonical, resolved
    return None


def _verdict_tecnica(
    req: dict[str, Any],
    plan: ArrangementPlan,
    source_by_name: dict[str, list[RenderedTrack]],
    rendered_by_name: dict[str, list[RenderedTrack]],
    rendered_tracks: list[RenderedTrack],
) -> RequisitoVerdict:
    family = req["familia"]
    if family not in STYLE_FAMILIES:
        return _nao_verificavel(
            req,
            f"tipo 'tecnica' exige familia em {list(STYLE_FAMILIES)}; "
            f"recebido {family!r}",
        )

    family_style = (plan.style or {}).get(family)
    declared = list(family_style.techniques) if family_style is not None else []
    if not declared:
        return _nao_atendido(
            req, {"tecnicas_no_plano": []},
            f"plan.style.{family}.techniques nao declara nenhuma tecnica",
        )

    match = _match_declared_technique(req, declared)
    if match is None:
        idx = build_index()
        names = [
            (idx.get(t.name).canonical if idx.get(t.name) else t.name)
            for t in declared
        ]
        return _nao_atendido(
            req, {"tecnicas_no_plano": names},
            "nenhuma tecnica declarada em plan.style bate com o alvo/descricao "
            "do requisito",
        )
    canonical, technique = match

    added = _family_added_notes(
        plan, family, source_by_name, rendered_by_name, rendered_tracks,
    )

    velocity_range: tuple[float, float] | None = None
    allowed_pitches: frozenset[int] | None = None
    if technique is not None:
        for p in technique.parameters:
            if p.name == "velocity" and p.range is not None:
                velocity_range = (float(p.range[0]), float(p.range[1]))
                break
        generic_recipe = (technique.tools or {}).get("generic")
        if isinstance(generic_recipe, dict):
            notes_list = generic_recipe.get("notes")
            if isinstance(notes_list, list) and notes_list and all(
                isinstance(n, int) for n in notes_list
            ):
                allowed_pitches = frozenset(notes_list)

    fora_da_faixa = 0
    if velocity_range is not None:
        lo, hi = velocity_range
        fora_da_faixa = sum(1 for n in added if not (lo <= n.velocity <= hi))
    posicoes_proibidas = 0
    if allowed_pitches is not None:
        posicoes_proibidas = sum(1 for n in added if n.pitch not in allowed_pitches)

    evidencia: dict[str, Any] = {
        "tecnica": canonical,
        "ocorrencias_inseridas": len(added),
        "posicoes_proibidas_usadas": posicoes_proibidas,
    }
    if velocity_range is not None:
        evidencia["faixa_velocity"] = [velocity_range[0], velocity_range[1]]
        evidencia["notas_fora_da_faixa"] = fora_da_faixa

    if not added:
        return _nao_atendido(
            req, evidencia,
            f"tecnica {canonical!r} esta declarada mas nao produziu nenhuma "
            f"ocorrencia nova no MIDI renderizado",
        )
    if fora_da_faixa > 0 or posicoes_proibidas > 0:
        return _parcial(
            req, evidencia,
            f"{fora_da_faixa} ocorrencia(s) fora da faixa de velocity do "
            f"manual e {posicoes_proibidas} em posicao nao permitida",
        )
    return _atendido(req, evidencia)


# --- tipo: reducao ---------------------------------------------------------
#
# `reducao` mede formas de reducao que a origem e o render permitem comparar
# NUMERICAMENTE, sempre origem-vs-renderizado. O eixo sai do texto do
# requisito (`alvo` + `descricao`, sem acento, minusculo); quando nenhuma
# palavra-chave casa, o eixo default e densidade de notas por compasso —
# a leitura mais generica de "reduzir" que ainda e objetiva.


def _has_keyword(haystack: str, keywords: frozenset[str]) -> bool:
    return any(kw in haystack for kw in keywords)


def _reducao_pct(antes: float, depois: float) -> float:
    if antes <= 0:
        return 0.0
    return round((antes - depois) / antes * 100, 2)


def _reducao_virada(
    req: dict[str, Any], source_mid: mido.MidiFile, rendered_mid: mido.MidiFile,
    base: dict[str, Any],
) -> RequisitoVerdict:
    """Densidade de virada de bateria, via `tools.techniques._fill_detection`
    — a mesma deteccao que o motor usa, nunca uma reimplementacao.

    So responde por BATERIA: `fill_windows` le exclusivamente o canal 9, e o
    chamador (`_verdict_reducao`) so despacha para ca quando a familia do
    requisito e `drums`. Requisito de outra familia que fale em "virada" e
    medido nos eixos daquela familia — julgar um baixo pela virada da
    bateria e veredito sobre track que nunca foi olhada.
    """
    ppq = source_mid.ticks_per_beat or 0
    if ppq <= 0:
        return _nao_verificavel(req, "MIDI de origem sem ticks_per_beat valido")

    source_notes = _drum_note_dicts(source_mid)
    if not source_notes:
        return _nao_verificavel(
            req, "nenhuma nota de bateria (canal 9) na origem para medir virada",
        )

    windows = fill_windows(source_notes, ticks_per_beat=ppq)
    if not windows:
        return _nao_verificavel(
            req, "nenhuma virada detectada na origem — nao ha o que reduzir",
        )

    rendered_notes = _drum_note_dicts(rendered_mid)
    rendered_windows = fill_windows(rendered_notes, ticks_per_beat=ppq)

    def _count_in_windows(
        notes: tuple[dict[str, int], ...], ranges: tuple[tuple[int, int], ...],
    ) -> int:
        return sum(
            1
            for start, end in ranges
            for n in notes
            if start <= n["start"] <= end
        )

    antes = _count_in_windows(source_notes, windows)
    depois = _count_in_windows(rendered_notes, windows)

    evidencia = dict(base)
    evidencia.update({
        "eixo": "virada",
        "viradas_antes": len(windows),
        "viradas_depois": len(rendered_windows),
        "notas_em_virada_antes": antes,
        "notas_em_virada_depois": depois,
        "reducao_pct": _reducao_pct(antes, depois),
        "minimo_pct": REDUCAO_MIN_PCT,
    })
    if depois >= antes:
        return _nao_atendido(req, evidencia, "densidade de virada nao caiu")
    if evidencia["reducao_pct"] < REDUCAO_MIN_PCT:
        # Mesmo piso dos demais eixos de `reducao`: uma nota atravessando a
        # borda da janela (a humanizacao mexe no timing) nao e reducao de
        # virada — seria `atendido` por ruido.
        return _parcial(
            req, evidencia,
            f"a densidade de virada caiu apenas {evidencia['reducao_pct']}%, "
            f"abaixo do minimo de {REDUCAO_MIN_PCT}% que este validador conta "
            f"como reducao efetiva",
        )
    if not rendered_windows:
        return _parcial(
            req, evidencia,
            "densidade caiu, mas nenhuma virada continua detectavel como "
            "virada — a estrutura (compassos de virada) pode ter se perdido",
        )
    return _atendido(req, evidencia)


def _reducao_instrumentacao(
    req: dict[str, Any],
    source_tracks: list[RenderedTrack],
    rendered_tracks: list[RenderedTrack],
    src_window: tuple[float, float] | None,
    ren_window: tuple[float, float] | None,
    base: dict[str, Any],
) -> RequisitoVerdict:
    """Reducao de INSTRUMENTACAO: quantas tracks da familia continuam
    soando (track sem nota nenhuma na janela nao conta) e quantas notas
    elas somam."""
    def _sounding(
        tracks: list[RenderedTrack], window: tuple[float, float] | None,
    ) -> tuple[int, int]:
        n_tracks = 0
        n_notes = 0
        for t in tracks:
            notes = _notes_in_window(list(t.notes), window)
            if notes:
                n_tracks += 1
                n_notes += len(notes)
        return n_tracks, n_notes

    tracks_antes, notas_antes = _sounding(source_tracks, src_window)
    tracks_depois, notas_depois = _sounding(rendered_tracks, ren_window)
    evidencia = dict(base)
    evidencia.update({
        "eixo": "instrumentacao",
        "tracks_soando_antes": tracks_antes,
        "tracks_soando_depois": tracks_depois,
        "notas_antes": notas_antes,
        "notas_depois": notas_depois,
        "reducao_pct": _reducao_pct(notas_antes, notas_depois),
    })
    if tracks_antes == 0:
        return _nao_verificavel(
            req,
            "nenhuma track da familia soa na origem — nao ha instrumentacao "
            "a reduzir (comparacao seria origem-vs-vazio)",
            evidencia,
        )
    if tracks_depois < tracks_antes:
        return _atendido(req, evidencia)
    if evidencia["reducao_pct"] >= REDUCAO_MIN_PCT:
        return _parcial(
            req, evidencia,
            f"as {tracks_antes} track(s) da familia continuam soando; caiu "
            f"apenas a densidade de notas ({evidencia['reducao_pct']}%)",
        )
    return _nao_atendido(
        req, evidencia,
        f"a instrumentacao da familia nao foi reduzida: {tracks_antes} -> "
        f"{tracks_depois} track(s) soando, {notas_antes} -> {notas_depois} nota(s)",
    )


def _reducao_camadas(
    req: dict[str, Any],
    source_notes: list[RenderedNote],
    rendered_notes: list[RenderedNote],
    base: dict[str, Any],
) -> RequisitoVerdict:
    """Reducao de CAMADAS/VOZES: polifonia (notas soando ao mesmo tempo)
    medida nos onsets — maximo e media."""
    max_antes, media_antes = _polyphony_stats(source_notes)
    max_depois, media_depois = _polyphony_stats(rendered_notes)
    evidencia = dict(base)
    evidencia.update({
        "eixo": "camadas",
        "polifonia_max_antes": max_antes,
        "polifonia_max_depois": max_depois,
        "polifonia_media_antes": media_antes,
        "polifonia_media_depois": media_depois,
        "reducao_pct": _reducao_pct(media_antes, media_depois),
    })
    media_caiu = evidencia["reducao_pct"] >= REDUCAO_MIN_PCT
    max_caiu = max_depois < max_antes
    if max_antes <= 1 and max_depois <= max_antes and media_depois <= media_antes:
        # Origem monofonica: nao existe sobreposicao de vozes a reduzir.
        # Mesma honestidade de `_reducao_virada`/`_reducao_instrumentacao` —
        # `nao_atendido` aqui afirmaria falha do render sobre algo que
        # nenhum render podia entregar.
        return _nao_verificavel(
            req,
            "a familia ja e monofonica na origem (polifonia maxima "
            f"{max_antes}) — nao ha sobreposicao de vozes a reduzir",
            evidencia,
        )
    if media_caiu and max_depois <= max_antes:
        return _atendido(req, evidencia)
    if media_caiu:
        return _parcial(
            req, evidencia,
            f"a polifonia media caiu {evidencia['reducao_pct']}% "
            f"({media_antes} -> {media_depois}), mas a maxima subiu "
            f"({max_antes} -> {max_depois})",
        )
    if max_caiu:
        return _parcial(
            req, evidencia,
            f"a polifonia maxima caiu ({max_antes} -> {max_depois}), mas a "
            f"media caiu apenas {evidencia['reducao_pct']}%, abaixo do minimo "
            f"de {REDUCAO_MIN_PCT}% que este validador conta como reducao "
            f"efetiva",
        )
    return _nao_atendido(
        req, evidencia,
        f"a sobreposicao de vozes nao caiu: media {media_antes} -> "
        f"{media_depois}, maxima {max_antes} -> {max_depois}",
    )


def _reducao_dinamica(
    req: dict[str, Any],
    source_notes: list[RenderedNote],
    rendered_notes: list[RenderedNote],
    base: dict[str, Any],
) -> RequisitoVerdict:
    """Reducao de FAIXA DINAMICA: amplitude (max - min) e desvio padrao das
    velocities."""
    antes = _velocity_stats(source_notes)
    depois = _velocity_stats(rendered_notes)
    evidencia = dict(base)
    evidencia.update({
        "eixo": "dinamica",
        "velocity_min_antes": antes["min"], "velocity_max_antes": antes["max"],
        "velocity_min_depois": depois["min"], "velocity_max_depois": depois["max"],
        "amplitude_antes": antes["amplitude"], "amplitude_depois": depois["amplitude"],
        "desvio_antes": antes["desvio"], "desvio_depois": depois["desvio"],
        "reducao_pct": _reducao_pct(antes["amplitude"], depois["amplitude"]),
    })
    amplitude_caiu = evidencia["reducao_pct"] >= REDUCAO_MIN_PCT
    desvio_caiu = depois["desvio"] < antes["desvio"]
    if (
        antes["amplitude"] == 0 and antes["desvio"] == 0
        and depois["amplitude"] <= antes["amplitude"]
        and depois["desvio"] <= antes["desvio"]
    ):
        # Origem 100% numa unica velocity: a faixa dinamica ja esta no piso
        # e o render tambem nao a abriu. Nenhum render possivel baixa
        # amplitude abaixo de zero — `nao_atendido` seria culpar o render por
        # algo impossivel. (Render que ABRE a faixa contra um pedido de
        # fechar continua caindo no `nao_atendido` abaixo: ai a falha e real.)
        return _nao_verificavel(
            req,
            "a origem ja esta em uma unica velocity (amplitude 0, desvio 0) "
            "— nao ha faixa dinamica a reduzir",
            evidencia,
        )
    if amplitude_caiu and desvio_caiu:
        return _atendido(req, evidencia)
    if amplitude_caiu:
        return _parcial(
            req, evidencia,
            f"a amplitude de velocity caiu {evidencia['reducao_pct']}% "
            f"({antes['amplitude']} -> {depois['amplitude']}), mas o desvio "
            f"nao caiu ({antes['desvio']} -> {depois['desvio']})",
        )
    if desvio_caiu:
        return _parcial(
            req, evidencia,
            f"o desvio de velocity caiu ({antes['desvio']} -> "
            f"{depois['desvio']}), mas a amplitude caiu apenas "
            f"{evidencia['reducao_pct']}%, abaixo do minimo de "
            f"{REDUCAO_MIN_PCT}% que este validador conta como reducao "
            f"efetiva",
        )
    return _nao_atendido(
        req, evidencia,
        f"a faixa dinamica nao caiu: amplitude {antes['amplitude']} -> "
        f"{depois['amplitude']}, desvio {antes['desvio']} -> {depois['desvio']}",
    )


def _reducao_densidade(
    req: dict[str, Any],
    source_notes: list[RenderedNote],
    rendered_notes: list[RenderedNote],
    bars: float,
    base: dict[str, Any],
) -> RequisitoVerdict:
    """Eixo default: densidade de notas POR COMPASSO, com o numero de
    compassos derivado do mapa real de formula de compasso (nunca 4/4
    assumido).

    O denominador e o MESMO dos dois lados — um quadro de referencia, nao
    duas medidas independentes. Medir `len(origem)/compassos_da_origem`
    contra `len(render)/compassos_do_render` compara duas escalas
    diferentes: `plan.elements[]` gera track nova, e uma que termine depois
    da ultima nota da origem alonga o arquivo renderizado e "reduz" a
    densidade de uma familia que saiu NOTA A NOTA IDENTICA (render mais
    curto produz o espelho, reprovando um render correto). O quadro de
    referencia e o comprimento da ORIGEM, que e contra o que o requisito do
    brief foi escrito — mesma convencao que o recorte por secao ja usa, onde
    `end_bar - start_bar` vale para os dois lados.
    """
    if bars <= 0:
        return _nao_verificavel(
            req,
            "nao foi possivel derivar o numero de compassos do MIDI "
            "(formula de compasso/ppq invalidos) para medir densidade",
        )
    densidade_antes = round(len(source_notes) / bars, 3)
    densidade_depois = round(len(rendered_notes) / bars, 3)
    evidencia = dict(base)
    evidencia.update({
        "eixo": "densidade",
        "notas_antes": len(source_notes),
        "notas_depois": len(rendered_notes),
        "compassos_referencia": round(bars, 3),
        "densidade_por_compasso_antes": densidade_antes,
        "densidade_por_compasso_depois": densidade_depois,
        "reducao_pct": _reducao_pct(densidade_antes, densidade_depois),
    })
    if evidencia["reducao_pct"] >= REDUCAO_MIN_PCT:
        return _atendido(req, evidencia)
    if densidade_depois < densidade_antes:
        return _parcial(
            req, evidencia,
            f"densidade caiu apenas {evidencia['reducao_pct']}%, abaixo do "
            f"minimo de {REDUCAO_MIN_PCT}% que este validador conta como "
            f"reducao efetiva",
        )
    return _nao_atendido(
        req, evidencia,
        f"densidade por compasso nao caiu: {densidade_antes} -> "
        f"{densidade_depois}",
    )


def _verdict_reducao(
    req: dict[str, Any],
    plan: ArrangementPlan,
    source_by_name: dict[str, list[RenderedTrack]],
    rendered_by_name: dict[str, list[RenderedTrack]],
    rendered_tracks: list[RenderedTrack],
    source_mid: mido.MidiFile,
    rendered_mid: mido.MidiFile,
) -> RequisitoVerdict:
    haystack = _normalize_text(f"{req['alvo']} {req['descricao']}")

    family = req["familia"]
    if family not in STYLE_FAMILIES:
        return _nao_verificavel(
            req,
            f"tipo 'reducao' precisa de familia em {list(STYLE_FAMILIES)} "
            f"para saber QUAIS tracks comparar (plan.edits[].profile / "
            f"plan.elements[].role); recebido {family!r}",
        )

    src_tracks = _family_source_tracks(plan, family, source_by_name)
    ren_tracks = _family_rendered_tracks(
        plan, family, rendered_by_name, rendered_tracks,
    )
    if not src_tracks and not ren_tracks:
        return _nao_verificavel(
            req,
            f"familia {family!r} nao aparece em plan.edits[].profile nem em "
            f"plan.elements[].role — nao ha track para comparar origem vs "
            f"renderizado",
        )

    base: dict[str, Any] = {"familia": family}
    if family == _FILL_FAMILY and _has_keyword(haystack, _FILL_KEYWORDS):
        # Eixo de virada: `fill_windows` le exclusivamente o canal 9, entao
        # so a familia `drums` pode ser julgada por ele. Requisito de outra
        # familia que fale em "virada" segue para os eixos daquela familia.
        return _reducao_virada(req, source_mid, rendered_mid, base)

    section = _match_section(plan, haystack)
    src_window: tuple[float, float] | None = None
    ren_window: tuple[float, float] | None = None
    if section is not None:
        src_window = _section_window(source_mid, section)
        ren_window = _section_window(rendered_mid, section)
        base["secao"] = section.label
        base["secao_compassos"] = [section.start_bar, section.end_bar]

    source_notes = _notes_in_window(
        [n for t in src_tracks for n in t.notes], src_window,
    )
    rendered_notes = _notes_in_window(
        [n for t in ren_tracks for n in t.notes], ren_window,
    )
    if len(source_notes) < _MIN_REDUCTION_SAMPLES:
        return _nao_verificavel(
            req,
            f"origem tem apenas {len(source_notes)} nota(s) na familia "
            f"{family!r}"
            + (f" dentro da secao {section.label!r}" if section else "")
            + " — amostra insuficiente para medir reducao",
        )

    if _has_keyword(haystack, _LAYER_KEYWORDS):
        return _reducao_camadas(req, source_notes, rendered_notes, base)
    if _has_keyword(haystack, _DYNAMIC_KEYWORDS):
        return _reducao_dinamica(req, source_notes, rendered_notes, base)
    if _has_keyword(haystack, _INSTRUMENTATION_KEYWORDS):
        return _reducao_instrumentacao(
            req, src_tracks, ren_tracks, src_window, ren_window, base,
        )
    if section is not None:
        bars = float(max(1, section.end_bar - section.start_bar))
    else:
        bars = _total_bars(source_mid)
    return _reducao_densidade(req, source_notes, rendered_notes, bars, base)


# --- tipo: criacao ---------------------------------------------------------


def _verdict_criacao(
    req: dict[str, Any],
    plan: ArrangementPlan,
    rendered_tracks: list[RenderedTrack],
    harmony_issues: list[HarmonyIssue],
    placement_issues: list[PlacementIssue],
) -> RequisitoVerdict:
    family = req["familia"]
    if family not in STYLE_FAMILIES:
        return _nao_verificavel(
            req,
            f"tipo 'criacao' exige familia em {list(STYLE_FAMILIES)}; "
            f"recebido {family!r}",
        )

    element_ids = _element_ids_for_family(plan, family)
    if not element_ids:
        return _nao_atendido(
            req, {"elementos_gerados": []},
            f"nenhum plan.elements[] com role da familia {family!r} foi "
            f"declarado",
        )

    element_id_set = frozenset(element_ids)
    notes = _notes_for_element_ids(rendered_tracks, element_id_set)
    if not notes:
        return _nao_atendido(
            req,
            {"elementos_gerados": element_ids, "notas_criadas": 0},
            "elemento(s) declarados mas nenhuma nota foi renderizada",
        )

    harmony_errors = sum(
        1 for i in harmony_issues
        if i.element_id in element_id_set and i.severity == SEVERITY_ERROR
    )
    placement_errors = sum(
        1 for i in placement_issues
        if i.element_id in element_id_set and i.severity == SEVERITY_ERROR
    )

    evidencia = {
        "elementos_gerados": element_ids,
        "notas_criadas": len(notes),
        "erros_harmonicos": harmony_errors,
        "erros_placement": placement_errors,
    }
    if harmony_errors == 0 and placement_errors == 0:
        return _atendido(req, evidencia)
    if harmony_errors >= len(notes):
        return _nao_atendido(
            req, evidencia,
            "notas criadas caem fora do campo harmonico da secao",
        )
    return _parcial(
        req, evidencia,
        f"{harmony_errors} nota(s) fora do campo harmonico e "
        f"{placement_errors} fora das secoes declaradas",
    )


# --- tipo: estilo -----------------------------------------------------------


_TIMING_DISPERSION_MARKERS: tuple[str, ...] = (
    "sigma", "jitter", "desvio", "stdev", "spread", "variacao", "aleator",
    "random", "range", "autocorr",
)
"""Marcadores de DISPERSAO. `hihat_timing_sigma_ms` (8.7ms, parametro real
de `drums.microtiming`) descreve o desvio-padrao de um sorteio gaussiano de
MEDIA ZERO: a mediana medida nunca chega perto de 8.7, e tratar sigma como
alvo de mediana reprova para sempre um render que fez exatamente o que o
manual manda. Sigma nao e vies."""

_TIMING_BIAS_MARKERS: tuple[str, ...] = ("bias", "offset", "vies")
"""Marcadores de vies DIRECIONAL — a unica coisa que o eixo de timing sabe
medir (mediana com sinal). Chave de timing sem nenhum deles nao declara
direcao e nao vira alvo de mediana."""


def _timing_parameter_key(parameters: dict[str, Any]) -> str | None:
    """Chave de `parameters` que declara vies DIRECIONAL de timing.

    Casa `timing` + marcador de vies (`bias`/`offset`/`vies`) e recusa
    marcador de dispersao (`sigma`, `jitter`, ...): dispersao e vies sao
    grandezas diferentes, e so o vies tem mediana com sinal para comparar.
    """
    for key in parameters:
        lower = _normalize_text(key)
        if "timing" not in lower:
            continue
        if any(marker in lower for marker in _TIMING_DISPERSION_MARKERS):
            continue
        if any(marker in lower for marker in _TIMING_BIAS_MARKERS):
            return key
    return None


def _velocity_parameter_key(parameters: dict[str, Any]) -> str | None:
    for key in parameters:
        lower = _normalize_text(key)
        if "velocity" in lower or "dinamic" in lower or "dynamic" in lower:
            return key
    return None


def _grid_offsets_ms(
    mid: mido.MidiFile, track_names: list[str],
) -> list[float]:
    """Offset COM SINAL de cada nota das tracks nomeadas ate a semicolcheia
    mais proxima, em ms. A conversao tick->ms usa o tempo VIGENTE no tick da
    nota (mapa de `learn._tempo_map`), nao o primeiro `set_tempo` do arquivo
    — arquivo com mudanca de andamento media errado de qualquer outro jeito."""
    ppq = mid.ticks_per_beat or 0
    if ppq <= 0:
        return []
    sixteenth = max(1, ppq // 4)
    tempo_ticks, tempo_values = _tempo_arrays(mid)
    offsets_ms: list[float] = []
    for track in mid.tracks:
        if _mido_track_name(track) not in track_names:
            continue
        for note in iter_note_dicts(track):
            offset_ticks = _signed_grid_offset_ticks(note["start"], sixteenth)
            tempo_us = _tempo_at(tempo_ticks, tempo_values, note["start"])
            offsets_ms.append(offset_ticks * tempo_us / (ppq * 1000.0))
    return offsets_ms


def _estilo_timing(
    req: dict[str, Any],
    family: str,
    timing_key: str,
    target_ms: float,
    edit_track_names: list[str],
    source_mid: mido.MidiFile,
    rendered_mid: mido.MidiFile,
) -> RequisitoVerdict:
    """Vies de timing que o render ACRESCENTOU sobre a origem.

    Mede o DELTA origem->render, como o resto deste modulo, nunca a
    distancia absoluta do render ate a grade de semicolcheia: numa origem
    nao quantizada (take humano tocando 12ms atras da grade) o feel da
    origem domina a mediana e reprova um render que aplicou exatamente o
    vies pedido. O que o plano declara e o deslocamento que o arranjador
    deve INTRODUZIR, e o unico jeito de medir isso e subtrair a mediana da
    origem da mediana do render.

    A medicao roda SO nas tracks de `plan.edits[]` da familia: sao as unicas
    que tem contraparte na origem (logo, delta) e as unicas que o
    `profile` da edit desloca. Track GERADA por `plan.elements[]` nasce na
    grade por construcao — jogar sua mediana aqui dentro so dilui o vies
    das editadas.
    """
    ppq = rendered_mid.ticks_per_beat or 0
    if ppq <= 0:
        return _nao_verificavel(req, "MIDI renderizado sem ticks_per_beat valido")
    if not edit_track_names:
        return _nao_verificavel(
            req,
            f"a familia {family!r} nao tem track em plan.edits[] — vies de "
            f"timing e medido como delta origem->render, e track gerada por "
            f"plan.elements[] nao tem contraparte na origem para comparar",
        )

    source_offsets = _grid_offsets_ms(source_mid, edit_track_names)
    offsets_ms = _grid_offsets_ms(rendered_mid, edit_track_names)

    if len(offsets_ms) < _MIN_TIMING_SAMPLES or len(source_offsets) < _MIN_TIMING_SAMPLES:
        return _nao_verificavel(
            req,
            f"apenas {len(source_offsets)} nota(s) na origem e "
            f"{len(offsets_ms)} no render nas track(s) editadas da familia "
            f"{family!r} — amostra insuficiente para medir vies de timing",
        )

    median_origem = statistics.median(source_offsets)
    median_render = statistics.median(offsets_ms)
    median_ms = median_render - median_origem
    diff = abs(median_ms - target_ms)
    evidencia = {
        "eixo": "timing",
        "parametro": timing_key,
        "alvo_ms": target_ms,
        "medido_ms": round(median_ms, 3),
        "mediana_origem_ms": round(median_origem, 3),
        "mediana_render_ms": round(median_render, 3),
        "tolerancia_ms": TIMING_BIAS_TOLERANCE_MS,
        "n_amostras": len(offsets_ms),
        "n_amostras_origem": len(source_offsets),
    }
    if diff <= TIMING_BIAS_TOLERANCE_MS:
        return _atendido(req, evidencia)
    if diff <= TIMING_BIAS_TOLERANCE_MS * 3:
        return _parcial(
            req, evidencia,
            f"vies medido ({median_ms:.2f}ms) fora da tolerancia de "
            f"{TIMING_BIAS_TOLERANCE_MS}ms do alvo ({target_ms}ms)",
        )
    return _nao_atendido(
        req, evidencia,
        f"vies medido ({median_ms:.2f}ms) muito distante do alvo "
        f"({target_ms}ms declarado em {timing_key!r})",
    )


def _estilo_velocity(
    req: dict[str, Any],
    family: str,
    velocity_key: str,
    target: Any,
    notes: list[RenderedNote],
    escopo: str,
) -> RequisitoVerdict:
    """Velocity das notas medidas contra o parametro declarado: par
    `[min, max]` vira percentual de notas DENTRO da faixa; escalar vira
    mediana medida contra tolerancia.

    `escopo` diz de ONDE vieram as notas — `familia` (todas as notas da
    familia no render) ou `tecnica:<canonico>` (so as notas ACRESCENTADAS,
    quando o parametro e de escopo de tecnica). O chamador decide; ver
    `_verdict_estilo`.
    """
    if len(notes) < _MIN_TIMING_SAMPLES:
        return _nao_verificavel(
            req,
            f"apenas {len(notes)} nota(s) medida(s) na familia {family!r} "
            f"(escopo {escopo}) no MIDI renderizado — amostra insuficiente "
            f"para medir velocity",
        )
    velocities = [n.velocity for n in notes]
    if isinstance(target, (list, tuple)):
        lo, hi = float(target[0]), float(target[1])
        dentro = sum(1 for v in velocities if lo <= v <= hi)
        pct = round(dentro / len(velocities) * 100, 2)
        evidencia = {
            "eixo": "velocity",
            "parametro": velocity_key,
            "escopo": escopo,
            "faixa_alvo": [lo, hi],
            "notas_medidas": len(velocities),
            "notas_dentro_da_faixa": dentro,
            "dentro_pct": pct,
            "minimo_pct": VELOCITY_RANGE_MIN_PCT,
        }
        if pct >= VELOCITY_RANGE_MIN_PCT:
            return _atendido(req, evidencia)
        if pct >= VELOCITY_RANGE_PARTIAL_PCT:
            return _parcial(
                req, evidencia,
                f"apenas {pct}% das notas medidas (escopo {escopo}) caem "
                f"na faixa [{lo}, {hi}] declarada em {velocity_key!r}",
            )
        return _nao_atendido(
            req, evidencia,
            f"so {pct}% das notas medidas (escopo {escopo}) respeitam a "
            f"faixa [{lo}, {hi}] declarada em {velocity_key!r}",
        )

    target_v = float(target)
    median_v = statistics.median(velocities)
    diff = abs(median_v - target_v)
    evidencia = {
        "eixo": "velocity",
        "parametro": velocity_key,
        "escopo": escopo,
        "alvo": target_v,
        "mediana_medida": round(median_v, 3),
        "tolerancia": VELOCITY_MEDIAN_TOLERANCE,
        "notas_medidas": len(velocities),
    }
    if diff <= VELOCITY_MEDIAN_TOLERANCE:
        return _atendido(req, evidencia)
    if diff <= VELOCITY_MEDIAN_TOLERANCE * 3:
        return _parcial(
            req, evidencia,
            f"mediana de velocity medida ({median_v}) fora da tolerancia de "
            f"{VELOCITY_MEDIAN_TOLERANCE} do alvo ({target_v})",
        )
    return _nao_atendido(
        req, evidencia,
        f"mediana de velocity medida ({median_v}) muito distante do alvo "
        f"({target_v} declarado em {velocity_key!r})",
    )


def _verdict_estilo(
    req: dict[str, Any],
    plan: ArrangementPlan,
    source_by_name: dict[str, list[RenderedTrack]],
    rendered_by_name: dict[str, list[RenderedTrack]],
    rendered_tracks: list[RenderedTrack],
    source_mid: mido.MidiFile,
    rendered_mid: mido.MidiFile,
) -> RequisitoVerdict:
    family = req["familia"]
    if family not in STYLE_FAMILIES:
        return _nao_verificavel(
            req,
            f"tipo 'estilo' exige familia em {list(STYLE_FAMILIES)}; "
            f"recebido {family!r}",
        )

    family_style = (plan.style or {}).get(family)
    parameters = dict(family_style.parameters or {}) if family_style is not None else {}
    if not parameters:
        return _nao_verificavel(
            req,
            f"plan.style.{family}.parameters nao declara parametro nenhum — "
            f"sem numero declarado nao ha o que medir contra o render",
        )

    track_names = _family_track_names(plan, family, rendered_tracks)
    if not track_names:
        return _nao_verificavel(
            req,
            f"familia {family!r} nao aparece em plan.edits[].profile nem em "
            f"plan.elements[].role — nao ha track da familia para medir",
        )

    haystack = _normalize_text(f"{req['alvo']} {req['descricao']}")
    timing_key = _timing_parameter_key(parameters)
    if timing_key is not None and isinstance(parameters[timing_key], (list, tuple)):
        timing_key = None  # o eixo de timing so mede alvo escalar
    velocity_key = _velocity_parameter_key(parameters)

    # O eixo sai do TEXTO do requisito, como em `reducao` — nunca da ordem
    # em que este arquivo testa os eixos. Escolher por ordem de codigo faz o
    # `velocity` declarado sumir do veredito e da evidencia sempre que
    # houver qualquer chave de timing escalar: parametro aceito, validado e
    # depois ignorado e parametro mentiroso (AGENTS.md).
    quer_timing = _has_keyword(haystack, _TIMING_TEXT_KEYWORDS)
    quer_velocity = _has_keyword(haystack, _VELOCITY_TEXT_KEYWORDS)
    if quer_velocity and not quer_timing:
        ordem = (("velocity", velocity_key), ("timing", timing_key))
    else:
        ordem = (("timing", timing_key), ("velocity", velocity_key))

    eixo, chave = next(((e, k) for e, k in ordem if k is not None), (None, None))
    if eixo is None or chave is None:
        return _nao_verificavel(
            req,
            f"nenhum parametro de plan.style.{family}.parameters "
            f"({sorted(parameters)}) casa com um eixo que este validador sabe "
            f"medir no MIDI: vies de timing (*timing*_bias/_offset, escalar) "
            f"ou velocity (*velocity*/*dinamica*, escalar ou faixa "
            f"[min, max])",
        )

    extra: dict[str, Any] = {
        "parametros_declarados": sorted(parameters),
        "parametros_nao_medidos": sorted(k for k in parameters if k != chave),
    }

    if eixo == "timing":
        scoped = _technique_scoped_parameter(family_style, chave)
        if scoped is not None:
            # Parametro de escopo de tecnica (ex.: `timing_offset_ms_laidback`
            # de `drums.ghost_notes`) descreve o deslocamento do ORNAMENTO,
            # nao a mediana da familia inteira. O eixo de timing mede a
            # mediana das tracks editadas e nao sabe isolar o ornamento, entao
            # a resposta honesta e nao medir — nunca reprovar a familia por um
            # numero que nunca foi promessa dela.
            return _nao_verificavel(
                req,
                f"{chave!r} e parametro da tecnica {scoped!r} no manual: vale "
                f"para o ornamento que a tecnica insere, nao para a mediana de "
                f"timing da familia {family!r}, que e o que este eixo mede",
                extra,
            )
        verdict = _estilo_timing(
            req, family, chave, float(parameters[chave]),
            _edit_track_names(plan, family), source_mid, rendered_mid,
        )
    else:
        scoped = _technique_scoped_parameter(family_style, chave)
        if scoped is not None:
            # Mesma leitura de `_verdict_tecnica`: `velocity` e nome de
            # parametro DE TECNICA no manual (ex.: `drums.ghost_notes` ->
            # [20, 45]) e o nivel `technique` tem contrato de NAO mexer na
            # velocity estrutural. Exigir a faixa da familia inteira e exigir
            # o estruturalmente impossivel; a faixa vale para o que a tecnica
            # ACRESCENTOU.
            notes = _family_added_notes(
                plan, family, source_by_name, rendered_by_name, rendered_tracks,
            )
            escopo = f"tecnica:{scoped}"
        else:
            notes = [
                n
                for t in _family_rendered_tracks(
                    plan, family, rendered_by_name, rendered_tracks,
                )
                for n in t.notes
            ]
            escopo = "familia"
        verdict = _estilo_velocity(
            req, family, chave, parameters[chave], notes, escopo,
        )

    return replace(verdict, evidencia={**verdict.evidencia, **extra})


# --- tipo: restricao ---------------------------------------------------------


def _verdict_restricao(
    req: dict[str, Any], plan: ArrangementPlan, rendered_tracks: list[RenderedTrack],
) -> RequisitoVerdict:
    family = req["familia"]
    if family not in STYLE_FAMILIES:
        haystack = _normalize_text(f"{req['alvo']} {req['descricao']}")
        family = next((f for f in STYLE_FAMILIES if f in haystack), None)
    if family is None:
        return _nao_verificavel(
            req,
            "restricao nao referencia uma familia de "
            f"{list(STYLE_FAMILIES)} — verificacao mecanica de restricao de "
            "padrao especifico (ex.: 'nada de double kick') fica fora do "
            "escopo automatico desta rodada",
        )

    element_ids = _element_ids_for_family(plan, family)
    element_id_set = frozenset(element_ids)
    notes = _notes_for_element_ids(rendered_tracks, element_id_set) if element_ids else []
    evidencia = {
        "familia": family,
        "elementos_gerados": element_ids,
        "notas_criadas": len(notes),
    }
    if element_ids or notes:
        return _nao_atendido(
            req, evidencia,
            f"familia {family!r} foi criada apesar da restricao — "
            f"elemento(s) {element_ids} produziram {len(notes)} nota(s)",
        )
    return _atendido(req, evidencia)


# --- tipo: intensidade -------------------------------------------------------


def _verdict_intensidade(
    req: dict[str, Any],
    plan: ArrangementPlan,
    source_by_name: dict[str, list[RenderedTrack]],
    rendered_by_name: dict[str, list[RenderedTrack]],
) -> RequisitoVerdict:
    family = req["familia"]
    candidate_families = [family] if family in STYLE_FAMILIES else list(STYLE_FAMILIES)

    edit_names: list[str] = []
    chosen_family: str | None = None
    for fam in candidate_families:
        names = _edit_track_names(plan, fam)
        if names:
            edit_names, chosen_family = names, fam
            break
    if not edit_names or chosen_family is None:
        return _nao_verificavel(
            req,
            "nenhuma track em plan.edits foi encontrada para medir "
            "intensidade/hierarquia (origem vs renderizado)",
        )

    source_notes = _notes_for_names(source_by_name, edit_names)
    rendered_notes = _notes_for_names(rendered_by_name, edit_names)
    if len(source_notes) < _MIN_TIMING_SAMPLES or len(rendered_notes) < _MIN_TIMING_SAMPLES:
        return _nao_verificavel(
            req,
            f"apenas {len(source_notes)} nota(s) na origem e "
            f"{len(rendered_notes)} no render — amostra insuficiente",
        )

    vel_antes = len({n.velocity for n in source_notes})
    vel_depois = len({n.velocity for n in rendered_notes})
    ghost_antes = sum(1 for n in source_notes if n.velocity <= GHOST_VELOCITY_CEILING)
    ghost_depois = sum(1 for n in rendered_notes if n.velocity <= GHOST_VELOCITY_CEILING)

    evidencia = {
        "familia": chosen_family,
        "velocities_distintas_antes": vel_antes,
        "velocities_distintas_depois": vel_depois,
        "ghost_notes_antes": ghost_antes,
        "ghost_notes_depois": ghost_depois,
    }

    hierarquia_emergiu = vel_depois > vel_antes
    ghosts_emergiram = ghost_depois > ghost_antes
    if hierarquia_emergiu and ghosts_emergiram:
        return _atendido(req, evidencia)
    if hierarquia_emergiu or ghosts_emergiram:
        return _parcial(
            req, evidencia,
            "so uma das duas dimensoes de intencao emergiu sobre a origem "
            "(hierarquia de velocity, ghost notes) — a outra continua "
            "identica a origem",
        )
    return _nao_atendido(
        req, evidencia,
        "nem hierarquia de velocity nem ghost notes emergiram sobre a origem",
    )


# --- API principal ----------------------------------------------------------


def validate_compliance(
    *,
    requisitos: list[dict[str, Any]],
    plan: ArrangementPlan,
    source_tracks: list[RenderedTrack],
    rendered_tracks: list[RenderedTrack],
    harmony_issues: list[HarmonyIssue],
    placement_issues: list[PlacementIssue],
    source_mid: mido.MidiFile,
    rendered_mid: mido.MidiFile,
) -> ComplianceReport:
    """Roda a verificacao de conformidade para cada `requisito` do brief.

    - `source_tracks`/`rendered_tracks`: `RenderedTrack` do MIDI de origem e
      do MIDI renderizado — mesma forma que `validate_harmony`/
      `validate_placement` consomem. Tracks de origem/edicao carregam
      `element_id` sintetico `source:<nome>` (`render._rendered_tracks_from_instrument_list`);
      este modulo casa por `track_name`, nao por `element_id`.
    - `harmony_issues`/`placement_issues`: saida JA CALCULADA dos
      respectivos validadores sobre `rendered_tracks` — reusado, nunca
      recalculado, para `criacao` confirmar campo harmonico/secoes.
    - `source_mid`/`rendered_mid`: MIDI de origem e renderizado abertos com
      `mido` — usados por `reducao` (deteccao de virada em ticks) e
      `estilo` (vies de timing em ticks).
    """
    source_by_name = _tracks_by_name(source_tracks)
    rendered_by_name = _tracks_by_name(rendered_tracks)

    verdicts: list[RequisitoVerdict] = []
    for req in requisitos:
        tipo = req["tipo"]
        if tipo == "tecnica":
            v = _verdict_tecnica(
                req, plan, source_by_name, rendered_by_name, rendered_tracks,
            )
        elif tipo == "reducao":
            v = _verdict_reducao(
                req, plan, source_by_name, rendered_by_name, rendered_tracks,
                source_mid, rendered_mid,
            )
        elif tipo == "criacao":
            v = _verdict_criacao(
                req, plan, rendered_tracks, harmony_issues, placement_issues,
            )
        elif tipo == "estilo":
            v = _verdict_estilo(
                req, plan, source_by_name, rendered_by_name, rendered_tracks,
                source_mid, rendered_mid,
            )
        elif tipo == "restricao":
            v = _verdict_restricao(req, plan, rendered_tracks)
        elif tipo == "intensidade":
            v = _verdict_intensidade(req, plan, source_by_name, rendered_by_name)
        else:
            # Vocabulario fechado ja garantido em brief_schema; guarda extra
            # caso a lista mude sem atualizar este modulo.
            v = _nao_verificavel(req, f"tipo de requisito desconhecido: {tipo!r}")
        verdicts.append(v)

    conforme = not any(v.status in BLOCKING_STATUSES for v in verdicts)
    return ComplianceReport(conforme=conforme, requisitos=tuple(verdicts))


__all__ = [
    "BLOCKING_STATUSES",
    "GHOST_VELOCITY_CEILING",
    "REDUCAO_MIN_PCT",
    "STATUS_ATENDIDO",
    "STATUS_NAO_ATENDIDO",
    "STATUS_NAO_VERIFICAVEL",
    "STATUS_PARCIAL",
    "STATUSES",
    "TIMING_BIAS_TOLERANCE_MS",
    "VELOCITY_MEDIAN_TOLERANCE",
    "VELOCITY_RANGE_MIN_PCT",
    "VELOCITY_RANGE_PARTIAL_PCT",
    "ComplianceReport",
    "RequisitoVerdict",
    "blocking_requisitos",
    "format_report",
    "validate_compliance",
]
