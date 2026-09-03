"""Regressao da segunda rodada do Codex no PR #107 (issue #45, terceira
rodada de achados sobre `drums.ghost_notes`).

Finding 1 (`tools/techniques/engine.py::_apply_drums_ghost_notes`,
`tools/render.py`): a cota por compasso (`max_per_bar`) ja era compartilhada
entre tracks fisicas DENTRO de uma so chamada da tecnica (fix anterior,
`test_per_bar_cap_is_shared_across_physical_tracks_of_the_same_edit_unit`
em `tests/test_drums_ghost_notes_section_density.py`), mas
`tools.render.render()` despacha a tecnica de bateria em chamadas
SEPARADAS — uma por `plan.edits[]` com `profile=drums` e mais uma por
elemento de bateria gerado — cada uma recriando `bar_counts`/`bar_targets`
do zero. Duas edits de bateria distintas, ou uma edit mais um elemento
gerado, ambos com backbeat no mesmo compasso, podiam somar mais que
`max_per_bar` no arquivo final. A correcao thread um dict MUTAVEL
(`context.parameters["drum_bar_quota"]`, criado uma vez por `render()`) por
TODO despacho de bateria da mesma chamada.

Finding 2 (`tools/render.py`, aviso de cobertura de `plan.sections`): o
aviso de "densidade caiu no default por secao" disparava mesmo sem NENHUM
alvo de bateria despachado (nem edit, nem elemento gerado), e tambem
falso-positivava quando o unico alvo de bateria tinha notas inteiramente
dentro das secoes declaradas mas outra track (nao-bateria) do arquivo se
estendia alem da ultima secao. A correcao gate o aviso em (a) haver pelo
menos um alvo de bateria de fato despachado e (b) as proprias notas DESSE
alvo (nunca o arquivo inteiro) terem candidato fora das janelas de secao.
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
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    SourceMidi,
    StyleTechnique,
)
from tools.render import render

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "corpus_drums"
DEIXE_IR = CORPUS_DIR / "DEIXE IR.mid"

GHOST_NOTES_MAX_PER_BAR = 3  # mesma constante de tools/techniques/engine.py
TICKS_PER_BEAT = 480
TICKS_PER_BAR = TICKS_PER_BEAT * 4


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_events(track: mido.MidiTrack, events: list[tuple[int, mido.Message]]) -> None:
    events.sort(key=lambda item: (item[0], 0 if item[1].type == "note_off" else 1))
    previous = 0
    for tick, msg in events:
        msg.time = tick - previous
        track.append(msg)
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))


def _drum_backbeats_source(bars: int, track_names: list[str]) -> mido.MidiFile:
    """MIDI sintetico 4/4 com uma ou mais tracks de bateria (canal 9),
    caixa alta (candidata a backbeat) nos tempos 2 e 4 de cada compasso —
    material suficiente para gerar candidatos a ghost em todo compasso."""

    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Src", time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(meta)
    for name in track_names:
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        events: list[tuple[int, mido.Message]] = []
        for bar in range(bars):
            bar_tick = bar * TICKS_PER_BAR
            for offset in (TICKS_PER_BEAT, 3 * TICKS_PER_BEAT):
                events.append((
                    bar_tick + offset,
                    mido.Message("note_on", channel=9, note=38, velocity=108, time=0),
                ))
                events.append((
                    bar_tick + offset + 60,
                    mido.Message("note_off", channel=9, note=38, velocity=0, time=0),
                ))
        _write_events(track, events)
        mid.tracks.append(track)
    return mid


def _low_velocity_ghost_pitch_counts_per_bar(mid: mido.MidiFile) -> Counter:
    """Conta note_on de canal 9, pitch 38 (nota de ghost da receita generic)
    com velocity dentro da faixa de ghost (<=45, manual `tecnicas_bateria_
    midi.md` §7.2) por compasso — a mesma assinatura que
    `drums.ghost_notes` grava, nunca presente no material de origem
    sintetico (backbeats saem com velocity 108)."""

    per_bar: Counter = Counter()
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if (
                msg.type == "note_on"
                and msg.velocity > 0
                and getattr(msg, "channel", None) == 9
                and msg.note == 38
                and msg.velocity <= 45
            ):
                per_bar[tick // TICKS_PER_BAR] += 1
    return per_bar


def _brief(tmp_path: Path, name: str) -> Path:
    brief_path = tmp_path / name
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
        encoding="utf-8",
    )
    return brief_path


# --- Finding 1a: duas edits de bateria distintas, mesmo compasso -----------

def test_per_bar_cap_is_shared_across_two_separate_drum_edit_units(tmp_path):
    """AC (1a): plan.edits com DUAS tracks de bateria de nomes diferentes,
    ambas com backbeat no mesmo compasso e densidade no maximo — a SOMA de
    ghosts das duas nunca pode ultrapassar `max_per_bar` no arquivo final.
    Cada edit dispara `_run_style_pipeline` numa chamada separada; sem a
    cota compartilhada entre chamadas, cada uma podia atingir o teto
    isoladamente e a soma estourar."""

    bars = 8
    src_path = tmp_path / "src.mid"
    _drum_backbeats_source(bars, ["DrumsA", "DrumsB"]).save(str(src_path))

    plan = ArrangementPlan(
        version=1,
        seed=9,
        source_midi=SourceMidi(path=str(src_path), sha256=_sha256_of_file(src_path)),
        route="cinematica_emocional",
        sections=[],
        elements=[],
        edits=[
            PlanEdit(track="DrumsA", profile="drums", intensity=0.0),
            PlanEdit(track="DrumsB", profile="drums", intensity=0.0),
        ],
        style={
            "drums": FamilyStyle(
                reference="Research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes", density=1.0)],
                parameters={},
            ),
        },
        brief_ref=BriefRef(
            path=str(_brief(tmp_path, "brief_1a.json")),
            sha256=brief_sha256(_brief(tmp_path, "brief_1a.json")),
        ),
    )
    out_path = tmp_path / "out_1a.mid"
    render(plan, out_path)

    out_mid = mido.MidiFile(str(out_path))
    per_bar = _low_velocity_ghost_pitch_counts_per_bar(out_mid)
    assert per_bar, "fixture precisa gerar ghost combinado pra teste valer"
    assert max(per_bar.values()) <= GHOST_NOTES_MAX_PER_BAR, (
        "soma de ghosts das duas edits de bateria distintas ultrapassou o "
        f"teto de {GHOST_NOTES_MAX_PER_BAR}/compasso — {dict(per_bar)}"
    )


# --- Finding 1b: edit de bateria + elemento de bateria gerado --------------

def test_per_bar_cap_is_shared_between_edit_track_and_generated_drum_element(tmp_path):
    """AC (1b): a track de bateria editada (`plan.edits`) e um elemento de
    bateria GERADO cobrindo a mesma secao — cada um cai em despacho
    separado de `_run_style_pipeline` (o loop de edits roda antes, o loop
    de elementos depois). A SOMA de ghosts das duas fontes no arquivo final
    nunca pode ultrapassar `max_per_bar`, mesmo com densidade no maximo."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)

    plan = ArrangementPlan(
        version=1,
        seed=5,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN", kind="verse", start_bar=0, end_bar=total_bars,
                source="marker", protagonist="texture",
                energy={
                    "densidade": 10, "impacto": 8, "largura": 8,
                    "altura": 7, "instabilidade": 3,
                },
            ),
        ],
        elements=[
            Element(
                id="drums_gen", role="drums", sections=["MAIN"], register=[0, 127],
                layers=1, sync_role="exact_anchor", articulation="tight",
                harmony="percussion",
                instrument={
                    "plugin": "Superior Drummer", "preset": "Metal Kit",
                    "verified": True,
                },
                rationale="Bateria gerada do zero pra testar cota "
                "compartilhada com a edit.",
            ),
        ],
        edits=[PlanEdit(track="MIDI", profile="drums", intensity=0.0)],
        style={
            "drums": FamilyStyle(
                reference="Research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes", density=1.0)],
                parameters={},
            ),
        },
        brief_ref=BriefRef(
            path=str(_brief(tmp_path, "brief_1b.json")),
            sha256=brief_sha256(_brief(tmp_path, "brief_1b.json")),
        ),
    )
    out_path = tmp_path / "out_1b.mid"
    render(plan, out_path)

    out_mid = mido.MidiFile(str(out_path))
    per_bar = _low_velocity_ghost_pitch_counts_per_bar(out_mid)
    assert per_bar, "fixture precisa gerar ghost combinado pra teste valer"
    assert max(per_bar.values()) <= GHOST_NOTES_MAX_PER_BAR, (
        "soma de ghosts da edit de bateria + elemento gerado ultrapassou o "
        f"teto de {GHOST_NOTES_MAX_PER_BAR}/compasso — {dict(per_bar)}"
    )


