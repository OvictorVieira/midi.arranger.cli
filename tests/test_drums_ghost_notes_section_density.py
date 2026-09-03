"""Regressao issue #45: densidade de ghost note por secao, nao por constante fixa.

Bug medido no arquivo real do usuario (`DEIXE IR`, 74 compassos com bateria):
86% dos compassos saiam com ghost, mediana 4/compasso, maximo 9 num compasso,
e a densidade nao variava por secao de forma coerente (chorus com MAIS ghost
que verse). A causa era `target_count` tratando `density` como fracao do
total de candidatos do ARQUIVO INTEIRO — qualquer fracao razoavel saturava as
regras de posicao (que ja limitam a no maximo 2 candidatos por intervalo
entre backbeats) e a musica inteira convergia pra quase-maximo.

A correcao faz a QUANTIDADE por compasso derivar do eixo `densidade` (0-10)
de `plan.sections[].energy` — nunca de uma constante fixa por musica —
enquanto a regra de ONDE (backbeats, semicolcheias, `violates_position_rules`
em `tools/techniques/engine.py`) continua intocada. Este arquivo cobre os
seis pontos exigidos pela issue; os testes de ONDE (4 regras de posicao) ja
existem em `tests/test_techniques_engine.py` e nao sao duplicados aqui.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from io import BytesIO
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
from tools.render import render
from tools.techniques import apply_technique

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "corpus_drums"
DEIXE_IR = CORPUS_DIR / "DEIXE IR.mid"

# Mesmo teto documentado em `tools/techniques/engine.py::_apply_drums_ghost_notes`
# (`max_per_bar`, CONVENCAO do motor — o manual nao publica numero nenhum de
# ghost/compasso). 9/compasso (o maximo medido na issue) e atulhamento em
# qualquer leitura; o teste de teto abaixo prova que o motor nunca mais
# produz isso.
GHOST_NOTES_MAX_PER_BAR = 3
BUG_MEASURED_PCT_BARS_WITH_GHOST = 0.86


# --- fixtures -----------------------------------------------------------

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


def _drums_with_backbeats(bars: int, ticks_per_beat: int = 480) -> mido.MidiFile:
    """Caixa alta (backbeat) nos tempos 2 e 4 de cada compasso — material
    suficiente pra ter candidatos a ghost em todo compasso."""

    notes = []
    for bar in range(bars):
        bar_tick = bar * ticks_per_beat * 4
        notes.append((bar_tick + ticks_per_beat, bar_tick + ticks_per_beat + 60, 38, 108))
        notes.append((bar_tick + 3 * ticks_per_beat, bar_tick + 3 * ticks_per_beat + 60, 38, 108))
    return _midi_with_notes("Drums", 9, notes)


def _drum_note_starts(mid: mido.MidiFile) -> list[int]:
    out = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and getattr(msg, "channel", -1) == 9:
                out.append(tick)
    return out


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


TICKS_PER_BAR = 480 * 4


def _ghost_starts(source: mido.MidiFile, result: mido.MidiFile) -> list[int]:
    before = set(_drum_note_starts(source))
    return [tick for tick in _drum_note_starts(result) if tick not in before]


# --- nivel motor: variacao por secao -------------------------------------

def test_ghost_density_varies_between_sections_of_different_energy():
    """AC: quantidade por secao deriva do eixo `densidade`, nao de constante
    fixa. Duas metades do MESMO arquivo, mesma seed, so o eixo muda."""

    bars = 16
    source = _drums_with_backbeats(bars)
    sections = (
        {"start_tick": 0, "end_tick": TICKS_PER_BAR * 8, "kind": "verse", "densidade": 9},
        {"start_tick": TICKS_PER_BAR * 8, "end_tick": TICKS_PER_BAR * 16, "kind": "verse", "densidade": 1},
    )
    result = apply_technique(
        "drums.ghost_notes", source, seed=1, parameters={"sections": sections},
    )
    ghosts = _ghost_starts(source, result)
    assert ghosts, "a fixture precisa gerar ghost para o teste valer"

    high_energy = sum(1 for tick in ghosts if tick < TICKS_PER_BAR * 8)
    low_energy = sum(1 for tick in ghosts if tick >= TICKS_PER_BAR * 8)
    assert high_energy > low_energy, (
        f"secao com densidade=9 tem que render mais ghost que secao com "
        f"densidade=1 no MESMO arquivo; veio {high_energy} vs {low_energy}"
    )


def test_chorus_receives_fewer_ghosts_per_bar_than_verse_same_file():
    """AC: kind chorus/breakdown recebe densidade MENOR que verse/intro —
    refrao e peso/clareza, ghost e textura de verso."""

    bars = 16
    source = _drums_with_backbeats(bars)
    sections = (
        {"start_tick": 0, "end_tick": TICKS_PER_BAR * 8, "kind": "verse", "densidade": 8},
        {"start_tick": TICKS_PER_BAR * 8, "end_tick": TICKS_PER_BAR * 16, "kind": "chorus", "densidade": 8},
    )
    result = apply_technique(
        "drums.ghost_notes", source, seed=2, parameters={"sections": sections},
    )
    ghosts = _ghost_starts(source, result)
    per_bar = Counter(tick // TICKS_PER_BAR for tick in ghosts)

    verse_avg = sum(per_bar.get(bar, 0) for bar in range(8)) / 8
    chorus_avg = sum(per_bar.get(bar, 0) for bar in range(8, 16)) / 8
    assert chorus_avg < verse_avg, (
        "mesmo eixo densidade (8) nas duas secoes, chorus tem que render "
        f"menos ghost por compasso que verse; veio verse={verse_avg} "
        f"chorus={chorus_avg}"
    )


def test_no_bar_exceeds_the_named_cap():
    """AC: teto por compasso, com constante nomeada e documentada — mesmo
    com densidade no maximo (10) em todo o arquivo."""

    bars = 16
    source = _drums_with_backbeats(bars)
    sections = ({"start_tick": 0, "end_tick": TICKS_PER_BAR * bars, "kind": "verse", "densidade": 10},)
    for seed in range(1, 6):
        result = apply_technique(
            "drums.ghost_notes", source, seed=seed, parameters={"sections": sections},
        )
        ghosts = _ghost_starts(source, result)
        per_bar = Counter(tick // TICKS_PER_BAR for tick in ghosts)
        assert max(per_bar.values(), default=0) <= GHOST_NOTES_MAX_PER_BAR, (
            f"seed={seed}: compasso ultrapassou o teto de "
            f"{GHOST_NOTES_MAX_PER_BAR}/compasso — {per_bar}"
        )


def test_not_every_eligible_bar_gets_a_ghost():
    """AC: nem todo compasso elegivel recebe ghost — selecao entre
    candidatos deriva da seed. Com densidade moderada (5) em 16 compassos
    todos elegiveis, pelo menos um fica sem ghost."""

    bars = 16
    source = _drums_with_backbeats(bars)
    sections = ({"start_tick": 0, "end_tick": TICKS_PER_BAR * bars, "kind": "verse", "densidade": 5},)
    result = apply_technique(
        "drums.ghost_notes", source, seed=3, parameters={"sections": sections},
    )
    ghosts = _ghost_starts(source, result)
    per_bar = Counter(tick // TICKS_PER_BAR for tick in ghosts)
    empty_bars = [bar for bar in range(bars) if per_bar.get(bar, 0) == 0]
    assert empty_bars, (
        "densidade moderada em 16 compassos elegiveis tem que deixar pelo "
        "menos um sem ghost — senao a selecao nao esta derivando da seed, "
        "esta aplicando toda vez que a fracao e positiva (o defeito da "
        "issue: 86% dos compassos com ghost)"
    )
    assert len(ghosts) > 0, "densidade 5 nao pode zerar a tecnica inteira"


def test_same_seed_same_placement_different_seed_different_placement():
    """AC: mesma seed -> mesma colocacao; seed diferente -> colocacao
    diferente. Cobre a decisao de ATIVACAO por compasso, nao so a de ONDE
    dentro do compasso (ja coberta em test_techniques_engine.py)."""

    bars = 16
    source = _drums_with_backbeats(bars)
    sections = ({"start_tick": 0, "end_tick": TICKS_PER_BAR * bars, "kind": "verse", "densidade": 7},)

    same_a = apply_technique(
        "drums.ghost_notes", source, seed=5, parameters={"sections": sections},
    )
    same_b = apply_technique(
        "drums.ghost_notes", source, seed=5, parameters={"sections": sections},
    )
    different = apply_technique(
        "drums.ghost_notes", source, seed=6, parameters={"sections": sections},
    )

    assert _midi_bytes(same_a) == _midi_bytes(same_b)
    assert _midi_bytes(same_a) != _midi_bytes(different)


def test_missing_section_falls_back_to_documented_default_density():
    """AC: sem `sections` no contexto (chamada direta, fora do pipeline de
    render), a tecnica continua funcionando com o default declarado — nao
    quebra, nao vira NO-OP silencioso."""

    bars = 16
    source = _drums_with_backbeats(bars)
    result = apply_technique("drums.ghost_notes", source, seed=9)
    ghosts = _ghost_starts(source, result)
    assert ghosts, "default sem sections nao pode zerar a tecnica"


# --- regressao Codex PR #107 (issue #45, terceira rodada) -----------------

def test_reapplication_with_section_derived_density_is_byte_identical():
    """Achado P1 do Codex no PR #107: com `density` omitido (fracao vinda da
    secao, < 1), reaplicar a tecnica com a MESMA seed sobre a SAIDA da
    primeira passada nao pode acrescentar ghost em tick novo. O pitch de
    cada semicolcheia candidata tinha que deixar de depender de
    `len(candidates)` (contagem de candidatas JA elegiveis, que muda quando
    o MIDI ja carrega ghost de uma passada anterior) — ver comentario de
    `candidate_index` em `_apply_drums_ghost_notes`."""

    bars = 16
    source = _drums_with_backbeats(bars)
    sections = ({"start_tick": 0, "end_tick": TICKS_PER_BAR * bars, "kind": "verse", "densidade": 5},)

    first_pass = apply_technique(
        "drums.ghost_notes", source, seed=11, parameters={"sections": sections},
    )
    assert _ghost_starts(source, first_pass), "fixture precisa gerar ghost pra teste valer"

    second_pass = apply_technique(
        "drums.ghost_notes", first_pass, seed=11, parameters={"sections": sections},
    )

    assert _midi_bytes(first_pass) == _midi_bytes(second_pass), (
        "reaplicar drums.ghost_notes com a mesma seed sobre a propria saida "
        "tem que ser byte-identico — o deduplicador central so descarta "
        "duplicata de assinatura EXATA, entao o aplicador precisa regerar o "
        "MESMO alvo (tick, pitch) a cada passada"
    )


def test_render_does_not_report_default_assumption_with_explicit_density_override():
    """Achado do Codex no PR #107: com `style.drums.techniques[].density`
    explicito, `bar_fraction` consulta o override ANTES de qualquer janela
    de secao — o caminho de default (densidade=5/10) nunca e alcancado,
    entao o aviso de cobertura de secao nao pode disparar mesmo quando o
    MIDI de origem tem material fora de toda janela declarada."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    partial_end = total_bars // 2

    # reaproveita o padrao de `_render_deixe_ir_with_sections`, mas escrito
    # aqui porque esse teste precisa de `density` explicito na tecnica, algo
    # que o helper nao aceita.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        brief_path = tmp_path / "brief_explicit_density.json"
        brief_path.write_text(
            json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
            encoding="utf-8",
        )
        plan = ArrangementPlan(
            version=1,
            seed=13,
            source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
            route="cinematica_emocional",
            sections=[
                PlanSection(
                    label="VERSE", kind="verse", start_bar=0, end_bar=partial_end,
                    source="marker", protagonist="texture",
                    energy={
                        "densidade": 6, "impacto": 5, "largura": 5,
                        "altura": 5, "instabilidade": 3,
                    },
                ),
            ],
            elements=[],
            edits=[PlanEdit(track="MIDI", profile="drums", intensity=0.0)],
            style={
                "drums": FamilyStyle(
                    reference="Research", researched_at="2026-08-24",
                    sources=["https://example.test/drums"], confidence="high",
                    techniques=[StyleTechnique(name="drums.ghost_notes", density=0.4)],
                    parameters={},
                ),
            },
            brief_ref=BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path)),
        )
        out_path = tmp_path / "out_explicit_density.mid"
        report = render(plan, out_path)

    assert not any("drums.ghost_notes" in w and "default" in w for w in report.warnings), (
        "density explicito na tecnica desliga o caminho de default por "
        f"secao — nao pode disparar o aviso; warnings={report.warnings}"
    )


