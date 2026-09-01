"""Testes do validador anti-copia (AC-15 e AC-16 do bloco M3).

Cada teste cobre uma decisao explicitamente documentada em
`tools/validators/anticopy.py`. Ao rever ou mudar politica (N default,
tratamento de ritmo isolado, invariancia de transposicao), leia primeiro
a docstring do modulo e ajuste teste + docstring juntos.
"""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.plan import (
    ArrangementPlan,
    FamilyStyle,
    PlanSection,
    PlanValidationError,
    SourceMidi,
    validate as validate_plan,
)
from tools.validators.anticopy import (
    DEFAULT_N,
    MIN_N,
    AntiCopyIssue,
    ReferenceSequence,
    format_issues,
    has_errors,
    validate_anticopy,
)
from tools.validators.harmony import SEVERITY_ERROR, RenderedNote, RenderedTrack

# --- fixtures ---------------------------------------------------------------

BAR_S = 2.0


def _analysis(bars: int = 8) -> Analysis:
    return Analysis(
        key_root=9,
        bars=[
            BarAnalysis(
                index=i,
                start=i * BAR_S,
                end=(i + 1) * BAR_S,
                chord=Chord(root=9, quality="minor"),
            )
            for i in range(bars)
        ],
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _plan() -> ArrangementPlan:
    """Plano minimo — o validador nao consome, mas a assinatura pede."""
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path="in.mid", sha256="0" * 64),
        route="cinematica_emocional",
        sections=[PlanSection(
            label="MAIN", kind="chorus", source="marker",
            start_bar=1, end_bar=2,
            energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 5},
            protagonist="vocal_hook",
        )],
        elements=[],
    )


def _notes(pitches: list[int], starts: list[float], dur: float = 0.4) -> tuple[RenderedNote, ...]:
    """Constroi tupla de notas a partir de listas paralelas."""
    assert len(pitches) == len(starts)
    return tuple(
        RenderedNote(pitch=p, velocity=90, start_s=s, end_s=s + dur)
        for p, s in zip(pitches, starts, strict=True)
    )


def _track(element_id: str, name: str, pitches: list[int], starts: list[float]) -> RenderedTrack:
    return RenderedTrack(
        element_id=element_id,
        track_name=name,
        notes=_notes(pitches, starts),
    )


def _ref(source: str, name: str, pitches: list[int], starts: list[float]) -> ReferenceSequence:
    return ReferenceSequence(source=source, track_name=name, notes=_notes(pitches, starts))


# Riff sintetico de 6 notas (>= DEFAULT_N) — melodia + ritmo caracteristicos.
RIFF_PITCHES = [60, 63, 65, 66, 65, 63]
RIFF_STARTS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]

# --- AC-15 (estrutural) -----------------------------------------------------

def test_style_no_sequence_of_notes_ac15():
    """AC-15: schema do `style` do plano rejeita sequencia de notas.

    Vinculo desta issue com a barreira ja existente em
    `tools.style_schema.find_style_musical_content` chamada por
    `tools.plan.validate`. Se alguem trocar a barreira, este teste quebra.
    """
    plan = _plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="Some artist",
            researched_at="2026-01-01",
            sources=["https://example.com/x"],
            confidence="high",
            # Sem tecnicas: isolamos a barreira anticopia estrutural. A
            # regra de autorizacao (que exige brief_ref) e um teste
            # separado — aqui estamos provando que a sequencia de notas
            # embaixo de `parameters` nao passa.
            techniques=[],
            # Sequencia de MIDI pitches disfarcada como parametro numerico:
            # a barreira anticopia estrutural derruba isso mesmo debaixo
            # de `parameters` (nao e par [min, max]).
            parameters={"riff_pitches": [60.0, 63.0, 65.0, 66.0]},
        ),
    }
    with pytest.raises(PlanValidationError) as excinfo:
        validate_plan(plan)
    msg = str(excinfo.value).lower()
    assert "riff_pitches" in msg
    assert "sequencia" in msg or "musical" in msg or "proibid" in msg


# --- AC-16 (comportamental) -------------------------------------------------

