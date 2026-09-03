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
casamento MELODICO (nao-percussao) usa so a tupla de intervalos como
chave; o ritmo continua calculado e entra na mensagem (`rhythm identical`
ou `rhythm differs`) como sinal informativo extra, nunca como filtro. O
teste `test_melody_match_detected_despite_rhythm_shift` fixa essa
decisao.

### Transposicao E copia
`ii V I` em Do e `ii V I` em Fa sao a mesma cadencia (permitido — e escala),
mas `Riff X em Mi` e `Riff X em Sol` sao a mesma coisa transposta — copia.
A assinatura de janela usa DIFERENcAS de pitch consecutivas, entao qualquer
transposicao (constante somada a todos os pitches) e invisivel a
comparacao. O teste `test_transposed_copy_is_detected` prova.

### Bateria preserva as vozes simultaneas (achado do Codex na PR #100)
A reducao monofonica de `_monophonic_line` (fica so a nota mais aguda por
onset) faz sentido para instrumento melodico: e a "linha que o ouvinte
canta". Para bateria essa reducao destroi exatamente o que define o
groove — kick e caixa quase sempre tem pitch MIDI menor que os pratos
(36/38 contra 42/46/49/51), entao a linha reduzida vira, na pratica, so a
sequencia de pratos, com kick/caixa completamente invisiveis a
comparacao. Duas grooves de kick/caixa totalmente diferentes com o mesmo
hi-hat fechado nos mesmos onsets colapsavam para a mesma "linha" (falso
positivo — corrigido: `test_percussion_false_positive_from_shared_cymbal_onsets`
prova que grooves de kick/caixa diferentes com o mesmo hi-hat NAO casam
mais). `_percussion_events` resolve isso preservando o CONJUNTO de
pitches que soa em cada onset (sem reduzir a um so), e a assinatura de
janela vira uma sequencia de conjuntos de pitch — pitch absoluto, sem
invariancia a transposicao, porque pitch de bateria e IDENTIDADE da peca
(36 = kick), nao um grau de escala transponivel. Uma
`ReferenceSequence`/`RenderedTrack` e tratada como bateria via
`ReferenceSequence.is_drum` (populado por `instrument.is_drum` do
pretty_midi em `load_reference_sequences`) e via `Element.role` do plano
(`tools.palette.drums.DRUMS_ROLES`) para a saida renderizada — mesma
convencao que o resto do motor usa para achar a familia de um elemento.

Limitacao conhecida, fora do escopo deste achado: o casamento de
percussao ainda exige o CONJUNTO INTEIRO de pitches identico em cada
onset da janela (kick + caixa + prato). Uma groove de kick/caixa
copiada nota por nota, mas com a CAMADA DE PRATO trocada (crash vira
ride num unico onset, por exemplo), ainda pode escapar — o
`_percussion_events` para de esconder kick/caixa atras do prato (o bug
relatado), mas nao separa "o que define o groove" (kick/caixa) de
"decoracao" (prato) para pesar cada um diferente na comparacao; isso
exigiria uma classificacao de pitches GM em grupos groove-defining vs.
decorativo, decisao de produto fora do pedido original desta PR.

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
from ..palette.drums import DRUMS_ROLES
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
    - is_drum: track de percussao (canal 10 GM / `instrument.is_drum` do
      pretty_midi). Roteia a comparacao para a assinatura multi-voz de
      `_percussion_events` em vez da reducao monofonica — ver docstring
      do modulo, secao "Bateria preserva as vozes simultaneas".
    """
    source: str
    track_name: str
    notes: tuple[RenderedNote, ...]
    is_drum: bool = False


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

    Fatorado de `_window_signature` para ser reaproveitado por
    `_percussion_window_signature` — a bucketizacao de ritmo e a mesma
    para melodia e percussao, so a fonte dos `starts` muda (uma nota por
    evento vs. o onset de um cluster de percussao).
    """
    n = len(starts)
    if n < 2:
        return ()
    iois = [starts[i + 1] - starts[i] for i in range(n - 1)]
    base = iois[0] if iois and iois[0] > 0 else 0.0
    if base <= 0.0:
        # Sem IOI base positivo (todos os eventos no mesmo onset — nao deve
        # acontecer, mas guarda contra divisao por zero).
        return ()
    return tuple(round((ratio / base) * RHYTHM_BUCKET) for ratio in iois)


