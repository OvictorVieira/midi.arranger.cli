"""Validador de conformidade com o brief (issue #5).

Cobre os seis tipos de requisito do vocabulario fechado
(`tools/validators/compliance.py`) — cada teste prova o veredito contra
evidencia numerica real, nunca fabricada. Os testes de `tecnica` e
`restricao` passam pelo pipeline INTEIRO (`tools.render.render`); o de
`estilo` mede a funcao de medicao diretamente sobre um MIDI construido a
mao com um vies de timing conhecido, porque nenhuma tecnica do motor hoje
consome `timing_bias_ms` de ponta a ponta (AGENTS.md: "fechar essa
propagacao e trabalho futuro") — testar so a medicao, honestamente, em vez
de fabricar um pipeline que nao existe.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mido
import pretty_midi
import pytest

from tools import contract as contract_mod  # noqa: F401 -- bootstrap do registry
from tools import render as render_mod
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
from tools.registry import call as registry_call
from tools.render import render
from tools.validators.compliance import (
    REDUCAO_MIN_PCT,
    STATUS_ATENDIDO,
    STATUS_NAO_ATENDIDO,
    STATUS_NAO_VERIFICAVEL,
    STATUS_PARCIAL,
    validate_compliance,
)

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "corpus_drums"


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attach_authorized_brief(
    plan: ArrangementPlan, tmp_path: Path, requisitos: list[dict],
) -> Path:
    """Grava um brief minimo (so os campos que este validador le) e amarra
    `plan.brief_ref` a ele — mesmo padrao de `tests/test_style_on_edits.py`,
    acrescido de `requisitos`."""
    authorized: dict[str, dict[str, list[str]]] = {}
    if isinstance(plan.style, dict):
        for family, entry in plan.style.items():
            names = [t.name for t in entry.techniques if isinstance(t, StyleTechnique)]
            if names:
                authorized[family] = {"authorized_techniques": names}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({"style": authorized, "requisitos": requisitos}, indent=2),
        encoding="utf-8",
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    return brief_path


def _build_flat_source(tmp_path: Path, name: str = "flat.mid") -> Path:
    """8 compassos 4/4 a 120bpm: piano de apoio + baixo chapado (uma unica
    velocity, sem ghost) + bateria chapada em 127. Mesma fixture de
    `tests/test_style_on_edits.py` — o "antes" sem intencao nenhuma."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    guitar = pretty_midi.Instrument(program=27, name="Guitar")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        # Uma frase de guitarra por compasso (com gap ate a proxima) — da
        # ao gerador de `shadow` (dobra o fim de frase de guitarra) algo
        # real para dobrar; sem isso o role fica implementado mas mudo.
        guitar.notes.append(pretty_midi.Note(
            velocity=95, pitch=52, start=start, end=start + bar_len * 0.8,
        ))
        for beat in range(4):
            # Duracao curta (metade do tempo) deixa um vao entre notas
            # estruturais — sem vao nenhum candidato de ghost note cabe
            # (o candidato cairia DENTRO da propria nota estrutural).
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + beat * beat_len + beat_len * 0.5,
            ))
            pitch = 36 if beat in (0, 2) else 38
            drums.notes.append(pretty_midi.Note(
                velocity=127, pitch=pitch,
                start=start + beat * beat_len,
                end=start + beat * beat_len + 0.1,
            ))
    pm.instruments.extend([piano, bass, drums, guitar])
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _base_plan(src: Path, *, edits: list[PlanEdit], style: dict, elements=None) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path=str(src), sha256=_sha256_bytes(src)),
        route="cinematica_emocional",
        sections=[PlanSection(
            label="MAIN", kind="chorus", start_bar=0, end_bar=8,
            source="marker", protagonist="drum_groove",
            energy={
                "densidade": 5, "impacto": 5, "largura": 5,
                "altura": 5, "instabilidade": 3,
            },
        )],
        elements=elements if elements is not None else [Element(
            id="pad_main",
            role="pad",
            sections=["MAIN"],
            register=[48, 71],
            layers=1,
            sync_role="sustain_through",
            articulation="sustained",
            harmony="follow_chords",
            dynamics={"shape": "hold"},
            instrument={
                "plugin": "Omnisphere", "preset": "Desert Wind", "verified": True,
            },
            rationale="Sustain que amarra o arranjo.",
        )],
        edits=edits,
        style=style,
    )


def _requisito(
    id_: str, familia: str, tipo: str, alvo: str, descricao: str,
) -> dict:
    return {"id": id_, "familia": familia, "tipo": tipo, "alvo": alvo, "descricao": descricao}


# --- tipo: tecnica -----------------------------------------------------------


def _plan_bass_ghost(
    src: Path, *, density: float | None,
) -> ArrangementPlan:
    technique = StyleTechnique(
        name="bass.ghost_notes",
        **({"density": density} if density is not None else {}),
    )
    return _base_plan(
        src,
        edits=[PlanEdit(track="Bass", profile="bass", intensity=0.0)],
        style={
            "bass": FamilyStyle(
                reference="Bass research", researched_at="2026-08-24",
                sources=["https://example.test/bass"], confidence="high",
                techniques=[technique], parameters={},
            ),
        },
    )