def test_no_corpus_skips_behavioral_check():
    """Sem corpus fornecido a checagem e pulada — nenhuma issue."""
    track = _track("el1", "Bass 1", RIFF_PITCHES, RIFF_STARTS)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=None)
    assert issues == []


def test_direct_copy_detected():
    """Saida deliberadamente igual a uma track do corpus dispara erro."""
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    # Saida = mesma sequencia (mesmo pitch, mesmo ritmo).
    track = _track("bass1", "Bass Gen", RIFF_PITCHES, RIFF_STARTS)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_ERROR
    assert issues[0].source == "ref_song.mid"


def test_transposed_copy_is_detected():
    """Copia transposta por 7 semitons continua sendo copia.

    Esta e a que quebra implementacao ingenua (comparar pitch absoluto).
    """
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    transposed = [p + 7 for p in RIFF_PITCHES]
    track = _track("bass1", "Bass Gen", transposed, RIFF_STARTS)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert len(issues) == 1
    assert "transposition-invariant" in issues[0].message


def test_tempo_change_still_detected():
    """Copia em tempo diferente (todas IOIs multiplicadas por 2) e copia."""
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    slow_starts = [s * 2 for s in RIFF_STARTS]
    track = _track("bass1", "Bass Gen", RIFF_PITCHES, slow_starts)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert len(issues) == 1


def test_inspired_but_different_passes():
    """Saida inspirada no corpus, mas com material proprio (intervalos e
    ritmo diferentes) — passa sem issue."""
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    # Contorno completamente diferente, ritmo variando.
    own = _track(
        "bass1", "Bass Gen",
        pitches=[48, 55, 50, 48, 55, 60, 52, 48],
        starts=[0.0, 0.3, 0.55, 0.9, 1.1, 1.35, 1.7, 2.0],
    )
    issues = validate_anticopy([own], _plan(), _analysis(), corpus=[ref])
    assert issues == []


def test_short_riff_of_exactly_n_notes_detected():
    """Riff de exatamente DEFAULT_N notas ainda dispara.

    Prova o extremo alto do N default: se N fosse maior, riff curto e
    reconhecivel passaria batido.
    """
    six_pitches = RIFF_PITCHES[:DEFAULT_N]
    six_starts = RIFF_STARTS[:DEFAULT_N]
    assert len(six_pitches) == DEFAULT_N
    ref = _ref("hit.mid", "Lead", six_pitches, six_starts)
    track = _track("lead1", "Lead Gen", six_pitches, six_starts)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert len(issues) == 1


def test_scale_run_not_flagged_false_positive():
    """Quatro notas de escala em comum nao disparam.

    Prova o extremo baixo do N default: se N fosse <= 4, escala diatonica
    ascendente coincidiria por acaso o tempo todo.
    """
    # Escala diatonica ascendente em Am — quatro notas em comum entre
    # saida e corpus, mas cada faixa continua diferente depois.
    ref = _ref(
        "scale_song.mid", "Bass",
        pitches=[57, 59, 60, 62, 64, 65, 67, 69, 71],
        starts=[i * 0.25 for i in range(9)],
    )
    # Saida comeca com quatro notas iguais de escala e diverge.
    track = _track(
        "bass1", "Bass Gen",
        pitches=[57, 59, 60, 62, 55, 53, 52, 50, 48],
        starts=[i * 0.25 for i in range(9)],
    )
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert issues == []


def test_same_rhythm_different_intervals_is_not_copy():
    """Ritmo identico com pitches sem relacao NAO e copia.

    Decisao documentada no modulo: ritmo isolado e convencao estilistica
    ampla. Copia real precisa de contorno melodico E ritmo coincidindo.
    Se alguem mudar a politica para 'ritmo identico = copia', este teste
    quebra — de proposito.
    """
    ref = _ref("ref_song.mid", "Drums", RIFF_PITCHES, RIFF_STARTS)
    # Mesmo ritmo (mesmos onsets), intervalos totalmente diferentes.
    other_pitches = [72, 60, 48, 60, 72, 60]
    track = _track("bass1", "Bass Gen", other_pitches, RIFF_STARTS)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert issues == []


