"""Validador de duas dimensoes na fronteira de secao (AC-14, issue #24).

A persona deste projeto e produtor: transicao fraca e a que muda so o
volume. `docs/objetivo.md` (AC-14, FR-20) exige que toda fronteira de
secao altere DE VERDADE ao menos DUAS das oito dimensoes abaixo — mudar
uma so (tipicamente velocity/volume) nao conta como transicao.

## Fronteiras vem de `plan.sections`, nao de `plan.transitions`

`plan.transitions` e metadado OPCIONAL — um plano valido pode encadear
varias secoes adjacentes sem declarar um registro de `Transition` pra
nenhuma delas (a validacao estrutural do plano nao exige isso), e AC-14
vale pra TODA fronteira mesmo assim. Por isso as fronteiras checadas sao
derivadas de `plan.sections` ordenadas por `start_bar` — cada par
adjacente E uma fronteira — e nao de `plan.transitions`. Quando existe um
`Transition` casando a fronteira (por `from_section`/`to_section`, ou por
`at_bar` batendo com a fronteira natural — ver `_boundary_transition`),
sua `dimensions_changed` alimenta a checagem de divergencia intencao x
realidade abaixo; sem `Transition` casando, so a checagem 1 (AC-14 em si)
roda — nao ha intencao declarada pra comparar.

## Fronteiras vem de tracks de origem tambem

`rendered_tracks` que `tools.render.render()` passa pra ca inclui NAO SO as
tracks de `plan.elements[]` recem-geradas, mas tambem as tracks de origem
clonadas (editadas por `plan.edits` ou nao) que compoem o MIDI final —
sem isso, um plano que so mexe no material do usuario via `plan.edits`
(sem elemento gerado nenhum) sempre teria `events_a`/`events_b` vazios nas
janelas e toda fronteira seria pulada, mesmo quando as duas metades do MIDI
de saida sao musicalmente bem diferentes. Cada track de origem/edicao vira
uma `RenderedTrack` com `element_id` sintetico `source:<nome da track>`
(nao ha `Element` do plano pra essas tracks) — usado como entidade distinta
em `perspectiva_espacial`/`protagonista`, igual a um `element_id` real.

## Bateria e dimensoes de pitch

Numero de nota MIDI de bateria e IDENTIDADE DE PECA DO KIT (kick=36,
caixa=38, hi-hat=42, ride=51, ... convencao GM), nao altura musical. Trocar
hi-hat por ride nao pode contar como mudanca de `registro`/`harmonia` —
seria satisfazer as duas dimensoes de AC-14 com uma unica troca de
articulacao de percussao, sem mudanca musical real nenhuma. Por isso
`registro`, `largura` e `harmonia` EXCLUEM notas de tracks de bateria
(`_drum_element_ids`: `element_id` de `plan.elements[]` cujo `role` mapeia
pra familia `drums`, e `element_id` sintetico `source:<track>` de
`plan.edits[]` com `profile == "drums"`). `densidade`, `subdivisao`,
`textura`, `perspectiva_espacial` e `protagonista` continuam contando
TODAS as notas, inclusive bateria — sao dimensoes ritmicas/texturais, nao
de pitch, e uma virada de bateria de verdade tem que continuar contando
pra elas.

## As oito dimensoes (nomes canonicos, PT-BR sem acento — mesma convencao
## de `tools.plan.ENERGY_AXES`)

`TRANSITION_DIMENSIONS`: subdivisao, registro, largura, textura,
densidade, harmonia, perspectiva_espacial, protagonista.

## Regra de negocio: intencao vs realidade

`plan.transitions[].dimensions_changed` e a INTENCAO da IA — o que ela
disse que ia mudar ao escrever o plano. Este validador mede a REALIDADE
RENDERIZADA (o MIDI de saida de fato) e faz DUAS checagens independentes:

1. **AC-14 em si**: conta quantas das 8 dimensoes realmente mudaram na
   janela antes/depois da fronteira, comparando o que foi RENDERIZADO —
   nao o que o plano prometeu. Menos de duas mudancas reais vira warning
   `weak_transition` nomeando quais dimensoes ficaram iguais. Roda pra
   TODA fronteira entre secoes adjacentes, com ou sem `Transition`
   declarado (ver secao acima).
2. **Divergencia intencao x realidade**: SO quando ha `Transition`
   declarado casando a fronteira — para cada dimensao que ele DECLAROU em
   `dimensions_changed`, confere se ela de fato mudou no render. Plano que
   promete `densidade` e `registro` mas renderiza os dois lados iguais
   gera warning `unrealized_intent` — exatamente o caso que a issue #24
   cita como motivador ("Plano que promete mudar densidade e registro mas
   renderiza igual dos dois lados e exatamente o que este validador
   existe para pegar").

Severidade sempre `warning` (nao ha `--allow-*`; e um validador de
qualidade de composicao, nao uma regra estrutural do formato como
`harmony`/`placement`).

## Como cada dimensao e medida (tudo a partir de `RenderedTrack`, o MESMO
## insumo que os demais validadores usam — nunca reabre o MIDI)

A janela de cada lado da fronteira usa `WINDOW_BARS` compassos (CONVENCAO,
ver docstring da constante), ANCORADOS no `at_bar` da fronteira — o
`at_bar` do `Transition` declarado quando existe, senao
`section_b.start_bar` (a fronteira natural, onde a secao A termina e a
secao B comeca por ordem do plano) — via `Analysis.bars` (grade de
compassos em segundos, calculada sobre o MIDI de origem — a grade de
tempo/compasso nao muda com o arranjo). A janela NUNCA confia em
`from_section.end_bar`/`to_section.start_bar` do rotulo declarado: um
plano onde `at_bar` nao cai na fronteira rotulada (dado malformado, mas
hoje valido) mediria compassos completamente errados se usasse a cauda/
cabeca das secoes em vez do proprio `at_bar` — ver docstring de
`_window_bounds`. Todas as notas de TODOS os `RenderedTrack` (qualquer
elemento) que atacam dentro da janela entram na medicao — "o que de fato
soa naquele instante", nao so a track de um elemento — EXCETO `registro`/
`largura`/`harmonia`, que excluem notas de tracks de bateria (ver secao
"Bateria e dimensoes de pitch" acima).

- `densidade`: notas por segundo na janela (contagem / duracao). Metrica
  de TAXA — quantos eventos por unidade de tempo.
- `subdivisao`: menor intervalo entre onsets distintos da janela (onsets
  colapsados em buckets de `ONSET_BUCKET_MS`, igual a
  `tools.validators.anticopy.ONSET_MS_BUCKET`, para colapsar chord voicing
  no mesmo evento). Proxy da grade ritmica mais fina em uso — sem tempo
  explicito em `Analysis`, comparar o menor gap entre onsets e suficiente
  pra pegar oitava->semicolcheia etc. Precisa de 2+ onsets distintos; senao
  fica sem dado (nao entra na comparacao).
- `registro`: par (pitch minimo, pitch maximo) das notas da janela — ONDE
  a musica esta posicionada no espectro.
- `largura`: `registro[1] - registro[0]` — QUAO ESPALHADA verticalmente a
  musica esta, independente de onde. Deliberadamente separado de
  `registro`: um acorde grave e fechado tem registro baixo e largura
  pequena; o mesmo acorde aberto em duas oitavas tem registro baixo e
  largura grande.
- `textura`: media do numero de notas SIMULTANEAS soando em cada onset
  distinto da janela (espessura do acorde/arranjo — mono vs polifonico).
- `harmonia`: conjunto de pitch classes (mod 12) presentes na janela —
  muda quando o conteudo harmonico muda, independente de oitava/inversao.
- `perspectiva_espacial`: numero de `element_id` DISTINTOS com pelo menos
  uma nota na janela. Proxy documentado — o motor nao emite CC de pan
  (nao ha estereo real no MIDI de origem nem no gerado), entao esta e a
  unica coisa REAL e determinista disponivel que se aproxima de "quantas
  partes distintas ocupam o espaco sonoro" (mais elementos tocando junto
  == mix mais largo/mais profundo). Nao e panning de verdade; e a melhor
  proxy sem inventar numero — CONVENCAO explicita, nunca apresentada como
  posicao estereo real.
- `protagonista`: `element_id` com maior "presenca" na janela — soma de
  `duracao * max(1, velocity)` de cada nota, por elemento. Desempate
  deterministico: maior presenca vence; empate exato vai para o
  `element_id` menor em ordem lexicografica (`_top_presence`).

Dimensao sem dado suficiente em QUALQUER um dos dois lados (janela vazia,
`densidade`/`subdivisao` sem onset) fica de fora da comparacao inteira —
nao conta como mudada nem como igual. Se sobrarem menos de
`MIN_DIMENSIONS_CHANGED` dimensoes MENSURAVEIS no total, a fronteira e
pulada (dado insuficiente pra concluir qualquer coisa, nao vira falso
positivo de "so mudou uma").

## Teste de cobertura

`tests/test_transitions.py` cobre uma transicao valida (>=2 dimensoes
realmente diferentes) e uma fraca (so densidade muda; registro, largura,
textura, harmonia, perspectiva e protagonista ficam identicos) —
verificando que o warning nomeia exatamente as dimensoes que nao mudaram,
e cobre o caso de divergencia intencao x realidade citado na issue.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..analyze import Analysis, BarAnalysis
from ..plan import ROLE_STYLE_FAMILIES, STYLE_FAMILIES, ArrangementPlan, PlanSection, Transition
from .harmony import SEVERITY_ERROR, SEVERITY_WARNING, RenderedNote, RenderedTrack

# --- vocabulario --------------------------------------------------------

TRANSITION_DIMENSIONS: tuple[str, ...] = (
    "subdivisao",
    "registro",
    "largura",
    "textura",
    "densidade",
    "harmonia",
    "perspectiva_espacial",
    "protagonista",
)
"""As oito dimensoes de AC-14, nomes canonicos PT-BR sem acento — mesma
convencao ASCII de `tools.plan.ENERGY_AXES`."""

MIN_DIMENSIONS_CHANGED: int = 2
"""AC-14: toda fronteira de secao muda ao menos DUAS dimensoes. Nao e
CONVENCAO — e o numero literal do criterio de aceite."""

WINDOW_BARS: int = 4
"""CONVENCAO: quantos compassos de cada lado da fronteira entram na
medicao, ancorados no `at_bar` da fronteira (ANTES/DEPOIS de `at_bar` —
ver `_window_bounds`). Fronteira perto demais do inicio/fim da grade de
compassos (`Analysis.bars` nao cobre a janela inteira) fica sem dado — a
fronteira e pulada, nao vira janela encolhida. Numero pequeno o bastante
para capturar SO o entorno imediato da fronteira (o que de fato muda NA
transicao, nao a media da secao inteira, que dilui uma mudanca real de 2
compassos numa secao de 16) e grande o bastante para nao depender de um
unico compasso ruidoso — mesmo raciocinio de janela fixa de
`tools.validators.anticopy.DEFAULT_N`."""

ONSET_BUCKET_MS: int = 5
"""Bucket de onset em ms para colapsar chord voicing no mesmo evento —
mesmo valor e mesma razao de `tools.validators.anticopy.ONSET_MS_BUCKET`."""

REGISTER_TOLERANCE_SEMITONES: int = 2
"""CONVENCAO: diferenca de ate 2 semitons no min/max de `registro` conta
como ruido de humanizacao/voicing, nao mudanca real de registro."""

WIDTH_TOLERANCE_SEMITONES: int = 3
"""CONVENCAO: mesma logica de `REGISTER_TOLERANCE_SEMITONES`, tolerancia
um pouco maior porque `largura` e a diferenca de dois valores (soma de
ruido dos dois lados)."""

TEXTURE_TOLERANCE: float = 1.0
"""CONVENCAO: diferenca de ate 1 nota simultanea em media nao conta como
mudanca de textura — precisa de pelo menos uma voz inteira a mais/menos."""

DENSITY_RELATIVE_TOLERANCE: float = 0.20
"""CONVENCAO: densidade precisa mudar mais de 20% pra contar — abaixo
disso e variacao natural de humanizacao/quantidade de compassos na
janela, nao uma transicao ritmica real."""

SUBDIVISION_TOLERANCE_S: float = 0.03
"""CONVENCAO: 30ms de diferenca no menor gap entre onsets — folga o
bastante pra nao reagir a jitter de humanizacao (`TIMING_JITTER_MS` vai
ate 250ms no dominio fisico validado por `StyleProfile`, mas o jitter
tipico aplicado e uma fracao disso) e apertada o bastante pra pegar uma
troca real de subdivisao (oitava ~250ms pra semicolcheia ~125ms a 120bpm,
uma diferenca de 125ms — bem acima do limiar)."""


_DIMENSION_ALIASES: dict[str, str] = {
    "subdivisao": "subdivisao",
    "subdivisão": "subdivisao",
    "subdivision": "subdivisao",
    "registro": "registro",
    "register": "registro",
    "altura": "registro",
    "pitch": "registro",
    "largura": "largura",
    "width": "largura",
    "textura": "textura",
    "texture": "textura",
    "densidade": "densidade",
    "density": "densidade",
    "harmonia": "harmonia",
    "harmony": "harmonia",
    "perspectiva_espacial": "perspectiva_espacial",
    "perspectiva espacial": "perspectiva_espacial",
    "espacial": "perspectiva_espacial",
    "spatial_perspective": "perspectiva_espacial",
    "spatial perspective": "perspectiva_espacial",
    "perspective": "perspectiva_espacial",
    "protagonista": "protagonista",
    "protagonist": "protagonista",
}
"""Normaliza `Transition.dimensions_changed` (campo livre, PT/EN) para o
vocabulario canonico. Chave desconhecida e ignorada na checagem de
divergencia (campo continua livre — este modulo nao aperta o schema)."""


def _normalize_dimension_name(raw: str) -> str | None:
    return _DIMENSION_ALIASES.get(raw.strip().lower())


# --- dataclasses ----------------------------------------------------------

@dataclass(frozen=True)
class TransitionIssue:
    """Divergencia encontrada numa fronteira de secao.

    - severity: sempre `warning` (ver docstring do modulo).
    - kind: `weak_transition` (menos de `MIN_DIMENSIONS_CHANGED` dimensoes
      realmente mudaram) ou `unrealized_intent` (o plano declarou uma
      dimensao em `dimensions_changed` que nao mudou de verdade).
    - dimensions: para `weak_transition`, as dimensoes MENSURAVEIS que
      ficaram iguais; para `unrealized_intent`, as dimensoes declaradas
      pelo plano que nao se realizaram.
    """
    severity: str
    at_bar: int
    from_section: str
    to_section: str
    kind: str
    dimensions: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class _Metrics:
    subdivisao: float | None
    registro: tuple[int, int] | None
    largura: int | None
    textura: float | None
    densidade: float | None
    harmonia: frozenset[int] | None
    perspectiva_espacial: int | None
    protagonista: str | None


# --- janela de tempo --------------------------------------------------------

def _bar_lookup(analysis: Analysis) -> dict[int, BarAnalysis]:
    return {b.index: b for b in analysis.bars}


def _window_bounds(
    at_bar: int,
    *,
    before: bool,
    bars_by_index: dict[int, BarAnalysis],
) -> tuple[float, float] | None:
    """Bordas em segundos da janela de `WINDOW_BARS` compassos ANTES
    (`before=True`) ou DEPOIS (`before=False`) de `at_bar` — a fronteira
    de secao declarada em `Transition.at_bar`, ou `section_b.start_bar`
    quando a fronteira nao tem `Transition` declarada (ver
    `_boundary_at_bar`). A janela e ancorada no PROPRIO `at_bar`, nunca em
    `PlanSection.start_bar`/`end_bar` de `from_section`/`to_section` — um
    plano onde `at_bar` nao cai exatamente na fronteira rotulada (dado
    malformado, mas hoje valido) media os compassos ao redor da transicao
    declarada, nao os de secoes possivelmente erradas. None quando a
    janela cai fora da grade (`Analysis.bars`, ex.: `at_bar` proximo do
    inicio/fim da analise do MIDI de origem)."""
    if before:
        lo, hi = at_bar - WINDOW_BARS, at_bar
    else:
        lo, hi = at_bar, at_bar + WINDOW_BARS
    if lo < 0 or hi <= lo:
        return None
    first = bars_by_index.get(lo)
    last = bars_by_index.get(hi - 1)
    if first is None or last is None:
        return None
    return (first.start, last.end)


def _notes_in_window(
    rendered_tracks: Sequence[RenderedTrack],
    bounds: tuple[float, float],
) -> list[tuple[str, RenderedNote]]:
    lo, hi = bounds
    events: list[tuple[str, RenderedNote]] = []
    for track in rendered_tracks:
        for note in track.notes:
            if lo <= note.start_s < hi:
                events.append((track.element_id, note))
    return events


# --- metricas ---------------------------------------------------------------

def _compute_metrics(
    events: list[tuple[str, RenderedNote]],
    duration_s: float,
    *,
    drum_element_ids: frozenset[str],
) -> _Metrics:
    if not events:
        return _Metrics(None, None, None, None, None, None, None, None)

    notes = [n for _eid, n in events]

    # `registro`/`largura`/`harmonia` sao dimensoes de PITCH MUSICAL — nota
    # de bateria e identidade de peca do kit (kick=36, caixa=38, hi-hat=42,
    # ride=51, ...), nao altura. Trocar hi-hat por ride nao pode contar como
    # mudanca de registro/harmonia (ver docstring do modulo, secao "Bateria
    # e dimensoes de pitch"). `densidade`/`subdivisao`/`textura`/
    # `perspectiva_espacial`/`protagonista` continuam usando TODAS as notas
    # (`notes`), inclusive bateria — sao ritmicas/texturais, nao de pitch.
    pitched_notes = [n for eid, n in events if eid not in drum_element_ids]
    if pitched_notes:
        pitches = [n.pitch for n in pitched_notes]
        registro = (min(pitches), max(pitches))
        largura = registro[1] - registro[0]
        harmonia = frozenset(p % 12 for p in pitches)
    else:
        registro = None
        largura = None
        harmonia = None

    densidade = (len(notes) / duration_s) if duration_s > 0 else None

    bucket_s = ONSET_BUCKET_MS / 1000.0
    onset_buckets = sorted({round(n.start_s / bucket_s) for n in notes})
    if len(onset_buckets) >= 2:
        gaps = [
            (onset_buckets[i + 1] - onset_buckets[i]) * bucket_s
            for i in range(len(onset_buckets) - 1)
        ]
        subdivisao = min(gaps)
    else:
        subdivisao = None

    distinct_starts = sorted({n.start_s for n in notes})
    concurrency = [
        sum(1 for n in notes if n.start_s <= t < n.end_s)
        for t in distinct_starts
    ]
    textura = (sum(concurrency) / len(concurrency)) if concurrency else None

    perspectiva_espacial = len({eid for eid, _n in events})

    presence: dict[str, float] = {}
    for eid, n in events:
        weight = max(0.0, n.end_s - n.start_s) * max(1, n.velocity)
        presence[eid] = presence.get(eid, 0.0) + weight
    protagonista = (
        min(presence.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if presence
        else None
    )

    return _Metrics(
        subdivisao=subdivisao,
        registro=registro,
        largura=largura,
        textura=textura,
        densidade=densidade,
        harmonia=harmonia,
        perspectiva_espacial=perspectiva_espacial,
        protagonista=protagonista,
    )


def _changed(dimension: str, before: _Metrics, after: _Metrics) -> bool | None:
    """True/False quando a dimensao e mensuravel dos dois lados; None
    quando falta dado em pelo menos um lado (dimensao fica de fora da
    comparacao inteira — ver docstring do modulo)."""
    b = getattr(before, dimension)
    a = getattr(after, dimension)
    if b is None or a is None:
        return None
    if dimension == "subdivisao":
        return abs(a - b) > SUBDIVISION_TOLERANCE_S
    if dimension == "registro":
        return (
            abs(a[0] - b[0]) > REGISTER_TOLERANCE_SEMITONES
            or abs(a[1] - b[1]) > REGISTER_TOLERANCE_SEMITONES
        )
    if dimension == "largura":
        return abs(a - b) > WIDTH_TOLERANCE_SEMITONES
    if dimension == "textura":
        return abs(a - b) > TEXTURE_TOLERANCE
    if dimension == "densidade":
        if a == 0.0 and b == 0.0:
            return False
        lo, hi = sorted((a, b))
        if lo <= 0.0:
            return True
        return (hi / lo - 1.0) > DENSITY_RELATIVE_TOLERANCE
    if dimension == "harmonia":
        return a != b
    if dimension == "perspectiva_espacial":
        return a != b
    if dimension == "protagonista":
        return a != b
    raise AssertionError(f"unknown dimension {dimension!r}")  # pragma: no cover


def _drum_element_ids(plan: ArrangementPlan) -> frozenset[str]:
    """`element_id` (real, de `plan.elements`, ou sintetico `source:<track>`
    de uma track de `plan.edits`) cuja familia de `style` e `drums` — usado
    para excluir notas de bateria das dimensoes derivadas de pitch
    (`registro`, `largura`, `harmonia`). Mesma logica de
    `tools.render._style_family_for_role`/`_style_family_for_edit`,
    reimplementada aqui em vez de importada: `tools.render` importa este
    modulo (`validate_transitions`), entao o caminho inverso criaria
    import circular. `source:<track>` casa com o `element_id` sintetico
    que `tools.render` atribui as `RenderedTrack` reconstruidas a partir
    das tracks de origem/edicao (ver docstring do modulo, secao
    "Fronteiras vem de tracks de origem tambem")."""
    ids: set[str] = set()
    for element in plan.elements:
        family = (
            element.role
            if element.role in STYLE_FAMILIES
            else ROLE_STYLE_FAMILIES.get(element.role)
        )
        if family == "drums":
            ids.add(element.id)
    for edit in plan.edits:
        if edit.profile == "drums":
            ids.add(f"source:{edit.track}")
    return frozenset(ids)


# --- API publica -------------------------------------------------------------

def _boundary_transition(
    section_a: PlanSection,
    section_b: PlanSection,
    transitions_by_pair: dict[tuple[str, str], Transition],
    transitions_by_at_bar: dict[int, Transition],
) -> tuple[int, Transition | None]:
    """Resolve o `at_bar` de ancoragem e o `Transition` declarado (se
    houver) para a fronteira `section_a` -> `section_b`.

    Toda fronteira entre secoes ADJACENTES precisa ser checada (AC-14),
    mesmo quando `plan.transitions` nao declara um registro pra ela — a
    ancoragem natural nesse caso e `section_b.start_bar` (onde a secao A
    termina e a secao B comeca, por ordem do plano). Quando existe um
    `Transition` cujo `from_section`/`to_section` casa com esta fronteira,
    ele e a intencao declarada e seu `at_bar` e a ancora AUTORITATIVA —
    mesmo que divirja de `section_b.start_bar` (dado malformado, mas hoje
    valido; ver docstring de `_window_bounds`). Na ausencia de casamento
    por rotulo, um `Transition` cujo `at_bar` bate com a fronteira natural
    ainda conta como declarado para ela (mesmo defeito de dado, caminho
    inverso)."""
    natural_at_bar = section_b.start_bar
    declared = transitions_by_pair.get((section_a.label, section_b.label))
    if declared is None:
        declared = transitions_by_at_bar.get(natural_at_bar)
    at_bar = declared.at_bar if declared is not None else natural_at_bar
    return at_bar, declared


def validate_transitions(
    rendered_tracks: Iterable[RenderedTrack],
    plan: ArrangementPlan,
    analysis: Analysis,
) -> list[TransitionIssue]:
    """AC-14: cada fronteira entre `PlanSection`s ADJACENTES do plano
    (ordenadas por `start_bar`) precisa mudar de verdade ao menos
    `MIN_DIMENSIONS_CHANGED` das `TRANSITION_DIMENSIONS`, medidas no
    RENDER (nao no plano) — vale para TODA fronteira, mesmo quando
    `plan.transitions` nao declara um registro pra ela (a IA pode deixar
    `transitions` vazio; a validacao estrutural do plano nao exige um
    registro por fronteira). Quando existe um `Transition` casando essa
    fronteira (ver `_boundary_transition`), a checagem adicional de
    divergencia intencao x realidade (`dimensions_changed`) tambem roda;
    sem `Transition` declarado, so a checagem AC-14 em si roda — nao ha
    intencao pra comparar. Ver docstring do modulo para o metodo de
    medicao. 0 ou 1 secao no plano nao tem fronteira nenhuma pra checar."""
    if len(plan.sections) < 2:
        return []

    tracks = list(rendered_tracks)
    bars_by_index = _bar_lookup(analysis)
    ordered_sections = sorted(plan.sections, key=lambda s: s.start_bar)
    drum_element_ids = _drum_element_ids(plan)

    transitions_by_pair: dict[tuple[str, str], Transition] = {}
    transitions_by_at_bar: dict[int, Transition] = {}
    for t in plan.transitions:
        transitions_by_pair.setdefault((t.from_section, t.to_section), t)
        transitions_by_at_bar.setdefault(t.at_bar, t)

    issues: list[TransitionIssue] = []
    for section_a, section_b in zip(ordered_sections, ordered_sections[1:], strict=False):
        at_bar, declared = _boundary_transition(
            section_a, section_b, transitions_by_pair, transitions_by_at_bar,
        )

        bounds_a = _window_bounds(at_bar, before=True, bars_by_index=bars_by_index)
        bounds_b = _window_bounds(at_bar, before=False, bars_by_index=bars_by_index)
        if bounds_a is None or bounds_b is None:
            continue

        events_a = _notes_in_window(tracks, bounds_a)
        events_b = _notes_in_window(tracks, bounds_b)
        if not events_a or not events_b:
            continue

        metrics_a = _compute_metrics(
            events_a, bounds_a[1] - bounds_a[0], drum_element_ids=drum_element_ids,
        )
        metrics_b = _compute_metrics(
            events_b, bounds_b[1] - bounds_b[0], drum_element_ids=drum_element_ids,
        )

        changed: dict[str, bool] = {}
        for dim in TRANSITION_DIMENSIONS:
            result = _changed(dim, metrics_a, metrics_b)
            if result is not None:
                changed[dim] = result

        if len(changed) < MIN_DIMENSIONS_CHANGED:
            # Dado insuficiente pra concluir qualquer coisa sobre esta
            # fronteira — nao vira falso positivo de "so mudou uma".
            continue

        changed_dims = tuple(d for d in TRANSITION_DIMENSIONS if changed.get(d))
        unchanged_dims = tuple(d for d in changed if not changed[d])

        if len(changed_dims) < MIN_DIMENSIONS_CHANGED:
            issues.append(TransitionIssue(
                severity=SEVERITY_WARNING,
                at_bar=at_bar,
                from_section=section_a.label,
                to_section=section_b.label,
                kind="weak_transition",
                dimensions=unchanged_dims,
                message=(
                    f"transition at bar {at_bar} ({section_a.label!r} -> "
                    f"{section_b.label!r}): only {len(changed_dims)} dimension(s) "
                    f"actually changed in the render "
                    f"({', '.join(changed_dims) or 'none'}) — AC-14 requires "
                    f"at least {MIN_DIMENSIONS_CHANGED}. Unchanged: "
                    f"{', '.join(unchanged_dims)}."
                ),
            ))

        if declared is None:
            # Sem `Transition` declarado nao ha intencao pra comparar —
            # so a checagem AC-14 acima roda para esta fronteira.
            continue

        declared_names = {
            norm
            for raw in declared.dimensions_changed
            if (norm := _normalize_dimension_name(raw)) is not None
        }
        unrealized = tuple(
            d for d in TRANSITION_DIMENSIONS
            if d in declared_names and d in unchanged_dims
        )
        if unrealized:
            issues.append(TransitionIssue(
                severity=SEVERITY_WARNING,
                at_bar=at_bar,
                from_section=section_a.label,
                to_section=section_b.label,
                kind="unrealized_intent",
                dimensions=unrealized,
                message=(
                    f"transition at bar {at_bar} ({section_a.label!r} -> "
                    f"{section_b.label!r}): plan declared "
                    f"dimensions_changed={list(declared.dimensions_changed)} but "
                    f"the render shows no real change in {', '.join(unrealized)}."
                ),
            ))
    return issues


def has_errors(issues: Sequence[TransitionIssue]) -> bool:
    """True quando ha pelo menos uma severidade `error` na lista. Mantido
    por simetria com os outros validadores — hoje sempre `False`, porque
    toda `TransitionIssue` e `warning` por construcao."""
    return any(i.severity == SEVERITY_ERROR for i in issues)


def format_issues(issues: Sequence[TransitionIssue]) -> str:
    """Pretty-print para o relatorio do render."""
    if not issues:
        return "Transitions: OK"
    lines = [f"Transition issues: {len(issues)} warning(s)"]
    for i in issues:
        lines.append(f"  [WARNING] {i.message}")
    return "\n".join(lines)


__all__ = [
    "DENSITY_RELATIVE_TOLERANCE",
    "MIN_DIMENSIONS_CHANGED",
    "ONSET_BUCKET_MS",
    "REGISTER_TOLERANCE_SEMITONES",
    "SUBDIVISION_TOLERANCE_S",
    "TEXTURE_TOLERANCE",
    "TRANSITION_DIMENSIONS",
    "WIDTH_TOLERANCE_SEMITONES",
    "WINDOW_BARS",
    "TransitionIssue",
    "format_issues",
    "has_errors",
    "validate_transitions",
]
