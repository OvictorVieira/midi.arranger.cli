"""`report` — relatorio de proveniencia, da influencia ao resultado MIDI (issue #77).

## A pergunta que este modulo responde

Depois do render, musico e agente precisam poder responder, sem abrir o MIDI
e sem confiar em memoria de conversa:

    o que foi pesquisado -> de qual fonte -> como virou tecnica ->
    onde a tecnica foi aplicada -> qual evidencia mensuravel existe no MIDI
    final -> e o que NAO pode ser aplicado nem verificado.

Este modulo monta essa cadeia lendo SO artefatos que ja existem. Ele nao
pesquisa, nao mapeia, nao aplica e nao valida nada por conta propria:

| elo da cadeia | de onde vem | modulo |
|---|---|---|
| `source`    | `InfluenceProfile.sources[]` | `tools/influence.py` |
| `finding`   | `InfluenceProfile.findings[]` | `tools/influence.py` |
| `mapping`   | `compile_influence()` (`MAPPING_RULES`, `INFLUENCE_MAPPING_VERSION`) | `tools/influence_compile.py` |
| `technique` | `plan.style.<familia>.techniques[]` + `authorized_techniques[]` do brief | `tools/plan.py`, `tools/brief_schema.py` |
| `track`     | carimbo `meta 0x01 text` em tick 0 (`techniques=[...]`) | escrito por `tools/render.py` |
| `section`   | `plan.elements[].sections` (elemento gerado) | `tools/plan.py` |
| `metric`    | medicao direta do MIDI renderizado + vereditos dos validadores | `mido`, `tools/validators/` |

## A regra central (issue #77)

**"Aplicada com sucesso" so aparece quando existe evidencia objetiva de
validador.** Sem validador que tenha COBERTO aquela track, o status e
`aplicada_nao_verificavel` — nunca "ok". E a mesma familia de regra que o
AGENTS.md repete: nunca apresentar ausencia de informacao como fato, nunca
no-op silencioso, nunca numero sem fonte. Por isso `ValidatorRun` carrega
`covered_tracks` explicito: ausencia de issue NAO prova que o validador
olhou para aquela track — so a lista do que ele recebeu prova.

`covered_tracks` so vale para validador POR TRACK (`scope="per_track"`).
Validador de escopo global (persona, colisao, conformidade) nao percorre
track nenhuma: ele declara `scope="global"`, nunca produz veredito `limpo`
por track, e seus erros — que nao carregam campo `track` — valem para
TODOS os alvos, rebaixando o status de qualquer tecnica que dependa deles.
Preencher `covered_tracks` de validador global com a lista de tracks
renderizadas foi o defeito que fez o relatorio afirmar cobertura que nunca
existiu (revisao adversarial do PR #119).

Autorizacao tambem e elo: tecnica carimbada na track sem autorizacao
rastreavel no brief desta execucao — ou sem declaracao no plano desta
execucao, o caso do carimbo remanescente de uma rodada anterior — nunca
vira `aplicada_verificada`. AGENTS.md: ausencia de autorizacao significa
NENHUMA tecnica.

Todo elo que falta vira entrada explicita em `missing_links`, com codigo
estavel, caminho e motivo. O relatorio nunca preenche elo ausente com
suposicao.

## Anticopia

- `InfluenceFinding.summary` (prosa livre, potencialmente longa) NUNCA e
  copiado: o relatorio registra so `summary_present` e `summary_chars`.
- `semantic_value` e citado apenas quando cabe em `MAX_QUOTE_CHARS`; acima
  disso vira `null` com nota `OMITIDO_LIMITE_CITACAO`.
- Qualquer string citada passa pela MESMA barreira anticopia do perfil
  (`tools.influence._validate_free_string`); string que a barreira recusa
  vira `null` com nota `OMITIDO_CONTEUDO_MUSICAL` em vez de derrubar o
  relatorio.
- Da fonte, so metadado de citacao (id, url, titulo, data) — nunca texto.

## Determinismo

Sem relogio, sem rede, sem `random`. Toda lista sai ordenada por chave
estavel (familia+tecnica, id de fonte/achado, indice de track). Mesma
entrada produz `json.dumps(..., sort_keys=True)` byte-identico.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mido

from . import influence as influence_mod
from . import plan as plan_mod
from .influence import INFLUENCE_SCHEMA_VERSION, InfluenceProfile
from .influence_compile import (
    INFLUENCE_MAPPING_VERSION,
    CompileResult,
)
from .plan import ArrangementPlan, StyleTechnique
from .render import STAMP_PREFIX
from .techniques.engine import SUPPORTED_TECHNIQUES
from .tracks import TrackNameError, name_for_element

REPORT_SCHEMA_VERSION = 2
"""Muda quando a forma do relatorio muda de modo observavel.

2: `ValidatorRun.scope`, `validator_evidence.erros_globais`,
`techniques.aplicadas_verificadas`/`por_status`, o status
`aplicada_sem_autorizacao`, `hashes.brief_path_divergente` e
`targets[].declarada_neste_plano` — correcoes da revisao adversarial do
PR #119."""

MAX_QUOTE_CHARS = 120
"""Teto de citacao de campo semantico da pesquisa. Acima disso o relatorio
omite: ele registra proveniencia, nao reproduz a fonte."""

VALIDATOR_NAMES: tuple[str, ...] = (
    "anticopia",
    "artificialidade",
    "colisao",
    "conformidade",
    "harmonia",
    "persona",
    "placement",
)
"""Os sete validadores que a issue #77 exige no relatorio. Nome extra
(ex.: `transicoes`) e aceito e reportado igual; nome desta tupla que nao
tiver `ValidatorRun` vira `missing_link` de `metric`."""

TECHNIQUE_STATUSES: tuple[str, ...] = (
    "aplicada_verificada",
    "aplicada_com_erro",
    "aplicada_sem_autorizacao",
    "aplicada_nao_verificavel",
    "autorizada_nao_aplicada",
    "sugerida_nao_autorizada",
    "nao_recomendada",
    "nao_suportada",
)

