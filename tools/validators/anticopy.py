"""Validador anti-copia (AC-16 do bloco M3 em `docs/objetivo.md`).

O arranjador se inspira em musicos reais. Isso so e aceitavel se ele levantar
tecnica e comportamento — jamais conteudo musical. AC-15 fecha o lado
estrutural: `style.<familia>` do plano nao carrega sequencia de notas (regra
ja aplicada por `tools.style_schema.find_style_musical_content` e chamada
por `tools.plan.validate`; o teste `test_style_no_sequence_of_notes` deste
modulo confirma o vinculo com esta issue).

Este modulo fecha o lado COMPORTAMENTAL: quando um corpus de referencia e
fornecido, comparamos janelas de N eventos consecutivos por track da saida
gerada contra janelas equivalentes das tracks do corpus, usando uma
assinatura que sobrevive a TRANSPOSIcAO (intervalos entre pitches, nao
pitch absoluto) e a MUDANcA DE TEMPO (razoes de IOI, nao ticks absolutos).

## Decisoes de projeto (todas testadas)

### N default = 6
Escolhido para maximizar sinal com falso positivo baixo:

- N=3-4: quatro notas em ordem diatonica coincidem por acaso (uma escala
  ascendente basica). Falso positivo garantido.
- N=5: melhor, mas riffs curtos de blues/pentatonica ainda casam por
  convencao (`I bVII IV V` com ritmo padrao).
- N=6: cobre a licao classica de plagio (`Smoke on the Water` tem 6 notas
  no riff principal, `Sunshine of Your Love` idem) e ja e longo o
  suficiente para exigir intencao — sequencia de 6 pitches + 5 IOIs
  identicos nao acontece por acaso.
- N=8+: deixa passar riff curto reconhecivel, e o objetivo aqui e barrar
  copia, nao aceitar quase-copia porque tem uma nota a mais.

Cobertura de teste: um mesmo caso de escala (`test_scale_run_not_flagged`)
prova o extremo baixo; um caso de riff curto (`test_short_riff_detected`)
prova o extremo alto.

### Ritmo identico com pitches trocados NAO e copia
Ritmo isolado e convencao estilistica ampla — `sincopa em colcheia + duas
semicolcheias` aparece em milhares de musicas de generos diferentes. O
teste `test_same_rhythm_different_intervals_is_not_copy` fixa essa
decisao; qualquer futura mudanca de politica precisa remover ou reescrever
esse teste, nao contornar em codigo.

### Melodia casa sozinha; ritmo NAO e porta de entrada obrigatoria (achado
### do Codex na PR #100)
A primeira versao deste modulo exigia intervalos E ritmo identicos para
disparar — um `onset` deslocado o suficiente para mudar de bucket de
`RHYTHM_BUCKET` deixava passar uma melodia identica nota a nota. Isso
inverte a proporcao do paragrafo acima: ritmo sozinho nunca e copia, mas
`N` pitches consecutivos com os mesmos intervalos (a mesma melodia) SAO
copia mesmo que o interprete/gerador altere o fraseado. Por isso o
casamento usa so a tupla de intervalos como chave; o ritmo continua
calculado e entra na mensagem (`rhythm identical` ou `rhythm differs`)
como sinal informativo extra, nunca como filtro. O teste
`test_melody_match_detected_despite_rhythm_shift` fixa essa decisao.

### Transposicao E copia
`ii V I` em Do e `ii V I` em Fa sao a mesma cadencia (permitido — e escala),
mas `Riff X em Mi` e `Riff X em Sol` sao a mesma coisa transposta — copia.
A assinatura de janela usa DIFERENcAS de pitch consecutivas, entao qualquer
transposicao (constante somada a todos os pitches) e invisivel a
comparacao. O teste `test_transposed_copy_is_detected` prova.

### Rendered tracks apenas
`rendered_tracks` que chega aqui contem so as tracks GERADAS pelo motor de
elemento (mesma convencao dos outros validadores desde `harmony`/`artifice`).
Tracks vindas de `plan.edits` sao humanizacoes do MIDI de origem do usuario:
elas sao a musica dele, nao material que o arranjador inventou. Se o
proprio MIDI de origem plagia algo, isso e responsabilidade da origem, nao
do arranjador. Manter o escopo consistente com os outros validadores evita
falso positivo em copia legitima que o proprio usuario colocou no source.

### Severidade sempre `error`
Copia nao e questao de gosto — nao ha flag `--allow-copy`. O relatorio marca
como `error` e o CLI decide (via `has_errors`) o exit code. Igual aos
outros validadores em severidade `error`, mas sem contrapartida de
`--allow-*` disponivel — de proposito.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pretty_midi

from ..analyze import Analysis, bar_number, find_bar
from ..plan import ArrangementPlan
from .harmony import SEVERITY_ERROR, RenderedNote, RenderedTrack

# --- constantes -------------------------------------------------------------

DEFAULT_N: int = 6
"""Tamanho da janela deslizante. Ver docstring do modulo para justificativa."""

MIN_N: int = 3
"""Piso defensivo: N < 3 nao tem intervalo suficiente para comparar contorno
(1 intervalo + 0 IOIs). Bloqueado com `ValueError`."""

RHYTHM_BUCKET: int = 12
"""Bucket para razoes de IOI (`round(ratio * 12)`). Da resolucao suficiente
para casar semicolcheia (0.5), triplete (0.333), colcheia pontuada (1.5) sem
ficar refem de arredondamento de ticks. Numero inteiro para a chave da
janela ser hashavel e comparavel."""

ONSET_MS_BUCKET: int = 5
"""Onset agrupado em bucket de 5 ms para colapsar chord voicing (notas do
mesmo acorde disparadas 'juntas' com jitter de humanizacao) em um so evento
melodico — a linha lider (nota mais aguda) e a que compara com corpus."""


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceSequence:
    """Uma track do corpus de referencia — o insumo do validador.

    - source: identificador humano da peca de referencia (path do arquivo
      MIDI, nome do disco, etc). Aparece no relatorio para o usuario saber
      contra o que casou.
    - track_name: nome da track dentro daquela peca; ajuda a diferenciar
      `bass` vs `lead` da mesma faixa.
    - notes: eventos da track em ordem cronologica.
    """
    source: str
    track_name: str
    notes: tuple[RenderedNote, ...]


@dataclass(frozen=True)
class AntiCopyIssue:
    """Casamento de janela de N eventos entre saida e corpus.

    - severity: sempre `error` (copia nao e questao de gosto).
    - element_id: elemento dono da track da saida.
    - track: nome da track na saida.
    - bar: numero 1-based do compasso do PRIMEIRO evento da janela na saida.
    - n: tamanho da janela (mesmo N usado na chamada).
    - source: identificador da referencia contra a qual casou.
    - source_track: nome da track na referencia.
    - message: mensagem pronta para exibicao.
    """
    severity: str
    element_id: str
    track: str
    bar: int
    n: int
    source: str
    source_track: str
    message: str


# --- extracao de eventos ----------------------------------------------------

def _monophonic_line(notes: Sequence[RenderedNote]) -> list[RenderedNote]:
    """Colapsa acordes: por bucket de onset, mantem a nota mais aguda.

    A comparacao de copia e feita sobre a linha lider — o que o ouvinte
    reconhece como 'a melodia da musica'. Uma trade harmonica de tres notas
    disparadas juntas nao vira tres eventos concorrentes de contorno, e sim
    um evento com pitch = topo. Isso previne que a mesma progressao vire
    tres janelas diferentes por chord voicing acidental.
    """
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start_s, -int(n.pitch)))
    by_bucket: dict[int, RenderedNote] = {}
    for note in ordered:
        bucket = round(float(note.start_s) * 1000 / ONSET_MS_BUCKET)
        current = by_bucket.get(bucket)
        if current is None or int(note.pitch) > int(current.pitch):
            by_bucket[bucket] = note
    return sorted(by_bucket.values(), key=lambda n: n.start_s)


def _window_signature(window: Sequence[RenderedNote]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Assinatura invariante a transposicao e a andamento.

    Devolve `(intervalos, ritmo)`:
    - intervalos: `pitch[i+1] - pitch[i]` para i em 0..N-2. Transposicao
      constante some.
    - ritmo: razoes `IOI[i+1] / IOI[0]` bucketizadas em `RHYTHM_BUCKET`
      para i em 0..N-3, mais o bucket base `1` na frente. Andamento
      global some (todas as IOIs multiplicam pelo mesmo fator, razoes
      preservam).

    Retorna duas tuplas — a chave composta que a busca de casamento usa.
    """
    n = len(window)
    if n < 2:
        return (), ()
    pitches = [int(note.pitch) for note in window]
    intervals = tuple(pitches[i + 1] - pitches[i] for i in range(n - 1))
    starts = [float(note.start_s) for note in window]
    rhythm = _rhythm_signature(starts)
    return intervals, rhythm