def test_report_names_track_and_bar_and_source():
    """Mensagem cita a track da saida, o compasso e a referencia casada."""
    ref = _ref("ref_song.mid", "Bass Ref", RIFF_PITCHES, RIFF_STARTS)
    # Coloca a copia no compasso 3: shift de 4 segundos (bar 3 comeca em 4.0).
    shifted = [s + 4.0 for s in RIFF_STARTS]
    track = _track("bass1", "Generated Bass", RIFF_PITCHES, shifted)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert len(issues) == 1
    msg = issues[0].message
    assert "Generated Bass" in msg
    assert "ref_song.mid" in msg
    assert "Bass Ref" in msg
    assert "bar 3" in msg
    assert issues[0].bar == 3


def test_n_below_min_raises():
    """N abaixo de MIN_N nao tem intervalo suficiente para comparar — erro."""
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    track = _track("bass1", "Bass Gen", RIFF_PITCHES, RIFF_STARTS)
    with pytest.raises(ValueError):
        validate_anticopy(
            [track], _plan(), _analysis(), corpus=[ref], n=MIN_N - 1,
        )


def test_configurable_n_makes_longer_windows_strict():
    """Aumentar N reduz falsos positivos: casamento de 6 nao dispara com N=8."""
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    track = _track("bass1", "Bass Gen", RIFF_PITCHES, RIFF_STARTS)
    # Com N=8 a janela nao cabe — nem no corpus nem na saida — nao ha casamento.
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref], n=8)
    assert issues == []


def test_one_issue_per_track_even_if_multiple_windows_match():
    """Copia sistematica: relatorio nao soterra — uma issue por track."""
    # Riff longo com dois casamentos consecutivos possiveis.
    long_pitches = RIFF_PITCHES + [67, 68, 65, 63, 60]
    long_starts = [i * 0.25 for i in range(len(long_pitches))]
    ref = _ref("ref_song.mid", "Bass", long_pitches, long_starts)
    track = _track("bass1", "Bass Gen", long_pitches, long_starts)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert len(issues) == 1


def test_has_errors_and_format():
    """`has_errors` e `format_issues` seguem contrato dos outros validadores."""
    assert has_errors([]) is False
    assert format_issues([]) == "Anti-copy: OK"
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    track = _track("bass1", "Bass Gen", RIFF_PITCHES, RIFF_STARTS)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    assert has_errors(issues) is True
    formatted = format_issues(issues)
    assert "Anti-copy issues" in formatted
    assert "[ERROR]" in formatted


def test_chord_voicing_does_not_generate_extra_matches():
    """Notas de acorde no mesmo onset colapsam para a nota mais aguda.

    Sem colapso, a linha lider ficaria fragmentada em varias sub-linhas por
    canal/voicing e a assinatura teria eventos concorrentes irreais.
    """
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    # Saida disparando cada nota junto com uma nota mais grave (chord):
    # a linha lider (topo) continua sendo RIFF_PITCHES.
    voiced_notes = []
    for pitch, start in zip(RIFF_PITCHES, RIFF_STARTS, strict=True):
        voiced_notes.append(RenderedNote(pitch=pitch, velocity=90, start_s=start, end_s=start + 0.4))
        voiced_notes.append(RenderedNote(pitch=pitch - 12, velocity=90, start_s=start, end_s=start + 0.4))
    track = RenderedTrack(element_id="bass1", track_name="Voiced Bass", notes=tuple(voiced_notes))
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    # Ainda casa (a copia real esta ali), mas so uma issue — o colapso
    # impediu duplicacao artificial de janelas.
    assert len(issues) == 1


def test_no_bypass_flag_available():
    """Copia nao tem `--allow-*`. Contrato mecanico: severidade sempre error.

    O teste garante que a barreira nao ganhou uma valvula de escape por
    engano (ao contrario de artifice/harmony que aceitam --allow-*).
    """
    ref = _ref("ref_song.mid", "Bass", RIFF_PITCHES, RIFF_STARTS)
    track = _track("bass1", "Bass Gen", RIFF_PITCHES, RIFF_STARTS)
    issues = validate_anticopy([track], _plan(), _analysis(), corpus=[ref])
    for issue in issues:
        assert isinstance(issue, AntiCopyIssue)
        assert issue.severity == SEVERITY_ERROR
