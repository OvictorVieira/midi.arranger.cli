"""Testes do renderer (US-016)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mido
import pretty_midi
import pytest

from tools.brief_ref import brief_sha256
from tools.palette.harmonic import PadNote
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    SourceMidi,
    StyleTechnique,
    dump,
)
from tools.render import (
    ElementRationale,
    RenderError,
    RenderReport,
    _apply_style_techniques_to_tracks,
    _canonical_style_technique,
    _element_seed,
    _notes_to_track,
    _tool_target_for_element,
    format_render_report,
    render,
    sha256_of_file,
)
from tools.techniques import (
    SUPPORTED_TECHNIQUES,
    TechniqueApplyResult,
    UnknownTechniqueError,
    build_index,
)

# --- fixtures ---------------------------------------------------------------

def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_synthetic_source(tmp_path: Path, name: str = "source.mid") -> Path:
    """MIDI: 8 compassos em 4/4 a 120bpm, piano com triade C + baixo
    quarter-notes (baixo garante onsets distintos suficientes para
    logicpro._bars_from cair no estimador de tempo do pretty_midi)."""
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
                velocity=90, pitch=36, start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.append(piano)
    pm.instruments.append(bass)
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _build_plan(source: Path, *, layers: int = 1) -> ArrangementPlan:
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
                layers=layers,
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
    )


def _attach_brief_authorizing_techniques(plan: ArrangementPlan, tmp_path: Path) -> None:
    """Anexa `plan.brief_ref` autorizando as tecnicas declaradas em `plan.style`.

    Depois de US-003 o render (via `plan.validate`) exige brief_ref quando
    ha tecnica declarada. Helper enxuto para os testes que ja tinham essa
    forma antes da mudanca.
    """
    import json as _json

    authorized: dict[str, dict[str, list[str]]] = {}
    if isinstance(plan.style, dict):
        for family, entry in plan.style.items():
            names = [
                t.name for t in entry.techniques if isinstance(t, StyleTechnique)
            ]
            if names:
                authorized[family] = {"authorized_techniques": names}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        _json.dumps({"style": authorized}, indent=2), encoding="utf-8"
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))


def _family_style(confidence: str, *, reference: str = "Style Reference") -> FamilyStyle:
    return FamilyStyle(
        reference=reference,
        researched_at="2026-08-24",
        sources=["https://example.test/style"],
        confidence=confidence,
        techniques=[],
        parameters={},
    )


# --- basicos ---------------------------------------------------------------

def test_render_produces_output_file(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert isinstance(report, RenderReport)
    assert report.output_path == out
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_returns_source_sha256_and_seed(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.source_sha256 == _sha256_bytes(src)
    assert report.seed == 42


def test_sha256_of_file_matches_hashlib(tmp_path):
    src = _build_synthetic_source(tmp_path)
    assert sha256_of_file(src) == _sha256_bytes(src)


# --- determinismo -----------------------------------------------------------

def test_render_is_deterministic_byte_for_byte(tmp_path):
    src = _build_synthetic_source(tmp_path)
    out1 = tmp_path / "out1.mid"
    out2 = tmp_path / "out2.mid"
    render(_build_plan(src), out1)  # planos identicos (novo objeto cada vez)
    render(_build_plan(src), out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_render_deterministic_with_multiple_layers(tmp_path):
    src = _build_synthetic_source(tmp_path)
    out1 = tmp_path / "out1.mid"
    out2 = tmp_path / "out2.mid"
    render(_build_plan(src, layers=3), out1)
    render(_build_plan(src, layers=3), out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_element_seed_is_deterministic_per_triple():
    a = _element_seed(42, "pad_main", "MAIN")
    b = _element_seed(42, "pad_main", "MAIN")
    assert a == b
    assert a != _element_seed(43, "pad_main", "MAIN")
    assert a != _element_seed(42, "pad_other", "MAIN")
    assert a != _element_seed(42, "pad_main", "OTHER")


# --- preservacao das tracks originais --------------------------------------

def test_render_preserves_original_tracks_note_by_note(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    render(plan, out)

    src_pm = pretty_midi.PrettyMIDI(str(src))
    out_pm = pretty_midi.PrettyMIDI(str(out))
    assert len(out_pm.instruments) > len(src_pm.instruments), (
        "output must add pad track(s) beyond source"
    )
    # A saida tem tracks a mais (os pads). Fatiar deixa a intencao explicita
    # e mantem strict=True checando que o prefixo tem o tamanho esperado.
    head = out_pm.instruments[: len(src_pm.instruments)]
    for src_inst, out_inst in zip(src_pm.instruments, head, strict=True):
        src_notes = [
            (n.pitch, n.velocity, round(n.start, 6), round(n.end, 6))
            for n in src_inst.notes
        ]
        out_notes = [
            (n.pitch, n.velocity, round(n.start, 6), round(n.end, 6))
            for n in out_inst.notes
        ]
        assert src_notes == out_notes


def test_render_preserves_ticks_per_beat_and_type(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    render(plan, out)

    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(str(out), charset="utf-8")
    assert out_mid.ticks_per_beat == src_mid.ticks_per_beat
    assert out_mid.type == src_mid.type


def test_render_preserves_markers_and_tempo_events(tmp_path):
    """Marcadores e set_tempo do source tem que sair do renderer intactos."""
    src = _build_synthetic_source(tmp_path)
    # Injeta marker e set_tempo no source usando mido, salva de volta.
    mid = mido.MidiFile(str(src))
    meta_track = mid.tracks[0]
    meta_track.insert(0, mido.MetaMessage("marker", text="INTRO", time=0))
    meta_track.insert(0, mido.MetaMessage("set_tempo", tempo=500000, time=0))
    mid.save(str(src))

    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    render(plan, out)

    out_mid = mido.MidiFile(str(out), charset="utf-8")
    markers_out = [m.text for tr in out_mid.tracks for m in tr if m.type == "marker"]
    tempos_out = [m.tempo for tr in out_mid.tracks for m in tr if m.type == "set_tempo"]
    assert "INTRO" in markers_out
    assert 500000 in tempos_out


# --- overwrite protection --------------------------------------------------

def test_render_never_overwrites_source(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    with pytest.raises(RenderError, match="overwrite source"):
        render(plan, src)


def test_render_does_not_mutate_source_bytes(tmp_path):
    src = _build_synthetic_source(tmp_path)
    before = _sha256_bytes(src)
    plan = _build_plan(src, layers=3)
    render(plan, tmp_path / "out.mid")
    after = _sha256_bytes(src)
    assert before == after


def test_render_default_output_goes_to_home_desktop(tmp_path, monkeypatch):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    fake_home = tmp_path / "home"
    (fake_home / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    report = render(plan)
    expected = fake_home / "Desktop" / f"{src.stem}_arranged.mid"
    assert report.output_path == expected
    assert expected.exists()


def test_render_creates_output_parent_dir(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    nested = tmp_path / "a" / "b" / "out.mid"
    render(plan, nested)
    assert nested.exists()


# --- source I/O ------------------------------------------------------------

def test_source_not_found_raises(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.source_midi.path = str(tmp_path / "nonexistent.mid")
    with pytest.raises(RenderError, match="source MIDI not found"):
        render(plan, tmp_path / "out.mid")


def test_source_path_override(tmp_path):
    src = _build_synthetic_source(tmp_path)
    other = _build_synthetic_source(tmp_path, name="other.mid")
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    render(plan, out, source_path=other)
    # Preserva as tracks do override, nao do plan.source_midi.path.
    out_pm = pretty_midi.PrettyMIDI(str(out))
    other_pm = pretty_midi.PrettyMIDI(str(other))
    assert [n.pitch for n in out_pm.instruments[0].notes] == \
        [n.pitch for n in other_pm.instruments[0].notes]


def test_sha256_mismatch_is_warning_not_error(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.source_midi.sha256 = "0" * 64
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert any("sha256" in w.lower() for w in report.warnings)
    assert out.exists()


def test_render_warns_for_low_style_confidence(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {"keys": _family_style("low", reference="Thin research")}
    out = tmp_path / "out.mid"

    report = render(plan, out)

    assert out.exists()
    warning = next(w for w in report.warnings if "confidence low" in w)
    assert "style.keys" in warning
    assert "Thin research" in warning


def test_render_warns_for_default_style_confidence_from_normalization(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"

    report = render(plan, out)

    assert out.exists()
    warning = next(w for w in report.warnings if "confidence default" in w)
    assert "style.keys" in warning
    assert "no style was researched" in warning


@pytest.mark.parametrize("confidence", ["high", "medium"])
def test_render_does_not_warn_for_high_or_medium_style_confidence(
    tmp_path, confidence: str,
):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {"keys": _family_style(confidence)}
    report = render(plan, tmp_path / "out.mid")

    assert not any("confidence" in w for w in report.warnings)


def test_render_applies_style_techniques_to_generated_tracks(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tools.techniques.SUPPORTED_TECHNIQUES",
        (*SUPPORTED_TECHNIQUES, "keys.hand_asynchrony"),
    )
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {
        "keys": FamilyStyle(
            reference="Pianist research",
            researched_at="2026-08-24",
            sources=["https://example.test/keys"],
            confidence="high",
            techniques=[
                StyleTechnique(
                    name="hand_asynchrony",
                    density=0.4,
                    rationale="Assincronia leve entre maos sem copiar frase.",
                ),
            ],
            parameters={"sem_sinal_tipico_ms": [25, 40]},
        ),
    }
    _attach_brief_authorizing_techniques(plan, tmp_path)
    calls: list[dict] = []

    def fake_apply_technique_with_warnings(
        canonical,
        midi,
        *,
        seed,
        parameters,
        tool,
        index,
    ):
        calls.append({
            "canonical": canonical,
            "seed": seed,
            "parameters": dict(parameters),
            "tool": tool,
            "has_index": index is not None,
        })
        midi.tracks[0].insert(
            1,
            mido.Message("note_on", channel=0, note=61, velocity=40, time=0),
        )
        midi.tracks[0].insert(
            2,
            mido.Message("note_off", channel=0, note=61, velocity=0, time=1),
        )
        return TechniqueApplyResult(
            result=midi,
            warnings=({
                "code": "W_TEST_TECHNIQUE",
                "message": "fake technique warning",
                "path": "style.keys.techniques[0]",
            },),
        )

    monkeypatch.setattr(
        "tools.render.apply_technique_with_warnings",
        fake_apply_technique_with_warnings,
    )

    out = tmp_path / "out.mid"
    report = render(plan, out)

    assert len(calls) == 1
    assert calls[0]["canonical"] == "keys.hand_asynchrony"
    assert calls[0]["tool"] == "omnisphere"
    assert calls[0]["has_index"] is True
    assert calls[0]["parameters"] == {
        "sem_sinal_tipico_ms": [25, 40],
        "density": 0.4,
    }
    assert calls[0]["seed"] != plan.seed
    assert any("W_TEST_TECHNIQUE" in w for w in report.warnings)
    assert any(issue.pitch == 61 for issue in report.harmony_issues)

    src_pm = pretty_midi.PrettyMIDI(str(src))
    out_pm = pretty_midi.PrettyMIDI(str(out))
    for src_inst, out_inst in zip(
        src_pm.instruments,
        out_pm.instruments[:len(src_pm.instruments)],
        strict=True,
    ):
        assert [
            (n.pitch, n.velocity, round(n.start, 6), round(n.end, 6))
            for n in out_inst.notes
        ] == [
            (n.pitch, n.velocity, round(n.start, 6), round(n.end, 6))
            for n in src_inst.notes
        ]


def test_render_accepts_plan_validated_with_supported_style_technique(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {
        "drums": FamilyStyle(
            reference="Drummer research",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    _attach_brief_authorizing_techniques(plan, tmp_path)

    report = render(plan, tmp_path / "out.mid")

    assert report.output_path.exists()
    assert all("unknown" not in warning.lower() for warning in report.warnings)


def test_render_style_technique_helpers_handle_empty_targets_and_bad_names(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    index = build_index()

    tool_element = Element(
        id="x",
        role="pad",
        sections=["MAIN"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        instrument={"plugin": "Superior Drummer 3!", "preset": "Default"},
        rationale="Teste de normalizacao de ferramenta.",
    )
    no_tool_element = Element(
        id="x",
        role="pad",
        sections=["MAIN"],
        register=[48, 72],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        instrument=None,
        rationale="Teste sem ferramenta declarada.",
    )

    assert _tool_target_for_element(tool_element) == "superior_drummer_3"
    assert _tool_target_for_element(no_tool_element) is None
    assert _canonical_style_technique(index, "keys", "hand_asynchrony") == (
        "keys.hand_asynchrony"
    )
    with pytest.raises(RenderError, match="not available"):
        _canonical_style_technique(index, "keys", "bass.ghost_notes")

    tracks, warnings, applied = _apply_style_techniques_to_tracks(
        [],
        plan=plan,
        family=None,
        tool_target=None,
        ticks_per_beat=480,
        midi_type=1,
        index=index,
    )
    assert (tracks, warnings, applied) == ([], [], False)


def test_render_wraps_style_technique_engine_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.techniques.SUPPORTED_TECHNIQUES",
        (*SUPPORTED_TECHNIQUES, "keys.hand_asynchrony"),
    )
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {
        "keys": FamilyStyle(
            reference="Pianist research",
            researched_at="2026-08-24",
            sources=["https://example.test/keys"],
            confidence="high",
            techniques=[StyleTechnique(name="hand_asynchrony")],
            parameters={},
        ),
    }
    _attach_brief_authorizing_techniques(plan, tmp_path)

    def boom(*_args, **_kwargs):
        raise UnknownTechniqueError("keys.hand_asynchrony", ("drums.ghost_notes",))

    monkeypatch.setattr("tools.render.apply_technique_with_warnings", boom)

    with pytest.raises(RenderError, match="style.keys.techniques"):
        render(plan, tmp_path / "out.mid")


# --- layers, track names, roles --------------------------------------------

def test_layers_produce_multiple_tracks(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src, layers=3)
    out = tmp_path / "out.mid"
    render(plan, out)
    src_pm = pretty_midi.PrettyMIDI(str(src))
    out_pm = pretty_midi.PrettyMIDI(str(out))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 3


def test_track_name_follows_us013_convention_verified(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    render(plan, out)
    mid = mido.MidiFile(str(out), charset="utf-8")
    pad_track = mid.tracks[-1]
    names = [m.name for m in pad_track if m.type == "track_name"]
    assert names, "pad track must carry a track_name meta"
    name = names[0]
    assert " - " in name
    assert "Omnisphere" in name
    assert "Desert Wind" in name
    assert name.endswith("*")


def test_track_name_unverified_uses_question_mark(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0].instrument["verified"] = False
    out = tmp_path / "out.mid"
    render(plan, out)
    mid = mido.MidiFile(str(out), charset="utf-8")
    name = [m.name for m in mid.tracks[-1] if m.type == "track_name"][0]
    assert name.endswith("?")


def test_layer_suffix_in_track_names_when_layers_gt_one(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src, layers=2)
    out = tmp_path / "out.mid"
    render(plan, out)
    mid = mido.MidiFile(str(out), charset="utf-8")
    pad_names = [
        m.name
        for tr in mid.tracks[-2:]
        for m in tr
        if m.type == "track_name"
    ]
    assert any("L1" in n for n in pad_names)
    assert any("L2" in n for n in pad_names)


def test_non_pad_element_is_skipped_with_note(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements.append(Element(
        id="piano_main",
        role="piano_motif",
        sections=["MAIN"],
        register=[60, 84],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        instrument={"plugin": "Alchemy", "preset": "Grand Motif", "verified": False},
        rationale="Piano motif fica fora do renderer de pad para validar o skip reportado.",
    ))
    out = tmp_path / "out.mid"
    report = render(plan, out)

    src_pm = pretty_midi.PrettyMIDI(str(src))
    out_pm = pretty_midi.PrettyMIDI(str(out))
    # Pad rendered, piano_motif skipped => exactly +1 track.
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1

    piano_report = next(e for e in report.elements if e.element_id == "piano_main")
    assert piano_report.rendered is False
    assert "not implemented" in piano_report.note.lower()


def test_pad_element_missing_instrument_raises(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0].instrument = {"plugin": "", "preset": ""}
    with pytest.raises(RenderError, match="missing instrument"):
        render(plan, tmp_path / "out.mid")


# --- rationale + collision integration -------------------------------------

def test_report_includes_rationale_per_element(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert len(report.elements) == 1
    er = report.elements[0]
    assert isinstance(er, ElementRationale)
    assert er.element_id == "pad_main"
    assert er.rationale == "Sustained pad glues the arrangement."
    assert er.plugin == "Omnisphere"
    assert er.preset == "Desert Wind"
    assert er.verified is True
    assert er.rendered is True


def test_report_runs_collision_validator(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0].register = [24, 34]  # dense pad entirely below C2
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.collision.relocations, (
        "collision validator should have relocated the sub-band dense pad"
    )
    relocation = report.collision.relocations[0]
    assert relocation.element_id == "pad_main"
    assert relocation.from_register == (24, 34)
    assert relocation.to_register == (36, 46)


def test_format_render_report_smoke(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    text = format_render_report(report)
    assert "Rendered:" in text
    assert "pad_main" in text
    assert "Omnisphere" in text
    assert "Desert Wind" in text
    assert "rationale: Sustained pad" in text


def test_format_render_report_shows_collision_details(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0].register = [24, 34]
    out = tmp_path / "out.mid"
    text = format_render_report(render(plan, out))
    assert "Collision relocations" in text


def test_format_render_report_prints_warnings_and_notes(tmp_path):
    """Exercita os ramos de warnings e notes do pretty-print — que so
    aparecem quando o render acumula avisos ou anota o elemento."""
    from tools.validators.collision import (
        CollisionReport,
        CollisionWarning,
    )
    warn = CollisionWarning(
        element_ids=("pad_main", "guitar_low"),
        section_label="MAIN",
        bar_range=(1, 4),
        band="low",
        reason="both elements occupy the low band",
    )
    report = RenderReport(
        output_path=tmp_path / "out.mid",
        source_sha256="deadbeef",
        seed=0,
        collision=CollisionReport(relocations=[], warnings=[warn]),
        elements=[ElementRationale(
            element_id="pad_main", role="pad",
            rationale="", plugin="Omnisphere", preset="Desert Wind",
            verified=True, layers=1, sections=("MAIN",),
            rendered=False, note="unsupported role — skipped",
        )],
        warnings=["global warning: check plugin scan"],
    )
    text = format_render_report(report)
    assert "note: unsupported role" in text
    assert "Collision warnings" in text
    assert "both elements occupy the low band" in text
    assert "Render warnings" in text
    assert "global warning" in text


# --- piano / rhodes integration (US-003) -----------------------------------

def _piano_element(*, role: str = "piano", use_sustain: bool = False) -> Element:
    pattern = {"use_sustain_cc64": True} if use_sustain else None
    return Element(
        id=f"{role}_main",
        role=role,
        sections=["MAIN"],
        register=[60, 84],
        layers=1,
        sync_role="sustain_through",
        articulation="tight" if role == "piano" else "open",
        harmony="follow_chords",
        pattern=pattern,
        dynamics={"shape": "hold"},
        instrument={"plugin": "Alchemy", "preset": "Grand Motif", "verified": True},
        rationale=f"{role} lead motif.",
    )


def test_render_supports_piano_role(tmp_path):
    """AC: 'Gerador em palette/harmonic.py ... consome plano + analise,
    devolve track(s)' — o pipeline end-to-end deve produzir uma track a
    mais quando o plano declara piano."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _piano_element(role="piano")
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1