MISSING_LINK_CODES: tuple[str, ...] = (
    "source",
    "finding",
    "mapping",
    "technique",
    "track",
    "metric",
)

TRACK_VERDICTS: tuple[str, ...] = ("limpo", "com_erro", "sem_cobertura")

VALIDATOR_SCOPES: tuple[str, ...] = ("per_track", "global")
"""`per_track`: o validador percorre track a track e `covered_tracks` diz
quais ele REALMENTE recebeu. `global`: o validador olha o plano/o arquivo
como um todo (persona, colisao, conformidade) — nao cobre track nenhuma e
seus erros valem para todos os alvos."""


# --- carimbo ---------------------------------------------------------------

STAMP_FAMILY_PREFIX = "midi-arranger"
"""Prefixo de familia do carimbo, sem versao. Texto que comeca com ele mas
nao casa com `STAMP_PREFIX` (`midi-arranger v1`) e carimbo de OUTRA versao:
vira problema declarado, nunca "track sem carimbo" — sumir com a tecnica do
relatorio por causa de versao futura seria mentir por omissao."""

STAMP_KNOWN_KEYS: frozenset[str] = frozenset({
    "role", "plugin", "preset", "verified", "techniques",
    "suggested_plugin", "suggested_preset", "suggested_verified",
})
"""Chaves que `tools.render._format_stamp` escreve. Chave fora desta lista
nao e descartada em silencio: vira problema declarado no carimbo."""


@dataclass(frozen=True)
class TrackStamp:
    """Carimbo `midi-arranger v1|...` lido de uma track do MIDI renderizado.

    E a UNICA prova, no arquivo final, de que uma tecnica foi despachada
    naquela track — `tools/render.py` grava, este modulo so le.
    """

    track_index: int
    track_name: str
    role: str
    techniques: tuple[str, ...] = ()
    plugin: str | None = None
    preset: str | None = None
    verified: bool | None = None
    problems: tuple[str, ...] = ()
    """Defeitos observados na leitura do carimbo (versao desconhecida,
    segmento sem `=`, chave desconhecida, `techniques` sem colchetes,
    carimbo sem campo nenhum). Carimbo corrompido vira `missing_link`, nao
    desaparece do relatorio."""


def parse_stamp(text: str) -> dict[str, Any] | None:
    """Devolve os campos do carimbo, ou `None` se `text` nao for carimbo.

    Formato (ver `tools.render._format_stamp`): `midi-arranger v1|k=v|...`,
    com `techniques=[a,b]`. Valor nunca contem `|` — o render recusa.

    `None` significa "isto nao e carimbo do midi-arranger" e so isso. Texto
    que e carimbo mas esta corrompido, incompleto ou de outra versao volta
    como dict com a chave `problemas` preenchida: quem le decide o que
    fazer, mas nada some em silencio. Um `midi-arranger v2` devolvendo
    `None` fazia a track virar "sem carimbo" e a tecnica sumir do relatorio
    — mentira por omissao, nao tolerancia a versao futura.
    """
    stripped = text.strip()
    if not stripped.startswith(STAMP_FAMILY_PREFIX):
        return None
    head = stripped.split("|", 1)[0].strip()
    rest = stripped[len(STAMP_PREFIX):]
    if not stripped.startswith(STAMP_PREFIX) or (rest and not rest.startswith("|")):
        return {"problemas": (f"versao_desconhecida:{head}",)}
    problems: list[str] = []
    fields: dict[str, Any] = {}
    for part in stripped.split("|")[1:]:
        if "=" not in part:
            problems.append(f"segmento_sem_igual:{part.strip()}")
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in STAMP_KNOWN_KEYS:
            problems.append(f"chave_desconhecida:{key}")
            continue
        if key == "techniques":
            inner = value.strip()
            if inner.startswith("[") and inner.endswith("]"):
                inner = inner[1:-1]
            else:
                problems.append("techniques_sem_colchetes")
            fields[key] = tuple(n for n in (t.strip() for t in inner.split(",")) if n)
        elif key in ("verified", "suggested_verified"):
            fields[key] = value.strip() == "true"
        else:
            fields[key] = value
    if not fields:
        problems.append("carimbo_sem_campos")
    elif "role" not in fields:
        problems.append("carimbo_sem_role")
    if problems:
        fields["problemas"] = tuple(problems)
    return fields


def read_stamps(mid: mido.MidiFile) -> tuple[TrackStamp, ...]:
    """Le o carimbo de cada track do MIDI renderizado.

    Track sem carimbo (track de origem nao declarada em `plan.edits`, que sai
    byte-identica) simplesmente nao aparece no resultado. Track com carimbo
    corrompido APARECE, com `problems` preenchido.
    """
    stamps: list[TrackStamp] = []
    for index, track in enumerate(mid.tracks):
        track_name = ""
        payload: dict[str, Any] | None = None
        for msg in track:
            if not msg.is_meta:
                continue
            if msg.type == "track_name" and not track_name:
                track_name = msg.name
            elif msg.type == "text":
                parsed = parse_stamp(msg.text)
                if parsed is not None:
                    payload = parsed
                    break
        if payload is None:
            continue
        stamps.append(TrackStamp(
            track_index=index,
            track_name=track_name,
            role=str(payload.get("role", "")),
            techniques=tuple(payload.get("techniques", ())),
            plugin=payload.get("plugin"),
            preset=payload.get("preset"),
            verified=payload.get("verified"),
            problems=tuple(payload.get("problemas", ())),
        ))
    return tuple(stamps)


# --- metrica medida no MIDI final ------------------------------------------