def test_per_bar_cap_is_shared_across_physical_tracks_of_the_same_edit_unit():
    """Achado do Codex no PR #107: quando um `edit.track` do plano resolve
    para MULTIPLAS tracks fisicas do MIDI de origem com o mesmo nome (a
    mesma unidade de edicao, AGENTS.md: "nomes repetidos de DAW sao
    tratados como uma unidade"), `_apply_style_techniques_to_edit_tracks`
    combina as tracks fisicas num unico `mido.MidiFile` e chama a tecnica
    UMA VEZ sobre ele — o teto por compasso tem que valer pra SOMA das duas
    tracks, nunca pra cada uma isolada."""

    bars = 8
    notes = []
    for bar in range(bars):
        bar_tick = bar * 480 * 4
        notes.append((bar_tick + 480, bar_tick + 480 + 60, 38, 108))
        notes.append((bar_tick + 3 * 480, bar_tick + 3 * 480 + 60, 38, 108))

    track_a = _midi_with_notes("Drums", 9, notes)
    track_b = _midi_with_notes("Drums", 9, notes)
    combined = mido.MidiFile(ticks_per_beat=480)
    combined.tracks.append(track_a.tracks[0])
    combined.tracks.append(track_a.tracks[1])
    combined.tracks.append(track_b.tracks[1])

    sections = ({"start_tick": 0, "end_tick": TICKS_PER_BAR * bars, "kind": "verse", "densidade": 10},)
    result = apply_technique(
        "drums.ghost_notes", combined, seed=17, parameters={"sections": sections},
    )

    before = set()
    for physical in (track_a.tracks[1], track_b.tracks[1]):
        tick = 0
        for msg in physical:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                before.add(tick)

    per_bar = Counter()
    for track in result.tracks[1:]:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and tick not in before:
                per_bar[tick // TICKS_PER_BAR] += 1

    assert per_bar, "fixture precisa gerar ghost combinado pra teste valer"
    assert max(per_bar.values()) <= GHOST_NOTES_MAX_PER_BAR, (
        f"soma das duas tracks fisicas ultrapassou o teto de "
        f"{GHOST_NOTES_MAX_PER_BAR}/compasso — {per_bar}"
    )


# --- nivel render: corpus real, cenario da issue --------------------------

def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_deixe_ir_with_sections(
    tmp_path: Path, *, seed: int, verse_densidade: int, chorus_densidade: int,
) -> tuple[Path, int]:
    """Renderiza DEIXE IR.mid com `plan.edits` humanizando a track de
    bateria e `style.drums.techniques=[ghost_notes]` autorizada, secoes
    verse (primeira metade) + chorus (segunda metade). Devolve
    (output_path, bar_divisor_entre_verse_e_chorus)."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    half = total_bars // 2

    brief_path = tmp_path / f"brief_{seed}.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
        encoding="utf-8",
    )
    plan = ArrangementPlan(
        version=1,
        seed=seed,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="VERSE", kind="verse", start_bar=0, end_bar=half,
                source="marker", protagonist="texture",
                energy={
                    "densidade": verse_densidade, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3,
                },
            ),
            PlanSection(
                label="CHORUS", kind="chorus", start_bar=half, end_bar=total_bars,
                source="marker", protagonist="texture",
                energy={
                    "densidade": chorus_densidade, "impacto": 8, "largura": 8,
                    "altura": 7, "instabilidade": 5,
                },
            ),
        ],
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
    out_path = tmp_path / f"out_{seed}.mid"
    render(plan, out_path)
    return out_path, half


def test_pct_of_bars_with_ghost_is_well_below_the_measured_bug_on_real_corpus(tmp_path):
    """AC: percentual de compassos com ghost fica bem abaixo de 86% num
    cenario equivalente — reproduz a medicao da issue sobre `DEIXE IR.mid`
    de ponta a ponta pelo `render()`, nao por fixture sintetica."""

    out_path, _half = _render_deixe_ir_with_sections(
        tmp_path, seed=1, verse_densidade=7, chorus_densidade=7,
    )

    src_mid = mido.MidiFile(str(DEIXE_IR))
    out_mid = mido.MidiFile(str(out_path))
    src_starts = set(_drum_note_starts(src_mid))
    out_starts = _drum_note_starts(out_mid)
    ghosts = [tick for tick in out_starts if tick not in src_starts]

    ticks_per_bar = src_mid.ticks_per_beat * 4
    bars_with_drums = {tick // ticks_per_bar for tick in src_starts}
    per_bar = Counter(tick // ticks_per_bar for tick in ghosts)
    pct_with_ghost = sum(
        1 for bar in bars_with_drums if per_bar.get(bar, 0) > 0
    ) / len(bars_with_drums)

    assert pct_with_ghost < BUG_MEASURED_PCT_BARS_WITH_GHOST - 0.2, (
        f"percentual de compassos com ghost ({pct_with_ghost:.2%}) precisa "
        f"ficar bem abaixo do medido na issue "
        f"({BUG_MEASURED_PCT_BARS_WITH_GHOST:.0%})"
    )
    assert max(per_bar.values(), default=0) <= GHOST_NOTES_MAX_PER_BAR


def test_render_chorus_gets_fewer_ghosts_per_bar_than_verse_on_real_corpus(tmp_path):
    """AC (via render, corpus real): refrao recebe menos ghost por compasso
    que verso no MESMO arquivo, com o mesmo eixo `densidade` nas duas
    secoes — so o `kind` muda."""

    out_path, half = _render_deixe_ir_with_sections(
        tmp_path, seed=2, verse_densidade=7, chorus_densidade=7,
    )

    src_mid = mido.MidiFile(str(DEIXE_IR))
    out_mid = mido.MidiFile(str(out_path))
    src_starts = set(_drum_note_starts(src_mid))
    out_starts = _drum_note_starts(out_mid)
    ghosts = [tick for tick in out_starts if tick not in src_starts]

    ticks_per_bar = src_mid.ticks_per_beat * 4
    bars_with_drums = sorted({tick // ticks_per_bar for tick in src_starts})
    per_bar = Counter(tick // ticks_per_bar for tick in ghosts)

    verse_bars = [bar for bar in bars_with_drums if bar < half]
    chorus_bars = [bar for bar in bars_with_drums if bar >= half]
    assert verse_bars and chorus_bars

    verse_avg = sum(per_bar.get(bar, 0) for bar in verse_bars) / len(verse_bars)
    chorus_avg = sum(per_bar.get(bar, 0) for bar in chorus_bars) / len(chorus_bars)
    assert chorus_avg < verse_avg, (
        f"verse_avg={verse_avg:.2f} chorus_avg={chorus_avg:.2f} — refrao "
        "tem que sair com MENOS ghost por compasso que verso"
    )


def test_render_reports_assumption_when_source_falls_outside_declared_sections(tmp_path):
    """AC: quando a secao nao declara energia (aqui: o MIDI de origem tem
    material fora de TODA janela de secao declarada), o render cai no
    default declarado e a suposicao aparece em `assumptions`
    (`RenderReport.warnings`)."""

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    # So declara secao ate a METADE do arquivo — o resto fica fora de toda
    # janela, forcando o default (densidade=5) la.
    partial_end = total_bars // 2

    brief_path = tmp_path / "brief_partial.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
        encoding="utf-8",
    )
    plan = ArrangementPlan(
        version=1,
        seed=4,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="VERSE", kind="verse", start_bar=0, end_bar=partial_end,
                source="marker", protagonist="texture",
                energy={
                    "densidade": 6, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3,
                },
            ),
        ],
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
    out_path = tmp_path / "out_partial.mid"
    report = render(plan, out_path)

    assert any(
        "drums.ghost_notes" in w and "default" in w for w in report.warnings
    ), f"esperava aviso de default por secao incompleta; warnings={report.warnings}"


def test_render_does_not_report_assumption_when_sections_cover_the_whole_file(tmp_path):
    """Contraparte do teste acima: secoes cobrindo o arquivo inteiro nao
    disparam o aviso de default — sem isso o aviso seria ruido em todo
    render normal."""

    out_path, _half = _render_deixe_ir_with_sections(
        tmp_path, seed=7, verse_densidade=6, chorus_densidade=6,
    )
    del out_path  # so o report interessa aqui; recalculamos pra pegá-lo

    from tools.analyze import analyze

    analysis = analyze(str(DEIXE_IR))
    total_bars = len(analysis.bars)
    half = total_bars // 2

    brief_path = tmp_path / "brief_full.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}}}),
        encoding="utf-8",
    )
    plan = ArrangementPlan(
        version=1,
        seed=8,
        source_midi=SourceMidi(path=str(DEIXE_IR), sha256=_sha256_of_file(DEIXE_IR)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="VERSE", kind="verse", start_bar=0, end_bar=half,
                source="marker", protagonist="texture",
                energy={
                    "densidade": 6, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3,
                },
            ),
            PlanSection(
                label="CHORUS", kind="chorus", start_bar=half, end_bar=total_bars,
                source="marker", protagonist="texture",
                energy={
                    "densidade": 6, "impacto": 8, "largura": 8,
                    "altura": 7, "instabilidade": 5,
                },
            ),
        ],
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
    out_path2 = tmp_path / "out_full.mid"
    report = render(plan, out_path2)

    assert not any("drums.ghost_notes" in w and "default" in w for w in report.warnings), (
        f"secoes cobrindo o arquivo inteiro nao podem disparar o aviso de "
        f"default; warnings={report.warnings}"
    )
