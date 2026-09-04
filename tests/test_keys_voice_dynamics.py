"""Testes de `keys.voice_dynamics` — a melodia e a voz mais forte do acorde.

O achado do manual (`tecnicas_teclas_midi.md` §2 e §6.4) e categorico: 22 de 22
pianistas tocaram a primeira voz mais forte que as outras, sem uma excecao. O
teste prova que o diferencial aparece no MIDI — e que ele aparece SEM inverter
a intencao da origem, que e a regra que `drums.accent_hierarchy` violou em
DEIXE IR.
"""

from __future__ import annotations

import mido

from tests._guitar_keys_fixtures import (
    build_track_midi,
    copy_midi,
    midi_bytes,
    note_events,
    reapplied,
)
from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

CANONICAL = "keys.voice_dynamics"
DELTA = 7  # derivado no manual de fhv_melodia_normal/enfatizada (Goebl 2001)


def _chord(velocities: tuple[int, int, int]) -> mido.MidiFile:
    low, middle, top = velocities
    return build_track_midi(
        [(0, 480, 60, low), (0, 480, 64, middle), (0, 480, 67, top)],
        name="Keys",
    )


def _apply(mid: mido.MidiFile, **parameters) -> mido.MidiFile:
    payload = {"density": 1.0}
    payload.update(parameters)
    return apply_technique(CANONICAL, mid, seed=5, parameters=payload, tool="generic")


def _velocities(mid: mido.MidiFile) -> dict[int, int]:
    return {
        pitch: velocity
        for _tick, kind, pitch, velocity in note_events(mid)
        if kind == "on"
    }


def test_voice_dynamics_is_registered_as_humanize_level():
    assert CANONICAL in SUPPORTED_TECHNIQUES
    assert get_technique(CANONICAL).level == "humanize"


def test_without_density_the_file_comes_out_byte_identical():
    untouched = midi_bytes(_chord((100, 110, 90)))
    for parameters in ({}, {"density": 0.0}):
        result = apply_technique(
            CANONICAL, _chord((100, 110, 90)), seed=5,
            parameters=parameters, tool="generic",
        )
        assert midi_bytes(result) == untouched


def test_top_voice_ends_up_the_loudest_by_the_manual_delta():
    result = _apply(_chord((100, 110, 90)))
    velocities = _velocities(result)

    assert velocities[67] == 110 + DELTA
    assert velocities[60] == 100, "voz interna nao pode ser rebaixada sem precisar"
    assert velocities[64] == 110


def test_flat_chord_at_127_loses_at_most_delta_points():
    """A invariante que impede a inversao de intencao: ninguem cai mais que o
    diferencial. Com tudo em 127 o topo nao tem para onde subir, entao as
    outras vozes cedem exatamente `delta` pontos — nunca mais que isso."""

    result = _apply(_chord((127, 127, 127)))
    velocities = _velocities(result)

    assert velocities[67] == 127
    assert velocities[64] == 127 - DELTA
    assert velocities[60] == 127 - DELTA
    assert min(velocities.values()) >= 127 - DELTA


def test_chord_already_voiced_is_left_alone():
    untouched = midi_bytes(_chord((90, 95, 110)))
    assert midi_bytes(_apply(_chord((90, 95, 110)))) == untouched


def test_declared_delta_commands_the_result():
    """Delta declarado no plano manda: com 20, o diferencial vira 20."""

    result = _apply(_chord((100, 110, 90)), delta_midi_melodia_vs_acompanhamento=20)
    velocities = _velocities(result)

    assert velocities[67] - max(velocities[60], velocities[64]) == 20
    assert velocities[67] == 127
    assert velocities[64] == 107


def test_drum_channel_is_never_voiced():
    kit = build_track_midi(
        [(0, 120, 36, 100), (0, 120, 38, 110), (0, 120, 42, 90)],
        channel=9, name="Drums",
    )
    assert midi_bytes(_apply(kit)) == midi_bytes(
        build_track_midi(
            [(0, 120, 36, 100), (0, 120, 38, 110), (0, 120, 42, 90)],
            channel=9, name="Drums",
        )
    )


def test_timing_and_pitches_are_untouched():
    source = _chord((100, 110, 90))
    before = [(tick, kind, pitch) for tick, kind, pitch, _v in note_events(source)]
    result = _apply(_chord((100, 110, 90)))
    after = [(tick, kind, pitch) for tick, kind, pitch, _v in note_events(result)]
    assert after == before


def test_reapplying_changes_nothing():
    once = _apply(_chord((100, 110, 90)))
    before, after = reapplied(once, _apply)
    assert after == before


def test_reapplying_with_fractional_density_is_stable():
    """A tecnica ja acertava isto — o teste TRAVA o acerto.

    Das tres tecnicas de teclas do PR #120 esta era a unica que mantinha o
    acorde ja vozeado no pool e apenas o pulava, entao densidade fracionaria
    nao convergia para 1.0 ao reaplicar. As outras duas foram corrigidas para
    seguir este comportamento; aqui fica a asserção que impede a regressao.
    """

    def chords() -> mido.MidiFile:
        return build_track_midi(
            [
                (bar * 480, 480, pitch, 80 + offset)
                for bar in range(12)
                for offset, pitch in enumerate((60, 64, 67))
            ],
            name="Keys",
        )

    for density in (0.3, 0.5, 0.9):
        mid = chords()
        passes = []
        for _ in range(3):
            mid = _apply(copy_midi(mid), density=density)
            passes.append(midi_bytes(mid))

        assert passes[0] != midi_bytes(chords()), (
            "a densidade precisa vozear algo para o teste valer"
        )
        assert passes[1] == passes[0]
        assert passes[2] == passes[0]


def test_lowering_never_drops_a_voice_more_than_delta():
    """A invariante de pressao e ARITMETICA, nao um guard que descarta acorde.

    O `if any(...)` que o PR #120 anunciava como protecao ativa era inalcancavel
    (so rodava com `new_top == 127`, onde a condicao vira `velocity > 127`) —
    forca bruta com 200 mil acordes nunca o disparou. O ramo morto saiu; esta
    varredura afirma a garantia que sobrou: com o topo em 127, o piso e
    `127 - delta` e ninguem cai mais que `delta` pontos.
    """

    for low in range(1, 128, 3):
        for middle in range(1, 128, 5):
            source = _chord((low, middle, 127))
            before = _velocities(source)
            after = _velocities(_apply(_chord((low, middle, 127))))
            for pitch, velocity in before.items():
                assert velocity - after[pitch] <= DELTA