def track_metrics(track: mido.MidiTrack) -> dict[str, Any]:
    """Mede a track renderizada. So numero observado — nada inferido.

    Cada chave e uma medicao direta de evento MIDI, com nome que diz o que
    foi contado. Nao ha aqui nenhuma afirmacao do tipo "isto prova que a
    tecnica X rodou": a prova de aplicacao e o carimbo, e a prova de
    conformidade e o validador. Esta medicao existe para o musico poder
    conferir com o proprio olho o que mudou no arquivo.
    """
    velocities: list[int] = []
    pitches: set[int] = set()
    cc_numbers: dict[int, int] = {}
    pitch_bend = 0
    note_on = 0
    note_off = 0
    first_tick: int | None = None
    last_tick = 0
    abs_tick = 0
    for msg in track:
        abs_tick += msg.time
        if msg.is_meta:
            continue
        last_tick = abs_tick
        if msg.type == "note_on" and msg.velocity > 0:
            note_on += 1
            velocities.append(int(msg.velocity))
            pitches.add(int(msg.note))
            if first_tick is None:
                first_tick = abs_tick
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            note_off += 1
        elif msg.type == "control_change":
            cc_numbers[int(msg.control)] = cc_numbers.get(int(msg.control), 0) + 1
        elif msg.type == "pitchwheel":
            pitch_bend += 1
    return {
        "note_on_count": note_on,
        "note_off_count": note_off,
        "distinct_pitch_count": len(pitches),
        "velocity_min": min(velocities) if velocities else None,
        "velocity_median": float(statistics.median(velocities)) if velocities else None,
        "velocity_max": max(velocities) if velocities else None,
        "control_change_counts": {str(k): cc_numbers[k] for k in sorted(cc_numbers)},
        "pitch_bend_count": pitch_bend,
        "first_note_tick": first_tick,
        "last_event_tick": last_tick,
    }


# --- validadores ------------------------------------------------------------


@dataclass(frozen=True)
class ValidatorRun:
    """O que UM validador realmente rodou.

    - `executed`: False significa "nao rodou". O relatorio marca ausencia de
      metrica, jamais aprova por omissao.
    - `scope`: `per_track` (percorre track a track) ou `global` (olha plano
      ou arquivo inteiro). Validador global NAO cobre track nenhuma; quem o
      declarasse com `covered_tracks` cheio estaria fabricando cobertura.
    - `covered_tracks`: os nomes de track que o validador REALMENTE
      percorreu — so faz sentido com `scope="per_track"`, e o construtor
      recusa a combinacao contraria. Sem isso nao ha como distinguir "olhou
      e nao achou problema" de "nem olhou" — e essa distincao e a regra
      central da issue #77.
    - `issues`: os objetos de issue do proprio validador (dataclasses com
      `severity` e, quando o validador e por track, `track`). Vereditos de
      conformidade (`RequisitoVerdict`) tambem entram aqui.
    - `note`: motivo, quando `executed=False`.
    """

    name: str
    executed: bool = False
    covered_tracks: tuple[str, ...] = ()
    issues: tuple[Any, ...] = ()
    note: str = ""
    scope: str = "per_track"

    def __post_init__(self) -> None:
        if self.scope not in VALIDATOR_SCOPES:
            raise ValueError(
                f"scope invalido {self.scope!r}; use um de {VALIDATOR_SCOPES}",
            )
        if self.scope == "global" and self.covered_tracks:
            raise ValueError(
                f"validador {self.name!r} e de escopo global e nao pode "
                f"declarar covered_tracks — cobertura de track so existe "
                f"em validador per_track",
            )


_COMPLIANCE_ERROR_STATUSES = frozenset({"nao_atendido", "parcial"})


def _issue_severity(issue: Any) -> str:
    severity = getattr(issue, "severity", None)
    if isinstance(severity, str) and severity:
        return severity
    status = getattr(issue, "status", None)
    if isinstance(status, str):
        if status in _COMPLIANCE_ERROR_STATUSES:
            return "error"
        if status == "nao_verificavel":
            return "warning"
        return "info"
    return "error"


def _issue_to_dict(validator: str, issue: Any) -> dict[str, Any]:
    element_ids = getattr(issue, "element_ids", None)
    if element_ids is None:
        single = getattr(issue, "element_id", None)
        element_ids = (single,) if isinstance(single, str) and single else ()
    message = getattr(issue, "message", None)
    if not isinstance(message, str) or not message:
        message = getattr(issue, "motivo", None) or ""
    track = getattr(issue, "track", None)
    return {
        "validator": validator,
        "id": getattr(issue, "id", None) if isinstance(getattr(issue, "id", None), str) else None,
        "severity": _issue_severity(issue),
        "track": track if isinstance(track, str) and track else None,
        "element_ids": sorted(str(e) for e in element_ids),
        "message": str(message),
    }


def _validators_block(runs: Sequence[ValidatorRun]) -> dict[str, Any]:
    block: dict[str, Any] = {}
    for run in sorted(runs, key=lambda r: r.name):
        issues = [_issue_to_dict(run.name, i) for i in run.issues]
        errors = [i for i in issues if i["severity"] == "error"]
        block[run.name] = {
            "executado": bool(run.executed),
            "motivo": run.note or None,
            "escopo": run.scope,
            "tracks_cobertas": sorted(set(run.covered_tracks)),
            "erros": len(errors),
            # Erro sem `track` (RequisitoVerdict, PersonaIssue,
            # CollisionWarning) e erro de escopo global: vale para todo alvo
            # e rebaixa o status de qualquer tecnica que dependa deste
            # validador. Contabilizar so o que tem `track` deixava esses
            # erros invisiveis para o status.
            "erros_globais": len([e for e in errors if e["track"] is None]),
            "issues": sorted(
                issues,
                key=lambda i: (i["severity"], i["track"] or "", i["message"]),
            ),
        }
    return block


# --- citacao segura ---------------------------------------------------------


