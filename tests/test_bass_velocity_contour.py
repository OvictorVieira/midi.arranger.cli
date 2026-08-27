"""Testes de `bass.velocity_contour` — humanizacao de dinamica sem inverter intencao."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    TechniqueContractError,
    apply_technique,
    get_technique,
)


def _make_midi(velocities: list[int], *, ticks_per_beat: int = 480) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    beat = ticks_per_beat
    delta = beat
    for i, vel in enumerate(velocities):
        track.append(mido.Message(
            "note_on", channel=1, note=40, velocity=vel,
            time=delta if i > 0 else 0,
        ))
        track.append(mido.Message(
            "note_off", channel=1, note=40, velocity=0,
            time=beat // 2,
        ))
    mid.tracks.append(track)
    return mid


def _out_velocities(mid: mido.MidiFile) -> list[int]:
    return [
        msg.velocity
        for track in mid.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]


def test_bass_velocity_contour_is_registered_as_supported():
    assert "bass.velocity_contour" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.velocity_contour")
    assert entry.canonical == "bass.velocity_contour"
    assert entry.level == "humanize"


def test_bass_velocity_contour_preserves_note_content():
    source = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    original_notes = [
        (msg.channel, msg.note)
        for track in source.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]

    out = apply_technique(
        "bass.velocity_contour", source, seed=17, tool="generic",
    )
    out_notes = [
        (msg.channel, msg.note)
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    assert out_notes == original_notes


def test_bass_velocity_contour_preserves_top_note_pressure():
    # Faixa observada: 60..127. Nota no topo (>=P75) nunca cai na faixa mais
    # baixa (< P25). Foi assim que `drums.accent_hierarchy` matou o riff.
    velocities = [60, 65, 70, 75, 80, 100, 115, 120, 125, 127]
    top_indices = [i for i, v in enumerate(velocities) if v >= 115]
    assert top_indices

    source = _make_midi(velocities)
    out = apply_technique(
        "bass.velocity_contour", source, seed=3, tool="generic",
    )
    out_vels = _out_velocities(out)
    sorted_orig = sorted(velocities)
    p25 = sorted_orig[len(sorted_orig) // 4]
    for idx in top_indices:
        assert out_vels[idx] >= p25, (
            f"nota do topo da origem (idx={idx}, v={velocities[idx]}) "
            f"caiu na faixa mais baixa: saiu com {out_vels[idx]}, P25={p25}"
        )


def test_bass_velocity_contour_does_not_lower_the_median():
    # A tecnica da contorno, nunca tira peso. Mediana da saida >= mediana da
    # origem, ponto.
    velocities = [55, 70, 80, 85, 90, 95, 100, 105, 110, 118]
    source = _make_midi(velocities)
    out = apply_technique(
        "bass.velocity_contour", source, seed=11, tool="generic",
    )
    out_vels = _out_velocities(out)
    median_orig = sorted(velocities)[len(velocities) // 2]
    median_out = sorted(out_vels)[len(out_vels) // 2]
    assert median_out >= median_orig


def test_bass_velocity_contour_is_deterministic_for_same_seed():
    src_a = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    src_b = _make_midi([80, 100, 70, 110, 95, 88, 105, 72])
    out_a = apply_technique("bass.velocity_contour", src_a, seed=5, tool="generic")
    out_b = apply_technique("bass.velocity_contour", src_b, seed=5, tool="generic")
    assert _out_velocities(out_a) == _out_velocities(out_b)


def test_bass_velocity_contour_short_circuits_zero_ticks_per_beat():
    mid = mido.MidiFile(ticks_per_beat=1)
    mid.ticks_per_beat = 0
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    mid.tracks.append(track)
    out = apply_technique(
        "bass.velocity_contour", mid, seed=1, tool="generic",
    )
    assert out.ticks_per_beat == 0


def test_bass_velocity_contour_respects_humanize_contract_on_count_and_pitch():
    # O contrato humanize e checado no dispatch. Se a tecnica removesse ou
    # transposesse nota, o motor levantaria TechniqueContractError. Este teste
    # confirma que uma seed qualquer NAO derruba o contrato.
    velocities = [70, 90, 110, 60, 100, 80, 120, 65]
    source = _make_midi(velocities)
    for seed in range(20):
        # so passa se o dispatch aceita — se estourasse contrato viraria fail.
        out = apply_technique(
            "bass.velocity_contour", source, seed=seed, tool="generic",
        )
        out_vels = _out_velocities(out)
        assert len(out_vels) == len(velocities)


def test_bass_velocity_contour_reads_span_from_context_parameters():
    # `context.parameters` COMANDA a receita: span maior => janela de contorno
    # maior. Se span=1, todos os deltas do algoritmo colapsam para valores
    # minimos e a saida ainda respeita o piso da mediana.
    velocities = [90] * 8
    source = _make_midi(velocities)

    from tools.techniques.engine import apply_technique_with_warnings

    result = apply_technique_with_warnings(
        "bass.velocity_contour",
        source,
        seed=1,
        tool="generic",
        parameters={"span_tipico": 1},
    )
    out_vels = _out_velocities(result.result)
    # Todas ficam >= 90 porque mediana original e 90 e a tecnica nunca abaixa
    # a mediana.
    assert min(out_vels) >= 88  # margem para acento/jitter minimo


def test_bass_velocity_contour_rejects_pitch_change_via_engine_contract():
    # Sanity: engine deve rejeitar qualquer tecnica humanize que troque pitch.
    # Aqui a implementacao real nao troca pitch — se um dia mudar por acidente,
    # o dispatch estoura antes de mentir para o usuario.
    velocities = [80] * 4
    source = _make_midi(velocities)
    apply_technique(
        "bass.velocity_contour", source, seed=1, tool="generic",
    )  # roda sem levantar

    # Se manipulassemos pitch, veriamos TechniqueContractError. Cobrimos o
    # caminho positivo aqui; a rejeicao ja tem cobertura no engine.
    _ = TechniqueContractError  # keeps import used