def _rhythm_signature(starts: Sequence[float]) -> tuple[int, ...]:
    """Razoes `IOI[i+1] / IOI[0]` bucketizadas em `RHYTHM_BUCKET`.

    Fatorado de `_window_signature` para virar um sinal informativo (o
    casamento nao depende mais dele — ver docstring do modulo, secao
    "Melodia casa sozinha").
    """
    n = len(starts)
    if n < 2:
        return ()
    iois = [starts[i + 1] - starts[i] for i in range(n - 1)]
    base = iois[0] if iois and iois[0] > 0 else 0.0
    if base <= 0.0:
        # Sem IOI base positivo (todas as notas no mesmo onset apos monofonizar
        # nao deve acontecer, mas guarda contra divisao por zero).
        return ()
    return tuple(round((ratio / base) * RHYTHM_BUCKET) for ratio in iois)


def _extract_windows(
    notes: Sequence[RenderedNote],
    n: int,
) -> list[tuple[tuple[tuple[int, ...], tuple[int, ...]], RenderedNote]]:
    """Devolve `[(assinatura, primeira_nota_da_janela)]` para cada janela.

    A primeira nota volta junto para o relatorio conseguir apontar o
    compasso (via `find_bar(analysis, first.start_s)`).
    """
    line = _monophonic_line(notes)
    if len(line) < n:
        return []
    windows: list[tuple[tuple[tuple[int, ...], tuple[int, ...]], RenderedNote]] = []
    for i in range(len(line) - n + 1):
        window = line[i : i + n]
        sig = _window_signature(window)
        windows.append((sig, window[0]))
    return windows


