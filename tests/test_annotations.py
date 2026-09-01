"""Testes de extracao de anotacoes textuais do MIDI (issue #32).

Cobre:
- Extracao de marker (nao-secao), text, cue_marker.
- Filtro de ruido (padroes default `END_OF_VOICE`, `MEASURE_\\d+` e limiar
  de repeticao) com relatorio explicito das razoes de descarte.
- Regra de escopo: proxima anotacao dentro da mesma secao OU fim da secao,
  o que vier primeiro; empate vai para `section_end`.
- Anotacao exatamente na fronteira de secao pertence a secao que COMECA ali.
- Fixture ancora sem regressao (10 secoes, 0 anotacoes).
- Fixture corpus_drums/ENTRE NOS.mid descarta 990 ruidos com relatorio.
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter

import mido
import pretty_midi
import pytest

from tools import analyze as analyze_mod
from tools import contract  # noqa: F401 — popula o registry por side effect
from tools.registry import call

ANCORA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)
DRUMS_ENTRE_NOS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "corpus_drums",
    "ENTRE NÓS.mid",
)


def _require(path: str) -> str:
    if not os.path.exists(path):
        pytest.skip(f"fixture nao presente: {path}")
    return path


# --- regressao: fixtures conhecidas ----------------------------------------

def test_ancora_has_10_sections_and_zero_annotations():
    """AC: `analyze` sobre ANCORA devolve as 10 secoes como hoje, sem regressao.
    Todos os markers do ancora sao rotulos de secao, entao nenhuma vira anotacao.
    """
    a = analyze_mod.analyze(_require(ANCORA))
    assert a.annotations == []
    assert a.discarded_annotations == []


def test_entre_nos_discards_990_daw_noise_with_reason_breakdown():
    """AC: `analyze` sobre ENTRE NOS descarta os markers de DAW como ruido e
    informa a contagem descartada. Sao 984 `MEASURE_*` + 6 `END_OF_VOICE`."""
    a = analyze_mod.analyze(_require(DRUMS_ENTRE_NOS))
    assert a.annotations == []
    assert len(a.discarded_annotations) == 990
    reasons = Counter(d.reason for d in a.discarded_annotations)
    assert reasons[r"pattern:^MEASURE_\d+$"] == 984
    assert reasons[r"pattern:^END_OF_VOICE$"] == 6


# --- extracao com fixture plantada -----------------------------------------

def _write_midi_with_events(
    path: str,
    marker_events: list[tuple[int, str]] | None = None,
    text_events: list[tuple[int, str]] | None = None,
    cue_marker_events: list[tuple[int, str]] | None = None,
    tempo_bpm: float = 120.0,
    bars: int = 16,
) -> None:
    """Escreve um MIDI de teste com secoes reais + eventos de anotacao.

    Usa `mido` diretamente para conseguir gravar text/cue_marker (o pretty_midi
    nao expoe). Grid 4/4 a `tempo_bpm`; cada compasso = 4 quartos.
    """
    marker_events = marker_events or []
    text_events = text_events or []
    cue_marker_events = cue_marker_events or []

    mid = mido.MidiFile()
    mid.ticks_per_beat = 480
    meta_track = mido.MidiTrack()
    mid.tracks.append(meta_track)
    meta_track.append(mido.MetaMessage("track_name", name="Metadata", time=0))
    tempo_us = int(round(60_000_000 / tempo_bpm))
    meta_track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    meta_track.append(mido.MetaMessage(
        "time_signature", numerator=4, denominator=4,
        clocks_per_click=24, notated_32nd_notes_per_beat=8, time=0,
    ))

    # Combina eventos meta e ordena por tick absoluto.
    events: list[tuple[int, str, str]] = []
    for tick, text in marker_events:
        events.append((tick, "marker", text))
    for tick, text in text_events:
        events.append((tick, "text", text))
    for tick, text in cue_marker_events:
        events.append((tick, "cue_marker", text))
    events.sort(key=lambda e: e[0])

    prev_tick = 0
    for tick, kind, text in events:
        delta = tick - prev_tick
        meta_track.append(mido.MetaMessage(kind, text=text, time=delta))
        prev_tick = tick

    # Track de notas com variacao de posicao — pretty_midi.estimate_tempi
    # devolve vazio quando todos os inter-onsets sao identicos, e o codigo
    # de bars_from cai nesse caminho. Duas notas por compasso em posicoes
    # ligeiramente diferentes garantem que a estimativa funcione mesmo sem
    # que qualquer teste dependa do tempo estimado.
    note_track = mido.MidiTrack()
    mid.tracks.append(note_track)
    note_track.append(mido.MetaMessage("track_name", name="Piano", time=0))
    tpb = mid.ticks_per_beat  # 480
    for bar in range(bars):
        base_offset = 0 if bar == 0 else tpb * 4 - (tpb + 240)
        # nota no downbeat
        note_track.append(mido.Message("note_on", note=60, velocity=90, time=base_offset))
        note_track.append(mido.Message("note_off", note=60, velocity=64, time=240))
        # segunda nota mais tarde no compasso
        note_track.append(mido.Message("note_on", note=62, velocity=90, time=tpb))
        note_track.append(mido.Message("note_off", note=62, velocity=64, time=240))
    mid.save(path)


def test_extracts_marker_text_and_cue_annotations_with_position(tmp_path):
    """AC: fixture com anotacoes plantadas — especifica, intermediaria, generica
    — e lida com texto, compasso e track corretos."""
    p = str(tmp_path / "planted.mid")
    _write_midi_with_events(
        p,
        marker_events=[
            (0, "INTRO"),          # rotulo de secao -> nao vira anotacao
            (960, "essa parte precisa de tensao"),  # generica
            (1920, "aqui entra a virada eletronica"),  # intermediaria
        ],
        text_events=[
            (2880, "pad Omnisphere entrando aqui, filtro abrindo devagar"),
        ],
        cue_marker_events=[
            (3840, "cue: quebra"),
        ],
    )
    a = analyze_mod.analyze(p)

    texts = {ann.text for ann in a.annotations}
    assert "essa parte precisa de tensao" in texts
    assert "aqui entra a virada eletronica" in texts
    assert "pad Omnisphere entrando aqui, filtro abrindo devagar" in texts
    assert "cue: quebra" in texts
    # rotulo de secao NAO virou anotacao — separacao intocavel do sections
    assert "INTRO" not in texts

    # tick + evento correto
    by_text = {ann.text: ann for ann in a.annotations}
    assert by_text["essa parte precisa de tensao"].tick == 960
    assert by_text["essa parte precisa de tensao"].event_type == "marker"
    assert by_text["pad Omnisphere entrando aqui, filtro abrindo devagar"].event_type == "text"
    assert by_text["cue: quebra"].event_type == "cue_marker"


def test_noise_pattern_filter_reports_reason_per_discarded_annotation(tmp_path):
    """AC: descartes carregam a razao textual — filtro silencioso esconderia
    anotacao real classificada errado."""
    p = str(tmp_path / "noisy.mid")
    _write_midi_with_events(
        p,
        marker_events=[
            (0, "INTRO"),
            (240, "END_OF_VOICE"),
            (480, "MEASURE_1"),
            (720, "MEASURE_2"),
            (960, "faz o pad crescer aqui"),
        ],
    )
    a = analyze_mod.analyze(p)
    reasons = {d.text: d.reason for d in a.discarded_annotations}
    assert reasons["END_OF_VOICE"] == r"pattern:^END_OF_VOICE$"
    assert reasons["MEASURE_1"] == r"pattern:^MEASURE_\d+$"
    assert reasons["MEASURE_2"] == r"pattern:^MEASURE_\d+$"
    # A anotacao real passou
    assert any(ann.text == "faz o pad crescer aqui" for ann in a.annotations)


def test_repetition_threshold_marks_repeated_text_as_noise(tmp_path):
    """Marker cujo texto se repete acima do limiar vira ruido — DAW gera assim."""
    p = str(tmp_path / "repeat.mid")
    ticks = [i * 240 for i in range(10)]
    _write_midi_with_events(
        p,
        marker_events=[(t, "HIT") for t in ticks],
    )
    a = analyze_mod.analyze(p)
    # 10 posicoes distintas > threshold default 5 -> tudo ruido
    assert a.annotations == []
    assert all(d.text == "HIT" for d in a.discarded_annotations)
    assert all("repetition" in d.reason for d in a.discarded_annotations)


# --- escopo ----------------------------------------------------------------

def test_scope_ends_at_next_annotation_within_same_section(tmp_path):
    """Escopo termina na proxima anotacao quando ela esta dentro da mesma secao."""
    p = str(tmp_path / "scope1.mid")
    _write_midi_with_events(
        p,
        marker_events=[
            (0, "INTRO"),
            (240, "primeira coisa"),
            (720, "segunda coisa"),
            (1920, "VERSE"),  # nova secao
        ],
    )
    a = analyze_mod.analyze(p)
    first = next(ann for ann in a.annotations if ann.text == "primeira coisa")
    assert first.end_tick == 720
    assert first.scope_end_source == "next_annotation"
    assert first.section_label == "INTRO"


def test_scope_ends_at_section_end_when_no_more_annotations_in_section(tmp_path):
    """Escopo termina no fim da secao quando nao ha proxima anotacao dentro dela."""
    p = str(tmp_path / "scope2.mid")
    _write_midi_with_events(
        p,
        marker_events=[
            (0, "INTRO"),
            (240, "marca no meio"),
            (1920, "VERSE"),
            (2160, "dentro da estrofe"),
        ],
    )
    a = analyze_mod.analyze(p)
    intro_ann = next(ann for ann in a.annotations if ann.text == "marca no meio")
    # end_tick igual ao inicio de VERSE (fim de INTRO = inicio de VERSE)
    assert intro_ann.end_tick == 1920
    assert intro_ann.scope_end_source == "section_end"


def test_annotation_at_section_boundary_belongs_to_starting_section(tmp_path):
    """AC: anotacao exatamente na fronteira pertence a secao que COMECA ali."""
    p = str(tmp_path / "boundary.mid")
    _write_midi_with_events(
        p,
        marker_events=[
            (0, "INTRO"),
            (1920, "VERSE"),
            # anotacao no mesmo tick que o inicio de VERSE
            # deve pertencer a VERSE, nao a INTRO
        ],
        text_events=[
            (1920, "abre com peso"),
        ],
    )
    a = analyze_mod.analyze(p)
    ann = next(ann for ann in a.annotations if ann.text == "abre com peso")
    assert ann.section_label == "VERSE"


def test_tie_between_next_annotation_and_section_end_goes_to_section_end(tmp_path):
    """Regra documentada: quando a proxima anotacao cai no mesmo tick que o
    fim da secao, o escopo termina no fim da secao (essa proxima anotacao
    ja esta na secao seguinte por causa da regra da fronteira)."""
    p = str(tmp_path / "tie.mid")
    _write_midi_with_events(
        p,
        marker_events=[
            (0, "INTRO"),
            (240, "unica anotacao"),
            (1920, "VERSE"),
        ],
        text_events=[
            (1920, "primeira na estrofe"),  # mesmo tick que fim de INTRO
        ],
    )
    a = analyze_mod.analyze(p)
    intro_ann = next(ann for ann in a.annotations if ann.text == "unica anotacao")
    assert intro_ann.end_tick == 1920
    assert intro_ann.scope_end_source == "section_end"


# --- envelope da tool `analyze` (fachada) ----------------------------------

def test_analyze_tool_envelope_includes_annotations_and_summary():
    """A tool `analyze` expoe `annotations`, `discarded_annotations` e o
    resumo agregado — o schema requer os tres campos."""
    env = call("analyze", {"midi_path": _require(DRUMS_ENTRE_NOS)})
    assert env["ok"] is True
    d = env["data"]
    assert "annotations" in d
    assert "discarded_annotations" in d
    assert "discarded_annotations_summary" in d
    assert len(d["discarded_annotations"]) == 990
    assert d["discarded_annotations_summary"][r"pattern:^MEASURE_\d+$"] == 984
    assert d["discarded_annotations_summary"][r"pattern:^END_OF_VOICE$"] == 6


# --- plan schema: source_annotation + PlanAnnotation (issue #32) ----------

from tools.plan import (
    ArrangementPlan,
    Element,
    PlanAnnotation,
    PlanSection,
    PlanValidationError,
    SourceAnnotation,
    SourceMidi,
    from_dict,
    to_dict,
    validate,
)


def _minimal_plan_with_element(element: Element) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=0,
        source_midi=SourceMidi(path="x.mid", sha256="a" * 64),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="INTRO",
                kind="intro",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={"densidade": 2, "impacto": 2, "largura": 2,
                        "altura": 2, "instabilidade": 2},
            ),
        ],
        elements=[element],
    )


def test_element_with_source_annotation_requires_text_in_rationale():
    """AC: rationale precisa citar o texto da anotacao — sem isso a autoria
    de anotacao vira decorativa."""
    el = Element(
        id="pad_hook",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="pad crescendo",  # NAO cita
        source_annotation=SourceAnnotation(
            text="filtro abrindo aqui", tick=100, bar=1,
            track="Piano", event_type="text",
        ),
    )
    plan = _minimal_plan_with_element(el)
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].rationale"
    assert "filtro abrindo aqui" in exc.value.message


def test_element_with_source_annotation_accepts_rationale_that_cites_text():
    el = Element(
        id="pad_hook",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="anotacao 'filtro abrindo aqui' pede pad crescente",
        source_annotation=SourceAnnotation(
            text="filtro abrindo aqui", tick=100, bar=1,
            track="Piano", event_type="text",
        ),
    )
    plan = _minimal_plan_with_element(el)
    validate(plan)


def test_source_annotation_survives_dict_round_trip():
    el = Element(
        id="pad_hook",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="'filtro abrindo aqui' — pad crescente",
        source_annotation=SourceAnnotation(
            text="filtro abrindo aqui", tick=100, bar=1,
            track="Piano", event_type="text",
        ),
    )
    plan = _minimal_plan_with_element(el)
    assert from_dict(to_dict(plan)) == plan


def test_plan_annotation_actioned_must_reference_existing_element_id():
    el = Element(
        id="pad_hook",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="'filtro abrindo' — pad crescente",
        source_annotation=SourceAnnotation(
            text="filtro abrindo", tick=100, bar=1,
            track="Piano", event_type="text",
        ),
    )
    plan = _minimal_plan_with_element(el)
    plan.annotations = [
        PlanAnnotation(
            text="filtro abrindo", tick=100, bar=1, track="Piano",
            event_type="text", status="actioned", element_id="nao_existe",
        ),
    ]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "annotations[0].element_id"
    assert "nao_existe" in exc.value.message


def test_plan_annotation_declined_requires_reason():
    plan = _minimal_plan_with_element(Element(
        id="anything",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="pad default",
    ))
    plan.annotations = [
        PlanAnnotation(
            text="algum pedido", tick=100, bar=1, track="Piano",
            event_type="text", status="declined", reason=None,
        ),
    ]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "annotations[0].reason"


def test_plan_annotation_conflict_requires_reason_naming_both_sides():
    """AC: anotacao que conflita com restricao do brief nao e executada e
    gera aviso nomeando os dois lados (validacao exige `reason` nao vazio)."""
    plan = _minimal_plan_with_element(Element(
        id="anything",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="pad default",
    ))
    plan.annotations = [
        PlanAnnotation(
            text="entra guitarra pesada", tick=100, bar=1, track="Piano",
            event_type="text", status="conflict",
            reason=(
                "anotacao pediu 'guitarra pesada' mas o brief veta a familia "
                "guitar; nao foi executada."
            ),
        ),
    ]
    validate(plan)  # aceita quando reason existe

    plan.annotations[0].reason = ""
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "annotations[0].reason"


def test_plan_annotation_actioned_must_not_carry_reason_only_element_id():
    plan = _minimal_plan_with_element(Element(
        id="pad_hook",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="'filtro abrindo' — pad crescente",
        source_annotation=SourceAnnotation(
            text="filtro abrindo", tick=100, bar=1,
            track="Piano", event_type="text",
        ),
    ))
    plan.annotations = [
        PlanAnnotation(
            text="filtro abrindo", tick=100, bar=1, track="Piano",
            event_type="text", status="actioned", element_id="pad_hook",
        ),
    ]
    validate(plan)  # OK


def test_plan_annotations_survive_dict_round_trip():
    plan = _minimal_plan_with_element(Element(
        id="pad_hook",
        role="pad",
        sections=["INTRO"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        rationale="'filtro abrindo' — pad crescente",
        source_annotation=SourceAnnotation(
            text="filtro abrindo", tick=100, bar=1,
            track="Piano", event_type="text",
        ),
    ))
    plan.annotations = [
        PlanAnnotation(
            text="filtro abrindo", tick=100, bar=1, track="Piano",
            event_type="text", status="actioned", element_id="pad_hook",
        ),
        PlanAnnotation(
            text="entra riff pesado", tick=200, bar=2, track="Piano",
            event_type="marker", status="conflict",
            reason="anotacao pediu guitarra pesada; brief veta guitar",
        ),
        PlanAnnotation(
            text="apenas texto", tick=300, bar=3, track="Piano",
            event_type="text", status="declined",
            reason="fora do escopo do brief para essa secao",
        ),
    ]
    assert from_dict(to_dict(plan)) == plan
