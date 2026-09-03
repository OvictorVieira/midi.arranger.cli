"""Regressao dos dois achados do Codex no PR #107 (issue #45 revisitada).

Finding 1 (`tools/techniques/engine.py::_apply_drums_ghost_notes`): a cota por
compasso e o lookup de densidade de secao usavam `ticks_per_beat * 4` — uma
suposicao de 4/4 constante. Em 3/4 (ou qualquer metro que nao seja 4/4), esse
bucket nao identifica compasso real nenhum: pode atravessar um compasso real
(estourando `max_per_bar`) ou aplicar a densidade da secao errada perto de uma
fronteira. A correcao thread `analysis.bars` (via `context.parameters["bars"]`
em `tools/render.py::_analysis_bar_windows`) e usa isso para agrupar/lookup.

Finding 2 (`tools/render.py`, aviso de cobertura de `plan.sections`): o
aviso so olhava a primeira e a ultima janela POR ORDEM DE LISTA, perdendo (a)
buraco entre duas secoes nao-adjacentes no MEIO e (b) falso-positivando quando
as secoes cobrem o arquivo inteiro mas vem fora de ordem cronologica (que
`plan.validate()` permite). A correcao ordena por `start_tick` e varre os
pares adjacentes (`tools/render.py::_section_windows_cover_range`).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import mido

from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    SourceMidi,
    StyleTechnique,
)
from tools.render import _section_windows_cover_range, render
from tools.techniques import apply_technique

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "corpus_drums"
DEIXE_IR = CORPUS_DIR / "DEIXE IR.mid"

GHOST_NOTES_MAX_PER_BAR = 3  # mesma constante de tools/techniques/engine.py


# --- fixtures 3/4 ----------------------------------------------------------

def _midi_with_notes(
    track_name: str, channel: int, notes: list[tuple[int, int, int, int]],
) -> mido.MidiFile:
    """notes: (start_tick, end_tick, pitch, velocity)."""

    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Meta", time=0))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    events: list[tuple[int, mido.Message]] = []
    for start, end, pitch, velocity in notes:
        events.append((
            start,
            mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=0),
        ))
        events.append((
            end,
            mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0),
        ))
    events.sort(key=lambda item: (item[0], 0 if item[1].type == "note_off" else 1))
    previous = 0
    for tick, msg in events:
        msg.time = tick - previous
        track.append(msg)
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.extend([meta, track])
    return mid


def _drum_note_starts(mid: mido.MidiFile) -> list[int]:
    out = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and getattr(msg, "channel", -1) == 9:
                out.append(tick)
    return out


def _ghost_starts(source: mido.MidiFile, result: mido.MidiFile) -> list[int]:
    before = set(_drum_note_starts(source))
    return [tick for tick in _drum_note_starts(result) if tick not in before]


TICKS_PER_BEAT = 480
TICKS_PER_BAR_34 = TICKS_PER_BEAT * 3  # 1440 — compasso 3/4 real
TICKS_PER_BAR_44_BUCKET = TICKS_PER_BEAT * 4  # 1920 — bucket antigo (bug)


def _drums_with_backbeats_34(bars: int) -> mido.MidiFile:
    """Caixa alta (candidata a backbeat) nos tempos 2 e 3 de cada compasso
    3/4 — material suficiente pra ter candidatos a ghost em todo compasso."""

    notes = []
    for bar in range(bars):
        bar_tick = bar * TICKS_PER_BAR_34
        notes.append((
            bar_tick + TICKS_PER_BEAT, bar_tick + TICKS_PER_BEAT + 60, 38, 108,
        ))
        notes.append((
            bar_tick + 2 * TICKS_PER_BEAT, bar_tick + 2 * TICKS_PER_BEAT + 60, 38, 108,
        ))
    return _midi_with_notes("Drums", 9, notes)


def _real_bars_34(bars: int) -> tuple[dict, ...]:
    return tuple(
        {
            "start_tick": bar * TICKS_PER_BAR_34,
            "end_tick": (bar + 1) * TICKS_PER_BAR_34,
            "index": bar,
        }
        for bar in range(bars)
    )


# --- Finding 1: cota por compasso REAL, nao por ticks_per_beat*4 -----------

def test_ghost_notes_never_exceeds_cap_per_real_bar_in_3_4_meter():
    """AC(a): em 3/4, nenhum compasso REAL (1440 ticks) recebe mais que
    `max_per_bar` ghosts, mesmo com densidade no maximo. Compasso real nao
    e multiplo do bucket antigo `ticks_per_beat*4` (1920) — se o motor ainda
    agrupasse por esse bucket, um compasso real poderia herdar sobra de dois
    buckets vizinhos e estourar o teto."""

    bars = 16
    source = _drums_with_backbeats_34(bars)
    real_bars = _real_bars_34(bars)
    sections = (
        {"start_tick": 0, "end_tick": TICKS_PER_BAR_34 * bars, "kind": "verse", "densidade": 10},
    )
    for seed in range(1, 6):
        result = apply_technique(
            "drums.ghost_notes", source, seed=seed,
            parameters={"sections": sections, "bars": real_bars},
        )
        ghosts = _ghost_starts(source, result)
        per_real_bar = Counter(tick // TICKS_PER_BAR_34 for tick in ghosts)
        assert max(per_real_bar.values(), default=0) <= GHOST_NOTES_MAX_PER_BAR, (
            f"seed={seed}: compasso 3/4 real ultrapassou o teto de "
            f"{GHOST_NOTES_MAX_PER_BAR}/compasso — {per_real_bar}"
        )


def test_ghost_notes_section_density_switches_at_real_bar_boundary_in_3_4_meter():
    """AC(b): a fronteira de secao que NAO cai em multiplo do bucket antigo
    (1920) ainda produz a queda de densidade no compasso real correto —
    prova que o lookup usa `analysis.bars`, nao `tick // (ticks_per_beat*4)`.

    Fronteira em compasso real 3 (tick 4320) — 4320 / 1920 = 2.25, fora de
    qualquer bucket 4/4: se o motor ainda usasse o bucket antigo, o lookup de
    secao pra ticks perto da fronteira cairia no bucket errado."""

    bars = 16
    boundary_bar = 3
    boundary_tick = boundary_bar * TICKS_PER_BAR_34
    assert boundary_tick % TICKS_PER_BAR_44_BUCKET != 0, (
        "fronteira precisa cair fora de um multiplo do bucket 4/4 antigo "
        "para o teste provar algo"
    )
    source = _drums_with_backbeats_34(bars)
    real_bars = _real_bars_34(bars)
    sections = (
        {"start_tick": 0, "end_tick": boundary_tick, "kind": "verse", "densidade": 1},
        {"start_tick": boundary_tick, "end_tick": TICKS_PER_BAR_34 * bars, "kind": "verse", "densidade": 10},
    )
    result = apply_technique(
        "drums.ghost_notes", source, seed=11,
        parameters={"sections": sections, "bars": real_bars},
    )
    ghosts = _ghost_starts(source, result)
    per_real_bar = Counter(tick // TICKS_PER_BAR_34 for tick in ghosts)

    low_density_bars = range(0, boundary_bar)
    high_density_bars = range(boundary_bar, bars)
    low_avg = sum(per_real_bar.get(b, 0) for b in low_density_bars) / len(list(low_density_bars))
    high_avg = sum(per_real_bar.get(b, 0) for b in high_density_bars) / len(list(high_density_bars))
    assert high_avg > low_avg, (
        f"compassos a partir do real #{boundary_bar} (densidade=10) tem que "
        f"render mais ghost por compasso que antes da fronteira "
        f"(densidade=1); veio low_avg={low_avg} high_avg={high_avg} "
        f"per_real_bar={dict(per_real_bar)}"
    )


def test_ghost_notes_falls_back_to_4_4_bucket_when_bars_not_supplied():
    """Retrocompatibilidade: chamada direta da tecnica sem `bars` no
    contexto (fora do pipeline de `tools.render`) continua funcionando —
    cai no bucket `ticks_per_beat*4` como antes, nunca quebra nem vira
    NO-OP silencioso."""

    bars = 16
    source = _drums_with_backbeats_34(bars)
    sections = (
        {"start_tick": 0, "end_tick": TICKS_PER_BAR_34 * bars, "kind": "verse", "densidade": 8},
    )
    result = apply_technique(
        "drums.ghost_notes", source, seed=21, parameters={"sections": sections},
    )
    ghosts = _ghost_starts(source, result)
    assert ghosts, "sem `bars`, a tecnica ainda precisa produzir ghost (fallback 4/4)"


# --- Finding 2: cobertura de plan.sections ----------------------------------

def test_section_windows_cover_range_detects_gap_between_non_adjacent_windows():
    """AC: buraco NO MEIO entre duas janelas nao-adjacentes e detectado —
    checar so a primeira/ultima janela (por ordem de lista OU por tick)
    deixava passar em silencio."""

    windows = (
        {"start_tick": 0, "end_tick": 100},
        {"start_tick": 200, "end_tick": 300},
    )
    assert _section_windows_cover_range(windows, total_ticks=300) is False


def test_section_windows_cover_range_true_when_fully_covered_out_of_chronological_order():
    """AC: `plan.validate()` permite `plan.sections[]` fora de ordem
    cronologica — a checagem de cobertura nao pode false-positivar so
    porque a ORDEM DE DECLARACAO nao bate com a ordem de tick."""

    windows = (
        {"start_tick": 200, "end_tick": 300},
        {"start_tick": 0, "end_tick": 200},
    )
    assert _section_windows_cover_range(windows, total_ticks=300) is True


def test_section_windows_cover_range_true_with_overlapping_windows():
    """Janelas sobrepostas/aninhadas nao podem confundir o merge por
    varredura — `covered_until` precisa ser o MAXIMO acumulado, nao so o
    fim da janela anterior por ordem de `start_tick`."""

    windows = (
        {"start_tick": 0, "end_tick": 150},
        {"start_tick": 100, "end_tick": 110},
        {"start_tick": 140, "end_tick": 300},
    )
    assert _section_windows_cover_range(windows, total_ticks=300) is True


def test_section_windows_cover_range_false_when_empty():
    assert _section_windows_cover_range((), total_ticks=300) is False


# --- Finding 2, nivel render: corpus real -----------------------------------

def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_with_sections(
    tmp_path: Path,
    *,
    seed: int,
    sections: list[PlanSection],
    brief_name: str,
) -> ArrangementPlan:
    brief_path = tmp_path / brief_name
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
        encoding="utf-8",
    )
    return ArrangementPlan(
        version=1,
        seed=seed,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=sections,
        elements=[],
        edits=[PlanEdit(track="MIDI", profile="drums", intensity=0.0)],
        style={
            "drums": FamilyStyle(
                reference="Research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes")],
                parameters={},
            ),
        },
        brief_ref=BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path)),
    )


def _energy(densidade: int) -> dict:
    return {
        "densidade": densidade, "impacto": 5, "largura": 5,
        "altura": 5, "instabilidade": 3,
    }


def test_render_reports_assumption_for_a_gap_between_two_non_adjacent_sections(tmp_path):
    """AC: `plan.sections` cobre o inicio e o fim do arquivo mas deixa um
    trecho NO MEIO descoberto — antes da correcao, o aviso so olhava a
    primeira/ultima janela e nao disparava aqui."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    q1 = max(1, total_bars // 4)
    q2 = max(q1 + 1, total_bars // 2)

    plan = _plan_with_sections(
        tmp_path,
        seed=101,
        sections=[
            PlanSection(
                label="A", kind="verse", start_bar=0, end_bar=q1,
                source="marker", protagonist="texture", energy=_energy(6),
            ),
            PlanSection(
                label="B", kind="chorus", start_bar=q2, end_bar=total_bars,
                source="marker", protagonist="texture", energy=_energy(6),
            ),
        ],
        brief_name="brief_gap.json",
    )
    out_path = tmp_path / "out_gap.mid"
    report = render(plan, out_path)

    assert any(
        "drums.ghost_notes" in w and "default" in w for w in report.warnings
    ), f"esperava aviso de default por buraco NO MEIO; warnings={report.warnings}"


def test_render_does_not_false_positive_when_full_coverage_sections_are_out_of_order(tmp_path):
    """AC: as mesmas duas secoes cobrindo o arquivo inteiro, so que
    DECLARADAS fora de ordem cronologica (permitido por `plan.validate()`),
    nao podem disparar o aviso de default."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    half = total_bars // 2

    plan = _plan_with_sections(
        tmp_path,
        seed=102,
        sections=[
            PlanSection(
                label="CHORUS", kind="chorus", start_bar=half, end_bar=total_bars,
                source="marker", protagonist="texture", energy=_energy(6),
            ),
            PlanSection(
                label="VERSE", kind="verse", start_bar=0, end_bar=half,
                source="marker", protagonist="texture", energy=_energy(6),
            ),
        ],
        brief_name="brief_out_of_order.json",
    )
    out_path = tmp_path / "out_out_of_order.mid"
    report = render(plan, out_path)

    assert not any(
        "drums.ghost_notes" in w and "default" in w for w in report.warnings
    ), (
        "secoes fora de ordem cronologica mas cobrindo o arquivo inteiro "
        f"nao podem disparar o aviso de default; warnings={report.warnings}"
    )