# --- API publica ------------------------------------------------------------

def validate_anticopy(
    rendered_tracks: Iterable[RenderedTrack],
    plan: ArrangementPlan,   # noqa: ARG001 — assinatura simetrica; futura evolucao pode filtrar por elemento
    analysis: Analysis,
    *,
    corpus: Iterable[ReferenceSequence] | None = None,
    n: int = DEFAULT_N,
) -> list[AntiCopyIssue]:
    """Compara janelas de N eventos da saida contra o corpus de referencia.

    - `corpus=None`: checagem comportamental e pulada (a estrutural, em
      `plan.validate`, sempre roda). Devolve lista vazia.
    - Casamento: janela de N eventos consecutivos da saida com a MESMA
      tupla de intervalos de alguma janela de qualquer `ReferenceSequence`
      — o ritmo (`_rhythm_signature`) e calculado e entra na mensagem, mas
      NAO decide o casamento (ver docstring do modulo, secao "Melodia
      casa sozinha").
    - Determinismo: itera na ordem de `rendered_tracks` e retorna no
      maximo uma issue por track (a primeira janela casada), para nao
      soterrar o relatorio quando a copia e sistematica. `has_errors`
      basta para o CLI decidir bloqueio.
    """
    if n < MIN_N:
        raise ValueError(f"n must be >= {MIN_N}, got {n}")
    if corpus is None:
        return []

    # Chave = so os intervalos. O ritmo do casamento do CORPUS viaja no
    # valor so para a mensagem poder dizer se o ritmo tambem bateu.
    corpus_index: dict[tuple[int, ...], tuple[str, str, tuple[int, ...]]] = {}
    for ref in corpus:
        for (intervals, rhythm), _first in _extract_windows(ref.notes, n):
            if not intervals:
                continue
            # Mesma assinatura em duas pecas de referencia: guardamos a
            # primeira ocorrencia estavel — o relatorio cita uma so, e a
            # ordem de iteracao do corpus e do chamador (deterministica).
            corpus_index.setdefault(intervals, (ref.source, ref.track_name, rhythm))

    if not corpus_index:
        return []

    issues: list[AntiCopyIssue] = []
    for track in rendered_tracks:
        for (intervals, rhythm), first in _extract_windows(track.notes, n):
            if not intervals:
                # Janela sem intervalos (ex.: N=1 defensivo, ja bloqueado
                # em MIN_N — guarda contra evolucao futura).
                continue
            match = corpus_index.get(intervals)
            if match is None:
                continue
            source, source_track, corpus_rhythm = match
            bar = find_bar(analysis, float(first.start_s))
            rhythm_note = (
                "intervals and rhythm identical"
                if rhythm == corpus_rhythm
                else "melodic contour identical, rhythm differs"
            )
            message = (
                f"element {track.element_id!r}, track {track.track_name!r}, "
                f"bar {bar_number(bar)}: {n}-event window matches "
                f"{source!r} / track {source_track!r} ({rhythm_note} — "
                f"transposition-invariant)."
            )
            issues.append(AntiCopyIssue(
                severity=SEVERITY_ERROR,
                element_id=track.element_id,
                track=track.track_name,
                bar=bar_number(bar),
                n=n,
                source=source,
                source_track=source_track,
                message=message,
            ))
            break   # uma issue por track — ver docstring
    return issues