def _quote(value: str | None) -> tuple[str | None, str | None]:
    """Devolve `(texto_citavel, nota_de_omissao)`.

    Reusa a barreira anticopia do perfil de influencia em vez de escrever uma
    segunda heuristica: string que aquela barreira recusaria como conteudo
    musical nunca entra no relatorio.
    """
    if value is None:
        return None, None
    text = value.strip()
    if not text:
        return None, None
    if len(text) > MAX_QUOTE_CHARS:
        return None, "OMITIDO_LIMITE_CITACAO"
    try:
        influence_mod._validate_free_string(text, "report")
    except influence_mod.InfluenceValidationError:
        return None, "OMITIDO_CONTEUDO_MUSICAL"
    return text, None


# --- helpers de leitura dos artefatos ---------------------------------------


def _family_of(technique_name: str) -> str:
    return technique_name.split(".", 1)[0]


def _brief_family_entry(brief: dict[str, Any] | None, family: str) -> dict[str, Any]:
    if not isinstance(brief, dict):
        return {}
    style = brief.get("style")
    if not isinstance(style, dict):
        return {}
    entry = style.get(family)
    return entry if isinstance(entry, dict) else {}


def _brief_names(entry: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = entry.get(key) or []
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return tuple(dict.fromkeys(names))


def _plan_techniques(plan: ArrangementPlan) -> dict[tuple[str, str], StyleTechnique]:
    out: dict[tuple[str, str], StyleTechnique] = {}
    style = plan.style or {}
    for family, entry in style.items():
        for technique in getattr(entry, "techniques", []) or []:
            if isinstance(technique, StyleTechnique):
                out[(family, technique.name)] = technique
    return out


def _plan_sha256(plan: ArrangementPlan) -> str:
    payload = json.dumps(
        plan_mod.to_dict(plan), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _element_track_names(plan: ArrangementPlan) -> dict[str, Any]:
    """`{nome_de_track: Element}` para as tracks que o render vai gerar.

    Reusa `tools.tracks.name_for_element` — a mesma funcao que o render usa
    para nomear —, entao o casamento nunca depende de heuristica de nome.
    """
    mapping: dict[str, Any] = {}
    for element in plan.elements:
        meta = element.instrument or {}
        plugin = str(meta.get("plugin", "")).strip()
        preset = str(meta.get("preset", "")).strip()
        verified = bool(meta.get("verified", False))
        if not plugin or not preset:
            continue
        for layer in range(element.layers):
            display = element.id if element.layers == 1 else f"{element.id} L{layer + 1}"
            try:
                mapping[name_for_element(display, element.role, plugin, preset, verified)] = element
            except TrackNameError:
                continue
    return mapping


# --- montagem da cadeia -----------------------------------------------------


@dataclass
class _Link:
    family: str
    technique: str
    status: str = "autorizada_nao_aplicada"
    sources: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    mapping: dict[str, Any] | None = None
    plan_declaration: dict[str, Any] | None = None
    suggested: bool = False
    authorized: bool = False
    targets: list[dict[str, Any]] = field(default_factory=list)
    stale_stamp: bool = False
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "technique": self.technique,
            "status": self.status,
            "suggested": self.suggested,
            "authorized": self.authorized,
            "supported": self.technique in SUPPORTED_TECHNIQUES,
            "sources": self.sources,
            "findings": self.findings,
            "mapping": self.mapping,
            "plan_declaration": self.plan_declaration,
            "stale_stamp": self.stale_stamp,
            "targets": self.targets,
            "missing_links": sorted(set(self.missing)),
            "notes": sorted(set(self.notes)),
        }


def _finding_entry(
    finding: influence_mod.InfluenceFinding,
) -> dict[str, Any]:
    semantic, omission = _quote(finding.semantic_value)
    entry: dict[str, Any] = {
        "id": finding.id,
        "dimension": finding.dimension,
        "intensity": finding.intensity,
        "confidence": finding.confidence,
        "user_stated": bool(finding.user_stated),
        "semantic_value": semantic,
        "source_ids": sorted(finding.source_ids),
        # A prosa livre do achado NUNCA e copiada — so o fato de existir e o
        # tamanho, para o musico saber que ha material lido pelo agente.
        "summary_present": bool(finding.summary),
        "summary_chars": len(finding.summary or ""),
    }
    if omission:
        entry["semantic_value_omitido"] = omission
    return entry


def _source_entry(source: influence_mod.InfluenceSource) -> dict[str, Any]:
    """Metadado de citacao da fonte — e so isso.

    A url passa pela MESMA barreira do titulo (`_quote`): `influence.validate`
    so exige que ela nao seja vazia, entao uma url pode carregar tablatura e
    sequencia de notas em query string. Url que nao passa vira `null` com
    nota de omissao: proveniencia nao justifica transportar conteudo musical
    da referencia para dentro do relatorio.
    """
    title, title_omission = _quote(source.title)
    url, url_omission = _quote(source.url)
    entry: dict[str, Any] = {
        "id": source.id,
        "url": url,
        "title": title,
        "retrieved_at": source.retrieved_at,
    }
    if title_omission:
        entry["title_omitido"] = title_omission
    if url_omission:
        entry["url_omitido"] = url_omission
    return entry


def build_report(
    *,
    plan: ArrangementPlan,
    rendered_midi_path: str | Path | None = None,
    rendered_mid: mido.MidiFile | None = None,
    influence: InfluenceProfile | None = None,
    compile_result: CompileResult | None = None,
    brief: dict[str, Any] | None = None,
    brief_path: str | Path | None = None,
    validators: Sequence[ValidatorRun] = (),
) -> dict[str, Any]:
    """Monta o relatorio de proveniencia como dict pronto para JSON.

    Nenhum argumento e obrigatorio alem do plano: cada artefato ausente vira
    `missing_links` explicito. O relatorio de um render sem pesquisa
    registrada e valido — ele so declara, alto e claro, que a cadeia comeca
    na autorizacao do usuario e nao em fonte nenhuma.
    """
    missing_links: list[dict[str, Any]] = []

    def _miss(code: str, path: str, message: str) -> None:
        assert code in MISSING_LINK_CODES, code
        missing_links.append({"code": code, "path": path, "message": message})

    # --- artefatos ---------------------------------------------------------
    if rendered_mid is None and rendered_midi_path is not None:
        rendered_mid = mido.MidiFile(str(rendered_midi_path))

    stamps = read_stamps(rendered_mid) if rendered_mid is not None else ()
    if rendered_mid is None:
        _miss(
            "track",
            "rendered_midi_path",
            "MIDI renderizado nao foi entregue ao relatorio — nenhuma "
            "aplicacao de tecnica pode ser confirmada por carimbo",
        )

    # Indexado por track_index, NUNCA por nome: DAW repete nome de track, e o
    # AGENTS.md trata nome repetido como caso de primeira classe. Com duas
    # tracks fisicas `Drums`, a chave por nome publicava a metrica da ultima
    # para as duas.
    metrics_by_index: dict[int, dict[str, Any]] = {}
    if rendered_mid is not None:
        for stamp in stamps:
            metrics_by_index[stamp.track_index] = track_metrics(
                rendered_mid.tracks[stamp.track_index],
            )
    name_counts: dict[str, int] = {}
    for stamp in stamps:
        name_counts[stamp.track_name] = name_counts.get(stamp.track_name, 0) + 1

    for stamp in stamps:
        if stamp.problems:
            _miss(
                "track",
                f"rendered.tracks[{stamp.track_index}]",
                f"carimbo da track {stamp.track_name!r} esta ilegivel ou "
                f"fora do formato conhecido ({', '.join(stamp.problems)}) — "
                f"o que ele afirma nao pode ser usado como prova",
            )

    if influence is None:
        _miss(
            "source",
            "influence",
            "nenhum InfluenceProfile foi entregue — o relatorio nao pode "
            "ligar tecnica a fonte pesquisada",
        )
        _miss(
            "finding",
            "influence",
            "nenhum InfluenceProfile foi entregue — o relatorio nao pode "
            "ligar tecnica a achado de pesquisa",
        )

    findings_by_id = {f.id: f for f in (influence.findings if influence else [])}
    sources_by_id = {s.id: s for s in (influence.sources if influence else [])}

    if compile_result is None and influence is not None:
        _miss(
            "mapping",
            "compile_result",
            "perfil de influencia presente sem resultado de "
            "influence.compile — a traducao achado -> tecnica nao pode ser "
            "auditada",
        )

    suggestions_by_key: dict[tuple[str, str], list[Any]] = {}
    not_recommended_by_key: dict[tuple[str, str], list[Any]] = {}
    if compile_result is not None:
        for suggestion in compile_result.suggestions:
            suggestions_by_key.setdefault(
                (suggestion.family, suggestion.name), [],
            ).append(suggestion)
        for item in compile_result.not_recommended:
            not_recommended_by_key.setdefault(
                (item.family, item.technique), [],
            ).append(item)

    if brief is None:
        _miss(
            "technique",
            "brief",
            "brief ausente — o relatorio nao pode distinguir tecnica "
            "sugerida de tecnica autorizada pelo usuario",
        )

    plan_techniques = _plan_techniques(plan)
    element_tracks = _element_track_names(plan)
    edit_tracks = {edit.track: edit for edit in plan.edits}

    # --- universo de tecnicas ---------------------------------------------
    keys: set[tuple[str, str]] = set(plan_techniques)
    keys |= set(suggestions_by_key) | set(not_recommended_by_key)
    for family in sorted({k[0] for k in keys} | set(plan_mod.STYLE_FAMILIES)):
        entry = _brief_family_entry(brief, family)
        for name in _brief_names(entry, "suggested_techniques"):
            keys.add((family, name))
        for name in _brief_names(entry, "authorized_techniques"):
            keys.add((family, name))
    for stamp in stamps:
        for name in stamp.techniques:
            keys.add((_family_of(name), name))

    links: list[_Link] = []
    for family, technique in sorted(keys):
        link = _Link(family=family, technique=technique)
        brief_entry = _brief_family_entry(brief, family)
        suggested_names = _brief_names(brief_entry, "suggested_techniques")
        authorized_names = _brief_names(brief_entry, "authorized_techniques")
        link.suggested = technique in suggested_names or bool(
            suggestions_by_key.get((family, technique)),
        )
        link.authorized = technique in authorized_names

        # --- elo mapping -> finding -> source ------------------------------
        finding_ids: list[str] = []
        declared = plan_techniques.get((family, technique))
        if declared is not None:
            link.plan_declaration = {
                "density": declared.density,
                "intensity": declared.intensity,
                "style": declared.style,
                "parameters": {k: declared.parameters[k] for k in sorted(declared.parameters)},
                "evidence_refs": sorted(declared.evidence_refs),
                "rationale_present": bool(declared.rationale),
            }
            finding_ids.extend(declared.evidence_refs)

        suggestions = suggestions_by_key.get((family, technique), [])
        if suggestions:
            link.mapping = {
                "mapping_version": suggestions[0].mapping_version,
                "rationales": sorted({s.rationale for s in suggestions}),
                "tool": suggestions[0].tool,
                "requested_tool": suggestions[0].requested_tool,
                "intensity": suggestions[0].intensity,
            }
            for suggestion in suggestions:
                finding_ids.extend(suggestion.finding_ids)
        elif declared is not None:
            link.missing.append("mapping")
            link.notes.append(
                "tecnica declarada no plano sem regra correspondente em "
                "influence_compile.MAPPING_RULES — autorizacao direta do "
                "usuario, nao traducao de achado de pesquisa",
            )

        seen: set[str] = set()
        for fid in finding_ids:
            if fid in seen:
                continue
            seen.add(fid)
            finding = findings_by_id.get(fid)
            if finding is None:
                link.missing.append("finding")
                link.notes.append(
                    f"achado {fid!r} citado como evidencia nao existe no "
                    f"perfil de influencia entregue",
                )
                continue
            link.findings.append(_finding_entry(finding))
            for sid in finding.source_ids:
                source = sources_by_id.get(sid)
                if source is None:
                    link.missing.append("source")
                    link.notes.append(
                        f"achado {fid!r} cita fonte {sid!r} ausente do perfil",
                    )
                    continue
                link.sources.append(_source_entry(source))
            if not finding.source_ids and not finding.user_stated:
                link.missing.append("source")
                link.notes.append(
                    f"achado {fid!r} nao cita fonte e nao e preferencia "
                    f"declarada do usuario",
                )
        if not link.findings:
            link.missing.append("finding")
        if not link.sources and not any(f["user_stated"] for f in link.findings):
            link.missing.append("source")
        link.findings.sort(key=lambda f: f["id"])
        link.sources = sorted(
            {s["id"]: s for s in link.sources}.values(), key=lambda s: s["id"],
        )

        # --- elo track/section --------------------------------------------
        for stamp in stamps:
            if technique not in stamp.techniques:
                continue
            element = element_tracks.get(stamp.track_name)
            if element is not None:
                kind = "element"
                sections = sorted(element.sections)
                sections_source = "plan.elements[].sections"
                element_id: str | None = element.id
            elif stamp.track_name in edit_tracks:
                kind = "edit"
                sections = []
                sections_source = "nao_declarado"
                element_id = None
            else:
                kind = "desconhecido"
                sections = []
                sections_source = "nao_declarado"
                element_id = None
            metrics = metrics_by_index.get(stamp.track_index, {})
            evidence = _track_evidence(
                stamp.track_name, validators,
                ambiguous_name=name_counts.get(stamp.track_name, 0) > 1,
            )
            target = {
                "track_name": stamp.track_name,
                "track_index": stamp.track_index,
                "kind": kind,
                "element_id": element_id,
                "role": stamp.role,
                "sections": sections,
                "sections_source": sections_source,
                "metrics": metrics,
                "validator_evidence": evidence,
                # O carimbo prova despacho; ele NAO prova que o despacho foi
                # desta execucao. MIDI ja arranjado reaproveitado como origem
                # sai com o carimbo velho (track nao declarada em plan.edits
                # sai byte-identica, carimbo incluso).
                "declarada_neste_plano": declared is not None,
                "carimbo_problemas": sorted(stamp.problems),
            }
            if kind == "edit":
                target["sections_nota"] = (
                    "track de plan.edits nao declara secao — a edicao vale "
                    "para a track inteira"
                )
            link.targets.append(target)
        link.targets.sort(key=lambda t: t["track_index"])

        # --- elo de autorizacao e de correspondencia com esta execucao ------
        if link.targets and not link.authorized:
            # Tecnica carimbada na track sem autorizacao rastreavel: elo
            # `technique` QUEBRADO. AGENTS.md: ausencia de autorizacao
            # significa NENHUMA tecnica — nunca "todas".
            link.missing.append("technique")
            link.notes.append(
                f"tecnica {technique!r} aparece em carimbo de track, mas nao "
                f"esta em style.{family}.authorized_techniques do brief "
                f"desta execucao",
            )
        stale = [t for t in link.targets if not t["declarada_neste_plano"]]
        unknown = [t for t in link.targets if t["kind"] == "desconhecido"]
        link.stale_stamp = bool(stale or unknown)
        if stale:
            link.missing.append("technique")
            link.notes.append(
                f"tecnica {technique!r} nao e declarada em "
                f"plan.style.{family}.techniques desta execucao — o carimbo "
                f"encontrado pode ser de uma rodada anterior",
            )
        if unknown:
            link.missing.append("track")
            link.notes.append(
                f"carimbo de {technique!r} em track que nao e elemento do "
                f"plano nem track de plan.edits: "
                f"{sorted(t['track_name'] for t in unknown)!r}",
            )

        # --- status --------------------------------------------------------
        link.status = _status_for(link, not_recommended_by_key)
        if not link.targets and link.authorized:
            # Autorizada pelo usuario e nao chegou a track nenhuma: a cadeia
            # QUEBROU. Tecnica so sugerida (ou nao suportada) sem track nao e
            # elo quebrado — a ausencia ali e o proprio resultado esperado, e
            # ja esta dita pelo status.
            link.missing.append("track")
            link.missing.append("metric")
        links.append(link)

    for link in links:
        for code in sorted(set(link.missing)):
            missing_links.append({
                "code": code,
                "path": f"chain[{link.technique}]",
                "message": _MISSING_MESSAGES[code].format(technique=link.technique),
            })

    ran = {run.name for run in validators if run.executed}
    for name in VALIDATOR_NAMES:
        if name not in ran:
            _miss(
                "metric",
                f"validators.{name}",
                f"validador {name!r} nao rodou — nenhuma tecnica pode ser "
                f"declarada verificada por ele",
            )

    hashes = _hashes_block(plan, brief_path, rendered_midi_path)
    if hashes["brief_path_divergente"]:
        _miss(
            "technique",
            "hashes.brief_path",
            f"o brief hasheado ({hashes['brief_path']!r}) nao e o brief "
            f"declarado em plan.brief_ref.path "
            f"({hashes['brief_path_declarado_no_plano']!r}): a prova de "
            f"autorizacao nao descreve o arquivo que o plano cita",
        )

    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "versions": _versions_block(plan, influence, compile_result),
        "hashes": hashes,
        "chain": [link.to_dict() for link in links],
        "techniques": _status_index(links),
        "validators": _validators_block(validators),
        "unmapped_findings": [
            _finding_entry(f)
            for f in sorted(
                compile_result.unmapped_findings if compile_result else (),
                key=lambda f: f.id,
            )
        ],
        "missing_links": sorted(
            missing_links, key=lambda m: (m["code"], m["path"], m["message"]),
        ),
    }
    report["summary_text"] = format_summary(report)
    return report


