"""Regressoes para validacao de plano in-memory no render."""

from __future__ import annotations

import pretty_midi
import pytest

from tests.test_persona import _element, _empty_analysis, _plan
from tests.test_render import _build_plan, _build_synthetic_source
from tools.plan import PlanValidationError
from tools.render import (
    _quantize_rendered_note_seconds,
    _quantize_rendered_tracks,
    render,
)
from tools.validators.harmony import RenderedNote, RenderedTrack
from tools.validators.persona import validate_persona


def test_render_validates_in_memory_plan_before_pipeline(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.sections[0].energy = None

    with pytest.raises(PlanValidationError) as exc:
        render(plan, tmp_path / "out.mid")

    assert exc.value.path == "sections[0].energy"
    assert "missing energy" in exc.value.message


@pytest.mark.parametrize("energy", [None, {"impacto": 5}])
def test_persona_density_inversion_ignores_missing_energy_defensively(energy):
    plan = _plan([_element("pad_main")])
    plan.sections[0].energy = energy

    assert validate_persona(plan, [], _empty_analysis()) == []


# --- issue #126: quantizacao dos segundos "ideais" para o tick real -------
#
# O gerador de paleta calcula `start_s`/`end_s` diretamente em ponto
# flutuante (`bar_start_s + offset_beats * seconds_per_beat`), mas o arquivo
# MIDI so grava TICK inteiro. `_notes_to_track` ja arredonda com
# `int(round(pm.time_to_tick(...)))`; estes testes isolam a mesma conta sem
# precisar do pipeline inteiro de `render()`.

def _pm_120bpm_480ppq() -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)


def test_quantize_rendered_note_seconds_matches_notes_to_track_rounding():
    pm = _pm_120bpm_480ppq()
    # A 120 bpm, 480 ticks/beat: 1 tick = 0.5/480 s ~= 0.0010417s. Um valor
    # "ideal" a meio caminho entre dois ticks tem que arredondar para o
    # MESMO tick que `int(round(pm.time_to_tick(...)))` produziria — a
    # mesma formula que grava o arquivo final.
    ideal_start = 1.9994  # um pouco antes do tick 1919 (fronteira de compasso)
    ideal_end = 1.9998
    expected_start_tick = int(round(pm.time_to_tick(ideal_start)))
    expected_end_tick = int(round(pm.time_to_tick(ideal_end)))

    q_start, q_end = _quantize_rendered_note_seconds(ideal_start, ideal_end, pm)

    assert q_start == pytest.approx(pm.tick_to_time(expected_start_tick))
    assert q_end == pytest.approx(pm.tick_to_time(expected_end_tick))
    # A quantizacao muda o valor exato — e exatamente por isso que o
    # validador em memoria precisa dela para concordar com o arquivo.
    assert q_start != ideal_start or q_end != ideal_end


def test_quantize_rendered_note_seconds_never_produces_zero_length_note():
    pm = _pm_120bpm_480ppq()
    # start e end tao proximos que arredondam para o MESMO tick — a mesma
    # guarda que `_notes_to_track` aplica (`end_tick <= start_tick`) precisa
    # valer aqui tambem, senao a nota quantizada teria duracao zero.
    start_s = 1.00001
    end_s = 1.00002

    q_start, q_end = _quantize_rendered_note_seconds(start_s, end_s, pm)

    assert q_end > q_start


def test_quantize_rendered_note_seconds_is_idempotent():
    pm = _pm_120bpm_480ppq()
    q_start, q_end = _quantize_rendered_note_seconds(1.9994, 1.9998, pm)

    q2_start, q2_end = _quantize_rendered_note_seconds(q_start, q_end, pm)

    assert q2_start == pytest.approx(q_start)
    assert q2_end == pytest.approx(q_end)


def test_quantize_rendered_tracks_preserves_pitch_velocity_and_track_identity():
    pm = _pm_120bpm_480ppq()
    track = RenderedTrack(
        element_id="baixo_1",
        track_name="Bass",
        is_drum=False,
        notes=(
            RenderedNote(pitch=40, velocity=90, start_s=1.9994, end_s=1.9998),
            RenderedNote(pitch=45, velocity=80, start_s=2.5001, end_s=2.9999),
        ),
    )

    quantized, = _quantize_rendered_tracks([track], pm)

    assert quantized.element_id == "baixo_1"
    assert quantized.track_name == "Bass"
    assert quantized.is_drum is False
    assert [n.pitch for n in quantized.notes] == [40, 45]
    assert [n.velocity for n in quantized.notes] == [90, 80]
    for original, updated in zip(track.notes, quantized.notes, strict=True):
        expected_start, expected_end = _quantize_rendered_note_seconds(
            original.start_s, original.end_s, pm,
        )
        assert updated.start_s == pytest.approx(expected_start)
        assert updated.end_s == pytest.approx(expected_end)
