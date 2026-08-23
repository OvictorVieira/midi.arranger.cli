"""Validador harmonico (FR-25, US-001).

Valida a SAIDA RENDERIZADA (tracks geradas) — o alvo e bug do gerador, nao
regra do plano. Cada nota de cada track gerada tem que pertencer ao campo
harmonico da secao em que soa, segundo o modo `harmony` declarado no
elemento correspondente.

Modos suportados (FR-25 do spec):
- `follow_chords`: pitch pertence ao acorde detectado no compasso do onset.
  Nota que atravessa compassos e validada pelo acorde do onset; se o pitch
  colidir fortemente com o acorde seguinte enquanto sustenta, emite AVISO
  (a nota nao vira erro — o gerador teve razao no onset).
- `pedal`: uma unica nota (mesma pitch class), tonica ou quinta do tom
  global. Qualquer coisa alem disso e erro.
- `unison_guitar`: cada nota tem uma nota de guitarra no mesmo onset
  (dentro de UNISON_MATCH_WINDOW_S) com a mesma pitch class (modulo oitava).
- `free`: isento de erro; AVISO quando o pitch cai fora da escala do tom.

Com `degrees` declarado no elemento, a lista de pitch classes permitidas
substitui a do modo — usada para arpejo que pega graus especificos (1, 5,
8, 10) sobre a escala do tom.

Compasso sem acorde deterministico (`chord=None` ou `quality='unknown'`):
o validador cai para a escala do tom e emite AVISO nomeando o compasso —
NAO bloqueia.

Consome apenas `analysis.py` (acordes, tom, guitar_notes) — nao reimplementa
deteccao. Roda depois do render; erros disparam exit code != 0 no CLI,
avisos so aparecem no relatorio.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .analyze import Analysis, BarAnalysis, GuitarNote, bar_number, find_bar
from .plan import ArrangementPlan, Element

# --- constantes -------------------------------------------------------------

PITCH_NAMES: tuple[str, ...] = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
)
"""Nomes de pitch class. C = 0, alteracoes sempre em sustenido — evita
disputa de nomenclatura entre gerador harmonico e leitor de relatorio."""

MIDI_MIDDLE_C = 60
"""C4 no padrao MIDI cientifico (yamaha/pretty_midi/logic). Usado para
nomear pitches (`C4`, `F#3`) no relatorio."""

CHORD_INTERVALS: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "power": (0, 7),
}
"""Pitch classes (relativas a raiz) que compoem cada qualidade de acorde
detectada em `analysis._classify_quality`. Qualidade 'unknown' NAO entra
aqui — cai para a escala do tom, com aviso."""

NATURAL_MINOR_INTERVALS: tuple[int, ...] = (0, 2, 3, 5, 7, 8, 10)
"""Escala menor natural (semitons a partir da tonica). Metal moderno vive
predominantemente no menor — a persona ARQUITETO tem a escala menor como
default. Documentado como padrao explicito: o campo `key_root` da analise
carrega so a raiz, sem modo; usamos menor natural para o teste de "nota
fora da escala" ate a analise resolver modo (fora do escopo desta rodada)."""

PEDAL_INTERVALS: frozenset[int] = frozenset({0, 7})
"""Intervalos permitidos em `harmony: pedal` — tonica e quinta justa do
tom global (FR-25 do spec)."""

UNISON_MATCH_WINDOW_S: float = 0.030
"""Tolerancia (segundos) para casar onset de nota gerada com onset de nota
de guitarra em `harmony: unison_guitar`. 30 ms cobre humanizacao e
antecipacao ate 2 semicolcheias a 174 bpm sem confundir compassos vizinhos."""

# Severidades — string em vez de Enum para economia de cerimonia e para o
# JSON de relatorio ficar legivel sem serializador especial.
SEVERITY_ERROR: str = "error"
SEVERITY_WARNING: str = "warning"


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class HarmonyIssue:
    """Problema harmonico em uma nota gerada.

    - severity: `error` (bloqueia render sem --allow-harmony) ou `warning`.
    - element_id: id do elemento no plano.
    - track: nome da track gerada onde a nota apareceu.
    - bar: numero do compasso (1-based, mesma convencao do CLI).
    - pitch: pitch MIDI da nota ofensora.
    - expected: descricao humana do que era esperado.
    - message: mensagem completa pronta para exibicao.
    """
    severity: str
    element_id: str
    track: str
    bar: int
    pitch: int
    expected: str
    message: str


@dataclass(frozen=True)
class RenderedNote:
    """Nota individual de uma track gerada — o insumo do validador."""
    pitch: int
    start_s: float
    end_s: float
    velocity: int = 0