_MISSING_MESSAGES: dict[str, str] = {
    "source": "tecnica {technique} nao chega a nenhuma fonte pesquisada",
    "finding": "tecnica {technique} nao chega a nenhum achado de pesquisa",
    "mapping": "tecnica {technique} nao tem regra de mapeamento que a explique",
    "technique": "tecnica {technique} sem autorizacao rastreavel no brief",
    "track": "tecnica {technique} nao aparece em carimbo de track nenhuma",
    "metric": "tecnica {technique} nao tem metrica medida no MIDI final",
}


def _track_evidence(
    track_name: str,
    validators: Sequence[ValidatorRun],
    *,
    ambiguous_name: bool = False,
) -> dict[str, Any]:
    """Veredito objetivo para uma track.

    - `sem_cobertura` quando nenhum validador POR TRACK executado percorreu
      esta track — ausencia de issue NAO e prova de conformidade, e
      validador de escopo global (persona, colisao, conformidade) nunca
      cobre track nenhuma;
    - `com_erro` quando ha erro atribuido a esta track OU erro de escopo
      global (issue sem campo `track`, como `RequisitoVerdict`,
      `PersonaIssue` e `CollisionWarning`). Erro global sem dono era erro
      que nao rebaixava nada.

    `ambiguous_name` marca nome de track repetido pela DAW: a cobertura dos
    validadores chega por NOME, entao ela vale para a unidade inteira
    (AGENTS.md trata nome repetido como uma unidade), e o relatorio diz isso
    em vez de fingir precisao fisica que os validadores nao entregam.
    """
    covered = sorted(
        run.name for run in validators
        if run.executed and run.scope == "per_track"
        and track_name in run.covered_tracks
    )
    global_runs = sorted(
        run.name for run in validators
        if run.executed and run.scope == "global"
    )
    errors: list[dict[str, Any]] = []
    global_errors: list[dict[str, Any]] = []
    for run in validators:
        if not run.executed:
            continue
        for issue in run.issues:
            entry = _issue_to_dict(run.name, issue)
            if entry["severity"] != "error":
                continue
            if entry["track"] == track_name:
                errors.append(entry)
            elif entry["track"] is None:
                global_errors.append(entry)
    if errors or global_errors:
        verdict = "com_erro"
    elif not covered:
        verdict = "sem_cobertura"
    else:
        verdict = "limpo"
    evidence: dict[str, Any] = {
        "veredito": verdict,
        "validadores": covered,
        "validadores_escopo_global": global_runs,
        "erros": sorted(errors, key=lambda e: (e["validator"], e["message"])),
        "erros_globais": sorted(
            global_errors, key=lambda e: (e["validator"], e["message"]),
        ),
    }
    if ambiguous_name:
        evidence["nome_ambiguo"] = True
        evidence["nome_ambiguo_nota"] = (
            "mais de uma track fisica com este nome: a cobertura e o erro "
            "de validador chegam por NOME e valem para a unidade inteira, "
            "nao para uma track fisica especifica"
        )
    return evidence


