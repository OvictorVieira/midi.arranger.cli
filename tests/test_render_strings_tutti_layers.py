"""Regressao para strings tutti com layers acima do limite do gerador."""

from __future__ import annotations

import mido

from tests.test_render import _build_plan, _build_synthetic_source, _strings_element
from tools.palette.harmonic import STRINGS_TUTTI_MAX_VOICES
from tools.render import render


def _note_on_count(track: mido.MidiTrack) -> int:
    return sum(
        1
        for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    )


def test_render_strings_tutti_caps_layers_without_empty_tracks(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _strings_element(role="strings", tutti=True, layers=12)

    out = tmp_path / "out.mid"
    report = render(plan, out)

    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(str(out), charset="utf-8")
    emitted_tracks = out_mid.tracks[len(src_mid.tracks):]

    assert len(emitted_tracks) == STRINGS_TUTTI_MAX_VOICES
    assert all(_note_on_count(track) > 0 for track in emitted_tracks)
    assert any(
        "strings_main: element.layers=12 reduced to 8" in warning
        for warning in report.warnings
    )