def _extract_windows(
    notes: Sequence[RenderedNote],
    n: int,
) -> list[tuple[tuple[tuple[int, ...], tuple[int, ...]], RenderedNote]]:
    """Devolve `[(assinatura, primeira_nota_da_janela)]` para cada janela.

    A primeira nota volta junto para o relatorio conseguir apontar o
    compasso (via `find_bar(analysis, first.start_s)`). Usa a reducao
    monofonica — para percussao, ver `_extract_percussion_windows`.
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


# --- extracao de eventos (percussao) -----------------------------------------

def _percussion_events(notes: Sequence[RenderedNote]) -> list[tuple[float, tuple[int, ...], RenderedNote]]:
    """Agrupa notas de bateria por bucket de onset SEM reduzir a uma so voz.

    Ao contrario de `_monophonic_line` (fica so a nota mais aguda), aqui o
    evento carrega o CONJUNTO ordenado de pitches que soam naquele onset —
    kick + caixa + prato continuam visiveis juntos. Ver docstring do
    modulo, secao "Bateria preserva as vozes simultaneas".

    Devolve `[(onset_s, pitches_ordenados, primeira_nota_do_bucket)]`; a
    nota volta para o relatorio apontar compasso, igual `_extract_windows`.
    """
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start_s, int(n.pitch)))
    by_bucket: dict[int, list[RenderedNote]] = {}
    for note in ordered:
        bucket = round(float(note.start_s) * 1000 / ONSET_MS_BUCKET)
        by_bucket.setdefault(bucket, []).append(note)
    events: list[tuple[float, tuple[int, ...], RenderedNote]] = []
    for bucket in sorted(by_bucket):
        bucket_notes = by_bucket[bucket]
        pitches = tuple(sorted({int(n.pitch) for n in bucket_notes}))
        first = min(bucket_notes, key=lambda n: n.start_s)
        events.append((float(first.start_s), pitches, first))
    return events


def _percussion_window_signature(
    window: Sequence[tuple[float, tuple[int, ...], RenderedNote]],
) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...]]:
    """Assinatura de janela de percussao: `(vozes, ritmo)`.

    - vozes: uma tupla de pitches (absolutos, sem invariancia a
      transposicao — pitch de bateria e IDENTIDADE da peca) por onset da
      janela.
    - ritmo: mesma bucketizacao de `_window_signature`, calculada sobre os
      onsets dos eventos (nao de cada nota individual).
    """
    n = len(window)
    if n < 2:
        return (), ()
    voices = tuple(pitches for _start, pitches, _first in window)
    starts = [start for start, _pitches, _first in window]
    rhythm = _rhythm_signature(starts)
    return voices, rhythm


def _extract_percussion_windows(
    notes: Sequence[RenderedNote],
    n: int,
) -> list[tuple[tuple[tuple[tuple[int, ...], ...], tuple[int, ...]], RenderedNote]]:
    """Equivalente a `_extract_windows`, mas para tracks de percussao —
    usa `_percussion_events`/`_percussion_window_signature` em vez da
    reducao monofonica."""
    events = _percussion_events(notes)
    if len(events) < n:
        return []
    windows: list[tuple[tuple[tuple[tuple[int, ...], ...], tuple[int, ...]], RenderedNote]] = []
    for i in range(len(events) - n + 1):
        window = events[i : i + n]
        sig = _percussion_window_signature(window)
        windows.append((sig, window[0][2]))
    return windows


def _is_drum_element(plan: ArrangementPlan, element_id: str) -> bool:
    """True quando `element_id` referencia um elemento de `plan.elements`
    com `role` em `DRUMS_ROLES` (mesma convencao de familia usada por
    `tools.render._style_family_for_role`). Elemento nao encontrado no
    plano (ex.: teste que nao popula `plan.elements`) devolve `False` —
    trata como melodico, comportamento anterior a este achado."""
    for element in plan.elements:
        if element.id == element_id:
            return element.role in DRUMS_ROLES
    return False


# --- API publica ------------------------------------------------------------

def validate_anticopy(
    rendered_tracks: Iterable[RenderedTrack],
    plan: ArrangementPlan,
    analysis: Analysis,
    *,
    corpus: Iterable[ReferenceSequence] | None = None,
    n: int = DEFAULT_N,
) -> list[AntiCopyIssue]:
    """Compara janelas de N eventos da saida contra o corpus de referencia.

    - `corpus=None`: checagem comportamental e pulada (a estrutural, em
      `plan.validate`, sempre roda). Devolve lista vazia.
    - Percussao (`ReferenceSequence.is_drum` do lado do corpus,
      `Element.role in DRUMS_ROLES` do lado da saida — via
      `_is_drum_element(plan, track.element_id)`) usa a assinatura
      multi-voz de `_percussion_window_signature`: casamento exige o MESMO
      conjunto de pitches em cada onset da janela E o mesmo ritmo — ver
      docstring do modulo, secao "Bateria preserva as vozes simultaneas".
    - Melodia (tudo que nao e percussao) usa `_window_signature`, mas o
      casamento agora depende SO da tupla de intervalos — ritmo diferente
      nao livra uma melodia identica; ver secao "Melodia casa sozinha".
    - Determinismo: itera na ordem de `rendered_tracks` e retorna no
      maximo uma issue por track (a primeira janela casada), para nao
      soterrar o relatorio quando a copia e sistematica. `has_errors`
      basta para o CLI decidir bloqueio.
    """
    if n < MIN_N:
        raise ValueError(f"n must be >= {MIN_N}, got {n}")
    if corpus is None:
        return []

    # Indice melodico: chave = so os intervalos (achado do Codex, PR #100 —
    # ritmo deixou de ser porta de entrada obrigatoria). O ritmo do
    # casamento do CORPUS viaja no valor so para a mensagem poder dizer se
    # o ritmo tambem bateu.
    melodic_index: dict[tuple[int, ...], tuple[str, str, tuple[int, ...]]] = {}
    # Indice de percussao: chave = assinatura completa (vozes + ritmo) —
    # aqui os dois eixos continuam obrigatorios, ver docstring.
    percussion_index: dict[
        tuple[tuple[tuple[int, ...], ...], tuple[int, ...]],
        tuple[str, str],
    ] = {}
    for ref in corpus:
        if ref.is_drum:
            for sig, _first in _extract_percussion_windows(ref.notes, n):
                if not sig[0]:
                    continue
                percussion_index.setdefault(sig, (ref.source, ref.track_name))
        else:
            for (intervals, rhythm), _first in _extract_windows(ref.notes, n):
                if not intervals:
                    continue
                # Mesma assinatura em duas pecas de referencia: guardamos a
                # primeira ocorrencia estavel — o relatorio cita uma so, e
                # a ordem de iteracao do corpus e do chamador
                # (deterministica).
                melodic_index.setdefault(intervals, (ref.source, ref.track_name, rhythm))

    if not melodic_index and not percussion_index:
        return []

    issues: list[AntiCopyIssue] = []
    for track in rendered_tracks:
        is_drum = _is_drum_element(plan, track.element_id)
        bar = None
        source = source_track = None
        message = ""
        if is_drum:
            for sig, first in _extract_percussion_windows(track.notes, n):
                if not sig[0]:
                    continue
                match = percussion_index.get(sig)
                if match is None:
                    continue
                source, source_track = match
                bar = find_bar(analysis, float(first.start_s))
                message = (
                    f"element {track.element_id!r}, track {track.track_name!r}, "
                    f"bar {bar_number(bar)}: {n}-event percussion window "
                    f"matches {source!r} / track {source_track!r} (same "
                    f"simultaneous voices and rhythm at every onset)."
                )
                break
        else:
            for (intervals, rhythm), first in _extract_windows(track.notes, n):
                if not intervals:
                    # Janela sem intervalos (ex.: N=1 defensivo, ja
                    # bloqueado em MIN_N — guarda contra evolucao futura).
                    continue
                match = melodic_index.get(intervals)
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
                break
        if bar is None:
            continue
        issues.append(AntiCopyIssue(
            severity=SEVERITY_ERROR,
            element_id=track.element_id,
            track=track.track_name,
            bar=bar_number(bar),
            n=n,
            source=source,   # type: ignore[arg-type]
            source_track=source_track,   # type: ignore[arg-type]
            message=message,
        ))   # uma issue por track — ver docstring
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
                is_drum=bool(instrument.is_drum),
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