def _status_for(
    link: _Link, not_recommended_by_key: dict[tuple[str, str], list[Any]],
) -> str:
    key = (link.family, link.technique)
    if link.targets:
        # Ordem deliberada: autorizacao vem ANTES de qualquer veredito de
        # validador. Tecnica aplicada sem autorizacao rastreavel no brief e
        # um problema de consentimento, nao de qualidade — e nenhum
        # validador limpo pode transforma-la em "verificada".
        if not link.authorized:
            return "aplicada_sem_autorizacao"
        if link.stale_stamp:
            # Carimbo que nao corresponde ao plano desta execucao (ou track
            # que nao e elemento nem edit) nao prova aplicacao AGORA.
            return "aplicada_nao_verificavel"
        verdicts = {t["validator_evidence"]["veredito"] for t in link.targets}
        if "com_erro" in verdicts:
            return "aplicada_com_erro"
        if verdicts == {"limpo"}:
            return "aplicada_verificada"
        return "aplicada_nao_verificavel"
    if link.technique not in SUPPORTED_TECHNIQUES:
        return "nao_suportada"
    if key in not_recommended_by_key:
        return "nao_recomendada"
    if link.authorized:
        return "autorizada_nao_aplicada"
    return "sugerida_nao_autorizada"


def _status_index(links: Sequence[_Link]) -> dict[str, Any]:
    """As listas legiveis por maquina, por nome de tecnica.

    `aplicadas` continua sendo o conjunto do que chegou a alguma track — mas
    ele SOZINHO nao distingue verificada de nao verificavel, de aplicada com
    erro, nem de aplicada sem autorizacao. Quem consome so essa lista lia
    "aplicada" e entendia "deu certo". Por isso ela vem acompanhada de
    `aplicadas_verificadas` (o unico subconjunto com evidencia objetiva) e
    de `por_status`, com o status exato de cada tecnica.
    """
    index: dict[str, list[str]] = {
        "sugeridas": [],
        "autorizadas": [],
        "aplicadas": [],
        "aplicadas_verificadas": [],
        "aplicadas_sem_evidencia": [],
        "ignoradas": [],
        "nao_suportadas": [],
    }
    por_status: dict[str, list[str]] = {status: [] for status in TECHNIQUE_STATUSES}
    for link in links:
        name = link.technique
        por_status.setdefault(link.status, []).append(name)
        if link.suggested:
            index["sugeridas"].append(name)
        if link.authorized:
            index["autorizadas"].append(name)
        if link.status.startswith("aplicada"):
            index["aplicadas"].append(name)
            if link.status == "aplicada_verificada":
                index["aplicadas_verificadas"].append(name)
            else:
                index["aplicadas_sem_evidencia"].append(name)
        elif link.technique not in SUPPORTED_TECHNIQUES:
            index["nao_suportadas"].append(name)
        else:
            index["ignoradas"].append(name)
    out: dict[str, Any] = {k: sorted(set(v)) for k, v in index.items()}
    out["por_status"] = {k: sorted(set(v)) for k, v in sorted(por_status.items())}
    return out