# --- Finding 2a: nenhum alvo de bateria despachado --------------------------

def test_coverage_warning_does_not_fire_without_any_drum_target(tmp_path):
    """AC (2a): `style.drums.techniques` declara `ghost_notes` sem
    `density` explicito (caminho de default por secao), mas nao ha NENHUM
    `plan.edits[]` com `profile=drums` nem elemento de bateria gerado — a
    tecnica nunca e despachada, entao o aviso de "densidade assumida no
    default" e falso e nao pode aparecer."""

    plan = ArrangementPlan(
        version=1,
        seed=1,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=[],
        elements=[],
        edits=[],
        style={
            "drums": FamilyStyle(
                reference="Research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes")],
                parameters={},
            ),
        },
        brief_ref=BriefRef(
            path=str(_brief(tmp_path, "brief_2a.json")),
            sha256=brief_sha256(_brief(tmp_path, "brief_2a.json")),
        ),
    )
    out_path = tmp_path / "out_2a.mid"
    report = render(plan, out_path)

    assert not any(
        "drums.ghost_notes" in w and "default" in w for w in report.warnings
    ), (
        "sem alvo de bateria nenhum, a tecnica nunca roda — o aviso de "
        f"default e falso; warnings={report.warnings}"
    )


# --- Finding 2b: elemento de bateria coberto, track NAO-bateria fora -------

