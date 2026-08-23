"""Validador de artificialidade (FR-19, US-008).

Detecta anti-padroes na SAIDA GERADA que denunciam MIDI robotico antes de
importar no DAW. Cada anti-padrao vira `ArtificeIssue` de severidade `error`
que bloqueia o render sem `--allow-artifice`.

Anti-padroes verificados (secao 3 do spec):

1. Grid perfeito: todos os onsets exatamente sobre o grid de 16 avos
   (dist < `GRID_TOLERANCE_S` em TODAS as notas).
2. End-chain: fim de cada nota coincide com onset da seguinte (nenhum silencio
   entre notas, respiracao zero).
3. Duracao unica: todas as notas com a mesma duracao dentro de bucket de 10 ms.
4. Velocities agrupadas: stddev populacional < `VELOCITY_STDDEV_MIN`.
5. Velocities aleatorias sem acento: stddev alto mas media na batida forte
   `<=` media fora dela — variacao sem cair na frase.
6. Notas repetidas identicas: sequencia de N notas iguais (mesmo pitch, mesma
   duracao, mesma velocity, espacamento constante).
7. Duplicacao do source: >=90% dos onsets da track batem com kick OU com
   guitarra do proprio source dentro de `DUPLICATE_ONSET_WINDOW_S` — camada
   sem conteudo ritmico proprio.

Regra base do spec: **so validamos tracks GERADAS**. Tracks originais do
source nao entram — a origem pode ser roboticamente legitima por conta propria
(quantizada de proposito, gerada por VST etc). Como o renderer so passa as
`RenderedTrack` das novas tracks para o validador, essa fronteira e mantida
por construcao.

Articulacoes `sustained`/`let_ring` sao isentas dos padroes 1/2/3 porque
notas seguradas sao MEANT to be held (pad longo cai em bar starts, dura ate
o fim da secao). O restante das checagens continua valendo.

Consome apenas `analysis.py` (bars, kick_positions, guitar_notes) e o plano
(para descobrir a articulacao de cada track via element_id). Nao reimplementa
deteccao de compasso nem de kick — reusa o que ja saiu do analyze().
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, pstdev

from ..analyze import Analysis, bar_number, find_bar
from ..plan import ArrangementPlan, Element
from .harmony import (
    SEVERITY_ERROR,
    RenderedTrack,
    pitch_name,
)

# --- constantes -------------------------------------------------------------

MIN_NOTES_FOR_CHECK: int = 8
"""Piso de notas para cada checagem. Track curta (pad de 4 notas)
naturalmente tem pouca variacao — checar seria falso positivo garantido."""

GRID_SUBDIVISIONS: int = 16
"""Grid de 16 avos por compasso. Menor subdivisao onde 'preso no grid'
significa alguma coisa musicalmente — 32 avos ja e granular demais para
qualquer humanizacao real."""

GRID_TOLERANCE_S: float = 0.002
"""Tolerancia (segundos) para considerar um onset 'exatamente no grid'.
2 ms e menor que qualquer jitter humano detectavel; renderer humaniza em
5-15 ms, entao qualquer track abaixo desse piso e artificial."""

END_CHAIN_TOLERANCE_S: float = 0.005
"""Distancia (segundos) para considerar que fim de nota e onset da seguinte
sao 'a mesma coisa'. 5 ms cobre o arredondamento de tick sem confundir com
uma respiracao intencional (10 ms ja e microrespiracao audivel)."""

DURATION_BUCKET_S: float = 0.010
"""Bucket para arredondar duracoes na checagem de duracao-unica. 10 ms:
notas dentro do mesmo bucket sao 'audivelmente identicas' mesmo com jitter
de renderer."""

VELOCITY_STDDEV_MIN: float = 3.0
"""Stddev populacional minimo de velocity para NAO ser 'agrupado demais'.
Uma velocity real varia com fraseado; abaixo de 3 unidades a curva e
essencialmente uma reta."""

VELOCITY_STDDEV_HIGH: float = 15.0
"""Stddev alto o suficiente para caracterizar 'variacao ampla'. Combinado
com media downbeat<=offbeat, denuncia jitter aleatorio sem seguir acento."""

VELOCITY_ACCENT_MIN_DIFF: float = 2.0
"""Diferenca minima (unidades MIDI) entre media na batida forte e media
fora dela para considerar 'ha acento'. Abaixo disso a velocity nao
diferencia o downbeat — random puro."""

DOWNBEAT_TOLERANCE_RATIO: float = 0.10
"""Fracao do bar_len considerada 'em cima do downbeat'. 10% (200ms num
compasso de 2s a 120 bpm) cobre o head do compasso incluindo antecipacoes."""

REPEATED_STREAK_MIN: int = 6
"""Sequencia de N notas identicas para disparar 'nota repetida'. 6 e
suficiente para caracterizar ostinato mecanico sem confundir com repeticao
proposital de duas semicolcheias."""

REPEATED_SPACING_TOLERANCE_S: float = 0.005
"""Tolerancia (segundos) para considerar espacamento entre notas 'igual'
na checagem de ostinato mecanico."""

DUPLICATE_ONSET_WINDOW_S: float = 0.030
"""Janela para casar onset da track com onset do source (kick/guitarra).
30 ms cobre humanizacao intencional sem confundir com colisao de barra."""

DUPLICATE_ONSET_RATIO: float = 0.90
"""Fracao dos onsets da track que precisam bater com o source para
caracterizar 'duplicacao sem conteudo ritmico proprio'."""

SUSTAINED_ARTICULATIONS: frozenset[str] = frozenset({"sustained", "let_ring"})
"""Articulacoes isentas dos padroes de grid/end-chain/duracao-unica —
notas seguradas legitimamente caem no downbeat, cobrem a secao inteira e
tem duracao homogenea."""

# Chaves nomeadas dos anti-padroes — string curto usado no relatorio e em
# `ArtificeIssue.pattern` para o consumidor filtrar por tipo sem parsear
# mensagem. Vocabulario fechado.
PATTERN_GRID: str = "grid_perfect"
PATTERN_END_CHAIN: str = "end_chain"
PATTERN_DURATION_UNIFORM: str = "duration_uniform"
PATTERN_VELOCITY_GROUPED: str = "velocity_grouped"
PATTERN_VELOCITY_RANDOM: str = "velocity_random_no_accent"
PATTERN_REPEATED_NOTES: str = "repeated_notes"
PATTERN_DUPLICATES_SOURCE: str = "duplicates_source"

ALL_PATTERNS: tuple[str, ...] = (
    PATTERN_GRID,
    PATTERN_END_CHAIN,
    PATTERN_DURATION_UNIFORM,
    PATTERN_VELOCITY_GROUPED,
    PATTERN_VELOCITY_RANDOM,
    PATTERN_REPEATED_NOTES,
    PATTERN_DUPLICATES_SOURCE,
)


# --- dataclass do issue -----------------------------------------------------

@dataclass(frozen=True)
class ArtificeIssue:
    """Anti-padrao detectado numa track gerada.

    - severity: sempre `error` — anti-padrao dispara exit code != 0.
    - element_id: id do elemento no plano dono da track.
    - track: nome da track ofensora.
    - bar: numero do compasso (1-based) onde o padrao foi flagrado. 0 quando
      o padrao e agregado (velocity, duplicacao) e nao aponta para um bar
      unico — o `message` carrega o detalhe agregado.
    - pattern: chave do anti-padrao (vocabulario `ALL_PATTERNS`).
    - message: mensagem pronta para exibicao.
    """
    severity: str
    element_id: str
    track: str
    bar: int
    pattern: str
    message: str


# --- helpers ----------------------------------------------------------------

def _issue(
    element_id: str,
    track: str,
    bar: int,
    pattern: str,
    detail: str,
) -> ArtificeIssue:
    """Constroi `ArtificeIssue` com mensagem uniforme.

    Formato: "element X, track T, bar N [pattern]: <detail>."
    Mantido igual ao dos outros validadores para o leitor do relatorio."""
    message = (
        f"element {element_id!r}, track {track!r}, bar {bar} "
        f"[{pattern}]: {detail}"
    )
    return ArtificeIssue(
        severity=SEVERITY_ERROR,
        element_id=element_id,
        track=track,
        bar=bar,
        pattern=pattern,
        message=message,
    )


# --- checagens individuais --------------------------------------------------

def _check_grid_perfect(
    element_id: str,
    track: RenderedTrack,
    analysis: Analysis,
) -> ArtificeIssue | None:
    """Todos os onsets exatamente no grid de 16 avos = erro."""
    notes = track.notes
    if len(notes) < MIN_NOTES_FOR_CHECK:
        return None
    for note in notes:
        bar = find_bar(analysis, note.start_s)
        if bar is None:
            # Nota fora do range de bars nao pode ser avaliada contra grid —
            # o placement validator ja cuida disso. Aqui: nao dispara.
            return None
        bar_len = bar.end - bar.start
        step = bar_len / GRID_SUBDIVISIONS
        rel = note.start_s - bar.start
        nearest = round(rel / step) * step
        if abs(rel - nearest) >= GRID_TOLERANCE_S:
            return None
    first_bar = find_bar(analysis, notes[0].start_s)
    return _issue(
        element_id, track.track_name, bar_number(first_bar), PATTERN_GRID,
        f"all {len(notes)} onsets align to the 16th-note grid within "
        f"{int(GRID_TOLERANCE_S * 1000)}ms — no micro-timing",
    )


def _check_end_chain(
    element_id: str,
    track: RenderedTrack,
) -> ArtificeIssue | None:
    """Fim de cada nota coincide com onset da seguinte = erro (respira zero)."""
    notes = sorted(track.notes, key=lambda n: n.start_s)
    if len(notes) < MIN_NOTES_FOR_CHECK:
        return None
    for a, b in zip(notes, notes[1:], strict=False):
        if abs(a.end_s - b.start_s) >= END_CHAIN_TOLERANCE_S:
            return None
    return _issue(
        element_id, track.track_name, 0, PATTERN_END_CHAIN,
        f"every note ends within {int(END_CHAIN_TOLERANCE_S * 1000)}ms of "
        f"the next onset — no breathing between notes",
    )


def _check_duration_uniform(
    element_id: str,
    track: RenderedTrack,
) -> ArtificeIssue | None:
    """Duracoes iguais dentro do bucket de 10 ms = erro."""
    notes = track.notes
    if len(notes) < MIN_NOTES_FOR_CHECK:
        return None
    buckets = {
        round((n.end_s - n.start_s) / DURATION_BUCKET_S)
        for n in notes
    }
    if len(buckets) > 1:
        return None
    dur = notes[0].end_s - notes[0].start_s
    return _issue(
        element_id, track.track_name, 0, PATTERN_DURATION_UNIFORM,
        f"all {len(notes)} notes share the same duration "
        f"(~{int(dur * 1000)}ms) — no dynamic phrasing",
    )


def _check_velocity_grouped(
    element_id: str,
    track: RenderedTrack,
) -> ArtificeIssue | None:
    """stddev de velocity < VELOCITY_STDDEV_MIN = erro."""
    notes = track.notes
    if len(notes) < MIN_NOTES_FOR_CHECK:
        return None
    vels = [int(n.velocity) for n in notes]
    stddev = pstdev(vels)
    if stddev >= VELOCITY_STDDEV_MIN:
        return None
    return _issue(
        element_id, track.track_name, 0, PATTERN_VELOCITY_GROUPED,
        f"velocity stddev {stddev:.2f} below {VELOCITY_STDDEV_MIN:.1f} — "
        f"dynamics too flat",
    )


def _check_velocity_random_no_accent(
    element_id: str,
    track: RenderedTrack,
    analysis: Analysis,
) -> ArtificeIssue | None:
    """Velocity variada mas nao acompanha o downbeat = jitter aleatorio."""
    notes = track.notes
    if len(notes) < MIN_NOTES_FOR_CHECK:
        return None
    vels = [int(n.velocity) for n in notes]
    stddev = pstdev(vels)
    if stddev < VELOCITY_STDDEV_HIGH:
        # Variacao baixa: o check acima (velocity_grouped) ja cobre;
        # se nao disparou, esta OK.
        return None
    downbeat: list[int] = []
    offbeat: list[int] = []
    for note in notes:
        bar = find_bar(analysis, note.start_s)
        if bar is None:
            continue
        bar_len = bar.end - bar.start
        rel = note.start_s - bar.start
        if rel < bar_len * DOWNBEAT_TOLERANCE_RATIO:
            downbeat.append(int(note.velocity))
        else:
            offbeat.append(int(note.velocity))
    if len(downbeat) < 2 or len(offbeat) < 2:
        return None
    mean_d = fmean(downbeat)
    mean_o = fmean(offbeat)
    if mean_d - mean_o >= VELOCITY_ACCENT_MIN_DIFF:
        return None
    return _issue(
        element_id, track.track_name, 0, PATTERN_VELOCITY_RANDOM,
        f"velocity stddev {stddev:.2f} is wide but downbeat mean "
        f"{mean_d:.1f} <= offbeat mean {mean_o:.1f} — random jitter, "
        f"not accent",
    )


def _check_repeated_notes(
    element_id: str,
    track: RenderedTrack,
    analysis: Analysis,
) -> ArtificeIssue | None:
    """Sequencia de N notas identicas em pitch/velocity/duracao/espacamento."""
    notes = sorted(track.notes, key=lambda n: n.start_s)
    if len(notes) < REPEATED_STREAK_MIN:
        return None

    def bucket(n) -> tuple[int, int, int]:
        return (
            int(n.pitch),
            int(n.velocity),
            round((n.end_s - n.start_s) / DURATION_BUCKET_S),
        )

    streak_start = 0
    for i in range(1, len(notes)):
        if bucket(notes[i]) != bucket(notes[i - 1]):
            streak_start = i
            continue
        spacing_prev = notes[i].start_s - notes[i - 1].start_s
        if i - streak_start >= 2:
            first_spacing = notes[streak_start + 1].start_s - notes[streak_start].start_s
            if abs(spacing_prev - first_spacing) >= REPEATED_SPACING_TOLERANCE_S:
                streak_start = i
                continue
        if i - streak_start + 1 >= REPEATED_STREAK_MIN:
            first = notes[streak_start]
            bar = find_bar(analysis, first.start_s)
            return _issue(
                element_id, track.track_name, bar_number(bar),
                PATTERN_REPEATED_NOTES,
                f"{i - streak_start + 1} consecutive identical notes "
                f"({pitch_name(first.pitch)} vel={first.velocity}) at "
                f"constant spacing",
            )
    return None


def _count_matches(
    source_times: list[float],
    track_times: list[float],
) -> int:
    """Numero de `track_times` que casam com algum `source_times` dentro
    de `DUPLICATE_ONSET_WINDOW_S`. `source_times` deve chegar ordenado
    (analysis ja garante para kick_positions e guitar_notes)."""
    if not source_times:
        return 0
    matched = 0
    j = 0
    for t in track_times:
        while j < len(source_times) and source_times[j] < t - DUPLICATE_ONSET_WINDOW_S:
            j += 1
        k = j
        while k < len(source_times) and source_times[k] <= t + DUPLICATE_ONSET_WINDOW_S:
            if abs(source_times[k] - t) <= DUPLICATE_ONSET_WINDOW_S:
                matched += 1
                break
            k += 1
    return matched


def _check_duplicates_source(
    element_id: str,
    track: RenderedTrack,
    analysis: Analysis,
) -> ArtificeIssue | None:
    """>=90% dos onsets da track batem com kick OU guitarra do source."""
    notes = track.notes
    if len(notes) < MIN_NOTES_FOR_CHECK:
        return None
    track_times = sorted(n.start_s for n in notes)
    kicks = analysis.kick_positions
    guitars = [g.start for g in analysis.guitar_notes]

    for label, source_times in (("kick", kicks), ("guitar", guitars)):
        if len(source_times) < MIN_NOTES_FOR_CHECK:
            continue
        matched = _count_matches(source_times, track_times)
        ratio = matched / len(track_times)
        if ratio < DUPLICATE_ONSET_RATIO:
            continue
        return _issue(
            element_id, track.track_name, 0, PATTERN_DUPLICATES_SOURCE,
            f"{matched}/{len(track_times)} onsets ({ratio * 100:.0f}%) "
            f"align with {label} within "
            f"{int(DUPLICATE_ONSET_WINDOW_S * 1000)}ms — layer has no "
            f"rhythmic content of its own",
        )
    return None


# --- API principal ----------------------------------------------------------

def _validate_track(
    element: Element,
    track: RenderedTrack,
    analysis: Analysis,
) -> list[ArtificeIssue]:
    """Roda cada checagem ativa para uma track.

    Articulacoes seguradas (`sustained`, `let_ring`) sao isentas de TODOS os
    checks estatisticos que assumem 'notas curtas com movimento': pad legitimo
    ataca uma vez por acorde, com velocity uniforme e duracao = extensao da
    secao. A checagem de duplicacao do source e a de ostinato continuam
    valendo mesmo para articulacao segurada — quem sustenta um pad duplicando
    kick ainda esta gerando lixo."""
    issues: list[ArtificeIssue] = []
    sustained = element.articulation in SUSTAINED_ARTICULATIONS

    if not sustained:
        grid = _check_grid_perfect(element.id, track, analysis)
        if grid is not None:
            issues.append(grid)
        chain = _check_end_chain(element.id, track)
        if chain is not None:
            issues.append(chain)
        dur = _check_duration_uniform(element.id, track)
        if dur is not None:
            issues.append(dur)
        grouped = _check_velocity_grouped(element.id, track)
        if grouped is not None:
            issues.append(grouped)
        random_ = _check_velocity_random_no_accent(element.id, track, analysis)
        if random_ is not None:
            issues.append(random_)

    repeated = _check_repeated_notes(element.id, track, analysis)
    if repeated is not None:
        issues.append(repeated)
    dupes = _check_duplicates_source(element.id, track, analysis)
    if dupes is not None:
        issues.append(dupes)
    return issues


def validate_artifice(
    rendered_tracks: list[RenderedTrack],
    plan: ArrangementPlan,
    analysis: Analysis,
) -> list[ArtificeIssue]:
    """Roda todas as verificacoes de artificialidade sobre as tracks geradas.

    Track cujo `element_id` nao aparece no plano e ignorada — mesmo contrato
    dos outros validadores. Tracks originais do source NUNCA sao validadas
    (o renderer nunca as expoe como `RenderedTrack`).
    """
    issues: list[ArtificeIssue] = []
    elements_by_id = {e.id: e for e in plan.elements}
    for track in rendered_tracks:
        element = elements_by_id.get(track.element_id)
        if element is None:
            continue
        issues.extend(_validate_track(element, track, analysis))
    return issues


def has_errors(issues: list[ArtificeIssue]) -> bool:
    """True quando ha pelo menos uma severidade `error` na lista."""
    return any(i.severity == SEVERITY_ERROR for i in issues)


def format_issues(issues: list[ArtificeIssue]) -> str:
    """Pretty-print para o relatorio do render."""
    if not issues:
        return "Artifice: OK"
    lines = [f"Artifice issues: {len(issues)} error(s)"]
    for i in issues:
        lines.append(f"  [ERROR]   {i.message}")
    return "\n".join(lines)


__all__ = [
    "ALL_PATTERNS",
    "ArtificeIssue",
    "DOWNBEAT_TOLERANCE_RATIO",
    "DUPLICATE_ONSET_RATIO",
    "DUPLICATE_ONSET_WINDOW_S",
    "DURATION_BUCKET_S",
    "END_CHAIN_TOLERANCE_S",
    "GRID_SUBDIVISIONS",
    "GRID_TOLERANCE_S",
    "MIN_NOTES_FOR_CHECK",
    "PATTERN_DUPLICATES_SOURCE",
    "PATTERN_DURATION_UNIFORM",
    "PATTERN_END_CHAIN",
    "PATTERN_GRID",
    "PATTERN_REPEATED_NOTES",
    "PATTERN_VELOCITY_GROUPED",
    "PATTERN_VELOCITY_RANDOM",
    "REPEATED_SPACING_TOLERANCE_S",
    "REPEATED_STREAK_MIN",
    "SUSTAINED_ARTICULATIONS",
    "VELOCITY_ACCENT_MIN_DIFF",
    "VELOCITY_STDDEV_HIGH",
    "VELOCITY_STDDEV_MIN",
    "format_issues",
    "has_errors",
    "validate_artifice",
]
