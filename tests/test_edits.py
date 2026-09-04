"""Testes do motor de edits opt-in (US-011 / FR-28)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import mido
import pretty_midi
import pytest

from tools.edits import (
    PROFILE_PARAMS,
    EditReport,
    apply_edit,
    apply_edits,
    collect_track_names,
)
from tools.plan import (
    ArrangementPlan,
    Element,
    PlanEdit,
    PlanSection,
    SourceMidi,
)
from tools.render import render

# --- fixture ---------------------------------------------------------------

def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_grid_source(
    tmp_path: Path,
    name: str = "grid.mid",
    include_drums: bool = False,
) -> Path:
    """MIDI perfeitamente alinhado ao grid: 8 compassos 4/4 a 120bpm.
    - Bass: 4 notas por compasso, sempre no beat (nunca fora do grid).
    - Piano: triade sustentada por compasso.
    - Drums (opcional): kick no beat 1 e 3, snare no beat 2 e 4.
    """
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.append(piano)
    pm.instruments.append(bass)
    if include_drums:
        drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
        for bar in range(8):
            start = bar * bar_len
            for beat in range(4):
                pitch = 36 if beat in (0, 2) else 38  # kick vs snare
                drums.notes.append(pretty_midi.Note(
                    velocity=100, pitch=pitch,
                    start=start + beat * beat_len,
                    end=start + beat * beat_len + 0.1,
                ))
        pm.instruments.append(drums)
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _build_plan(
    source: Path,
    edits: list[PlanEdit] | None = None,
) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(path=str(source), sha256=_sha256_bytes(source)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN",
                kind="chorus",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={
                    "densidade": 5, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3,
                },
            ),
        ],
        elements=[
            Element(
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
                    "plugin": "Omnisphere",
                    "preset": "Desert Wind",
                    "verified": True,
                },
                rationale="Sustained pad glues the arrangement.",
            ),
        ],
        edits=edits or [],
    )


def _notes_of_track_by_name(
    mid_path: Path, name: str,
) -> list[tuple[int, int, int, int]]:
    """Devolve (start_tick, end_tick, pitch, velocity) para a track cujo
    nome de track_name meta seja `name`. Usa mido para ler ticks exatos."""
    mid = mido.MidiFile(str(mid_path))
    out: list[tuple[int, int, int, int]] = []
    for tr in mid.tracks:
        tname = None
        for msg in tr:
            if msg.is_meta and msg.type == "track_name":
                tname = msg.name
                break
        if tname != name:
            continue
        abs_tick = 0
        open_map: dict[tuple[int, int], tuple[int, int]] = {}
        for msg in tr:
            abs_tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                open_map[(msg.channel, msg.note)] = (abs_tick, msg.velocity)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if key in open_map:
                    start, vel = open_map.pop(key)
                    out.append((start, abs_tick, msg.note, vel))
    return out


def _track_messages(mid_path: Path, name: str) -> list[str]:
    """Repr de todas as messages de uma track, incluindo delta ticks."""
    mid = mido.MidiFile(str(mid_path))
    for tr in mid.tracks:
        for msg in tr:
            if msg.is_meta and msg.type == "track_name" and msg.name == name:
                return [str(m) for m in tr]
    return []


# --- profile params: presenca do vocabulario fechado -----------------------

def test_profile_params_has_all_five_profiles():
    assert set(PROFILE_PARAMS.keys()) == {"bass", "drums", "guitar", "keys", "generic"}


def test_bass_profile_has_negative_bias():
    """Doc modo_bass / secao 7 spec: baixo adiantado -3 a -4ms."""
    assert PROFILE_PARAMS["bass"].bias_ms < 0


def test_drums_profile_has_kick_and_snare_anchors():
    anchors = PROFILE_PARAMS["drums"].anchor_pitches
    assert 36 in anchors  # GM kick
    assert 38 in anchors  # GM snare


# --- track name lookup ------------------------------------------------------

def test_collect_track_names_reads_track_name_meta(tmp_path):
    src = _build_grid_source(tmp_path)
    mid = mido.MidiFile(str(src))
    names = collect_track_names(mid.tracks)
    assert "Bass" in names
    assert "Piano" in names


# --- intensity 0.0: no-op ---------------------------------------------------

def test_intensity_zero_is_no_op_even_when_declared(tmp_path):
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=0.0),
    ])
    out = tmp_path / "out.mid"
    render(plan, out)

    src_bass = _notes_of_track_by_name(src, "Bass")
    out_bass = _notes_of_track_by_name(out, "Bass")
    assert src_bass == out_bass


def test_intensity_zero_produces_report_with_zero_touched(tmp_path):
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=0.0),
    ])
    report = render(plan, tmp_path / "out.mid")
    assert len(report.edits) == 1
    assert report.edits[0].notes_touched == 0


# --- track fora de edits: byte-identica -------------------------------------

def test_track_not_in_edits_is_byte_identical(tmp_path):
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    out = tmp_path / "out.mid"
    render(plan, out)

    src_piano = _track_messages(src, "Piano")
    out_piano = _track_messages(out, "Piano")
    assert src_piano == out_piano, (
        "Piano nao foi declarada em edits — deve sair identica ao source"
    )


def test_render_without_edits_matches_baseline_output(tmp_path):
    """Byte-identidade sem edits: sem plan.edits o pipeline nao pode
    alterar tracks originais."""
    src = _build_grid_source(tmp_path)
    plan_no_edits = _build_plan(src)
    plan_with_zero = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=0.0),
    ])
    out_no = tmp_path / "no.mid"
    out_zero = tmp_path / "zero.mid"
    render(plan_no_edits, out_no)
    render(plan_with_zero, out_zero)
    src_bass = _notes_of_track_by_name(src, "Bass")
    no_bass = _notes_of_track_by_name(out_no, "Bass")
    zero_bass = _notes_of_track_by_name(out_zero, "Bass")
    assert src_bass == no_bass == zero_bass


# --- bass: offsets dentro dos ranges do doc + preservacao de identidade ----

def test_bass_edit_moves_onsets_off_grid(tmp_path):
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    out = tmp_path / "out.mid"
    render(plan, out)

    src_bass = _notes_of_track_by_name(src, "Bass")
    out_bass = _notes_of_track_by_name(out, "Bass")
    assert len(src_bass) == len(out_bass)
    # Pelo menos algumas notas devem ter mudado de tick (grid perfeito
    # entrando + ranges do doc = quase impossivel que todas fiquem no mesmo tick).
    moved = sum(
        1 for (s, o) in zip(src_bass, out_bass, strict=True) if s[0] != o[0]
    )
    # A maioria das notas do grid perfeito deve sair do grid quando o gauss
    # empurra ate 3-sigma; algumas caem no mesmo tick por arredondamento.
    assert moved >= int(len(src_bass) * 0.75)


def test_bass_edit_preserves_pitches_and_order(tmp_path):
    """AC: humanizacao toca apenas timing/velocity/duracao — pitch e ordem
    de notas jamais mudam."""
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    render(plan, tmp_path / "out.mid")

    src_bass = _notes_of_track_by_name(src, "Bass")
    out_bass = _notes_of_track_by_name(tmp_path / "out.mid", "Bass")
    src_pitches = [n[2] for n in src_bass]
    out_pitches = [n[2] for n in out_bass]
    assert src_pitches == out_pitches


def test_bass_edit_offset_magnitudes_within_doc_range(tmp_path):
    """AC: MIDI sintetico em grid perfeito + edits no baixo = offsets
    dentro dos ranges do doc (bias 3-4ms + sigma bucket normal ate 8ms).
    Tolerancia generosa (3 sigmas = ~24ms + bias) pois gauss tem cauda."""
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    render(plan, tmp_path / "out.mid")

    pm = pretty_midi.PrettyMIDI(str(src))
    ms_per_tick = (60_000.0 / (120.0 * pm.resolution))
    src_bass = _notes_of_track_by_name(src, "Bass")
    out_bass = _notes_of_track_by_name(tmp_path / "out.mid", "Bass")
    offsets_ms = [
        (o[0] - s[0]) * ms_per_tick
        for s, o in zip(src_bass, out_bass, strict=True)
    ]
    assert max(abs(off) for off in offsets_ms) < 40.0  # 3-sigma + bias
    # Sanity: media negativa (baixo adiantado — bias -3.5ms).
    assert sum(offsets_ms) / len(offsets_ms) < 5.0  # nao positivo por sorte


# --- drums: ancoras kick/snare preservadas em downbeat ---------------------

def test_drums_edit_keeps_downbeat_kick_within_anchor_window(tmp_path):
    """AC: kick/snare em downbeat estrutural nunca sai do bucket 'anchor'
    (+-3ms). Todos os beats desta fixture caem em downbeats (4/4), entao
    todo kick/snare aqui e ancora."""
    src = _build_grid_source(tmp_path, include_drums=True)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Drums", profile="drums", intensity=1.0),
    ])
    render(plan, tmp_path / "out.mid")

    pm = pretty_midi.PrettyMIDI(str(src))
    ms_per_tick = (60_000.0 / (120.0 * pm.resolution))
    src_drums = _notes_of_track_by_name(src, "Drums")
    out_drums = _notes_of_track_by_name(tmp_path / "out.mid", "Drums")
    # Apenas notas com pitch=36 (kick) na fixture caem em downbeat real do bar (beat 1).
    downbeat_pairs = [
        (s, o) for s, o in zip(src_drums, out_drums, strict=True)
        if s[2] == 36 and s[0] % (pm.resolution * 4) == 0
    ]
    assert downbeat_pairs, "fixture deve conter pelo menos 1 kick em downbeat"
    for s, o in downbeat_pairs:
        offset_ms = (o[0] - s[0]) * ms_per_tick
        assert abs(offset_ms) <= 3.5, (
            f"kick em downbeat fora do bucket anchor: {offset_ms:+.2f}ms"
        )


# --- determinismo ----------------------------------------------------------

def test_edits_are_deterministic_across_runs(tmp_path):
    src = _build_grid_source(tmp_path)
    plan1 = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    plan2 = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    out1 = tmp_path / "out1.mid"
    out2 = tmp_path / "out2.mid"
    render(plan1, out1)
    render(plan2, out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_edits_seed_changes_with_plan_seed(tmp_path):
    """Seed derivada de plan.seed: mudar seed muda a saida."""
    src = _build_grid_source(tmp_path)
    plan_a = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    plan_b = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    plan_b.seed = plan_a.seed + 1
    out_a = tmp_path / "a.mid"
    out_b = tmp_path / "b.mid"
    render(plan_a, out_a)
    render(plan_b, out_b)
    assert out_a.read_bytes() != out_b.read_bytes()


# --- report ----------------------------------------------------------------

def test_render_report_lists_applied_edits(tmp_path):
    """AC: relatorio do render lista o que foi editado por track (notas
    tocadas, offset medio)."""
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    report = render(plan, tmp_path / "out.mid")
    assert len(report.edits) == 1
    assert isinstance(report.edits[0], EditReport)
    assert report.edits[0].track == "Bass"
    assert report.edits[0].profile == "bass"
    assert report.edits[0].notes_touched == 32  # 8 bars * 4 notes
    # bias -3.5ms — offset medio deve ficar proximo disso.
    assert -8.0 < report.edits[0].mean_offset_ms < 3.0


def test_format_render_report_prints_edits_block(tmp_path):
    from tools.render import format_render_report

    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=0.5),
    ])
    report = render(plan, tmp_path / "out.mid")
    text = format_render_report(report)
    assert "Edits applied" in text
    assert "Bass" in text


# --- validacao contra o MIDI -----------------------------------------------

def test_render_rejects_edit_for_nonexistent_track(tmp_path):
    from tools.plan import PlanValidationError

    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bas", profile="bass", intensity=0.5),
    ])
    with pytest.raises(PlanValidationError) as exc:
        render(plan, tmp_path / "out.mid")
    assert "Bass" in exc.value.message  # sugestao do nome mais proximo


# --- apply_edit direto (sem passar por render) -----------------------------

def test_apply_edit_direct_returns_touched_count(tmp_path):
    src = _build_grid_source(tmp_path)
    mid = mido.MidiFile(str(src))
    pm = pretty_midi.PrettyMIDI(str(src))
    bass_track = next(
        t for t in mid.tracks
        if any(m.is_meta and m.type == "track_name" and m.name == "Bass" for m in t)
    )
    touched, mean_offset = apply_edit(
        bass_track, PROFILE_PARAMS["bass"], 1.0, seed=1, pm=pm,
    )
    assert touched == 32
    assert isinstance(mean_offset, float)


def test_apply_edit_direct_zero_intensity_returns_no_touch(tmp_path):
    src = _build_grid_source(tmp_path)
    mid = mido.MidiFile(str(src))
    pm = pretty_midi.PrettyMIDI(str(src))
    bass_track = next(
        t for t in mid.tracks
        if any(m.is_meta and m.type == "track_name" and m.name == "Bass" for m in t)
    )
    before = [str(m) for m in bass_track]
    touched, mean_offset = apply_edit(
        bass_track, PROFILE_PARAMS["bass"], 0.0, seed=1, pm=pm,
    )
    after = [str(m) for m in bass_track]
    assert touched == 0
    assert mean_offset == 0.0
    assert before == after


def test_apply_edits_batch_reports_one_entry_per_edit(tmp_path):
    src = _build_grid_source(tmp_path, include_drums=True)
    mid = mido.MidiFile(str(src))
    pm = pretty_midi.PrettyMIDI(str(src))
    edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.5),
        PlanEdit(track="Drums", profile="drums", intensity=0.5),
    ]
    reports = apply_edits(list(mid.tracks), edits, plan_seed=42, pm=pm)
    assert [r.track for r in reports] == ["Bass", "Drums"]


def test_apply_edits_default_style_profile_is_byte_identical_to_none(tmp_path):
    """Regressao AC #3: chamada sem style_profile deve produzir MIDI
    byte-identico a chamada com StyleProfile.default() — a nova via so pode
    reproduzir o baseline quando o perfil e o default."""
    from tools.style_profile import StyleProfile

    src = _build_grid_source(tmp_path, include_drums=True)
    edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.7),
        PlanEdit(track="Drums", profile="drums", intensity=0.6),
    ]

    def _render_bytes(kwargs):
        mid = mido.MidiFile(str(src))
        pm = pretty_midi.PrettyMIDI(str(src))
        apply_edits(list(mid.tracks), edits, plan_seed=2026, pm=pm, **kwargs)
        out = tmp_path / f"out_{'default' if kwargs else 'none'}.mid"
        mid.save(str(out))
        return out.read_bytes()

    assert _render_bytes({}) == _render_bytes(
        {"style_profile": StyleProfile.default()}
    )


def test_apply_edits_custom_style_profile_changes_gate_ratios(tmp_path):
    """style_profile customizado com `tight` deslocado muda a duracao
    resultante do baixo — prova que o perfil e de fato propagado ao
    calculo de gate em apply_edit."""
    from tools.style_profile import StyleProfile

    src = _build_grid_source(tmp_path)
    edits = [PlanEdit(track="Bass", profile="bass", intensity=1.0)]

    baseline = StyleProfile.default()
    custom_ratios = dict(baseline.gate_ratios)
    custom_ratios["tight"] = (0.10, 0.15)
    custom = StyleProfile(
        velocity_ranges=baseline.velocity_ranges,
        gate_ratios=custom_ratios,
        timing_jitter_ms=baseline.timing_jitter_ms,
    )

    def _bass_durations(profile):
        mid = mido.MidiFile(str(src))
        pm = pretty_midi.PrettyMIDI(str(src))
        apply_edits(
            list(mid.tracks), edits, plan_seed=2026, pm=pm,
            style_profile=profile,
        )
        # ordena por on_tick e extrai (off - on) por par
        for track in mid.tracks:
            has_bass = any(
                m.is_meta and m.type == "track_name" and m.name == "Bass"
                for m in track
            )
            if not has_bass:
                continue
            abs_tick, open_ons, pairs = 0, {}, []
            for msg in track:
                abs_tick += msg.time
                if msg.is_meta:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    open_ons.setdefault((msg.channel, msg.note), []).append(
                        abs_tick,
                    )
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0
                ):
                    key = (msg.channel, msg.note)
                    if key in open_ons and open_ons[key]:
                        pairs.append(abs_tick - open_ons[key].pop())
            return pairs
        return []

    default_durations = _bass_durations(None)
    custom_durations = _bass_durations(custom)

    assert default_durations and custom_durations
    # gate muito mais apertado -> duracoes menores em media
    assert sum(custom_durations) < sum(default_durations)


def test_downbeat_tolerance_hits_adjacent_tick():
    """Ancora e detectada mesmo com arredondamento de +/-1 tick — cobre o
    ramo de tolerancia de `_tick_near`."""
    from tools.edits import _tick_near

    downbeats = {960, 1920}
    assert _tick_near(959, downbeats)  # -1 tick
    assert _tick_near(1921, downbeats)  # +1 tick
    assert not _tick_near(500, downbeats)


def test_downbeat_ticks_empty_when_no_time_signature():
    """PrettyMIDI sem time signature nem notas retorna downbeats vazio —
    ramo defensivo do `_downbeat_ticks`."""
    from tools.edits import _downbeat_ticks

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    assert _downbeat_ticks(pm) == set()


def test_duration_clamp_never_produces_zero_length_note(tmp_path):
    """Intensity 1.0 no baixo (gate ratio ~0.6-0.82) so vezes gera duracao 0
    apos arredondamento em gap muito curto. Garantia: nota sempre tem
    pelo menos 1 tick — validado pelo re-parse subsequente sem erro."""
    src = _build_grid_source(tmp_path)
    plan = _build_plan(src, edits=[
        PlanEdit(track="Bass", profile="bass", intensity=1.0),
    ])
    out = tmp_path / "out.mid"
    render(plan, out)
    out_bass = _notes_of_track_by_name(out, "Bass")
    # Todas as notas duram >= 1 tick.
    assert all(end > start for (start, end, _pitch, _vel) in out_bass)


def test_empty_track_produces_zero_report(tmp_path):
    """Track sem notas nao quebra apply_edit — retorna (0, 0.0)."""
    src = _build_grid_source(tmp_path)
    pm = pretty_midi.PrettyMIDI(str(src))
    # Uma track so com meta track_name — nenhum note_on/off.
    empty = mido.MidiTrack()
    empty.append(mido.MetaMessage("track_name", name="Empty", time=0))
    touched, mean = apply_edit(
        empty, PROFILE_PARAMS["bass"], 1.0, seed=1, pm=pm,
    )
    assert touched == 0
    assert mean == 0.0
