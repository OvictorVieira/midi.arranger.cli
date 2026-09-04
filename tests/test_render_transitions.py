"""Testes de integracao do render para os roles de transicao (issue #23):
riser, downer, impact, reverse — dispatch em `_ROLE_RENDERERS`, fronteira
de secao correta no MIDI final, `plan.transitions[].elements` (filtro
`only="transitions"`) e avisos de `element.pattern` nao suportado.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pretty_midi
import pytest

from tools.plan import ArrangementPlan, Element, PlanSection, SourceMidi, Transition
from tools.render import SUPPORTED_ROLES, render


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_source(tmp_path: Path) -> Path:
    """8 compassos (0-8) em 4/4 a 120bpm (bar_len=2s); secao A = bars 0-4,
    secao B = bars 4-8 — a fronteira A->B cai em t=8.0s."""
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
    pm.instruments.extend([piano, bass])
    dest = tmp_path / "source.mid"
    pm.write(str(dest))
    return dest


def _sections() -> list[PlanSection]:
    return [
        PlanSection(
            label="A", kind="verse", start_bar=0, end_bar=4, source="marker",
            protagonist="texture",
            energy={"densidade": 4, "impacto": 4, "largura": 4, "altura": 4, "instabilidade": 2},
        ),
        PlanSection(
            label="B", kind="chorus", start_bar=4, end_bar=8, source="marker",
            protagonist="texture",
            energy={"densidade": 8, "impacto": 8, "largura": 7, "altura": 7, "instabilidade": 6},
        ),
    ]


def _instrument(plugin: str, preset: str) -> dict:
    return {"plugin": plugin, "preset": preset, "verified": True}


def _build_plan(
    source: Path, elements: list[Element], *, transitions: list[Transition] | None = None,
) -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=11,
        source_midi=SourceMidi(path=str(source), sha256=_sha256_bytes(source)),
        route="hook_eletronico_pesado",
        sections=_sections(),
        elements=elements,
        transitions=transitions or [],
    )


# --- dispatch ---------------------------------------------------------------

def test_all_four_transition_roles_are_registered():
    assert {"riser", "downer", "impact", "reverse"} <= SUPPORTED_ROLES


# --- riser: termina antes do downbeat de B ----------------------------------

def test_riser_element_ends_before_section_b_downbeat(tmp_path):
    src = _build_source(tmp_path)
    element = Element(
        id="riser_to_b", role="riser", sections=["B"], register=[48, 84], layers=1,
        sync_role="response", articulation="sustained", harmony="free",
        instrument=_instrument("Serum", "Riser FX"),
        rationale="Riser builds tension into the chorus (section B).",
    )
    plan = _build_plan(src, [element])
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert any(e.element_id == "riser_to_b" and e.rendered for e in report.elements)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    riser_track = next(i for i in out_pm.instruments if "riser_to_b" in (i.name or ""))
    assert riser_track.notes
    boundary_s = 8.0  # bar 4 * 2.0s
    assert max(n.end for n in riser_track.notes) < boundary_s
    for cc in riser_track.control_changes:
        assert cc.time < boundary_s


# --- impact: hit alinhado exatamente no downbeat de B -----------------------

def test_impact_element_hits_exactly_at_section_b_downbeat(tmp_path):
    src = _build_source(tmp_path)
    element = Element(
        id="impact_b", role="impact", sections=["B"], register=[24, 84], layers=1,
        sync_role="exact_anchor", articulation="staccato", harmony="percussion",
        instrument=_instrument("Logic Sampler", "Impact Hit"),
        rationale="Impact marks the arrival of the chorus (section B).",
    )
    plan = _build_plan(src, [element])
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    impact_track = next(i for i in out_pm.instruments if "impact_b" in (i.name or ""))
    boundary_s = 8.0
    assert len(impact_track.notes) >= 2
    assert {round(n.start, 6) for n in impact_track.notes} == {boundary_s}
    tails = {round(n.end - n.start, 3) for n in impact_track.notes}
    assert len(tails) == len(impact_track.notes)


# --- reverse: resolve exatamente no downbeat de B ---------------------------

def test_reverse_element_resolves_exactly_at_section_b_downbeat(tmp_path):
    src = _build_source(tmp_path)
    element = Element(
        id="reverse_b", role="reverse", sections=["B"], register=[48, 72], layers=1,
        sync_role="response", articulation="sustained", harmony="free",
        instrument=_instrument("Omnisphere", "Reverse Swell"),
        rationale="Reverse swell resolves into the chorus (section B).",
    )
    plan = _build_plan(src, [element])
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    reverse_track = next(i for i in out_pm.instruments if "reverse_b" in (i.name or ""))
    boundary_s = 8.0
    assert len(reverse_track.notes) == 1
    assert round(reverse_track.notes[0].end, 6) == boundary_s
    assert reverse_track.control_changes
    last_cc_time = max(cc.time for cc in reverse_track.control_changes)
    assert round(last_cc_time, 6) == boundary_s


def test_reverse_freeze_pattern_field_is_honored(tmp_path):
    src = _build_source(tmp_path)
    element = Element(
        id="reverse_freeze", role="reverse", sections=["B"], register=[20, 100], layers=1,
        sync_role="response", articulation="sustained", harmony="free",
        pattern={"freeze_pitch": 67, "freeze_velocity": 101},
        instrument=_instrument("Omnisphere", "Reverse Freeze"),
        rationale="Freezes and reverses the last note of section A as the swell source.",
    )
    plan = _build_plan(src, [element])
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    track = next(i for i in out_pm.instruments if "reverse_freeze" in (i.name or ""))
    assert track.notes[0].pitch == 67
    assert track.notes[0].velocity == 101


# --- downer: mesma fronteira, direcao invertida -----------------------------

def test_downer_element_ends_before_section_b_downbeat(tmp_path):
    src = _build_source(tmp_path)
    element = Element(
        id="downer_to_b", role="downer", sections=["B"], register=[48, 84], layers=1,
        sync_role="response", articulation="sustained", harmony="free",
        instrument=_instrument("Omnisphere", "Downer FX"),
        rationale="Downer falls into the chorus (section B).",
    )
    plan = _build_plan(src, [element])
    out = tmp_path / "out.mid"
    render(plan, out)

    out_pm = pretty_midi.PrettyMIDI(str(out))
    track = next(i for i in out_pm.instruments if "downer_to_b" in (i.name or ""))
    boundary_s = 8.0
    assert max(n.end for n in track.notes) < boundary_s
    filt = [cc.value for cc in sorted(track.control_changes, key=lambda c: c.time) if cc.number == 74]
    assert filt == sorted(filt, reverse=True)


# --- avisos de pattern nao suportado ----------------------------------------

def test_unsupported_pattern_field_on_impact_is_warned(tmp_path):
    src = _build_source(tmp_path)
    element = Element(
        id="impact_b", role="impact", sections=["B"], register=[24, 84], layers=1,
        sync_role="exact_anchor", articulation="staccato", harmony="percussion",
        pattern={"totally_unsupported_field": True},
        instrument=_instrument("Logic Sampler", "Impact Hit"),
        rationale="Impact marks the arrival of the chorus (section B).",
    )
    plan = _build_plan(src, [element])
    out = tmp_path / "out.mid"
    report = render(plan, out)
    assert any(
        "totally_unsupported_field" in w and "not supported for role 'impact'" in w
        for w in report.warnings
    )


# --- plan.transitions[].elements + filtro only="transitions" ---------------

def test_only_transitions_filter_selects_declared_transition_elements(tmp_path):
    src = _build_source(tmp_path)
    riser = Element(
        id="riser_to_b", role="riser", sections=["B"], register=[48, 84], layers=1,
        sync_role="response", articulation="sustained", harmony="free",
        instrument=_instrument("Serum", "Riser FX"),
        rationale="Riser builds tension into the chorus (section B).",
    )
    pad = Element(
        id="pad_bg", role="pad", sections=["A", "B"], register=[48, 71], layers=1,
        sync_role="sustain_through", articulation="sustained", harmony="follow_chords",
        instrument=_instrument("Omnisphere", "Pad"),
        rationale="Background pad, not part of the transition.",
    )
    transition = Transition(
        at_bar=4, from_section="A", to_section="B",
        dimensions_changed=["densidade", "registro"],
        elements=["riser_to_b"], technique="riser",
    )
    plan = _build_plan(src, [riser, pad], transitions=[transition])
    out = tmp_path / "out.mid"
    report = render(plan, out, only="transitions")
    rendered_ids = {e.element_id for e in report.elements if e.rendered}
    assert rendered_ids == {"riser_to_b"}


@pytest.mark.parametrize("role", ["riser", "downer", "impact", "reverse"])
def test_transition_roles_are_deterministic_byte_for_byte(tmp_path, role):
    src = _build_source(tmp_path)
    element = Element(
        id=f"{role}_b", role=role, sections=["B"],
        register=[24, 84] if role == "impact" else [48, 84],
        layers=1,
        sync_role="exact_anchor" if role == "impact" else "response",
        articulation="staccato" if role == "impact" else "sustained",
        harmony="percussion" if role == "impact" else "free",
        instrument=_instrument("Logic Sampler", "Preset"),
        rationale=f"Deterministic check for role {role}.",
    )
    out1 = tmp_path / "out1.mid"
    out2 = tmp_path / "out2.mid"
    render(_build_plan(src, [element]), out1)
    render(_build_plan(src, [element]), out2)
    assert out1.read_bytes() == out2.read_bytes()