def _source_with_bass_extending_past_declared_sections(bars: int) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="Src", time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(meta)
    bass = mido.MidiTrack()
    bass.append(mido.MetaMessage("track_name", name="Bass", time=0))
    events: list[tuple[int, mido.Message]] = []
    for bar in range(bars):
        bar_tick = bar * TICKS_PER_BAR
        events.append((
            bar_tick, mido.Message("note_on", channel=1, note=40, velocity=90, time=0),
        ))
        events.append((
            bar_tick + 400,
            mido.Message("note_off", channel=1, note=40, velocity=0, time=0),
        ))
    _write_events(bass, events)
    mid.tracks.append(bass)
    return mid


def test_coverage_warning_ignores_unrelated_non_drum_track_extending_past_sections(
    tmp_path,
):
    """AC (2b): um elemento de bateria GERADO cujas proprias notas ficam
    inteiramente dentro das secoes declaradas (bars 0-8), mas uma track
    NAO-bateria do arquivo de origem (baixo) se estende ate o bar 15 — o
    aviso de cobertura tem que olhar so as notas do ALVO DE BATERIA, nunca
    "o arquivo inteiro"; antes da correcao, o range do arquivo inteiro
    (que inclui o baixo fora da secao) disparava o aviso mesmo sem nenhuma
    nota de bateria fora de cobertura."""

    total_bars = 16
    src_path = tmp_path / "src.mid"
    _source_with_bass_extending_past_declared_sections(total_bars).save(str(src_path))

    plan = ArrangementPlan(
        version=1,
        seed=3,
        source_midi=SourceMidi(path=str(src_path), sha256=_sha256_of_file(src_path)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN", kind="verse", start_bar=0, end_bar=8,
                source="marker", protagonist="texture",
                energy={
                    "densidade": 8, "impacto": 6, "largura": 6,
                    "altura": 6, "instabilidade": 3,
                },
            ),
        ],
        elements=[
            Element(
                id="drums_gen", role="drums", sections=["MAIN"], register=[0, 127],
                layers=1, sync_role="exact_anchor", articulation="tight",
                harmony="percussion",
                instrument={
                    "plugin": "Superior Drummer", "preset": "Metal Kit",
                    "verified": True,
                },
                rationale="Bateria gerada so na secao declarada.",
            ),
        ],
        edits=[],
        style={
            "drums": FamilyStyle(
                reference="Research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes")],
                parameters={},
            ),
        },
        brief_ref=BriefRef(
            path=str(_brief(tmp_path, "brief_2b.json")),
            sha256=brief_sha256(_brief(tmp_path, "brief_2b.json")),
        ),
    )
    out_path = tmp_path / "out_2b.mid"
    report = render(plan, out_path)

    assert not any(
        "drums.ghost_notes" in w and "default" in w for w in report.warnings
    ), (
        "notas de bateria inteiramente dentro da secao declarada; o baixo "
        "fora da secao nao pode disparar o aviso de default; "
        f"warnings={report.warnings}"
    )
