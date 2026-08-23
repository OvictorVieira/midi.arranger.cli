"""Regressoes para campos `element.pattern` consumidos pelo renderer."""

from __future__ import annotations

from pathlib import Path

import mido

from tests.test_render import _build_plan, _build_synthetic_source, _strings_element
from tools.render import render


def _ghost_note_on_count(path: Path) -> int:
    mid = mido.MidiFile(str(path))
    return sum(
        1
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and 0 < msg.velocity < 40
    )


def test_render_strings_ghost_ratio_from_pattern_changes_output(tmp_path):
    src = _build_synthetic_source(tmp_path)

    zero_plan = _build_plan(src)
    zero = _strings_element(role="strings", layers=5)
    zero.pattern = {"ghost_ratio": 0.0}
    zero_plan.elements[0] = zero
    zero_out = tmp_path / "zero.mid"
    render(zero_plan, zero_out)

    high_plan = _build_plan(src)
    high = _strings_element(role="strings", layers=5)
    high.pattern = {"ghost_ratio": 1.0}
    high_plan.elements[0] = high
    high_out = tmp_path / "high.mid"
    render(high_plan, high_out)

    assert _ghost_note_on_count(zero_out) == 0
    assert _ghost_note_on_count(high_out) > 0


def test_render_warns_when_pattern_field_is_not_supported(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    strings = _strings_element(role="strings", layers=3)
    strings.pattern = {"voices": 5}
    plan.elements[0] = strings

    report = render(plan, tmp_path / "out.mid")

    assert any(
        "strings_main: element.pattern.voices" in warning
        and "ignored" in warning
        for warning in report.warnings
    )