@dataclass(frozen=True)
class RenderedTrack:
    """Track gerada por um elemento do plano.

    O renderer emite uma `RenderedTrack` por track de MIDI que produz
    (uma por layer). `element_id` amarra a track ao elemento no plano
    para o validador achar `harmony`, `degrees` etc.
    """
    element_id: str
    track_name: str
    notes: tuple[RenderedNote, ...] = field(default_factory=tuple)


# --- helpers puros ----------------------------------------------------------

def pitch_name(pitch: int) -> str:
    """Nome cientifico do pitch MIDI (C4 = 60). Ex: 54 -> 'F#3', 60 -> 'C4'."""
    octave = (pitch // 12) - 1
    return f"{PITCH_NAMES[pitch % 12]}{octave}"


def chord_pcs(chord) -> frozenset[int] | None:
    """Pitch classes do acorde ou None quando qualidade indeterminada."""
    if chord is None:
        return None
    intervals = CHORD_INTERVALS.get(chord.quality)
    if intervals is None:
        return None
    return frozenset((chord.root + i) % 12 for i in intervals)


def key_scale_pcs(key_root: int) -> frozenset[int]:
    """Pitch classes da escala menor natural do tom global."""
    return frozenset((key_root + i) % 12 for i in NATURAL_MINOR_INTERVALS)


def degrees_pcs(key_root: int, degrees: Iterable[int]) -> frozenset[int]:
    """Pitch classes correspondentes aos graus declarados sobre a escala do
    tom. Graus 1-based; degrees > 7 enrolam (8 == 1, 10 == 3) — a
    convencao do plano exemplo (`[1, 5, 8, 10]`) trata numeros altos como
    o mesmo grau em oitava acima. Grau <= 0 e ignorado."""
    scale = NATURAL_MINOR_INTERVALS
    pcs: set[int] = set()
    for d in degrees:
        if not isinstance(d, int) or d < 1:
            continue
        idx = (d - 1) % len(scale)
        pcs.add((key_root + scale[idx]) % 12)
    return frozenset(pcs)


def _allowed_pcs_for_bar(
    analysis: Analysis,
    bar: BarAnalysis | None,
) -> tuple[frozenset[int], bool]:
    """Devolve (pcs permitidos, is_fallback_to_key) para o bar.

    Compasso sem acorde deterministico (`chord=None` ou qualidade `unknown`)
    cai para a escala do tom com is_fallback=True — quem chama emite aviso."""
    if bar is None:
        return key_scale_pcs(analysis.key_root), True
    pcs = chord_pcs(bar.chord)
    if pcs is None:
        return key_scale_pcs(analysis.key_root), True
    return pcs, False


def _next_bar(analysis: Analysis, bar_idx: int) -> BarAnalysis | None:
    """Proximo compasso na lista da analise; None se `bar_idx` for o ultimo."""
    for bar in analysis.bars:
        if bar.index == bar_idx + 1:
            return bar
    return None


# --- validacao por modo -----------------------------------------------------

def _issue(
    severity: str,
    element_id: str,
    track: str,
    bar: int,
    pitch: int,
    expected: str,
    detail: str,
) -> HarmonyIssue:
    """Constroi `HarmonyIssue` com mensagem uniforme.

    Mensagem: "element X, track T, bar N: <detail>. Expected <expected>."
    Um formato so garante que quem le o relatorio nao precisa aprender
    dois estilos."""
    message = (
        f"element {element_id!r}, track {track!r}, bar {bar}: {detail}. "
        f"Expected {expected}."
    )
    return HarmonyIssue(
        severity=severity,
        element_id=element_id,
        track=track,
        bar=bar,
        pitch=pitch,
        expected=expected,
        message=message,
    )


def _validate_pedal(
    track: RenderedTrack,
    element: Element,
    analysis: Analysis,
) -> list[HarmonyIssue]:
    """`harmony: pedal` — uma unica pitch class, tonica ou quinta do tom."""
    issues: list[HarmonyIssue] = []
    if not track.notes:
        return issues
    key_root = analysis.key_root
    allowed = {(key_root + i) % 12 for i in PEDAL_INTERVALS}
    allowed_names = ", ".join(sorted(PITCH_NAMES[pc] for pc in allowed))
    expected = f"single pedal note (root/fifth of key: {allowed_names})"
    distinct_pcs = {n.pitch % 12 for n in track.notes}

    if len(distinct_pcs) > 1:
        # Toma a primeira nota fora do pc dominante como exemplo — mais
        # informativo que so contar. Ordem por onset preserva a "primeira
        # ofensa" no tempo.
        first = min(track.notes, key=lambda n: n.start_s)
        bar = find_bar(analysis, first.start_s)
        issues.append(_issue(
            SEVERITY_ERROR, element.id, track.track_name,
            bar_number(bar), first.pitch, expected,
            f"pedal requires a single pitch class; found {len(distinct_pcs)} "
            f"({', '.join(sorted(PITCH_NAMES[pc] for pc in distinct_pcs))})",
        ))
        return issues

    for note in track.notes:
        if (note.pitch % 12) in allowed:
            continue
        bar = find_bar(analysis, note.start_s)
        issues.append(_issue(
            SEVERITY_ERROR, element.id, track.track_name,
            bar_number(bar), note.pitch, expected,
            f"note {pitch_name(note.pitch)} is not root or fifth of key",
        ))
    return issues


def _validate_free(
    track: RenderedTrack,
    element: Element,
    analysis: Analysis,
) -> list[HarmonyIssue]:
    """`harmony: free` — sem erros; AVISO quando pitch fora da escala do tom."""
    issues: list[HarmonyIssue] = []
    scale = key_scale_pcs(analysis.key_root)
    scale_names = ", ".join(PITCH_NAMES[pc] for pc in sorted(scale))
    expected = f"pitch class in key scale ({scale_names})"
    for note in track.notes:
        if (note.pitch % 12) in scale:
            continue
        bar = find_bar(analysis, note.start_s)
        issues.append(_issue(
            SEVERITY_WARNING, element.id, track.track_name,
            bar_number(bar), note.pitch, expected,
            f"note {pitch_name(note.pitch)} lies outside the key scale",
        ))
    return issues


def _validate_unison(
    track: RenderedTrack,
    element: Element,
    analysis: Analysis,
) -> list[HarmonyIssue]:
    """`harmony: unison_guitar` — cada nota tem guitarra no mesmo onset e
    mesma pitch class (modulo oitava). Onsets fora da janela ou pitch
    diferente disparam erro."""
    issues: list[HarmonyIssue] = []
    guitar_notes = analysis.guitar_notes
    if not guitar_notes:
        # Sem guitarra na analise, nao ha unisono possivel. Erro por nota
        # geraria ruido — um so aviso por track e suficiente.
        if track.notes:
            first = min(track.notes, key=lambda n: n.start_s)
            bar = find_bar(analysis, first.start_s)
            issues.append(_issue(
                SEVERITY_ERROR, element.id, track.track_name,
                bar_number(bar), first.pitch,
                "guitar attack within ±30ms with matching pitch class",
                "harmony=unison_guitar declared but analysis exposed no "
                "guitar notes",
            ))
        return issues

    for note in track.notes:
        if _find_unison_match(note, guitar_notes) is not None:
            continue
        bar = find_bar(analysis, note.start_s)
        issues.append(_issue(
            SEVERITY_ERROR, element.id, track.track_name,
            bar_number(bar), note.pitch,
            "guitar attack within ±30ms with matching pitch class",
            f"note {pitch_name(note.pitch)} @ {note.start_s:.3f}s has "
            f"no guitar unison within ±{int(UNISON_MATCH_WINDOW_S*1000)}ms",
        ))
    return issues


def _find_unison_match(
    note: RenderedNote,
    guitar_notes: list[GuitarNote],
) -> GuitarNote | None:
    """Primeira nota de guitarra dentro da janela E com mesma pitch class."""
    target_pc = note.pitch % 12
    for g in guitar_notes:
        if g.start > note.start_s + UNISON_MATCH_WINDOW_S:
            # guitar_notes vem ordenado por start em analysis.analyze;
            # podemos abortar cedo.
            return None
        if g.start < note.start_s - UNISON_MATCH_WINDOW_S:
            continue
        if (g.pitch % 12) == target_pc:
            return g
    return None


def _validate_follow_chords_or_degrees(
    track: RenderedTrack,
    element: Element,
    analysis: Analysis,
) -> list[HarmonyIssue]:
    """`harmony: follow_chords` (com ou sem `degrees` declarados)."""
    issues: list[HarmonyIssue] = []
    use_degrees = element.degrees is not None and len(element.degrees) > 0
    if use_degrees:
        allowed_key = degrees_pcs(analysis.key_root, element.degrees)
        allowed_names = ", ".join(sorted(PITCH_NAMES[pc] for pc in allowed_key))
        degrees_desc = ",".join(str(d) for d in element.degrees)
        expected_degrees = (
            f"pitch class in declared degrees [{degrees_desc}] "
            f"over key ({allowed_names})"
        )

    # Compassos vazios que servem de fallback ganham apenas UM aviso, para
    # nao inundar o relatorio quando o pad passa 8 compassos sem acorde
    # deterministico.
    warned_fallback: set[int] = set()

    for note in track.notes:
        bar = find_bar(analysis, note.start_s)
        bar_num = bar_number(bar)

        if use_degrees:
            if (note.pitch % 12) not in allowed_key:
                issues.append(_issue(
                    SEVERITY_ERROR, element.id, track.track_name, bar_num,
                    note.pitch, expected_degrees,
                    f"note {pitch_name(note.pitch)} lies outside declared "
                    f"degrees over key scale",
                ))
            continue

        allowed, is_fallback = _allowed_pcs_for_bar(analysis, bar)
        if is_fallback and bar_num not in warned_fallback:
            warned_fallback.add(bar_num)
            scale_names = ", ".join(sorted(PITCH_NAMES[pc] for pc in allowed))
            issues.append(_issue(
                SEVERITY_WARNING, element.id, track.track_name, bar_num,
                note.pitch,
                f"pitch class in key scale ({scale_names})",
                "bar has no deterministic chord — falling back to key scale",
            ))

        if (note.pitch % 12) not in allowed:
            allowed_names = ", ".join(sorted(PITCH_NAMES[pc] for pc in allowed))
            severity = SEVERITY_WARNING if is_fallback else SEVERITY_ERROR
            issues.append(_issue(
                severity, element.id, track.track_name, bar_num, note.pitch,
                f"pitch class in {{{allowed_names}}}",
                f"note {pitch_name(note.pitch)} lies outside the chord",
            ))
            continue

        # Cross-bar warning: nota que segue no proximo compasso e conflita
        # com o proximo acorde. Aviso, nunca erro — a nota estava certa no
        # onset. So aplicavel a follow_chords sem degrees.
        if bar is not None and note.end_s > bar.end:
            nb = _next_bar(analysis, bar.index)
            next_pcs = chord_pcs(nb.chord) if nb is not None else None
            if next_pcs is not None and (note.pitch % 12) not in next_pcs:
                next_names = ", ".join(sorted(PITCH_NAMES[pc] for pc in next_pcs))
                issues.append(_issue(
                    SEVERITY_WARNING, element.id, track.track_name,
                    bar_num, note.pitch,
                    f"pitch class in {{{next_names}}} at bar {nb.index + 1}",
                    f"sustained note {pitch_name(note.pitch)} crosses into "
                    f"bar {nb.index + 1} and clashes with next chord",
                ))
    return issues


# --- API principal ----------------------------------------------------------

def validate_harmony(
    rendered_tracks: list[RenderedTrack],
    plan: ArrangementPlan,
    analysis: Analysis,
) -> list[HarmonyIssue]:
    """Roda todas as regras harmonicas sobre as tracks geradas.

    Ordem: pedal -> follow_chords/degrees -> unison_guitar -> free.
    Track cujo `element_id` nao aparece no plano e ignorada sem erro — o
    contrato do renderer garante o vinculo. Tracks originais do source
    NUNCA sao validadas: o alvo do validador e bug de gerador, e o source
    pode ser roboticamente legitimo por conta propria (secao 3 do spec).
    """
    issues: list[HarmonyIssue] = []
    elements_by_id = {e.id: e for e in plan.elements}

    for track in rendered_tracks:
        element = elements_by_id.get(track.element_id)
        if element is None:
            continue
        mode = element.harmony
        if mode == "pedal":
            issues.extend(_validate_pedal(track, element, analysis))
        elif mode == "follow_chords":
            issues.extend(_validate_follow_chords_or_degrees(track, element, analysis))
        elif mode == "unison_guitar":
            issues.extend(_validate_unison(track, element, analysis))
        elif mode == "free":
            issues.extend(_validate_free(track, element, analysis))
        else:
            # Vocabulario fechado ja garantido em plan.validate; guarda
            # extra caso a lista mude sem atualizar este modulo.
            continue
    return issues


def has_errors(issues: list[HarmonyIssue]) -> bool:
    """True quando ha pelo menos uma severidade `error` na lista."""
    return any(i.severity == SEVERITY_ERROR for i in issues)


def format_issues(issues: list[HarmonyIssue]) -> str:
    """Pretty-print para o relatorio do render (ordem: errors primeiro)."""
    if not issues:
        return "Harmony: OK"
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    warnings = [i for i in issues if i.severity == SEVERITY_WARNING]
    lines = [f"Harmony issues: {len(errors)} error(s), {len(warnings)} warning(s)"]
    for i in errors:
        lines.append(f"  [ERROR]   {i.message}")
    for i in warnings:
        lines.append(f"  [WARNING] {i.message}")
    return "\n".join(lines)


__all__ = [
    "CHORD_INTERVALS",
    "HarmonyIssue",
    "NATURAL_MINOR_INTERVALS",
    "PEDAL_INTERVALS",
    "PITCH_NAMES",
    "RenderedNote",
    "RenderedTrack",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "UNISON_MATCH_WINDOW_S",
    "chord_pcs",
    "degrees_pcs",
    "format_issues",
    "has_errors",
    "key_scale_pcs",
    "pitch_name",
    "validate_harmony",
]
