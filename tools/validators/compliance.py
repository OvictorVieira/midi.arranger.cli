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
- `reducao`: hoje mede especificamente reducao de DENSIDADE DE VIRADA de
  bateria (`tools.techniques._fill_detection`), o unico caso do vocabulario
  do motor com deteccao mecanica pronta — so roda quando `alvo`/`descricao`
  menciona virada/fill; reducao de outra natureza (cortar instrumentacao
  de uma secao, por exemplo) sai `nao_verificavel` com o motivo explicito,
  em vez de fabricar uma metrica sem base.
- `criacao`: confere que a familia pedida tem `plan.elements[]` cujo role
  mapeia pra ela (`ROLE_STYLE_FAMILIES`), que essas tracks renderizaram
  nota, e reusa os `HarmonyIssue`/`PlacementIssue` JA CALCULADOS pelos
  validadores harmonico/placement (nunca reimplementa deteccao de campo
  harmonico) para confirmar que o conteudo criado fica dentro do campo
  harmonico e das secoes declaradas.
- `estilo`: mede o vies de timing REAL das tracks editadas da familia
  contra a grade de semicolcheia e confere contra
  `plan.style.<familia>.parameters.<...timing..._ms|..._bias>` dentro de
  uma tolerancia — a unica dimensao de estilo com medicao mecanica pronta
  nesta rodada (densidade de tecnica fica coberta pelo tipo `tecnica`
  acima); outro parametro de estilo sai `nao_verificavel`.
- `restricao`: prova por AUSENCIA — nenhum `plan.elements[]` da familia
  vetada, confirmado contra a contagem real de notas renderizadas daqueles
  elementos (zero elemento -> zero nota, nunca confianca cega no plano).
- `intensidade`: compara a ORIGEM contra o RENDERIZADO na mesma track
  editada — quantas velocities distintas existiam antes/depois (hierarquia
  de acento) e quantas ghost notes existiam antes/depois — o mesmo par de
  numeros que provou o defeito historico do motor (`ENTRE NOS.mid`: 1
  velocity distinta, 0 ghost antes de qualquer tecnica).

## O que fica fora desta rodada, declarado explicitamente