def has_errors(issues: Sequence[AntiCopyIssue]) -> bool:
    """True quando ha pelo menos uma severidade `error` na lista.

    Como toda `AntiCopyIssue` e `error` por construcao (copia nao tem flag
    de bypass), na pratica equivale a `bool(issues)` — mas mantemos a
    forma para simetria com os outros validadores."""
    return any(i.severity == SEVERITY_ERROR for i in issues)


def format_issues(issues: Sequence[AntiCopyIssue]) -> str:
    """Pretty-print para o relatorio do render."""
    if not issues:
        return "Anti-copy: OK"
    lines = [f"Anti-copy issues: {len(issues)} error(s)"]
    for i in issues:
        lines.append(f"  [ERROR]   {i.message}")
    return "\n".join(lines)


# --- carregador de corpus (opcional, usado pela integracao no render) -------

def load_reference_sequences(paths: Iterable[str | Path]) -> list[ReferenceSequence]:
    """Le arquivos MIDI de referencia e devolve uma `ReferenceSequence` por
    track de cada arquivo.

    Fica aqui (e nao em `tools/render.py`) para o validador ser autocontido
    para testes. Usa apenas `pretty_midi` (ja em `requirements.txt`);
    determinismo: paths sao lidos na ordem informada, tracks na ordem
    do arquivo — sem relogio, sem rede.
    """
    sequences: list[ReferenceSequence] = []
    for raw in paths:
        path = Path(raw).expanduser()
        pm = pretty_midi.PrettyMIDI(str(path))
        for idx, instrument in enumerate(pm.instruments):
            track_name = instrument.name or f"track {idx + 1}"
            notes = tuple(
                RenderedNote(
                    pitch=int(note.pitch),
                    velocity=int(note.velocity),
                    start_s=float(note.start),
                    end_s=float(note.end),
                )
                for note in instrument.notes
            )
            if not notes:
                continue
            sequences.append(ReferenceSequence(
                source=str(path),
                track_name=track_name,
                notes=notes,
            ))
    return sequences


__all__ = [
    "AntiCopyIssue",
    "DEFAULT_N",
    "MIN_N",
    "ReferenceSequence",
    "format_issues",
    "has_errors",
    "load_reference_sequences",
    "validate_anticopy",
]