def test_render_supports_rhodes_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _piano_element(role="rhodes")
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True


def test_render_piano_default_emits_no_cc64(tmp_path):
    """AC: 'Teste verifica que nenhum CC64 e emitido quando o elemento nao pede'."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _piano_element(role="piano", use_sustain=False)
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    # A ultima track e a track do piano — nenhuma msg control_change/CC64.
    piano_tr = mid.tracks[-1]
    cc64s = [m for m in piano_tr if m.type == "control_change" and m.control == 64]
    assert cc64s == []


def test_render_piano_with_sustain_emits_cc64(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _piano_element(role="piano", use_sustain=True)
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    piano_tr = mid.tracks[-1]
    cc64s = [m for m in piano_tr if m.type == "control_change" and m.control == 64]
    # AC US-007: pedal sincopado — pisa/solta por frase, nao ativa uma vez
    # e segura ate o fim. Comeca em 127, termina em 0, e alterna no meio.
    assert len(cc64s) >= 4
    assert cc64s[0].value == 127
    assert cc64s[-1].value == 0
    for i, m in enumerate(cc64s):
        assert m.value == (127 if i % 2 == 0 else 0)


def test_iter_element_sections_skips_unknown_labels(tmp_path):
    """Guarda defensiva: se element.sections referencia label ausente do plano
    (mutacao pos-validate), o renderer ignora em silencio em vez de crashar."""
    from tools.render import _iter_element_sections
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    element = plan.elements[0]
    element.sections = ["NON_EXISTENT", "MAIN"]
    resolved = _iter_element_sections(element, plan)
    assert len(resolved) == 1
    assert resolved[0][0].label == "MAIN"


def test_render_piano_passes_harmony_and_placement_validators(tmp_path):
    """AC: 'Render com piano passa nos validadores harmonico e de placement'."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _piano_element(role="piano")
    out = tmp_path / "out.mid"
    report = render(plan, out)

    # Nenhum erro em harmony/placement — avisos sao tolerados.
    from tools.validators.harmony import has_errors as harmony_has_errors
    from tools.validators.placement import has_errors as placement_has_errors
    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]


