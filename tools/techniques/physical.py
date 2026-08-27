"""Validacao fisica dos ornamentos escritos pelo motor de tecnicas.

As regras aqui rejeitam somente notas novas criadas por uma aplicacao de
tecnica. Material estrutural que ja existia no MIDI de entrada e preservado
pelos contratos do motor; este modulo impede que um ornamento novo exija um
corpo impossivel antes de o despacho aceitar o resultado.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import mido

from .notes import _collect_notes


class TechniquePhysicalError(ValueError):
    """Ornamento rejeitado por plausibilidade fisica do instrumento."""


# Fonte: `knowledge/tecnicas/tecnicas_bateria_midi.md`, §1.1/§1.2.
# No mapa GM/SD3 central, apenas bumbo e hi-hat pedal entram no orçamento de pes;
# toms (48), ride/crash (59) e qualquer outra peca contam como mao.
_DRUM_FOOT_NOTES: frozenset[int] = frozenset({35, 36, 44})
_BASS_DEFAULT_TUNING: tuple[int, ...] = (28, 33, 38, 43)
_GUITAR_TUNINGS: dict[str, tuple[int, ...]] = {
    "e_padrao": (40, 45, 50, 55, 59, 64),
    "drop_d": (38, 45, 50, 55, 59, 64),
    "d_padrao": (38, 43, 48, 53, 57, 62),
    "drop_c": (36, 43, 48, 53, 57, 62),
    "drop_c_sharp": (37, 44, 49, 54, 58, 63),
    "drop_b": (35, 42, 47, 52, 56, 61),
    "drop_a_sharp": (34, 41, 46, 51, 55, 60),
    "drop_a": (33, 40, 45, 50, 54, 59),
    "sete_cordas_b": (35, 40, 45, 50, 55, 59, 64),
    "sete_cordas_drop_a": (33, 40, 45, 50, 55, 59, 64),
    "oito_cordas_f_sharp": (30, 35, 40, 45, 50, 55, 59, 64),
    "oito_cordas_drop_e": (28, 35, 40, 45, 50, 55, 59, 64),
}
_BASS_TUNINGS: dict[str, tuple[int, ...]] = {
    "e_padrao": _BASS_DEFAULT_TUNING,
    "quatro_cordas_e": _BASS_DEFAULT_TUNING,
    "cinco_cordas_b": (23, 28, 33, 38, 43),
}


@dataclass(frozen=True, order=True)
class _NoteIdentity:
    track_index: int
    channel: int
    pitch: int
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class _PhysicalNote:
    identity: _NoteIdentity
    occurrence: int

    @property
    def track_index(self) -> int:
        return self.identity.track_index

    @property
    def channel(self) -> int:
        return self.identity.channel

    @property
    def pitch(self) -> int:
        return self.identity.pitch

    @property
    def start_tick(self) -> int:
        return self.identity.start_tick

    @property
    def end_tick(self) -> int:
        return self.identity.end_tick


def validate_physical_plausibility(
    canonical: str,
    before: mido.MidiFile,
    after: mido.MidiFile,
    parameters: Mapping[str, Any],
    recipe: Mapping[str, Any] | None = None,
) -> None:
    """Rejeita ornamentos novos que violem regras fisicas por familia."""

    family = canonical.split(".", 1)[0]
    notes = _notes_from_midi(after)
    new_notes = _new_notes(_notes_from_midi(before), notes)
    if not new_notes:
        return

    keyswitch_pitches = _keyswitch_pitches_from_recipe(recipe or {})
    if keyswitch_pitches:
        new_notes = tuple(
            note for note in new_notes if note.pitch not in keyswitch_pitches
        )
        notes = tuple(
            note for note in notes if note.pitch not in keyswitch_pitches
        )
        if not new_notes:
            return

    if family == "drums":
        _validate_drums(canonical, notes, new_notes)
    elif family in {"bass", "guitar"}:
        _validate_strings(canonical, family, notes, new_notes, parameters)
    elif family == "keys":
        _validate_keys(canonical, notes, new_notes, parameters)


def _keyswitch_pitches_from_recipe(recipe: Mapping[str, Any]) -> frozenset[int]:
    """Pitches de keyswitch declarados na receita — fora da regiao tocavel.

    Reconhece a chave canonica `keyswitch` (int ou lista) e qualquer chave que
    comece com `keyswitch_` (int), para receitas como `bass.attack_style` do
    MODO BASS, que declara `keyswitch_dedo`, `keyswitch_palheta`,
    `keyswitch_slap`, `keyswitch_forcar_primeiro` e `keyswitch_forcar_segundo`.
    """

    pitches: set[int] = set()
    for key, value in recipe.items():
        if not isinstance(key, str):
            continue
        if key != "keyswitch" and not key.startswith("keyswitch_"):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            pitches.add(value)
        elif isinstance(value, Sequence) and not isinstance(value, str):
            pitches.update(p for p in value if isinstance(p, int) and not isinstance(p, bool))
    return frozenset(pitches)


def _notes_from_midi(mid: mido.MidiFile) -> tuple[_PhysicalNote, ...]:
    return tuple(
        _PhysicalNote(
            identity=_NoteIdentity(
                track_index=raw.track_index,
                channel=raw.channel,
                pitch=raw.pitch,
                start_tick=raw.start_tick,
                end_tick=raw.end_tick,
            ),
            occurrence=raw.occurrence,
        )
        for raw in _collect_notes(mid)
    )


def _new_notes(
    before: tuple[_PhysicalNote, ...],
    after: tuple[_PhysicalNote, ...],
) -> tuple[_PhysicalNote, ...]:
    before_counts: dict[_NoteIdentity, int] = {}
    for note in before:
        before_counts[note.identity] = before_counts.get(note.identity, 0) + 1
    return tuple(
        note
        for note in after
        if note.occurrence >= before_counts.get(note.identity, 0)
    )


def _validate_drums(
    canonical: str,
    notes: tuple[_PhysicalNote, ...],
    new_notes: tuple[_PhysicalNote, ...],
) -> None:
    for new_note in new_notes:
        simultaneous = [
            note
            for note in notes
            if note.channel == new_note.channel
            and note.track_index == new_note.track_index
            and note.start_tick == new_note.start_tick
        ]
        hands = [note for note in simultaneous if note.pitch not in _DRUM_FOOT_NOTES]
        feet = [note for note in simultaneous if note.pitch in _DRUM_FOOT_NOTES]
        if len(hands) > 2:
            raise TechniquePhysicalError(
                f"plausibilidade fisica violada por {canonical}: bateria "
                f"exigiria {len(hands)} maos no tick {new_note.start_tick}; "
                "maximo fisico sao duas maos"
            )
        if len(feet) > 2:
            raise TechniquePhysicalError(
                f"plausibilidade fisica violada por {canonical}: bateria "
                f"exigiria {len(feet)} pes no tick {new_note.start_tick}; "
                "maximo fisico sao dois pes"
            )


def _validate_strings(
    canonical: str,
    family: str,
    notes: tuple[_PhysicalNote, ...],
    new_notes: tuple[_PhysicalNote, ...],
    parameters: Mapping[str, Any],
) -> None:
    tuning = _tuning_from_parameters(family, parameters)
    max_fret = _positive_int_parameter(parameters, "max_fret", 24)
    floor = min(tuning)
    for new_note in new_notes:
        if new_note.pitch < floor:
            raise TechniquePhysicalError(
                f"plausibilidade fisica violada por {canonical}: nota "
                f"{new_note.pitch} abaixo da corda solta mais grave "
                f"da afinacao declarada ({floor})"
            )
        active = [
            note
            for note in notes
            if note.track_index == new_note.track_index
            and note.channel == new_note.channel
            and note.start_tick < new_note.end_tick
            and new_note.start_tick < note.end_tick
        ]
        if not _assignable_to_distinct_strings(active, tuning, max_fret):
            raise TechniquePhysicalError(
                f"plausibilidade fisica violada por {canonical}: notas "
                "simultaneas exigem mais de uma nota na mesma corda"
            )


def _validate_keys(
    canonical: str,
    notes: tuple[_PhysicalNote, ...],
    new_notes: tuple[_PhysicalNote, ...],
    parameters: Mapping[str, Any],
) -> None:
    max_span = _positive_int_parameter(parameters, "max_hand_span", 13)
    hand = parameters.get("hand")
    for new_note in new_notes:
        active = [
            note
            for note in notes
            if note.track_index == new_note.track_index
            and note.channel == new_note.channel
            and note.start_tick < new_note.end_tick
            and new_note.start_tick < note.end_tick
        ]
        pitches = tuple(sorted(note.pitch for note in active))
        if not pitches:
            continue
        if hand in {"left", "right"}:
            if pitches[-1] - pitches[0] > max_span:
                raise TechniquePhysicalError(
                    f"plausibilidade fisica violada por {canonical}: extensao "
                    f"de mao {pitches[-1] - pitches[0]} semitons excede "
                    f"o limite de {max_span}"
                )
        elif not _fits_two_keyboard_hands(pitches, max_span):
            raise TechniquePhysicalError(
                f"plausibilidade fisica violada por {canonical}: voicing de "
                "teclas nao cabe em duas maos com extensao maxima declarada"
            )


def _tuning_from_parameters(
    family: str,
    parameters: Mapping[str, Any],
) -> tuple[int, ...]:
    raw = (
        parameters.get("tuning")
        or parameters.get("afinacao")
        or parameters.get("open_strings")
    )
    if raw is None:
        return _BASS_DEFAULT_TUNING if family == "bass" else _GUITAR_TUNINGS["e_padrao"]
    if isinstance(raw, str):
        table = _BASS_TUNINGS if family == "bass" else _GUITAR_TUNINGS
        try:
            return table[raw]
        except KeyError:
            raise TechniquePhysicalError(
                f"afinacao declarada {raw!r} nao e conhecida para {family}"
            ) from None
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        tuning = tuple(raw)
        if tuning and all(isinstance(pitch, int) for pitch in tuning):
            return tuning
    raise TechniquePhysicalError(
        "afinacao declarada precisa ser nome conhecido ou lista de MIDI ints"
    )


def _positive_int_parameter(
    parameters: Mapping[str, Any],
    name: str,
    default: int,
) -> int:
    raw = parameters.get(name, default)
    if isinstance(raw, int) and raw > 0:
        return raw
    raise TechniquePhysicalError(f"parametro fisico {name!r} precisa ser int positivo")


def _assignable_to_distinct_strings(
    notes: list[_PhysicalNote],
    tuning: tuple[int, ...],
    max_fret: int,
) -> bool:
    candidates = [
        [
            string_index
            for string_index, open_pitch in enumerate(tuning)
            if open_pitch <= note.pitch <= open_pitch + max_fret
        ]
        for note in notes
    ]
    if any(not item for item in candidates):
        return False
    candidates.sort(key=len)
    return _assign_candidates(candidates, 0, set())


def _assign_candidates(
    candidates: list[list[int]],
    index: int,
    used: set[int],
) -> bool:
    if index == len(candidates):
        return True
    for string_index in candidates[index]:
        if string_index in used:
            continue
        used.add(string_index)
        if _assign_candidates(candidates, index + 1, used):
            return True
        used.remove(string_index)
    return False


def _fits_two_keyboard_hands(pitches: tuple[int, ...], max_span: int) -> bool:
    if pitches[-1] - pitches[0] <= max_span:
        return True
    for split in range(1, len(pitches)):
        left = pitches[:split]
        right = pitches[split:]
        if left[-1] - left[0] <= max_span and right[-1] - right[0] <= max_span:
            return True
    return False


__all__ = [
    "TechniquePhysicalError",
    "validate_physical_plausibility",
]