def _versions_block(
    plan: ArrangementPlan,
    influence: InfluenceProfile | None,
    compile_result: CompileResult | None,
) -> dict[str, Any]:
    return {
        "report_schema": REPORT_SCHEMA_VERSION,
        "plan_schema": plan.version,
        "influence_schema": INFLUENCE_SCHEMA_VERSION if influence else None,
        "influence_profile": influence.version if influence else None,
        "influence_mapping": (
            compile_result.mapping_version if compile_result
            else (INFLUENCE_MAPPING_VERSION if influence else None)
        ),
    }


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Dois caminhos apontam para o mesmo arquivo? So compara texto depois
    de normalizar — caminho relativo e absoluto do mesmo brief nao sao
    divergencia."""
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except OSError:
        return str(a) == str(b)


def _hashes_block(
    plan: ArrangementPlan,
    brief_path: str | Path | None,
    rendered_midi_path: str | Path | None,
) -> dict[str, Any]:
    """Hashes de prova.

    `brief_sha256` e `brief_path` tem que descrever O MESMO arquivo: o hash
    existe para provar qual autorizacao estava em vigor, e publica-lo ao lado
    do caminho de outro arquivo inverte a prova. Por isso o caminho publicado
    e sempre o que foi realmente hasheado, e a divergencia com
    `plan.brief_ref.path` sai DECLARADA em vez de silenciada.
    """
    from .brief_ref import brief_sha256

    brief_hash: str | None = None
    hashed_path: str | None = None
    hash_source: str | None = None
    if brief_path is not None:
        brief_hash = brief_sha256(brief_path)
        hashed_path = str(brief_path)
        hash_source = "brief_path"
    elif plan.brief_ref is not None:
        brief_hash = plan.brief_ref.sha256
        hashed_path = str(plan.brief_ref.path)
        hash_source = "plan.brief_ref.sha256"
    declared = str(plan.brief_ref.path) if plan.brief_ref else None
    divergent = bool(
        hashed_path and declared and not _same_file(hashed_path, declared),
    )
    rendered_hash: str | None = None
    if rendered_midi_path is not None:
        rendered_hash = _sha256_of_file(Path(rendered_midi_path))
    return {
        "brief_sha256": brief_hash,
        "brief_path": hashed_path,
        "brief_sha256_origem": hash_source,
        "brief_path_declarado_no_plano": declared,
        "brief_path_divergente": divergent,
        "plan_sha256": _plan_sha256(plan),
        "source_midi_sha256": plan.source_midi.sha256,
        "rendered_sha256": rendered_hash,
    }


# --- resumo legivel por musico ----------------------------------------------


def format_summary(report: dict[str, Any]) -> str:
    """Resumo curto, em portugues, do que o relatorio de maquina afirma.

    Regra da issue: nada aqui diz "aplicado com sucesso" sem que o status do
    elo seja `aplicada_verificada` — o resto e nomeado como nao verificavel.
    """
    lines: list[str] = ["Relatorio de proveniencia do arranjo"]
    by_status: dict[str, list[str]] = {}
    for link in report.get("chain", []):
        by_status.setdefault(link["status"], []).append(link["technique"])

    rotulos = {
        "aplicada_verificada": "aplicadas e verificadas por validador",
        "aplicada_com_erro": "aplicadas, mas com erro de validador",
        "aplicada_sem_autorizacao": (
            "aplicadas SEM autorizacao rastreavel no brief"
        ),
        "aplicada_nao_verificavel": "aplicadas, porem NAO verificaveis",
        "autorizada_nao_aplicada": "autorizadas e nao aplicadas",
        "sugerida_nao_autorizada": "sugeridas e nao autorizadas",
        "nao_recomendada": "nao recomendadas pela pesquisa",
        "nao_suportada": "nao suportadas pelo motor",
    }
    for status in TECHNIQUE_STATUSES:
        names = sorted(set(by_status.get(status, [])))
        if names:
            lines.append(f"- {rotulos[status]}: {', '.join(names)}")
    if len(lines) == 1:
        lines.append("- nenhuma tecnica declarada, sugerida ou aplicada")

    nao_executados = sorted(
        name for name, block in report.get("validators", {}).items()
        if not block.get("executado")
    )
    faltantes = sorted({
        name for name in VALIDATOR_NAMES
        if name not in report.get("validators", {})
    } | set(nao_executados))
    if faltantes:
        lines.append(f"- validadores que NAO rodaram: {', '.join(faltantes)}")
    else:
        lines.append("- todos os sete validadores rodaram")

    missing = report.get("missing_links", [])
    if missing:
        codes = sorted({m["code"] for m in missing})
        lines.append(
            f"- elos ausentes na cadeia ({len(missing)}): {', '.join(codes)} "
            f"— o que esta ausente esta declarado, nao suposto",
        )
    else:
        lines.append("- cadeia completa: fonte -> achado -> mapeamento -> "
                     "tecnica -> track -> metrica")
    return "\n".join(lines)


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    """Grava o relatorio como `arrangement-report.json` deterministico."""
    dest = Path(path)
    payload = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    dest.write_text(payload + "\n", encoding="utf-8")
    return dest


def report_sha256(report: dict[str, Any]) -> str:
    """Hash do relatorio serializado canonicamente — prova de determinismo."""
    payload = json.dumps(
        report, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_QUOTE_CHARS",
    "MISSING_LINK_CODES",
    "REPORT_SCHEMA_VERSION",
    "TECHNIQUE_STATUSES",
    "TRACK_VERDICTS",
    "VALIDATOR_NAMES",
    "TrackStamp",
    "ValidatorRun",
    "build_report",
    "format_summary",
    "parse_stamp",
    "read_stamps",
    "report_sha256",
    "track_metrics",
    "write_report",
]