`criacao`/`restricao` so classificam familia via `plan.elements[].role`
(`ROLE_STYLE_FAMILIES`) e `plan.edits[].profile` — nunca por heuristica de
nome de instrumento MIDI, porque o plano ja e a fonte de verdade estrutural
usada pelo resto do motor (render/plan.validate). `estilo` so mede vies de
timing; density de tecnica ja tem o tipo `tecnica` dedicado. `reducao` so
mede virada de bateria. Um requisito fora dessas formas concretas sai
`nao_verificavel` — nunca inventa numero para nao ficar em branco.
"""

from __future__ import annotations

import statistics
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import mido

from ..constants import VELOCITY_RANGES
from ..plan import ROLE_STYLE_FAMILIES, STYLE_FAMILIES, ArrangementPlan
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

_FILL_KEYWORDS: frozenset[str] = frozenset({"virada", "viradas", "fill", "fills"})
"""Palavras que precisam aparecer em `alvo`/`descricao` para o tipo
`reducao` rodar a medicao de densidade de virada — a unica forma de
`reducao` com deteccao mecanica pronta nesta rodada (ver docstring do
modulo)."""

_MIN_TIMING_SAMPLES = 4
"""Amostra minima de notas pra `estilo` (vies de timing) e `intensidade`
declararem medicao — abaixo disso a mediana e ruido, nao evidencia."""


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


def _first_tempo_us(mid: mido.MidiFile) -> int:
    for track in mid.tracks:
        for msg in track:
            if msg.is_meta and msg.type == "set_tempo":
                return int(msg.tempo)
    return 500_000  # 120 BPM — default MIDI/mido.


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


def _nao_verificavel(req: dict[str, Any], motivo: str) -> RequisitoVerdict:
    return RequisitoVerdict(
        req["id"], req["descricao"], STATUS_NAO_VERIFICAVEL, {}, motivo,
    )


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

    edit_names = _edit_track_names(plan, family)
    element_ids = frozenset(_element_ids_for_family(plan, family))

    source_notes = _notes_for_names(source_by_name, edit_names)
    rendered_edit_notes = _notes_for_names(rendered_by_name, edit_names)
    added = _added_notes(source_notes, rendered_edit_notes)
    added += _notes_for_element_ids(rendered_tracks, element_ids)

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


def _verdict_reducao(
    req: dict[str, Any], source_mid: mido.MidiFile, rendered_mid: mido.MidiFile,
) -> RequisitoVerdict:
    haystack = _normalize_text(f"{req['alvo']} {req['descricao']}")
    if not any(kw in haystack for kw in _FILL_KEYWORDS):
        return _nao_verificavel(
            req,
            "tipo 'reducao' so mede mecanicamente densidade de virada de "
            "bateria nesta rodada (palavra 'virada'/'fill' no alvo ou "
            "descricao); reducao de outra natureza (ex.: cortar "
            "instrumentacao de uma secao) fica fora do escopo automatico",
        )

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
    reducao_pct = 0.0 if antes == 0 else round((antes - depois) / antes * 100, 2)

    evidencia = {
        "viradas_antes": len(windows),
        "viradas_depois": len(rendered_windows),
        "notas_em_virada_antes": antes,
        "notas_em_virada_depois": depois,
        "reducao_pct": reducao_pct,
    }
    if depois >= antes:
        return _nao_atendido(req, evidencia, "densidade de virada nao caiu")
    if not rendered_windows:
        return _parcial(
            req, evidencia,
            "densidade caiu, mas nenhuma virada continua detectavel como "
            "virada — a estrutura (compassos de virada) pode ter se perdido",
        )
    return _atendido(req, evidencia)


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


def _timing_parameter_key(parameters: dict[str, Any]) -> str | None:
    for key in parameters:
        lower = key.lower()
        if "timing" in lower and ("ms" in lower or "bias" in lower):
            return key
    return None


def _verdict_estilo(
    req: dict[str, Any], plan: ArrangementPlan, rendered_mid: mido.MidiFile,
) -> RequisitoVerdict:
    family = req["familia"]
    if family not in STYLE_FAMILIES:
        return _nao_verificavel(
            req,
            f"tipo 'estilo' exige familia em {list(STYLE_FAMILIES)}; "
            f"recebido {family!r}",
        )

    family_style = (plan.style or {}).get(family)
    parameters = family_style.parameters if family_style is not None else {}
    timing_key = _timing_parameter_key(parameters or {})
    if timing_key is None:
        return _nao_verificavel(
            req,
            f"plan.style.{family}.parameters nao declara parametro de vies "
            f"de timing (*_ms/*_bias) para medir — o unico eixo de estilo "
            f"com medicao mecanica pronta nesta rodada",
        )
    target = parameters[timing_key]
    if isinstance(target, (list, tuple)):
        return _nao_verificavel(
            req,
            f"{timing_key!r} e declarado como par [min, max]; medicao de "
            f"vies pontual so se aplica a um numero escalar",
        )
    target_ms = float(target)

    edit_names = _edit_track_names(plan, family)
    if not edit_names:
        return _nao_verificavel(
            req,
            f"nenhuma track em plan.edits com profile={family!r} — 'estilo' "
            f"so mede tracks editadas nesta rodada",
        )

    ppq = rendered_mid.ticks_per_beat or 0
    if ppq <= 0:
        return _nao_verificavel(req, "MIDI renderizado sem ticks_per_beat valido")
    sixteenth = max(1, ppq // 4)
    tempo_us = _first_tempo_us(rendered_mid)

    offsets_ms: list[float] = []
    for track in rendered_mid.tracks:
        if _mido_track_name(track) not in edit_names:
            continue
        for note in iter_note_dicts(track):
            offset_ticks = _signed_grid_offset_ticks(note["start"], sixteenth)
            offsets_ms.append(offset_ticks * tempo_us / (ppq * 1000.0))

    if len(offsets_ms) < _MIN_TIMING_SAMPLES:
        return _nao_verificavel(
            req,
            f"apenas {len(offsets_ms)} nota(s) na(s) track(s) editada(s) — "
            f"amostra insuficiente para medir vies de timing",
        )

    median_ms = statistics.median(offsets_ms)
    diff = abs(median_ms - target_ms)
    evidencia = {
        "parametro": timing_key,
        "alvo_ms": target_ms,
        "medido_ms": round(median_ms, 3),
        "tolerancia_ms": TIMING_BIAS_TOLERANCE_MS,
        "n_amostras": len(offsets_ms),
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
            v = _verdict_reducao(req, source_mid, rendered_mid)
        elif tipo == "criacao":
            v = _verdict_criacao(
                req, plan, rendered_tracks, harmony_issues, placement_issues,
            )
        elif tipo == "estilo":
            v = _verdict_estilo(req, plan, rendered_mid)
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
    "STATUS_ATENDIDO",
    "STATUS_NAO_ATENDIDO",
    "STATUS_NAO_VERIFICAVEL",
    "STATUS_PARCIAL",
    "STATUSES",
    "TIMING_BIAS_TOLERANCE_MS",
    "ComplianceReport",
    "RequisitoVerdict",
    "blocking_requisitos",
    "format_report",
    "validate_compliance",
]