def test_tecnica_ghost_notes_no_baixo_sem_ocorrencia_e_nao_atendido(tmp_path):
    """AC-01/issue #5: brief pedindo ghost notes no baixo + render que nao
    pos nenhuma (density=0 desliga a tecnica) -> nao_atendido com evidencia."""
    src = _build_flat_source(tmp_path)
    plan = _plan_bass_ghost(src, density=0.0)
    requisitos = [_requisito(
        "R1", "bass", "tecnica", "ghost notes", "por ghost notes no baixo",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    plan_dict = json.loads(
        json.dumps(_plan_to_payload(plan)),
    )
    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": plan_dict,
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "E_COMPLIANCE_NOT_MET"
    report = envelope["error"]["context"]["report"]
    assert report["conforme"] is False
    r1 = report["requisitos"][0]
    assert r1["status"] == STATUS_NAO_ATENDIDO
    assert r1["evidencia"]["ocorrencias_inseridas"] == 0
    assert r1["evidencia"]["tecnica"] == "bass.ghost_notes"


def test_tecnica_ghost_notes_no_baixo_atendido_com_contagem_e_faixa(tmp_path):
    """Mesmo brief + render correto (density default) -> atendido, com
    contagem de ocorrencias e faixa de velocity do MANUAL (nao hardcoded)."""
    from tools.techniques import build_index

    src = _build_flat_source(tmp_path)
    plan = _plan_bass_ghost(src, density=None)
    requisitos = [_requisito(
        "R1", "bass", "tecnica", "ghost notes", "por ghost notes no baixo",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    idx = build_index()
    manual_range = next(
        p.range for p in idx.get("bass.ghost_notes").parameters if p.name == "velocity"
    )

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is True, envelope
    report = envelope["data"]
    assert report["conforme"] is True
    r1 = report["requisitos"][0]
    assert r1["status"] == STATUS_ATENDIDO
    assert r1["evidencia"]["ocorrencias_inseridas"] > 0
    assert r1["evidencia"]["faixa_velocity"] == [manual_range[0], manual_range[1]]
    assert r1["evidencia"]["notas_fora_da_faixa"] == 0


# --- tipo: restricao ---------------------------------------------------------


def test_restricao_sem_guitarra_com_guitarra_criada_e_nao_atendido(tmp_path):
    """AC-04/issue #5: restricao 'sem guitarra' + render que criou guitarra
    -> nao_atendido, provado por presenca real de nota na track gerada."""
    src = _build_flat_source(tmp_path)
    elements = [
        Element(
            id="pad_main", role="pad", sections=["MAIN"], register=[48, 71],
            layers=1, sync_role="sustain_through", articulation="sustained",
            harmony="follow_chords", dynamics={"shape": "hold"},
            instrument={"plugin": "Omnisphere", "preset": "Desert Wind", "verified": True},
            rationale="Sustain que amarra o arranjo.",
        ),
        Element(
            # role "guitar" nao tem renderer implementado (arquitetura.md:
            # "guitar: tudo documentado, nada executado"); "shadow" e o role
            # implementado que MAPEIA pra familia guitar (ROLE_STYLE_FAMILIES)
            # e de fato produz nota — o caminho real pelo qual guitarra
            # indevida entraria num render de verdade.
            id="riff_intro", role="shadow", sections=["MAIN"], register=[48, 84],
            layers=1, sync_role="response", articulation="sustained",
            harmony="free", pattern={"octave_shift": 12, "tail_notes": 2},
            dynamics={"shape": "hold"},
            instrument={"plugin": "Amp Designer", "preset": "Crunch", "verified": True},
            rationale="Guitarra pedida apesar da restricao — cobre o teste de restricao.",
        ),
    ]
    plan = _base_plan(src, edits=[], style={}, elements=elements)
    requisitos = [_requisito(
        "R5", "guitar", "restricao", "sem guitarra",
        "Nao quero guitarra gerada nesta musica.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is False
    report = envelope["error"]["context"]["report"]
    r1 = report["requisitos"][0]
    assert r1["status"] == STATUS_NAO_ATENDIDO
    assert r1["evidencia"]["elementos_gerados"] == ["riff_intro"]
    assert r1["evidencia"]["notas_criadas"] > 0


def test_restricao_sem_guitarra_respeitada_e_atendido(tmp_path):
    src = _build_flat_source(tmp_path)
    plan = _base_plan(src, edits=[], style={})
    requisitos = [_requisito(
        "R5", "guitar", "restricao", "sem guitarra",
        "Nao quero guitarra gerada nesta musica.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is True, envelope
    r1 = envelope["data"]["requisitos"][0]
    assert r1["status"] == STATUS_ATENDIDO
    assert r1["evidencia"]["elementos_gerados"] == []


# --- tipo: estilo -------------------------------------------------------


def _midi_with_signed_bias(bias_ms: float, *, ppq: int = 480, bpm: float = 120.0) -> mido.MidiFile:
    """Bateria (16 semicolcheias) com onset deslocado de `bias_ms` em
    relacao a grade — o "renderizado" hipotetico de um vies de timing
    conhecido, usado para provar a MEDICAO (nao existe hoje tecnica no
    motor que consuma `timing_bias_ms` de ponta a ponta)."""
    mid = mido.MidiFile(ticks_per_beat=ppq)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    tempo_us = round(60_000_000 / bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    sixteenth = ppq // 4
    ms_per_tick = tempo_us / (ppq * 1000.0)
    bias_ticks = round(bias_ms / ms_per_tick)
    base_offset = 4 * sixteenth  # folga pra `onset` nunca ficar negativo
    events: list[tuple[int, str, int]] = []
    for i in range(16):
        onset = base_offset + i * sixteenth + bias_ticks
        events.append((onset, "note_on", 38))
        events.append((onset + 30, "note_off", 38))
    events.sort(key=lambda e: e[0])
    prev = 0
    for onset, kind, pitch in events:
        delta = onset - prev
        prev = onset
        if kind == "note_on":
            track.append(mido.Message("note_on", channel=9, note=pitch, velocity=100, time=delta))
        else:
            track.append(mido.Message("note_off", channel=9, note=pitch, velocity=0, time=delta))
    return mid


def _plan_for_timing_estilo(src: Path, *, target_bias_ms: float) -> ArrangementPlan:
    return _base_plan(
        src,
        edits=[PlanEdit(track="Drums", profile="drums", intensity=0.0)],
        style={
            "drums": FamilyStyle(
                reference="Drummer research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[], parameters={"timing_bias_ms": target_bias_ms},
            ),
        },
    )


def test_estilo_timing_bias_medido_bate_com_o_declarado_dentro_da_tolerancia(tmp_path):
    """AC-05/issue #5: `timing_bias_ms: -8` -> mede o offset real e
    confirma dentro da tolerancia."""
    src = _build_flat_source(tmp_path)
    plan = _plan_for_timing_estilo(src, target_bias_ms=-8.0)
    requisitos = [_requisito(
        "R4", "drums", "estilo", "referencia timing",
        "Bateria deve laid-back -8ms.",
    )]
    _attach_authorized_brief(plan, tmp_path, requisitos)

    rendered_mid = _midi_with_signed_bias(-8.0)
    source_mid = mido.MidiFile(str(src))
    source_pm = pretty_midi.PrettyMIDI(str(src))
    source_tracks = render_mod._rendered_tracks_from_instrument_list(source_pm.instruments)
    rendered_tracks = render_mod._rendered_tracks_from_instrument_list([
        _instrument_from_mido(rendered_mid, "Drums"),
    ])

    report = validate_compliance(
        requisitos=requisitos,
        plan=plan,
        source_tracks=source_tracks,
        rendered_tracks=rendered_tracks,
        harmony_issues=[],
        placement_issues=[],
        source_mid=source_mid,
        rendered_mid=rendered_mid,
    )
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["alvo_ms"] == -8.0
    assert abs(r1.evidencia["medido_ms"] - (-8.0)) <= 5.0


def _instrument_from_mido(mid: mido.MidiFile, name: str) -> pretty_midi.Instrument:
    from io import BytesIO
    temp = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat, type=mid.type)
    temp.tracks.extend(mid.tracks)
    buf = BytesIO()
    temp.save(file=buf)
    buf.seek(0)
    pm = pretty_midi.PrettyMIDI(buf)
    for inst in pm.instruments:
        if inst.name == name:
            return inst
    raise AssertionError(f"track {name!r} nao encontrada")


# --- tipo: nao_verificavel ----------------------------------------------


def test_requisito_subjetivo_e_nao_verificavel_sem_bloquear(tmp_path):
    """AC-issue #5: requisito subjetivo -> nao_verificavel, sem bloquear
    (nao entra em BLOCKING_STATUSES, `conforme` continua True quando os
    demais requisitos passam)."""
    src = _build_flat_source(tmp_path)
    plan = _plan_bass_ghost(src, density=None)
    requisitos = [
        _requisito(
            "R1", "bass", "tecnica", "ghost notes", "por ghost notes no baixo",
        ),
        _requisito(
            "R6", "geral", "reducao", "clima",
            "Quero que a musica soe mais emocionante e menos mecanica.",
        ),
    ]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is True, envelope
    report = envelope["data"]
    assert report["conforme"] is True
    statuses = {r["id"]: r["status"] for r in report["requisitos"]}
    assert statuses["R1"] == STATUS_ATENDIDO
    assert statuses["R6"] == STATUS_NAO_VERIFICAVEL
    # nao_verificavel surge como warning, nunca erro, na fachada.
    assert any(w["code"] == "W_REQUISITO_NAO_VERIFICAVEL" for w in envelope["warnings"])


# --- tipo: intensidade, corpus real ------------------------------------


def test_entre_nos_intensidade_atendido_com_hierarquia_e_ghosts(tmp_path):
    """Teste do proprio issue #5 sobre `tests/fixtures/corpus_drums/ENTRE
    NOS.mid`: entrada tem 1 velocity distinta e 0 ghost; com
    accent_hierarchy + ghost_notes autorizadas, a saida sai atendido com
    evidencia de hierarquia e de ghosts."""
    src = CORPUS_DIR / "ENTRE NÓS.mid"
    assert src.exists()

    plan = _base_plan(
        src,
        edits=[PlanEdit(track=_only_track_name(src), profile="drums", intensity=1.0)],
        style={
            "drums": FamilyStyle(
                reference="research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[
                    StyleTechnique(name="drums.accent_hierarchy"),
                    StyleTechnique(name="drums.ghost_notes"),
                ],
                parameters={},
            ),
        },
    )
    requisitos = [_requisito(
        "R6", "drums", "intensidade", "mais intencao",
        "Quero mais intencao na bateria: hierarquia de acento e ghost notes.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    src_mid = mido.MidiFile(str(src))
    src_velocities = {
        msg.velocity
        for tr in src_mid.tracks for msg in tr
        if msg.type == "note_on" and msg.velocity > 0 and msg.channel == 9
    }
    assert len(src_velocities) == 1, "fixture precisa ser 100% em uma unica velocity"

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is True, envelope
    r1 = envelope["data"]["requisitos"][0]
    assert r1["status"] == STATUS_ATENDIDO, r1
    assert r1["evidencia"]["velocities_distintas_antes"] == 1
    assert r1["evidencia"]["velocities_distintas_depois"] > 1
    assert r1["evidencia"]["ghost_notes_antes"] == 0
    assert r1["evidencia"]["ghost_notes_depois"] > 0


def _only_track_name(path: Path) -> str:
    """Nome da track que carrega nota no canal 9 (bateria GM) — o titulo do
    arquivo tambem vira uma track nomeada (meta sem nota nenhuma), entao
    'a primeira track nomeada' nao basta."""
    mid = mido.MidiFile(str(path))
    for tr in mid.tracks:
        has_drum_note = any(
            msg.type == "note_on" and msg.velocity > 0 and msg.channel == 9
            for msg in tr
        )
        if not has_drum_note:
            continue
        for msg in tr:
            if msg.is_meta and msg.type == "track_name":
                return msg.name
    raise AssertionError(f"nenhuma track com nota de bateria (canal 9) em {path}")


# --- serializacao do plano para payload de tool --------------------------


def _plan_to_payload(plan: ArrangementPlan) -> dict:
    """Serializa um `ArrangementPlan` construido em memoria para o mesmo
    dict que `plan.from_dict` reconstroi — reusa `tools.plan.to_dict` se
    existir; senao monta o subconjunto que as tools deste arquivo precisam."""
    from tools import plan as plan_mod

    if hasattr(plan_mod, "to_dict"):
        return plan_mod.to_dict(plan)
    raise AssertionError("tools.plan.to_dict nao encontrado — ajuste o helper do teste")


# ============================================================================
# tipo: reducao — os eixos alem da virada de bateria (issue #5, divida)
# ============================================================================


def _load_pair(path: Path) -> tuple[mido.MidiFile, list]:
    """`(mido.MidiFile, [RenderedTrack])` do MESMO arquivo — o par que
    `validate_compliance` consome para as medicoes em ticks e em segundos."""
    mid = mido.MidiFile(str(path))
    pm = pretty_midi.PrettyMIDI(str(path))
    return mid, render_mod._rendered_tracks_from_instrument_list(pm.instruments)


def _write_bass_midi(
    dest: Path,
    *,
    notes_per_bar: int,
    bars: int = 8,
    velocity: int = 90,
    pitches: tuple[int, ...] = (36,),
    name: str = "Bass",
) -> Path:
    """Track de baixo previsivel: `notes_per_bar` ataques por compasso de
    4/4 a 120bpm, `pitches` empilhados em cada ataque (para medir camadas)."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    inst = pretty_midi.Instrument(program=32, name=name)
    bar_len = 2.0
    step = bar_len / notes_per_bar
    for bar in range(bars):
        for i in range(notes_per_bar):
            start = bar * bar_len + i * step
            for pitch in pitches:
                inst.notes.append(pretty_midi.Note(
                    velocity=velocity, pitch=pitch,
                    start=start, end=start + step * 0.5,
                ))
    pm.instruments.append(inst)
    pm.write(str(dest))
    return dest


def _plan_bass_edit(src: Path, *, style: dict | None = None) -> ArrangementPlan:
    return _base_plan(
        src,
        edits=[PlanEdit(track="Bass", profile="bass", intensity=0.0)],
        style=style if style is not None else {},
    )


def _run_compliance(
    plan: ArrangementPlan, requisitos: list[dict], src: Path, rendered: Path,
):
    """Roda o validador direto (sem a fachada) sobre um par origem/render ja
    escrito em disco."""
    source_mid, source_tracks = _load_pair(src)
    rendered_mid, rendered_tracks = _load_pair(rendered)
    return validate_compliance(
        requisitos=requisitos,
        plan=plan,
        source_tracks=source_tracks,
        rendered_tracks=rendered_tracks,
        harmony_issues=[],
        placement_issues=[],
        source_mid=source_mid,
        rendered_mid=rendered_mid,
    )


def test_reducao_densidade_e_o_eixo_default_com_notas_por_compasso(tmp_path):
    """Requisito de `reducao` sem palavra-chave de eixo cai na densidade de
    notas POR COMPASSO — 8 ataques/compasso na origem contra 4 no render
    viram 8.0 -> 4.0 e 50% de reducao."""
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=8)
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=4)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "baixo", "Quero o baixo mais aberto.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "densidade"
    assert r1.evidencia["notas_antes"] == 64
    assert r1.evidencia["notas_depois"] == 32
    # O numero de compassos sai do ultimo evento real de cada arquivo
    # (`learn._bars_before_tick`), nao de um total redondo assumido — dai a
    # tolerancia em vez de 8.0/4.0 cravados.
    assert r1.evidencia["densidade_por_compasso_antes"] == pytest.approx(8.0, abs=0.2)
    assert r1.evidencia["densidade_por_compasso_depois"] == pytest.approx(4.0, abs=0.2)
    assert r1.evidencia["reducao_pct"] > 45.0


def test_reducao_densidade_sem_queda_e_nao_atendido_ponta_a_ponta(tmp_path):
    """Ponta a ponta por `render()`: track de origem editada sai com a MESMA
    contagem de notas (o motor humaniza, nao apaga) — logo a reducao pedida
    nao aconteceu e o veredito e `nao_atendido`, com os dois numeros."""
    src = _build_flat_source(tmp_path)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "baixo", "Quero o baixo mais aberto.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is False
    r1 = envelope["error"]["context"]["report"]["requisitos"][0]
    assert r1["status"] == STATUS_NAO_ATENDIDO
    assert r1["evidencia"]["eixo"] == "densidade"
    assert r1["evidencia"]["notas_antes"] == r1["evidencia"]["notas_depois"] == 32


def _write_two_section_midi(
    dest: Path, *, notes_first: int, notes_second: int, notes_third: int = 8,
) -> Path:
    """TRES secoes, com a de 3/4 NO MEIO: 4 compassos de 4/4 a 120bpm, 4
    compassos de 3/4 a 90bpm, e 4 compassos de 4/4 de volta a 120bpm.

    A secao de 3/4 precisa ficar no meio, com nota depois dela, para que o
    teste prove a aritmetica de `_bar_boundary_tick`. Com a de 3/4 no FIM,
    trocar `ppq * 4 * num / den` por `ppq * 4` (4/4 chumbado) so alonga a
    janela do REFRAO para depois do fim do arquivo — nao ha nota nenhuma la
    para mudar a contagem, e a mutacao passa despercebida. Com a terceira
    secao existindo, a janela errada engole um compasso de notas que nao
    pertencem ao REFRAO e a contagem muda.
    """
    ppq = 480
    mid = mido.MidiFile(ticks_per_beat=ppq)
    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    first_ticks = 4 * ppq * 4  # 4 compassos de 4/4
    meta.append(mido.MetaMessage(
        "time_signature", numerator=3, denominator=4, time=first_ticks,
    ))
    meta.append(mido.MetaMessage("set_tempo", tempo=666_667, time=0))
    second_ticks = first_ticks + 4 * ppq * 3  # + 4 compassos de 3/4
    meta.append(mido.MetaMessage(
        "time_signature", numerator=4, denominator=4, time=second_ticks - first_ticks,
    ))
    meta.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))

    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    onsets: list[int] = []
    for bar in range(4):  # secao 1 — compasso de 4 seminimas
        base = bar * ppq * 4
        for i in range(notes_first):
            onsets.append(base + i * (ppq * 4 // notes_first))
    for bar in range(4):  # secao 2 — compasso de 3 seminimas
        base = first_ticks + bar * ppq * 3
        for i in range(notes_second):
            onsets.append(base + i * (ppq * 3 // notes_second))
    for bar in range(4):  # secao 3 — 4/4 de novo, DEPOIS da fronteira
        base = second_ticks + bar * ppq * 4
        for i in range(notes_third):
            onsets.append(base + i * (ppq * 4 // notes_third))
    onsets.sort()
    prev = 0
    for onset in onsets:
        track.append(mido.Message(
            "note_on", channel=1, note=36, velocity=90, time=onset - prev,
        ))
        track.append(mido.Message(
            "note_off", channel=1, note=36, velocity=0, time=60,
        ))
        prev = onset + 60
    mid.save(str(dest))
    return dest


def _two_section_plan(src: Path) -> ArrangementPlan:
    plan = _plan_bass_edit(src)
    plan.sections = [
        PlanSection(
            label="VERSO", kind="verse", start_bar=0, end_bar=4,
            source="marker", protagonist="drum_groove",
            energy={
                "densidade": 4, "impacto": 4, "largura": 4,
                "altura": 4, "instabilidade": 2,
            },
        ),
        PlanSection(
            label="REFRAO", kind="chorus", start_bar=4, end_bar=8,
            source="marker", protagonist="drum_groove",
            energy={
                "densidade": 6, "impacto": 6, "largura": 5,
                "altura": 5, "instabilidade": 3,
            },
        ),
        PlanSection(
            label="FINAL", kind="outro", start_bar=8, end_bar=12,
            source="marker", protagonist="drum_groove",
            energy={
                "densidade": 5, "impacto": 5, "largura": 4,
                "altura": 4, "instabilidade": 2,
            },
        ),
    ]
    plan.elements = []
    return plan


def test_reducao_escopada_por_secao_usa_mapa_real_de_compasso_e_tempo(tmp_path):
    """Reducao acontece SO na secao do MEIO (REFRAO, 3/4 a 90bpm). O requisito
    que nomeia REFRAO ve a queda; o que nomeia VERSO nao ve — prova que a
    janela da secao sai do mapa real de formula de compasso e de tempo.

    A fixture tem uma TERCEIRA secao (4/4 de volta) depois do REFRAO, e as
    contagens do REFRAO sao afirmadas exatamente. Sem ela, chumbar 4/4 em
    `_bar_boundary_tick` (`bar_ticks = ppq * 4`) so estenderia a janela do
    REFRAO para depois do fim do arquivo, sem nota nenhuma para mudar a
    contagem — a mutacao passava com os testes todos verdes.
    """
    src = _write_two_section_midi(tmp_path / "src.mid", notes_first=8, notes_second=6)
    rendered = _write_two_section_midi(tmp_path / "out.mid", notes_first=8, notes_second=2)
    plan = _two_section_plan(src)
    requisitos = [
        _requisito("R1", "bass", "reducao", "REFRAO", "Aliviar o baixo no REFRAO."),
        _requisito("R2", "bass", "reducao", "VERSO", "Aliviar o baixo no VERSO."),
    ]

    report = _run_compliance(plan, requisitos, src, rendered)
    refrao, verso = report.requisitos
    assert refrao.status == STATUS_ATENDIDO, refrao
    assert refrao.evidencia["secao"] == "REFRAO"
    assert refrao.evidencia["secao_compassos"] == [4, 8]
    assert refrao.evidencia["notas_antes"] == 24
    assert refrao.evidencia["notas_depois"] == 8
    assert refrao.evidencia["densidade_por_compasso_antes"] == 6.0
    assert refrao.evidencia["densidade_por_compasso_depois"] == 2.0

    assert verso.status == STATUS_NAO_ATENDIDO, verso
    assert verso.evidencia["secao"] == "VERSO"
    assert verso.evidencia["notas_antes"] == verso.evidencia["notas_depois"] == 32


def test_reducao_de_camadas_mede_polifonia_maxima_e_media(tmp_path):
    """`camadas`/`vozes` no texto -> polifonia medida nos onsets: 3 vozes
    empilhadas na origem contra 1 no render."""
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4, pitches=(36, 43, 48))
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=4, pitches=(36,))
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "camadas", "Menos camadas empilhadas no baixo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "camadas"
    assert r1.evidencia["polifonia_max_antes"] == 3
    assert r1.evidencia["polifonia_max_depois"] == 1
    assert r1.evidencia["polifonia_media_antes"] == 3.0
    assert r1.evidencia["polifonia_media_depois"] == 1.0


def test_reducao_de_camadas_nao_atendida_quando_a_polifonia_nao_cai(tmp_path):
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4, pitches=(36, 43))
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=4, pitches=(36, 43, 48))
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "vozes", "Menos vozes simultaneas no baixo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_ATENDIDO, r1
    assert r1.evidencia["polifonia_max_antes"] == 2
    assert r1.evidencia["polifonia_max_depois"] == 3


def _write_dynamic_bass(dest: Path, velocities: tuple[int, ...]) -> Path:
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    inst = pretty_midi.Instrument(program=32, name="Bass")
    step = 0.5
    for i in range(32):
        start = i * step
        inst.notes.append(pretty_midi.Note(
            velocity=velocities[i % len(velocities)], pitch=36,
            start=start, end=start + step * 0.5,
        ))
    pm.instruments.append(inst)
    pm.write(str(dest))
    return dest


def test_reducao_de_faixa_dinamica_mede_amplitude_e_desvio(tmp_path):
    src = _write_dynamic_bass(tmp_path / "src.mid", (30, 60, 90, 120))
    rendered = _write_dynamic_bass(tmp_path / "out.mid", (74, 78, 82, 86))
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "dinamica", "Fechar a faixa dinamica do baixo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "dinamica"
    assert r1.evidencia["amplitude_antes"] == 90
    assert r1.evidencia["amplitude_depois"] == 12
    assert r1.evidencia["desvio_depois"] < r1.evidencia["desvio_antes"]


def test_entre_nos_reducao_de_dinamica_nao_atendida_ponta_a_ponta(tmp_path):
    """Corpus real: `ENTRE NOS.mid` e 100% em velocity 127 (amplitude 0). O
    render com accent_hierarchy + ghost_notes ABRE a faixa dinamica em vez de
    fechar — o validador precisa dizer `nao_atendido` com os numeros, nao
    `nao_verificavel`."""
    src = CORPUS_DIR / "ENTRE NÓS.mid"
    assert src.exists()
    plan = _base_plan(
        src,
        edits=[PlanEdit(track=_only_track_name(src), profile="drums", intensity=1.0)],
        style={
            "drums": FamilyStyle(
                reference="research", researched_at="2026-08-24",
                sources=["https://example.test/drums"], confidence="high",
                techniques=[
                    StyleTechnique(name="drums.accent_hierarchy"),
                    StyleTechnique(name="drums.ghost_notes"),
                ],
                parameters={},
            ),
        },
    )
    requisitos = [_requisito(
        "R1", "drums", "reducao", "dinamica",
        "Fechar a faixa dinamica da bateria.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is False
    r1 = envelope["error"]["context"]["report"]["requisitos"][0]
    assert r1["status"] == STATUS_NAO_ATENDIDO, r1
    assert r1["evidencia"]["eixo"] == "dinamica"
    assert r1["evidencia"]["amplitude_antes"] == 0
    assert r1["evidencia"]["amplitude_depois"] > 0


def test_reducao_de_instrumentacao_conta_tracks_que_param_de_soar(tmp_path):
    """`tirar`/`instrumentacao` -> conta tracks da familia que continuam
    soando. Origem com baixo soando, render com a track vazia: atendido."""
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4)
    empty = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    empty.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    empty.instruments.append(pretty_midi.Instrument(program=32, name="Bass"))
    other = pretty_midi.Instrument(program=0, name="Piano")
    other.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=1.0))
    empty.instruments.append(other)
    rendered = tmp_path / "out.mid"
    empty.write(str(rendered))

    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "instrumentacao", "Tirar o baixo do arranjo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "instrumentacao"
    assert r1.evidencia["tracks_soando_antes"] == 1
    assert r1.evidencia["tracks_soando_depois"] == 0
    assert r1.evidencia["notas_depois"] == 0


def test_reducao_de_instrumentacao_com_track_intacta_e_nao_atendida(tmp_path):
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4)
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=4)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "instrumentacao", "Tirar o baixo do arranjo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_ATENDIDO, r1
    assert r1.evidencia["tracks_soando_antes"] == 1
    assert r1.evidencia["tracks_soando_depois"] == 1


def test_reducao_de_familia_fora_do_plano_continua_nao_verificavel(tmp_path):
    """Honestidade: familia valida, mas que nao aparece em `plan.edits` nem em
    `plan.elements` — nao ha track para comparar, entao `nao_verificavel` com
    motivo concreto (nunca um numero inventado, nunca `nao_atendido`)."""
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4)
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=2)
    plan = _base_plan(src, edits=[], style={}, elements=[])
    requisitos = [_requisito(
        "R1", "keys", "reducao", "teclado", "Menos teclado no arranjo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_VERIFICAVEL, r1
    assert "plan.edits" in r1.motivo
    assert r1.evidencia == {}


# ============================================================================
# tipo: estilo — vies de timing de ponta a ponta e velocity
# ============================================================================


def _write_sixteenth_bass(dest: Path, *, n: int = 128) -> Path:
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    inst = pretty_midi.Instrument(program=32, name="Bass")
    step = 0.125  # semicolcheia a 120bpm
    for i in range(n):
        start = i * step
        inst.notes.append(pretty_midi.Note(
            velocity=90, pitch=36, start=start, end=start + step * 0.5,
        ))
    pm.instruments.append(inst)
    pm.write(str(dest))
    return dest


def _plan_bass_style(
    src: Path, *, intensity: float, parameters: dict,
) -> ArrangementPlan:
    return _base_plan(
        src,
        edits=[PlanEdit(track="Bass", profile="bass", intensity=intensity)],
        style={
            "bass": FamilyStyle(
                reference="Bass research", researched_at="2026-08-24",
                sources=["https://example.test/bass"], confidence="high",
                techniques=[], parameters=parameters,
            ),
        },
    )


def _estilo_evidencia(tmp_path: Path, plan: ArrangementPlan, src: Path) -> dict:
    requisitos = [_requisito(
        "R1", "bass", "estilo", "timing", "Baixo levemente adiantado da grade.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / f"out-{plan.edits[0].intensity}.mid"
    render(plan, out)
    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    report = envelope["data"] if envelope["ok"] else (
        envelope["error"]["context"]["report"]
    )
    return report["requisitos"][0]


def test_estilo_timing_bias_medido_ponta_a_ponta_pelo_profile_do_edit(tmp_path):
    """Ponta a ponta REAL: `plan.edits[].profile='bass'` adianta a nota via
    `tools/edits.py::PROFILE_PARAMS['bass'].bias_ms` (-3.5ms) escalado por
    `intensity`. Com intensity 1.0 o validador mede vies negativo proximo de
    -3.5; com intensity 0.0 mede exatamente 0.0. Nenhuma tecnica do motor de
    `tools/techniques/engine.py` consome vies direcional de timing
    (`drums.microtiming` sorteia gaussiana de media ZERO) — o caminho de
    ponta a ponta que existe e este."""
    src = _write_sixteenth_bass(tmp_path / "src.mid")

    quente = _estilo_evidencia(
        tmp_path,
        _plan_bass_style(src, intensity=1.0, parameters={"timing_bias_ms": -3.5}),
        src,
    )
    assert quente["status"] == STATUS_ATENDIDO, quente
    assert quente["evidencia"]["n_amostras"] == 128
    medido = quente["evidencia"]["medido_ms"]
    assert medido < 0.0, "profile 'bass' adianta a nota; o vies medido tem que ser negativo"
    assert abs(medido - (-3.5)) <= 1.5, medido

    frio = _estilo_evidencia(
        tmp_path,
        _plan_bass_style(src, intensity=0.0, parameters={"timing_bias_ms": -3.5}),
        src,
    )
    assert frio["evidencia"]["medido_ms"] == 0.0, frio


def test_estilo_timing_bias_impossivel_para_o_motor_sai_nao_atendido(tmp_path):
    """O motor nao entrega -20ms em profile nenhum. O validador tem que
    reportar `nao_atendido` com o numero medido — nunca `atendido` por
    inercia, nunca `nao_verificavel` para nao dar a noticia ruim."""
    src = _write_sixteenth_bass(tmp_path / "src.mid")
    r1 = _estilo_evidencia(
        tmp_path,
        _plan_bass_style(src, intensity=1.0, parameters={"timing_bias_ms": -20.0}),
        src,
    )
    assert r1["status"] == STATUS_NAO_ATENDIDO, r1
    assert r1["evidencia"]["alvo_ms"] == -20.0
    assert abs(r1["evidencia"]["medido_ms"]) < 10.0


def test_estilo_timing_usa_o_tempo_vigente_no_tick_da_nota(tmp_path):
    """Regressao: a conversao tick->ms usava o PRIMEIRO `set_tempo` do
    arquivo. Num MIDI que comeca a 240bpm e cai para 60bpm antes de qualquer
    nota, o mesmo deslocamento em ticks vale 4x mais em ms — medir pelo
    primeiro tempo dava -2ms onde o certo e -8ms."""
    src = _write_sixteenth_bass(tmp_path / "src.mid", n=8)
    plan = _plan_bass_style(src, intensity=0.0, parameters={"timing_bias_ms": -8.0})

    ppq = 480
    rendered = mido.MidiFile(ticks_per_beat=ppq)
    track = mido.MidiTrack()
    rendered.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=250_000, time=0))  # 240bpm
    track.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))  # 60bpm
    sixteenth = ppq // 4
    # -8ms a 60bpm = -0.008 * 480 / 1.0 = -3.84 ticks -> -4 ticks.
    bias_ticks = -4
    prev = 0
    for i in range(16):
        onset = 4 * sixteenth + i * sixteenth + bias_ticks
        track.append(mido.Message(
            "note_on", channel=1, note=36, velocity=90, time=onset - prev,
        ))
        track.append(mido.Message("note_off", channel=1, note=36, velocity=0, time=20))
        prev = onset + 20
    dest = tmp_path / "out.mid"
    rendered.save(str(dest))

    report = _run_compliance(
        plan,
        [_requisito("R1", "bass", "estilo", "timing", "Baixo adiantado -8ms.")],
        src, dest,
    )
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert abs(r1.evidencia["medido_ms"] - (-8.333)) < 0.01, r1.evidencia


def test_estilo_velocity_em_faixa_mede_percentual_dentro_da_faixa(tmp_path):
    """Parametro de estilo declarado como faixa de velocity: o validador mede
    quantas notas da familia caem dentro dela no render."""
    src = _write_dynamic_bass(tmp_path / "src.mid", (30, 60, 90, 120))
    rendered = _write_dynamic_bass(tmp_path / "out.mid", (72, 76, 80, 84))
    plan = _plan_bass_style(src, intensity=0.0, parameters={"velocity": [70, 90]})
    requisitos = [_requisito(
        "R1", "bass", "estilo", "dinamica", "Baixo dentro da faixa de referencia.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "velocity"
    assert r1.evidencia["faixa_alvo"] == [70.0, 90.0]
    assert r1.evidencia["dentro_pct"] == 100.0

    fora = _write_dynamic_bass(tmp_path / "fora.mid", (30, 35, 40, 45))
    report_fora = _run_compliance(plan, requisitos, src, fora)
    r2 = report_fora.requisitos[0]
    assert r2.status == STATUS_NAO_ATENDIDO, r2
    assert r2.evidencia["dentro_pct"] == 0.0


def test_estilo_com_parametro_fora_dos_eixos_e_nao_verificavel_nomeando_o(tmp_path):
    """Honestidade: parametro de estilo que nao vira medicao no MIDI sai
    `nao_verificavel` NOMEANDO o parametro — nunca um numero fabricado."""
    src = _write_sixteenth_bass(tmp_path / "src.mid", n=16)
    rendered = _write_sixteenth_bass(tmp_path / "out.mid", n=16)
    plan = _plan_bass_style(src, intensity=0.0, parameters={"swing_ratio": 0.6})
    requisitos = [_requisito(
        "R1", "bass", "estilo", "referencia", "Baixo no estilo da referencia.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_VERIFICAVEL, r1
    assert "swing_ratio" in r1.motivo


# ============================================================================
# Regressao da revisao adversarial do PR #118
# ============================================================================


def _write_pad_tail_render(dest: Path, *, bass_bars: int, pad_bars: int) -> Path:
    """Baixo NOTA A NOTA identico ao de `_write_bass_midi(notes_per_bar=4)`,
    mais um pad que continua tocando depois do fim do baixo — a track nova
    que `plan.elements[]` cria e que alonga o arquivo renderizado."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    bass = pretty_midi.Instrument(program=32, name="Bass")
    for bar in range(bass_bars):
        for i in range(4):
            start = bar * 2.0 + i * 0.5
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36, start=start, end=start + 0.25,
            ))
    pad = pretty_midi.Instrument(program=0, name="Pad")
    for bar in range(pad_bars):
        pad.notes.append(pretty_midi.Note(
            velocity=70, pitch=60, start=bar * 2.0, end=bar * 2.0 + 1.9,
        ))
    pm.instruments.extend([bass, pad])
    pm.write(str(dest))
    return dest


def test_reducao_densidade_usa_o_mesmo_denominador_dos_dois_lados(tmp_path):
    """Regressao (falso `atendido`): o denominador da densidade era o numero
    de compassos do ARQUIVO INTEIRO, medido separadamente na origem e no
    render. Um pad de 4 compassos a mais no render — track nova de
    `plan.elements[]`, coisa corriqueira — alongava `compassos_depois` e
    "reduzia" 34% a densidade de um baixo que saiu NOTA A NOTA IDENTICO.

    O denominador agora e um quadro de referencia unico (o comprimento da
    origem), entao baixo identico da 0% de reducao e `nao_atendido`.
    """
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4, bars=8)
    rendered = _write_pad_tail_render(tmp_path / "out.mid", bass_bars=8, pad_bars=12)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "baixo", "Quero o baixo mais aberto.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["notas_antes"] == r1.evidencia["notas_depois"] == 32
    assert r1.evidencia["reducao_pct"] == 0.0
    assert r1.status == STATUS_NAO_ATENDIDO, r1
    assert "compassos_antes" not in r1.evidencia
    assert "compassos_depois" not in r1.evidencia
    assert r1.evidencia["compassos_referencia"] > 0


def test_reducao_densidade_nao_reprova_render_mais_curto_que_a_origem(tmp_path):
    """O espelho do mesmo defeito, que reprovava um render CORRETO: o baixo
    sai de 32 para 12 notas, mas concentradas em 2 compassos em vez de 8.
    Com denominadores independentes o render curto inflava a densidade
    medida (12/1.94 = 6.19 contra 32/7.88 = 4.06) e o veredito era
    `nao_atendido` para uma reducao real de 62%."""
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4, bars=8)
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=6, bars=2)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "baixo", "Quero o baixo mais aberto.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["notas_antes"] == 32
    assert r1.evidencia["notas_depois"] == 12
    assert r1.evidencia["reducao_pct"] > 60.0
    assert r1.status == STATUS_ATENDIDO, r1


def _write_bass_plus_drum_fill(dest: Path, *, fill_notes: int) -> Path:
    """Baixo sempre identico + bateria com groove nos 7 primeiros compassos e
    uma virada no ultimo. So a virada muda entre origem e render."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    bass = pretty_midi.Instrument(program=32, name="Bass")
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    for bar in range(8):
        for i in range(4):
            start = bar * 2.0 + i * 0.5
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36, start=start, end=start + 0.25,
            ))
        if bar < 7:
            for i in range(4):
                start = bar * 2.0 + i * 0.5
                drums.notes.append(pretty_midi.Note(
                    velocity=100, pitch=36 if i % 2 == 0 else 38,
                    start=start, end=start + 0.1,
                ))
        else:
            step = 0.125  # semicolcheia: o gap maximo que o detector aceita
            pieces = (38, 45, 47, 50)
            for i in range(fill_notes):
                start = bar * 2.0 + 2.0 - fill_notes * step + i * step
                drums.notes.append(pretty_midi.Note(
                    velocity=110, pitch=pieces[i % 4], start=start, end=start + 0.08,
                ))
    pm.instruments.extend([bass, drums])
    pm.write(str(dest))
    return dest


def test_reducao_de_virada_nao_responde_por_familia_que_nao_e_bateria(tmp_path):
    """Regressao (falso `atendido`): a palavra "virada" no texto do requisito
    despachava para a deteccao de fill ANTES do gate de familia, e
    `_reducao_virada` so olha o canal 9. Um requisito de BAIXO saia
    `atendido` porque a virada da BATERIA caiu — com o baixo nota a nota
    identico, nunca olhado, e sem `familia` na evidencia."""
    src = _write_bass_plus_drum_fill(tmp_path / "src.mid", fill_notes=16)
    rendered = _write_bass_plus_drum_fill(tmp_path / "out.mid", fill_notes=8)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "viradas",
        "Menos viradas de baixo no final das frases.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["familia"] == "bass"
    assert r1.evidencia["eixo"] != "virada"
    assert r1.evidencia["notas_antes"] == r1.evidencia["notas_depois"] == 32
    assert r1.status == STATUS_NAO_ATENDIDO, r1


def test_reducao_de_virada_continua_medindo_bateria_e_carrega_a_familia(tmp_path):
    """O contraponto do teste acima: com `familia: drums`, o eixo de virada
    continua sendo o escolhido — e agora a evidencia diz de qual familia
    esta falando."""
    src = _write_bass_plus_drum_fill(tmp_path / "src.mid", fill_notes=16)
    rendered = _write_bass_plus_drum_fill(tmp_path / "out.mid", fill_notes=8)
    plan = _base_plan(
        src,
        edits=[PlanEdit(track="Drums", profile="drums", intensity=0.0)],
        style={},
    )
    requisitos = [_requisito(
        "R1", "drums", "reducao", "viradas", "Menos viradas na bateria.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "virada"
    assert r1.evidencia["familia"] == "drums"
    assert r1.evidencia["notas_em_virada_antes"] == 16
    assert r1.evidencia["notas_em_virada_depois"] == 8


def _write_long_fill(dest: Path, *, nudge_last: bool) -> Path:
    """Virada longa (2 compassos de semicolcheia). `nudge_last` empurra a
    ultima nota 20ms para fora da janela — o que a humanizacao de timing faz
    sozinha."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    pieces = (38, 45, 47, 50)
    for i in range(32):
        start = i * 0.125 + (0.02 if (nudge_last and i == 31) else 0.0)
        drums.notes.append(pretty_midi.Note(
            velocity=110, pitch=pieces[i % 4], start=start, end=start + 0.08,
        ))
    pm.instruments.append(drums)
    pm.write(str(dest))
    return dest


def test_reducao_de_virada_abaixo_do_piso_minimo_sai_parcial(tmp_path):
    """Regressao (falso `atendido`): o eixo de virada era o unico sem o piso
    `REDUCAO_MIN_PCT`. Como as notas do render sao contadas dentro das
    janelas DA ORIGEM e a humanizacao mexe no timing, UMA nota atravessando a
    borda da janela (3.12% de "queda") ja bastava para `atendido`."""
    src = _write_long_fill(tmp_path / "src.mid", nudge_last=False)
    rendered = _write_long_fill(tmp_path / "out.mid", nudge_last=True)
    plan = _base_plan(
        src,
        edits=[PlanEdit(track="Drums", profile="drums", intensity=0.0)],
        style={},
    )
    requisitos = [_requisito(
        "R1", "drums", "reducao", "viradas", "Menos viradas na bateria.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["notas_em_virada_antes"] == 32
    assert r1.evidencia["notas_em_virada_depois"] == 31
    assert r1.evidencia["reducao_pct"] < REDUCAO_MIN_PCT
    assert r1.status == STATUS_PARCIAL, r1
    assert str(REDUCAO_MIN_PCT) in r1.motivo


def test_reducao_de_dinamica_sem_faixa_a_reduzir_e_nao_verificavel(tmp_path):
    """Regressao (falso `nao_atendido`): origem 100% numa unica velocity tem
    amplitude 0 e desvio 0 — nenhum render possivel baixa a faixa dinamica
    abaixo disso. O veredito honesto e `nao_verificavel` (que nao bloqueia),
    com os numeros junto, nunca `nao_atendido`."""
    src = _write_dynamic_bass(tmp_path / "src.mid", (100,))
    rendered = _write_dynamic_bass(tmp_path / "out.mid", (100,))
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "dinamica", "Fechar a faixa dinamica do baixo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_VERIFICAVEL, r1
    assert report.conforme is True, "nao_verificavel nunca bloqueia"
    assert r1.evidencia["amplitude_antes"] == 0
    assert r1.evidencia["amplitude_depois"] == 0


def test_reducao_de_camadas_em_origem_monofonica_e_nao_verificavel(tmp_path):
    """Regressao (falso `nao_atendido`): baixo monofonico na origem
    (polifonia maxima 1) nao tem sobreposicao de vozes a reduzir. Mesma
    honestidade de `_reducao_virada`/`_reducao_instrumentacao`."""
    src = _write_bass_midi(tmp_path / "src.mid", notes_per_bar=4, pitches=(36,))
    rendered = _write_bass_midi(tmp_path / "out.mid", notes_per_bar=4, pitches=(36,))
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "camadas", "Menos camadas no baixo.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_VERIFICAVEL, r1
    assert report.conforme is True
    assert r1.evidencia["polifonia_max_antes"] == 1
    assert "monofonica" in r1.motivo


def _write_stacked_bass(dest: Path, *, peak_voices: int) -> Path:
    """100 ataques de 3 vozes, com UM ataque de `peak_voices` — a media mal se
    mexe quando o pico cai, mas a maxima cai muito."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    inst = pretty_midi.Instrument(program=32, name="Bass")
    for i in range(100):
        start = i * 0.25
        pitches = tuple(range(36, 36 + peak_voices)) if i == 50 else (36, 43, 48)
        for pitch in pitches:
            inst.notes.append(pretty_midi.Note(
                velocity=90, pitch=pitch, start=start, end=start + 0.12,
            ))
    pm.instruments.append(inst)
    pm.write(str(dest))
    return dest


def test_reducao_de_camadas_parcial_descreve_qual_dimensao_falhou(tmp_path):
    """Regressao de mensagem enganosa num veredito BLOQUEANTE: quando a media
    cai menos de 5% mas cai, e a maxima cai de verdade, o motivo antigo dizia
    "so uma das duas dimensoes caiu" — falso, as duas cairam. O motivo agora
    diz o que aconteceu de fato."""
    src = _write_stacked_bass(tmp_path / "src.mid", peak_voices=8)
    rendered = _write_stacked_bass(tmp_path / "out.mid", peak_voices=3)
    plan = _plan_bass_edit(src)
    requisitos = [_requisito(
        "R1", "bass", "reducao", "camadas", "Menos camadas empilhadas.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_PARCIAL, r1
    assert r1.evidencia["polifonia_max_antes"] == 8
    assert r1.evidencia["polifonia_max_depois"] == 3
    assert r1.evidencia["polifonia_media_depois"] < r1.evidencia["polifonia_media_antes"]
    assert "so uma das duas dimensoes caiu" not in r1.motivo
    assert "maxima caiu" in r1.motivo and "media caiu apenas" in r1.motivo


def test_reducao_de_instrumentacao_sem_track_na_origem_devolve_a_evidencia():
    """Regressao: `_reducao_instrumentacao` montava a evidencia completa e a
    DESCARTAVA no caminho `nao_verificavel`. O caminho e defensivo (o
    despacho de `_verdict_reducao` ja barra origem sem nota antes), entao a
    prova e sobre a funcao: numero calculado nao se perde."""
    from tools.validators.compliance import _reducao_instrumentacao

    req = _requisito("R1", "bass", "reducao", "instrumentacao", "Tirar o baixo.")
    verdict = _reducao_instrumentacao(
        req, [], [], None, None, {"familia": "bass"},
    )
    assert verdict.status == STATUS_NAO_VERIFICAVEL
    assert verdict.evidencia["familia"] == "bass"
    assert verdict.evidencia["eixo"] == "instrumentacao"
    assert verdict.evidencia["tracks_soando_antes"] == 0


def test_estilo_escolhe_o_eixo_pelo_texto_do_requisito(tmp_path):
    """Regressao (falso `atendido`): o eixo do tipo `estilo` saia da ORDEM DO
    CODIGO — havendo qualquer chave de timing escalar, velocity nunca era
    medido. Com `timing_bias_ms: 0.0` declarado junto de `velocity: [70, 90]`
    e um requisito cujo alvo e velocity, o render com 25% das notas dentro da
    faixa saia `atendido` pelo eixo de timing, e o `velocity` declarado nao
    aparecia nem na evidencia.

    Agora o eixo sai do texto do requisito (como em `reducao`) e o parametro
    que nao foi medido aparece na evidencia, nunca some.
    """
    src = _write_dynamic_bass(tmp_path / "src.mid", (80,))
    rendered = _write_dynamic_bass(tmp_path / "out.mid", (72, 30, 30, 30))
    plan = _plan_bass_style(
        src, intensity=0.0,
        parameters={"timing_bias_ms": 0.0, "velocity": [70, 90]},
    )
    requisitos = [_requisito(
        "R1", "bass", "estilo", "velocity",
        "Baixo na faixa de velocity da referencia.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["eixo"] == "velocity"
    assert r1.evidencia["dentro_pct"] == 25.0
    assert r1.status == STATUS_NAO_ATENDIDO, r1
    assert r1.evidencia["parametros_nao_medidos"] == ["timing_bias_ms"]


def test_estilo_requisito_de_timing_continua_escolhendo_o_eixo_de_timing(tmp_path):
    """O contraponto: mesmo par de parametros, requisito que fala de timing —
    o eixo de timing e o escolhido e `velocity` fica listado como nao
    medido."""
    src = _write_sixteenth_bass(tmp_path / "src.mid", n=32)
    rendered = _write_sixteenth_bass(tmp_path / "out.mid", n=32)
    plan = _plan_bass_style(
        src, intensity=0.0,
        parameters={"timing_bias_ms": 0.0, "velocity": [70, 90]},
    )
    requisitos = [_requisito(
        "R1", "bass", "estilo", "timing", "Baixo colado na grade.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_ATENDIDO, r1
    assert r1.evidencia["eixo"] == "timing"
    assert r1.evidencia["parametros_nao_medidos"] == ["velocity"]


def _write_drums_with_ghosts(dest: Path, *, ghosts: bool) -> Path:
    """Bateria estrutural em velocity 110; com `ghosts`, mais uma ghost note
    de caixa em velocity 32 entre cada ataque — o render PERFEITO de
    `drums.ghost_notes`, cuja faixa de velocity no manual e [20, 45]."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    for bar in range(8):
        for beat in range(4):
            start = bar * 2.0 + beat * 0.5
            drums.notes.append(pretty_midi.Note(
                velocity=110, pitch=36 if beat % 2 == 0 else 38,
                start=start, end=start + 0.1,
            ))
            if ghosts:
                drums.notes.append(pretty_midi.Note(
                    velocity=32, pitch=38, start=start + 0.25, end=start + 0.32,
                ))
    pm.instruments.append(drums)
    pm.write(str(dest))
    return dest


def _plan_drums_style(src: Path, *, techniques: list, parameters: dict) -> ArrangementPlan:
    return _base_plan(
        src,
        edits=[PlanEdit(track="Drums", profile="drums", intensity=0.0)],
        style={"drums": FamilyStyle(
            reference="Drummer research", researched_at="2026-08-24",
            sources=["https://example.test/drums"], confidence="high",
            techniques=techniques, parameters=parameters,
        )},
    )


def test_estilo_velocity_de_escopo_de_tecnica_mede_so_o_que_foi_acrescentado(tmp_path):
    """Regressao (falso `nao_atendido`): `velocity` e nome de parametro DE
    TECNICA no manual (`drums.ghost_notes` -> [20, 45]), e o nivel `technique`
    tem contrato de nao mexer na velocity estrutural. Exigir que 95% de TODAS
    as notas da familia caiam em [20, 45] e exigir o estruturalmente
    impossivel: o render perfeito (32 ghosts em 32, estruturais intactas em
    110) media 50% e saia `nao_atendido`.

    `_verdict_tecnica` ja media a mesma faixa do mesmo manual so sobre as
    notas ACRESCENTADAS; os dois tratamentos agora estao alinhados.
    """
    src = _write_drums_with_ghosts(tmp_path / "src.mid", ghosts=False)
    rendered = _write_drums_with_ghosts(tmp_path / "out.mid", ghosts=True)
    plan = _plan_drums_style(
        src,
        techniques=[StyleTechnique(name="drums.ghost_notes")],
        parameters={"velocity": [20, 45]},
    )
    requisitos = [_requisito(
        "R1", "drums", "estilo", "velocity", "Ghost notes na faixa do manual.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["escopo"] == "tecnica:drums.ghost_notes"
    assert r1.evidencia["notas_medidas"] == 32
    assert r1.evidencia["dentro_pct"] == 100.0
    assert r1.status == STATUS_ATENDIDO, r1


def test_estilo_velocity_sem_tecnica_declarada_continua_medindo_a_familia(tmp_path):
    """O contraponto: sem tecnica declarada, `velocity` nao e parametro de
    escopo de tecnica — a faixa vale para a familia inteira e o escopo
    aparece na evidencia."""
    src = _write_drums_with_ghosts(tmp_path / "src.mid", ghosts=False)
    rendered = _write_drums_with_ghosts(tmp_path / "out.mid", ghosts=True)
    plan = _plan_drums_style(src, techniques=[], parameters={"velocity": [20, 45]})
    requisitos = [_requisito(
        "R1", "drums", "estilo", "velocity", "Bateria na faixa de velocity.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.evidencia["escopo"] == "familia"
    assert r1.evidencia["notas_medidas"] == 64
    assert r1.status == STATUS_NAO_ATENDIDO, r1


def _write_drums_zero_mean_jitter(dest: Path) -> Path:
    """Chimbal com jitter de MEDIA ZERO (sigma ~8.7ms), exatamente o que
    `drums.microtiming` manda fazer. A sequencia e fixa (nao sorteada) para o
    teste continuar deterministico."""
    offsets_ms = (-9.0, 7.0, -8.0, 9.0, -7.0, 8.0, -9.0, 7.0)
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    for i in range(64):
        start = i * 0.125 + offsets_ms[i % len(offsets_ms)] / 1000.0
        drums.notes.append(pretty_midi.Note(
            velocity=90, pitch=42, start=start, end=start + 0.05,
        ))
    pm.instruments.append(drums)
    pm.write(str(dest))
    return dest


def test_estilo_nao_trata_sigma_de_timing_como_vies_de_mediana(tmp_path):
    """Regressao (falso `parcial`, que bloqueia entrega): `hihat_timing_sigma_ms`
    (8.7, parametro real de `drums.microtiming`) casava no matcher de timing e
    virava ALVO DE MEDIANA. Mas o manual manda sortear gaussiana de MEDIA
    ZERO — a mediana nunca chega a 8.7, e um render que fez exatamente o
    certo ficava reprovado para sempre.

    Sigma e dispersao, nao vies: o eixo nao o adota, e o parametro aparece
    nomeado no motivo em vez de sumir.
    """
    src = _write_drums_zero_mean_jitter(tmp_path / "src.mid")
    rendered = _write_drums_zero_mean_jitter(tmp_path / "out.mid")
    plan = _plan_drums_style(
        src, techniques=[], parameters={"hihat_timing_sigma_ms": 8.7},
    )
    requisitos = [_requisito(
        "R1", "drums", "estilo", "timing", "Chimbal com microtiming humano.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_VERIFICAVEL, r1
    assert report.conforme is True, "nao_verificavel nunca bloqueia"
    assert "hihat_timing_sigma_ms" in r1.motivo


def test_estilo_timing_de_escopo_de_tecnica_nao_cobra_a_mediana_da_familia(tmp_path):
    """`timing_offset_ms_laidback` e direcional, mas o manual o declara dentro
    de `drums.ghost_notes`: vale para o ornamento que a tecnica insere, nao
    para a mediana de timing da familia inteira. Cobrar a familia por ele
    reprovaria um render correto — a resposta honesta e nao medir, citando a
    tecnica dona do parametro."""
    src = _write_drums_with_ghosts(tmp_path / "src.mid", ghosts=False)
    rendered = _write_drums_with_ghosts(tmp_path / "out.mid", ghosts=True)
    plan = _plan_drums_style(
        src,
        techniques=[StyleTechnique(name="drums.ghost_notes")],
        parameters={"timing_offset_ms_laidback": 5.0},
    )
    requisitos = [_requisito(
        "R1", "drums", "estilo", "timing", "Ghost notes um pouco atras da grade.",
    )]

    report = _run_compliance(plan, requisitos, src, rendered)
    r1 = report.requisitos[0]
    assert r1.status == STATUS_NAO_VERIFICAVEL, r1
    assert report.conforme is True
    assert "drums.ghost_notes" in r1.motivo
    assert r1.evidencia["parametros_declarados"] == ["timing_offset_ms_laidback"]


def _write_late_bass(dest: Path, *, late_s: float, n: int = 128) -> Path:
    """Baixo em semicolcheias com um atraso CONSTANTE contra a grade — o take
    humano nao quantizado."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    inst = pretty_midi.Instrument(program=32, name="Bass")
    for i in range(n):
        start = i * 0.125 + late_s
        inst.notes.append(pretty_midi.Note(
            velocity=90, pitch=36, start=start, end=start + 0.06,
        ))
    pm.instruments.append(inst)
    pm.write(str(dest))
    return dest


def test_estilo_timing_mede_delta_origem_render_em_take_nao_quantizado(tmp_path):
    """Regressao (falso `parcial`, que bloqueia entrega): o eixo de timing
    media a distancia ABSOLUTA do render ate a grade de semicolcheia, nao o
    delta origem->render como o resto do modulo. Numa origem nao quantizada
    (baixo 12ms atras da grade), o feel da origem dominava a mediana: o render
    aplicava os -3.5ms pedidos pelo profile e era medido em +8.85ms contra o
    alvo de -3.5, saindo `parcial`.

    Ponta a ponta por `render()` — o delta real e medido nota a nota abaixo
    para provar que a evidencia bate com o que o motor fez de fato.
    """
    src = _write_late_bass(tmp_path / "src.mid", late_s=0.012)
    plan = _plan_bass_style(src, intensity=1.0, parameters={"timing_bias_ms": -3.5})
    requisitos = [_requisito(
        "R1", "bass", "estilo", "timing", "Baixo levemente adiantado.",
    )]
    _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    report = _run_compliance(plan, requisitos, src, out)
    r1 = report.requisitos[0]
    assert r1.evidencia["mediana_origem_ms"] > 10.0, "a origem e um take atrasado"
    assert r1.evidencia["medido_ms"] < 0.0, "o render adiantou contra a origem"
    assert abs(r1.evidencia["medido_ms"] - (-3.5)) <= 1.5, r1.evidencia
    assert r1.status == STATUS_ATENDIDO, r1

    # O delta real nota a nota, medido fora do validador.
    delta_ms = _median_onset_delta_ms(src, out, "Bass")
    assert abs(r1.evidencia["medido_ms"] - delta_ms) < 0.5, (r1.evidencia, delta_ms)


def _median_onset_delta_ms(src: Path, rendered: Path, name: str) -> float:
    """Mediana do deslocamento nota a nota entre origem e render, em ms —
    medida fora do validador, para conferir a evidencia contra o motor."""
    import statistics

    def onsets(path: Path) -> list[int]:
        mid = mido.MidiFile(str(path))
        out: list[int] = []
        for track in mid.tracks:
            track_name = next(
                (m.name for m in track if m.is_meta and m.type == "track_name"), None,
            )
            if track_name != name:
                continue
            tick = 0
            for msg in track:
                tick += msg.time
                if msg.type == "note_on" and msg.velocity > 0:
                    out.append(tick)
        return sorted(out)

    ms_per_tick = 500_000 / (480 * 1000.0)  # 120bpm, ppq 480
    return statistics.median(
        (b - a) * ms_per_tick for a, b in zip(onsets(src), onsets(rendered), strict=True)
    )


def test_estilo_timing_ignora_track_gerada_e_so_mede_a_editada(tmp_path):
    """Regressao (veredito BLOQUEANTE sobre track que nao podia carregar o
    vies): sem track em `plan.edits[]`, o eixo de timing media a track GERADA
    por `plan.elements[]`. O gerador escreve na grade, entao a medicao saia
    +1.56ms contra o alvo de -3.5ms e o requisito virava `parcial` — a
    entrega travava por causa de uma track que nenhum `profile` desloca.

    Vies de timing e delta origem->render, e track gerada nao tem contraparte
    na origem: a resposta honesta e `nao_verificavel`, que nao bloqueia.
    """
    src = _write_late_bass(tmp_path / "src.mid", late_s=0.012, n=64)
    plan = _base_plan(
        src,
        edits=[],
        style={"bass": FamilyStyle(
            reference="Bass research", researched_at="2026-08-24",
            sources=["https://example.test/bass"], confidence="high",
            techniques=[], parameters={"timing_bias_ms": -3.5},
        )},
        elements=[Element(
            id="bass_new", role="bass", sections=["MAIN"], register=[36, 55],
            layers=1, sync_role="kick_support", articulation="staccato",
            harmony="follow_chords", dynamics={"shape": "hold"},
            instrument={"plugin": "Trilian", "preset": "Fingered", "verified": True},
            rationale="Baixo gerado do zero para sustentar a secao.",
        )],
    )
    requisitos = [_requisito(
        "R1", "bass", "estilo", "timing", "Baixo levemente adiantado.",
    )]
    brief_path = _attach_authorized_brief(plan, tmp_path, requisitos)
    out = tmp_path / "out.mid"
    render(plan, out)

    envelope = registry_call("compliance.validate", {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": json.loads(json.dumps(_plan_to_payload(plan))),
        "brief_path": str(brief_path),
    })
    assert envelope["ok"] is True, envelope
    r1 = envelope["data"]["requisitos"][0]
    assert r1["status"] == STATUS_NAO_VERIFICAVEL, r1
    assert "plan.edits" in r1["motivo"]