# --- strings / choir integration (US-004) ----------------------------------

def _strings_element(*, role: str = "strings", tutti: bool = False, layers: int = 3) -> Element:
    pattern: dict | None = {"tutti": True} if tutti else None
    return Element(
        id=f"{role}_main",
        role=role,
        sections=["MAIN"],
        register=[48, 84],
        layers=layers,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="follow_chords",
        pattern=pattern,
        dynamics={"shape": "hold"},
        instrument={"plugin": "Omnisphere", "preset": "Layered Strings", "verified": True},
        rationale=f"{role} lines behind the chorus.",
    )


def test_render_supports_strings_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _strings_element(role="strings", layers=3)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    # Uma track por voz.
    assert len(out_pm.instruments) == len(src_pm.instruments) + 3


def test_render_supports_choir_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _strings_element(role="choir", layers=3)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True


def test_render_strings_emits_cc11(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _strings_element(role="strings", layers=3)
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    # Ultima track (voz aguda) deve carregar eventos CC11.
    strings_tr = mid.tracks[-1]
    cc11s = [m for m in strings_tr if m.type == "control_change" and m.control == 11]
    assert len(cc11s) > 0


def test_render_strings_passes_harmony_and_placement_validators(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _strings_element(role="strings", layers=3)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    from tools.validators.harmony import has_errors as harmony_has_errors
    from tools.validators.placement import has_errors as placement_has_errors
    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]


def test_render_tutti_in_multiple_sections_produces_warning(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    # Adiciona segunda secao e tutti nas duas.
    plan.sections.append(PlanSection(
        label="PEAK", kind="chorus", start_bar=4, end_bar=8, source="marker",
        protagonist="texture",
        energy={"densidade": 6, "impacto": 6, "largura": 6, "altura": 6, "instabilidade": 4},
    ))
    plan.sections[0].end_bar = 4
    strings = _strings_element(role="strings", tutti=True, layers=3)
    strings.sections = ["MAIN", "PEAK"]
    plan.elements[0] = strings
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert any("tutti" in w for w in report.warnings)


# --- drone / nota-pedal integration (US-005) -------------------------------

def _drone_element(
    *, pedal: bool = False, pedal_pitch: str = "tonic",
    register: list[int] | None = None, layers: int = 1,
    filter_cycle_bars: int = 4, modulation_bars: int = 16,
) -> Element:
    pattern: dict = {
        "pedal": pedal,
        "pedal_pitch": pedal_pitch,
        "filter_cycle_bars": filter_cycle_bars,
        "modulation_bars": modulation_bars,
    }
    return Element(
        id="drone_main",
        role="drone",
        sections=["MAIN"],
        register=register or [48, 71],
        layers=layers,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="pedal" if pedal else "free",
        pattern=pattern,
        dynamics={"shape": "hold"},
        instrument={"plugin": "Alchemy", "preset": "Sub Drone", "verified": True},
        rationale="Drone underneath the arrangement.",
    )


def test_render_supports_drone_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _drone_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1


def test_render_drone_emits_cc74_and_cc11(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _drone_element(pedal=False)
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    drone_tr = mid.tracks[-1]
    cc74 = [m for m in drone_tr if m.type == "control_change" and m.control == 74]
    cc11 = [m for m in drone_tr if m.type == "control_change" and m.control == 11]
    assert cc74, "no CC74 (filter) events emitted"
    assert cc11, "no CC11 (expression) events emitted"


def test_render_drone_pedal_mode_emits_no_cc(tmp_path):
    """AC: 'Modo pedal: nota unica sustentada sem drift'."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _drone_element(pedal=True)
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    drone_tr = mid.tracks[-1]
    cc_events = [m for m in drone_tr if m.type == "control_change"]
    assert cc_events == []


def test_render_drone_multiple_layers(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _drone_element(layers=2)
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 2


def test_render_drone_passes_harmony_and_placement_validators(tmp_path):
    """AC: 'Render com drone passa nos validadores harmonico e de placement'."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _drone_element(pedal=True)
    out = tmp_path / "out.mid"
    report = render(plan, out)
    from tools.validators.harmony import has_errors as harmony_has_errors
    from tools.validators.placement import has_errors as placement_has_errors
    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]


# --- rhythmic (arp / rhythmic_machine) integration (US-006) ----------------

def _rhythmic_element(
    *, role: str = "arp",
    layers: int = 1,
    register: list[int] | None = None,
    pattern_bars: int = 1,
    mutate_every_bars: int | None = None,
    interlock: bool = False,
) -> Element:
    pattern: dict = {
        "pattern_bars": pattern_bars,
        "interlock": interlock,
    }
    if mutate_every_bars is not None:
        pattern["mutate_every_bars"] = mutate_every_bars
    return Element(
        id=f"{role}_main",
        role=role,
        sections=["MAIN"],
        register=register or [72, 96],
        layers=layers,
        sync_role="response",
        articulation="staccato",
        harmony="follow_chords",
        pattern=pattern,
        dynamics={"shape": "hold"},
        instrument={"plugin": "Omnisphere", "preset": "Bright Arp", "verified": True},
        rationale=f"{role} riff layer for the chorus.",
    )


def test_render_supports_arp_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _rhythmic_element(role="arp")
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1


def test_render_supports_rhythmic_machine_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _rhythmic_element(role="rhythmic_machine")
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True


def test_render_arp_emits_cc74(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _rhythmic_element(role="arp")
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    arp_tr = mid.tracks[-1]
    cc74 = [m for m in arp_tr if m.type == "control_change" and m.control == 74]
    assert cc74, "no CC74 (filter) events emitted"


def test_render_arp_multiple_layers(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _rhythmic_element(role="arp", layers=2)
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 2


def test_render_arp_passes_harmony_and_placement_validators(tmp_path):
    """AC: 'Render com arp passa nos validadores harmonico e de placement'."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _rhythmic_element(role="arp")
    out = tmp_path / "out.mid"
    report = render(plan, out)
    from tools.validators.harmony import has_errors as harmony_has_errors
    from tools.validators.placement import has_errors as placement_has_errors
    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]


def test_render_arp_mutate_and_interlock_from_pattern(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _rhythmic_element(
        role="arp", mutate_every_bars=4, interlock=True,
    )
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True


# --- plan-as-path ----------------------------------------------------------

def test_render_accepts_plan_path(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan_path = tmp_path / "plan.json"
    dump(plan, plan_path)
    out = tmp_path / "out.mid"
    report = render(plan_path, out)
    assert report.output_path == out
    assert out.exists()


# --- motor / shadow integration (US-007) -----------------------------------

def _build_source_with_guitar(tmp_path: Path, name: str = "source_guit.mid") -> Path:
    """Source com piano + bass + duas guitarras (2 tracks para gerar unisons)."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    g1 = pretty_midi.Instrument(program=27, name="Guitar 1")
    g2 = pretty_midi.Instrument(program=27, name="Guitar 2")
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
                velocity=90, pitch=36, start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
            # Duas guitarras em unisono no downbeat de cada bar (beat 0) +
            # notas de riff diferentes nos outros beats.
            gpitch = 40 if beat == 0 else 40 + beat
            g1.notes.append(pretty_midi.Note(
                velocity=100, pitch=gpitch,
                start=start + beat * beat_len,
                end=start + (beat + 0.5) * beat_len,
            ))
            g2.notes.append(pretty_midi.Note(
                velocity=100, pitch=gpitch,
                start=start + beat * beat_len,
                end=start + (beat + 0.5) * beat_len,
            ))
    pm.instruments.extend([piano, bass, g1, g2])
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _motor_element(*, layers: int = 1) -> Element:
    return Element(
        id="motor_main",
        role="motor",
        sections=["MAIN"],
        register=[48, 71],
        layers=layers,
        sync_role="sustain_through",
        articulation="staccato",
        harmony="follow_chords",
        pattern={"subdivision": "sixteenth"},
        dynamics={"shape": "hold"},
        instrument={
            "plugin": "Omnisphere",
            "preset": "Motor Pluck",
            "verified": True,
        },
        rationale="Motor keeps the chorus moving without competing with the riff.",
    )


def _shadow_element(*, layers: int = 1) -> Element:
    return Element(
        id="shadow_main",
        role="shadow",
        sections=["MAIN"],
        register=[48, 84],
        layers=layers,
        sync_role="response",
        articulation="sustained",
        harmony="free",
        pattern={"octave_shift": 12, "tail_notes": 2},
        dynamics={"shape": "hold"},
        instrument={
            "plugin": "Omnisphere",
            "preset": "Ghost Shadow",
            "verified": True,
        },
        rationale="Shadow doubles the end of each guitar phrase one octave up.",
    )


def _hat_elec_element(*, pattern_mode: str = "sixteenth", layers: int = 1) -> Element:
    return Element(
        id="hat_elec_main",
        role="hat_elec",
        sections=["MAIN"],
        register=[70, 70],
        layers=layers,
        sync_role="response",
        articulation="staccato",
        harmony="free",
        pattern={"pattern_mode": pattern_mode},
        dynamics={"shape": "hold"},
        instrument={
            "plugin": "Addictive Drums 2",
            "preset": "Electronic Hat",
            "verified": True,
        },
        rationale="Electronic hi-hat drives the eletronico ritmico groove.",
    )


def _sub_element(*, follow: str = "tonic", degrees: list[int] | None = None) -> Element:
    return Element(
        id="sub_main",
        role="sub",
        sections=["MAIN"],
        register=[24, 40],
        layers=1,
        sync_role="sustain_through",
        articulation="sustained",
        harmony="free",
        pattern={"follow": follow},
        degrees=degrees,
        dynamics={"shape": "hold"},
        instrument={
            "plugin": "Omnisphere",
            "preset": "Breakdown Sub",
            "verified": True,
        },
        rationale="Sub-bass carries the breakdown.",
    )


def _sub_drop_element() -> Element:
    return Element(
        id="sub_drop_main",
        role="sub_drop",
        sections=["MAIN"],
        register=[24, 40],
        layers=1,
        sync_role="exact_anchor",
        articulation="staccato",
        harmony="free",
        pattern={},
        dynamics={"shape": "hold"},
        instrument={
            "plugin": "Logic Sampler",
            "preset": "Sub Drop",
            "verified": True,
        },
        rationale="Sub-drop marks the section boundary.",
    )


def test_render_supports_hat_elec_role(tmp_path):
    """AC (issue #22): hi-hat eletronico gera pitch fixo, 100% monofonico,
    velocity/gate/offset lidos do manual via build_index()."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _hat_elec_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)

    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(str(out), charset="utf-8")
    hat_track = out_mid.tracks[len(src_mid.tracks)]

    notes = []
    t = 0
    open_note = None
    for msg in hat_track:
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            open_note = (msg.note, t)
        elif msg.type in ("note_off",) or (msg.type == "note_on" and msg.velocity == 0):
            assert open_note is not None
            notes.append((open_note[0], open_note[1], t))
            open_note = None
    assert notes
    pitches = {n[0] for n in notes}
    assert pitches == {70}, "hat_elec pitch must never vary within the track"
    # 100% monofonico: nenhum note_on comeca antes do note_off anterior.
    notes.sort(key=lambda n: n[1])
    for i in range(len(notes) - 1):
        assert notes[i][2] <= notes[i + 1][1], "hat_elec must be zero-overlap"
    assert report.elements[0].rendered is True


def test_render_hat_elec_statistics_match_manual(tmp_path):
    """Estatistica de velocity/offset/gate bate com o que o manual declara
    (issue #22): velocity media ~95 desvio ~8 em [79,113], gate escalando
    com o BPM real do arquivo (120bpm neste fixture, nao 174 da referencia),
    offset com vies negativo (levemente adiantado)."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _hat_elec_element()
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    hat = next(i for i in out_pm.instruments if i.name and "hat_elec" in i.name)
    assert len(hat.notes) == 128  # 8 bars * 16 steps (pattern 'sixteenth')

    velocities = [n.velocity for n in hat.notes]
    assert all(79 <= v <= 113 for v in velocities)
    mean_v = sum(velocities) / len(velocities)
    assert 87 <= mean_v <= 103, f"mean velocity {mean_v} should be near 95"

    # Passo real de 16a a 120bpm = 125ms; gate scala proporcional ao gate
    # medido a 174bpm (83-86ms), entao aqui o teto sobe (< 125ms sempre).
    step_ms_at_120bpm = 60_000.0 / 120.0 / 4.0
    durations_ms = sorted((n.end - n.start) * 1000 for n in hat.notes)
    assert all(d <= step_ms_at_120bpm + 1e-6 for d in durations_ms)
    median_ms = durations_ms[len(durations_ms) // 2]
    assert median_ms > step_ms_at_120bpm * 0.7, "gate should stay close to the full step"

    hat.notes.sort(key=lambda n: n.start)
    overlaps = sum(
        1 for i in range(len(hat.notes) - 1)
        if hat.notes[i].end > hat.notes[i + 1].start
    )
    assert overlaps == 0


def test_render_hat_elec_pattern_modes_change_density(tmp_path):
    src = _build_synthetic_source(tmp_path)

    counts = {}
    for mode in ("sixteenth", "gaps", "half_time"):
        plan = _build_plan(src)
        plan.elements[0] = _hat_elec_element(pattern_mode=mode)
        out = tmp_path / f"hat_{mode}.mid"
        render(plan, out)
        out_pm = pretty_midi.PrettyMIDI(str(out))
        hat = next(i for i in out_pm.instruments if i.name and "hat_elec" in i.name)
        counts[mode] = len(hat.notes)

    assert counts["sixteenth"] == 128
    assert counts["gaps"] == 96          # 3 de cada 4 steps ativos * 8 bars * 16
    assert counts["half_time"] == 64     # metade da densidade continua


def test_render_hat_elec_rejects_unknown_pattern_mode(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _hat_elec_element(pattern_mode="triplet")
    out = tmp_path / "out.mid"
    with pytest.raises(ValueError, match="pattern_mode"):
        render(plan, out)


def _build_source_with_kick(tmp_path: Path, name: str = "source_kick.mid") -> Path:
    """Como `_build_synthetic_source`, mas com uma track de bateria com
    kick (nota 36) em cada beat — necessaria para `sub` `follow=kick`."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
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
                velocity=90, pitch=36, start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
        drums.notes.append(pretty_midi.Note(
            velocity=100, pitch=36, start=start, end=start + 0.1,
        ))
    pm.instruments.extend([piano, bass, drums])
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def test_render_sub_role_is_strictly_monophonic_never_a_chord(tmp_path):
    """AC (issue #22): 'Nunca gera acorde no sub — nota unica sempre, sem
    excecao nem flag'. Testa as tres modalidades de `follow`."""
    src = _build_synthetic_source(tmp_path)
    src_kick = _build_source_with_kick(tmp_path)
    for follow in ("tonic", "kick", "riff"):
        plan = _build_plan(src_kick if follow == "kick" else src)
        plan.elements[0] = _sub_element(follow=follow, degrees=[0, 3, 7])
        out = tmp_path / f"sub_{follow}.mid"
        report = render(plan, out)

        out_pm = pretty_midi.PrettyMIDI(str(out))
        sub = next(i for i in out_pm.instruments if i.name and "sub_main" in i.name)
        sub.notes.sort(key=lambda n: n.start)
        assert sub.notes, f"follow={follow} produced no notes"
        for i in range(len(sub.notes) - 1):
            assert sub.notes[i].end <= sub.notes[i + 1].start + 1e-6, (
                f"follow={follow}: overlapping notes -> would sound like a chord"
            )
        assert report.elements[0].rendered is True


def test_render_sub_role_accents_first_impact(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _sub_element(follow="tonic")
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    sub = next(i for i in out_pm.instruments if i.name and "sub_main" in i.name)
    sub.notes.sort(key=lambda n: n.start)
    assert len(sub.notes) >= 2
    first_vel = sub.notes[0].velocity
    later_vels = [n.velocity for n in sub.notes[1:]]
    assert first_vel > max(later_vels), (
        "first impact of the section should be louder than the repeats"
    )


def test_render_sub_drop_is_single_note_with_monotonic_pitch_bend(tmp_path):
    """AC (issue #22): evento pontual, nota unica, curva de pitch bend
    monotonica descendente."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _sub_drop_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    drop = next(i for i in out_pm.instruments if i.name and "sub_drop" in i.name)
    assert len(drop.notes) == 1, "sub_drop must always emit a single note"
    assert drop.notes[0].start == pytest.approx(0.0, abs=1e-6)

    bends = sorted(drop.pitch_bends, key=lambda b: b.time)
    assert len(bends) >= 3
    values = [b.pitch for b in bends]
    # O ultimo evento e o reset de canal para 0 (achado do Codex na review
    # pos-merge da PR #68): sem ele, todo evento seguinte no canal 0
    # continuaria desafinado ao maximo. A curva de descida em si (tudo
    # antes do reset) continua monotonica.
    descent = values[:-1]
    assert descent == sorted(descent, reverse=True), "pitch bend curve must be monotonic descending"
    assert descent[0] == 0
    assert descent[-1] == -8192
    assert values[-1] == 0, "pitch wheel must reset to center after the drop"
    assert report.elements[0].rendered is True


def test_notes_to_track_orders_pitchwheel_before_note_on_on_the_same_tick():
    """Regressao do achado do Codex na review pos-merge da PR #68: com
    varias secoes, o drop anterior deixa o pitch bend em -8192 e o bend
    zerado do proximo drop pode compartilhar tick com o `note_on` dele.
    `events.sort()` numerava pitchwheel como kind=3 e note_on como kind=2
    — como 3 > 2, o note_on saia ANTES do reset no mesmo tick, e o drop
    seguinte atacava com o bend ja no maximo. O comentario no codigo ja
    especifica a ordem pretendida (pitchwheel antes de note_on no mesmo
    tick); este teste crava essa ordem no MIDI escrito."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    track = _notes_to_track(
        [PadNote(pitch=24, velocity=100, start_s=1.0, end_s=1.5)],
        pm,
        "Same Tick Bend",
        channel=0,
        pitch_bend_events=[(1.0, 0)],
    )

    msgs_at_note_on_tick = []
    tick = 0
    for msg in track:
        tick += msg.time
        if msg.type in {"note_on", "pitchwheel"}:
            msgs_at_note_on_tick.append((tick, msg.type))

    note_on_tick = next(t for t, kind in msgs_at_note_on_tick if kind == "note_on")
    same_tick = [kind for t, kind in msgs_at_note_on_tick if t == note_on_tick]
    assert same_tick == ["pitchwheel", "note_on"], (
        "pitchwheel reset must be written before note_on on the same tick"
    )


def test_render_supports_motor_role(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _motor_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1


def test_render_motor_emits_cc74(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _motor_element()
    out = tmp_path / "out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    motor_tr = mid.tracks[-1]
    cc74 = [m for m in motor_tr if m.type == "control_change" and m.control == 74]
    assert cc74, "no CC74 (filter) events emitted"


def test_render_motor_multiple_layers(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _motor_element(layers=2)
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 2


def test_render_motor_passes_harmony_and_placement_validators(tmp_path):
    """AC: 'Render com motor... passa nos validadores harmonico e de
    placement'."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _motor_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)
    from tools.validators.harmony import has_errors as harmony_has_errors
    from tools.validators.placement import has_errors as placement_has_errors
    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]


def test_render_motor_custom_steps_from_pattern(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    element = _motor_element()
    # gap 3 steps (6-8), subdivision sixteenth
    element.pattern["custom_steps"] = (
        1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1,
    )
    plan.elements[0] = element
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True


def test_render_supports_shadow_role(tmp_path):
    src = _build_source_with_guitar(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _shadow_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1


def test_render_shadow_multiple_layers(tmp_path):
    src = _build_source_with_guitar(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _shadow_element(layers=2)
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert len(out_pm.instruments) == len(src_pm.instruments) + 2


def test_render_shadow_passes_harmony_and_placement_validators(tmp_path):
    """AC: 'Render com... shadow passa nos validadores harmonico e de
    placement'."""
    src = _build_source_with_guitar(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _shadow_element()
    out = tmp_path / "out.mid"
    report = render(plan, out)
    from tools.validators.harmony import has_errors as harmony_has_errors
    from tools.validators.placement import has_errors as placement_has_errors
    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]


def test_render_shadow_octave_shift_from_pattern(tmp_path):
    src = _build_source_with_guitar(tmp_path)
    plan = _build_plan(src)
    element = _shadow_element()
    element.pattern["octave_shift"] = -12
    plan.elements[0] = element
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert report.elements[0].rendered is True


# --- US-004: barreira de autorizacao no render ------------------------------

@pytest.mark.parametrize(
    ("family", "authorized", "declared"),
    [
        ("drums", "drums.ghost_notes", "drums.flam"),
        ("bass", "bass.ghost_notes", "bass.palm_mute"),
        ("guitar", "guitar.palm_mute", "guitar.bend"),
        ("keys", "keys.hand_asynchrony", "keys.rolled_chord"),
    ],
)
def test_render_refuses_unauthorized_style_technique_per_family(
    tmp_path, family, authorized, declared,
):
    """AC US-004: render recusa tecnica fora de `authorized_techniques` para
    cada uma das quatro familias — RenderError explicito citando familia e
    tecnica, sem arquivo de saida."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {
        family: FamilyStyle(
            reference="Research",
            researched_at="2026-08-24",
            sources=["https://example.test/style"],
            confidence="high",
            techniques=[StyleTechnique(name=declared)],
            parameters={},
        ),
    }
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({"style": {family: {"authorized_techniques": [authorized]}}}),
        encoding="utf-8",
    )
    plan.brief_ref = BriefRef(
        path=str(brief_path), sha256=brief_sha256(brief_path),
    )
    out = tmp_path / "out.mid"
    with pytest.raises(RenderError) as excinfo:
        render(plan, out)
    msg = str(excinfo.value)
    assert f"style.{family}.techniques[0].name" in msg
    assert declared in msg
    assert family in msg
    assert not out.exists(), (
        "unauthorized technique must not produce an output file"
    )


def test_authorization_is_what_makes_the_difference_in_the_output(tmp_path):
    """AC US-004, em forma DIFERENCIAL: a autorizacao tem que ser a unica
    coisa que muda entre sair ornamentado e nao sair.

    A versao anterior deste teste zerava `plan.elements` e nao declarava
    tecnica nenhuma — passava mesmo que o render ignorasse
    `authorized_techniques` por completo. Teste que passa com a barreira
    desligada nao testa barreira. Aqui o MESMO plano roda duas vezes,
    mudando so o brief:

    - brief AUTORIZA `drums.ghost_notes` -> saida ganha ornamento
    - brief com `authorized_techniques: []` -> render RECUSA, e o arquivo
      de saida nem chega a existir
    """
    # Fonte propria: a sintetica compartilhada nao tem bateria no canal 9,
    # entao `drums.ghost_notes` nao teria backbeat para ornamentar e os DOIS
    # lados do teste sairiam iguais por falta de material, nao por barreira.
    src = tmp_path / "drums_source.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    previous = 0
    for tick in (480, 1440, 2400, 3360, 4320, 5280, 6240, 7200):
        track.append(mido.Message(
            "note_on", note=38, velocity=100, channel=9, time=tick - previous,
        ))
        track.append(mido.Message(
            "note_off", note=38, velocity=0, channel=9, time=60,
        ))
        previous = tick + 60
    mid.tracks.append(track)
    mid.save(str(src))

    def brief_with(authorized: list[str]) -> BriefRef:
        path = tmp_path / f"brief_{len(authorized)}.json"
        path.write_text(
            json.dumps({
                "style": {
                    fam: {
                        "authorized_techniques": (
                            authorized if fam == "drums" else []
                        ),
                    }
                    for fam in ("bass", "drums", "guitar", "keys")
                },
            }),
            encoding="utf-8",
        )
        return BriefRef(path=str(path), sha256=brief_sha256(path))

    def plan_declaring_ghost_notes(ref: BriefRef):
        plan = _build_plan(src)
        plan.brief_ref = ref
        plan.elements = []
        # A tecnica so alcanca track de origem que esteja em `plan.edits`.
        plan.edits = [PlanEdit(track="Drums", profile="drums", intensity=0.0)]
        plan.style = {
            "drums": FamilyStyle(
                reference="Research",
                researched_at="2026-08-24",
                sources=["https://example.test/drums"],
                confidence="high",
                techniques=[StyleTechnique(name="drums.ghost_notes")],
                parameters={},
            ),
        }
        return plan

    # 1) autorizado: renderiza e ornamenta
    autorizado = tmp_path / "autorizado.mid"
    render(
        plan_declaring_ghost_notes(brief_with(["drums.ghost_notes"])),
        autorizado,
    )
    assert autorizado.exists()

    def drum_note_count(path) -> int:
        mid = mido.MidiFile(str(path))
        return sum(
            1
            for tr in mid.tracks
            for msg in tr
            if msg.type == "note_on"
            and msg.velocity > 0
            and getattr(msg, "channel", -1) == 9
        )

    assert drum_note_count(autorizado) > drum_note_count(src), (
        "com a tecnica autorizada o render tem que acrescentar ornamento — "
        "sem isso o outro lado do teste nao prova nada"
    )

    # 2) nao autorizado: MESMO plano, so o brief muda -> recusa
    negado = tmp_path / "negado.mid"
    with pytest.raises(RenderError) as exc:
        render(plan_declaring_ghost_notes(brief_with([])), negado)
    assert "drums.ghost_notes" in str(exc.value)
    assert not negado.exists(), (
        "render recusado nao pode deixar arquivo de saida para tras"
    )


def test_render_refuses_style_technique_without_brief_ref(tmp_path):
    """AC US-004: sem `brief_ref` a barreira do render tambem age como
    `RenderError` — nao como `PlanValidationError` — porque render e a
    ultima linha de defesa."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {
        "drums": FamilyStyle(
            reference="Research",
            researched_at="2026-08-24",
            sources=["https://example.test/style"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    plan.brief_ref = None
    out = tmp_path / "out.mid"
    with pytest.raises(RenderError) as excinfo:
        render(plan, out)
    msg = str(excinfo.value)
    assert "brief_ref" in msg
    assert "drums" in msg
    assert not out.exists()


def test_render_refuses_when_brief_sha256_mismatches(tmp_path):
    """AC US-004: brief editado apos aprovacao (sha divergente) tambem para
    o render com `RenderError`."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.style = {
        "drums": FamilyStyle(
            reference="Research",
            researched_at="2026-08-24",
            sources=["https://example.test/style"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({
            "style": {"drums": {"authorized_techniques": ["drums.ghost_notes"]}},
        }),
        encoding="utf-8",
    )
    plan.brief_ref = BriefRef(
        path=str(brief_path),
        sha256="0" * 64,  # sha propositalmente errado
    )
    out = tmp_path / "out.mid"
    with pytest.raises(RenderError, match="brief_ref.sha256"):
        render(plan, out)
    assert not out.exists()


# --- issue #44 / PR #64 (achado P1) — instruments alimenta o pipeline ------

def _build_low_bass_source(tmp_path: Path) -> Path:
    """Baixo em pitch 24 — abaixo do piso da afinacao PADRAO de 4 cordas
    (28), mas dentro do piso de uma afinacao de 5 cordas em B (23)."""
    src = tmp_path / "low_bass_source.mid"
    mid = mido.MidiFile(ticks_per_beat=480, type=1)
    tempo_track = mido.MidiTrack()
    tempo_track.append(
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0)
    )
    mid.tracks.append(tempo_track)

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    previous = 0
    for tick in (0, 960, 1920, 2880, 3840, 4800, 5760, 6720):
        track.append(mido.Message(
            "note_on", note=24, velocity=90, channel=1, time=tick - previous,
        ))
        track.append(mido.Message(
            "note_off", note=24, velocity=0, channel=1, time=480,
        ))
        previous = tick + 480
    mid.tracks.append(track)
    mid.save(str(src))
    return src


def _low_bass_plan(src: Path, ref: BriefRef) -> ArrangementPlan:
    plan = ArrangementPlan(
        version=1,
        seed=7,
        source_midi=SourceMidi(path=str(src), sha256=_sha256_bytes(src)),
        route="cinematica_emocional",
        sections=[],
        elements=[],
    )
    plan.brief_ref = ref
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.0)]
    plan.style = {
        "bass": FamilyStyle(
            reference="Research",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[StyleTechnique(name="bass.ghost_notes")],
            parameters={"density": 1.0},
        ),
    }
    return plan


def _low_bass_brief_ref(tmp_path: Path, instruments: dict | None) -> BriefRef:
    payload: dict = {
        "style": {
            fam: {
                "authorized_techniques": (
                    ["bass.ghost_notes"] if fam == "bass" else []
                ),
            }
            for fam in ("bass", "drums", "guitar", "keys")
        },
    }
    if instruments is not None:
        payload["instruments"] = instruments
    name = "brief_with_instruments.json" if instruments else "brief_bare.json"
    brief_path = tmp_path / name
    brief_path.write_text(json.dumps(payload), encoding="utf-8")
    return BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))


def test_render_uses_declared_instrument_tuning_for_physical_plausibility(
    tmp_path,
):
    """Achado P1 do PR #64: `brief.instruments.<familia>.tuning` (issue #44)
    tem que alimentar `TechniqueContext.parameters["tuning"]` de verdade —
    antes desta correcao, `instruments` era validado e ignorado (o
    "parametro mentiroso" que o AGENTS.md proibe).

    Baixo escrito em pitch 24 (abaixo do piso da afinacao PADRAO de 4
    cordas, 28) so pode ganhar ornamento de `bass.ghost_notes` (que herda o
    pitch da nota estrutural anterior) se a afinacao de 5 cordas em B
    (piso 23) declarada no brief realmente chegar ao validador fisico.
    """
    src = _build_low_bass_source(tmp_path)

    # Sem `instruments` no brief: cai no default fisico (piso 28) e o
    # motor recusa o ornamento em pitch 24.
    bare_ref = _low_bass_brief_ref(tmp_path, None)
    out_bare = tmp_path / "out_bare.mid"
    with pytest.raises(RenderError, match="afinacao declarada"):
        render(_low_bass_plan(src, bare_ref), out_bare)
    assert not out_bare.exists()

    # Com `instruments.bass` declarando 5 cordas em B (piso 23): o mesmo
    # ornamento passa, e a saida ganha nota nova.
    declared_ref = _low_bass_brief_ref(tmp_path, {
        "bass": {
            "known": True,
            "strings": 5,
            "tuning": {"name": None, "notes": [23, 28, 33, 38, 43]},
            "playing_style": "finger",
            "notation": "sounding",
        },
    })
    out_declared = tmp_path / "out_declared.mid"
    render(_low_bass_plan(src, declared_ref), out_declared)
    assert out_declared.exists()

    def bass_note_count(path: Path) -> int:
        m = mido.MidiFile(str(path))
        return sum(
            1
            for tr in m.tracks
            for msg in tr
            if msg.type == "note_on"
            and msg.velocity > 0
            and getattr(msg, "channel", -1) == 1
        )

    assert bass_note_count(out_declared) > bass_note_count(src), (
        "com a afinacao declarada o motor tem que acrescentar ornamento — "
        "sem isso o outro lado do teste nao prova nada"
    )


# --- edit.tool resolve receita especifica de tecnica (achado real) ---------
#
# Sem `edit.tool`, `_apply_style_techniques_to_edit_tracks` passava
# `tool_target=None` incondicionalmente — a track de `plan.edits` NUNCA
# conseguia pedir a receita `modo_bass`, so a `generic`. Para
# `bass.attack_style`, a receita `generic` nao tem `keyswitch_dedo`
# nenhum: a funcao le `recipe.get(style_key)`, acha `None` e devolve o
# MIDI sem tocar em nada — o keyswitch que diz ao MODO BASS pra tocar
# com dedo nunca era inserido, apesar da tecnica estar "aplicada" sem
# erro nenhum. Achado real, numa musica de verdade, com brief pedindo
# "fingers" explicitamente.

def _bass_attack_style_plan(source: Path, *, tool: str | None) -> ArrangementPlan:
    plan = _build_plan(source)
    plan.elements = []  # so testamos o caminho de edits aqui
    plan.edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.5, tool=tool),
    ]
    plan.style = {
        "bass": FamilyStyle(
            reference="Baixo com fingers",
            researched_at="2026-09-01",
            sources=["teste"],
            confidence="medium",
            techniques=[StyleTechnique(
                name="bass.attack_style", density=1.0,
                rationale="fingers", style="dedo",
            )],
            parameters={},
        ),
    }
    return plan


def _has_note_on(path: Path, pitch: int) -> bool:
    m = mido.MidiFile(str(path))
    return any(
        msg.type == "note_on" and msg.velocity > 0 and msg.note == pitch
        for tr in m.tracks for msg in tr
    )


def test_edit_tool_resolves_modo_bass_recipe_and_inserts_keyswitch(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _bass_attack_style_plan(src, tool="MODO Bass")
    _attach_brief_authorizing_techniques(plan, tmp_path)
    out = tmp_path / "out.mid"

    render(plan, out)

    # keyswitch_dedo = 13 no manual (tecnicas_baixo_midi.md, bass.attack_style,
    # tools.modo_bass) — so aparece quando a receita `modo_bass` foi resolvida.
    assert _has_note_on(out, 13), (
        "com edit.tool='MODO Bass', bass.attack_style tem que inserir o "
        "keyswitch_dedo (13) que diz ao plugin para tocar com dedo"
    )


def test_edit_without_tool_falls_back_to_generic_without_keyswitch(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _bass_attack_style_plan(src, tool=None)
    _attach_brief_authorizing_techniques(plan, tmp_path)
    out = tmp_path / "out.mid"

    render(plan, out)

    # Documenta o comportamento correto do fallback: sem `tool` declarado,
    # a receita e `generic` (sem keyswitch) por design — nao e erro, so
    # nao ha ferramenta especifica pedida. Continua sem keyswitch 13.
    assert not _has_note_on(out, 13)


def _build_bass_string_switch_source(tmp_path: Path) -> Path:
    """Baixo com riff em duas cordas: metade em pitch alcancavel so pela
    corda E (padrao 4 cordas), metade so pela corda D — pra exercitar
    bass.string_selection de ponta a ponta pelo pipeline de render real."""
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
        # 4 primeiros compassos: pitch 30, so alcancavel pela corda E
        # (28..52 com max_fret=24). Ultimos 4: pitch 60, so alcancavel pela
        # corda D (38..62; a corda E para em 52, nao alcanca).
        pitch = 30 if bar < 4 else 60
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=pitch, start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.append(piano)
    pm.instruments.append(bass)
    dest = tmp_path / "bass_string_switch.mid"
    pm.write(str(dest))
    return dest


def _bass_string_selection_brief_ref(tmp_path: Path) -> BriefRef:
    """Brief autorizando bass.string_selection e declarando a afinacao
    padrao de 4 cordas (E-A-D-G) via instruments.bass.tuning — mesmo
    caminho (`tools.plan.load_brief_instrument_tuning`) que ja alimenta
    `TechniqueContext.parameters["tuning"]` pra elemento gerado e edit."""
    payload = {
        "style": {
            fam: {
                "authorized_techniques": (
                    ["bass.string_selection"] if fam == "bass" else []
                ),
            }
            for fam in ("bass", "drums", "guitar", "keys")
        },
        "instruments": {
            "bass": {
                "known": True,
                "strings": 4,
                "tuning": {"name": "Standard", "notes": [28, 33, 38, 43]},
                "playing_style": "finger",
                "notation": "sounding",
            },
        },
    }
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps(payload), encoding="utf-8")
    return BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))


def test_bass_string_selection_reachable_end_to_end_via_real_render(tmp_path):
    """Teste de integracao pedido em revisao humana na PR: exercita o
    caminho REAL de render (nao `apply_technique` direto) com
    `instruments.bass.tuning` declarado no brief, `bass.string_selection`
    autorizada e aplicada via `plan.edits[].tool="MODO Bass"`, confirmando
    que a saida carrega os keyswitches de corda esperados pelo manual
    (tecnicas_baixo_midi.md, secao 5.9) para cada trecho do riff."""
    src = _build_bass_string_switch_source(tmp_path)
    plan = _build_plan(src)
    plan.elements = []
    plan.edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.0, tool="MODO Bass"),
    ]
    plan.style = {
        "bass": FamilyStyle(
            reference="Baixo em drop, riff trocando de corda",
            researched_at="2026-09-01",
            sources=["teste"],
            confidence="medium",
            techniques=[StyleTechnique(name="bass.string_selection")],
            parameters={},
        ),
    }
    plan.brief_ref = _bass_string_selection_brief_ref(tmp_path)
    out = tmp_path / "out.mid"

    render(plan, out)

    # keyswitch_corda_E = 16, keyswitch_corda_D = 14 no manual
    # (tecnicas_baixo_midi.md, secao 5.9, tools.modo_bass) — os dois
    # precisam aparecer, um pra cada metade do riff que trocou de corda.
    assert _has_note_on(out, 16), (
        "riff nos primeiros 4 compassos (pitch 30) so alcanca a corda E "
        "com a afinacao declarada — keyswitch_corda_E (16) precisa sair"
    )
    assert _has_note_on(out, 14), (
        "riff nos ultimos 4 compassos (pitch 60) so alcanca a corda D "
        "com a afinacao declarada — keyswitch_corda_D (14) precisa sair"
    )


def test_edit_tool_modo_bass_palm_mute_reports_generic_fallback(tmp_path):
    """Render nao promete a curva CC que o MODO BASS nao documenta."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements = []
    plan.edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.5, tool="MODO Bass"),
    ]
    plan.style = {
        "bass": FamilyStyle(
            reference="Baixo com palm mute",
            researched_at="2026-09-01",
            sources=["teste"],
            confidence="medium",
            techniques=[StyleTechnique(
                name="bass.palm_mute", density=1.0, rationale="mute",
            )],
            parameters={},
        ),
    }
    _attach_brief_authorizing_techniques(plan, tmp_path)
    out = tmp_path / "out.mid"

    report = render(plan, out)

    assert any("W_NO_TOOL_RECIPE" in warning for warning in report.warnings)
