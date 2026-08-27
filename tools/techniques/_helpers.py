"""Helpers compartilhados pelos aplicadores de tecnicas.

Os aplicadores registrados em `engine.py` importam estas funcoes dentro do
corpo da funcao. Isso reduz duplicacao sem fazer o aplicador capturar nomes
globais, contrato protegido por teste.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import mido

from .index import Technique, build_index

DRUM_HAND_FOOT_NOTES = frozenset({35, 36, 44})


def technique_from_manual(context: Any) -> Technique:
    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )
    return technique


def recipe_from_context(
    context: Any,
    technique: Technique,
    *,
    require_explicit_tool: bool = False,
) -> dict[str, Any]:
    recipe = dict(context.recipe)
    if recipe:
        return recipe
    if require_explicit_tool:
        available = sorted(technique.tools.keys())
        raise ValueError(
            f"tecnica {context.canonical!r} exige ferramenta-alvo; "
            f"receitas disponiveis: {available!r}"
        )
    return dict(technique.tools.get(context.tool) or technique.tools["generic"])


def parameter_value(
    context: Any,
    technique: Technique,
    name: str,
    fallback: float | None = None,
) -> float | None:
    value = context.parameters.get(name)
    if value is not None:
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(item, (int, float)) for item in value)
        ):
            return (float(value[0]) + float(value[1])) / 2
        if isinstance(value, (int, float)):
            return float(value)

    params = {param.name: param for param in technique.parameters}
    parameter = params.get(name)
    if parameter is None:
        return fallback
    if isinstance(parameter.value, (int, float)):
        return float(parameter.value)
    if parameter.range is not None:
        return (float(parameter.range[0]) + float(parameter.range[1])) / 2
    return fallback


def manual_value(context: Any, technique: Technique, name: str) -> Any:
    params = {param.name: param for param in technique.parameters}
    parameter = params.get(name)
    if parameter is None or parameter.value is None:
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar {name} no manual"
        )
    return parameter.value


def notes_for(
    recipe: Mapping[str, Any],
    name: str,
    canonical: str,
    *,
    message_suffix: str = "como lista de MIDI ints",
) -> tuple[int, ...]:
    values = recipe.get(name)
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(note, int) for note in values)
    ):
        raise ValueError(
            f"tecnica {canonical!r} precisa declarar {name} {message_suffix}"
        )
    return tuple(int(note) for note in values)


def positive_float(
    mapping: Mapping[str, Any],
    name: str,
    canonical: str,
    *,
    location: str = "na receita",
) -> float:
    value = mapping.get(name)
    if not isinstance(value, (int, float)) or float(value) <= 0:
        raise ValueError(
            f"tecnica {canonical!r} precisa declarar {name} como numero "
            f"positivo {location}"
        )
    return float(value)


def density_disabled(context: Any) -> bool:
    density = context.parameters.get("density")
    return isinstance(density, (int, float)) and float(density) <= 0.0


def target_count(size: int, density: Any) -> int:
    if size <= 0:
        return 0
    if isinstance(density, (int, float)):
        requested = float(density)
        if requested <= 0.0:
            return 0
        return max(1, min(size, int(round(size * requested))))
    return size


def select_by_density(
    candidates: list[dict[str, Any]],
    *,
    density: Any,
    rng: Any,
    sort_key: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
    wanted = target_count(len(candidates), density)
    if wanted == 0:
        return []
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    return sorted(shuffled[:wanted], key=sort_key)


def sort_by_track_start_pitch(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (item["track_index"], item["start"], item["pitch"])


def selected_by_track(
    candidates: list[dict[str, Any]],
    *,
    density: Any,
    rng: Any,
    value: Callable[[dict[str, Any]], Any],
) -> dict[int, list[Any]]:
    by_track: dict[int, list[Any]] = {}
    for candidate in select_by_density(
        candidates,
        density=density,
        rng=rng,
        sort_key=sort_by_track_start_pitch,
    ):
        selected_value = value(candidate)
        if isinstance(selected_value, list | tuple):
            by_track.setdefault(candidate["track_index"], []).extend(selected_value)
        else:
            by_track.setdefault(candidate["track_index"], []).append(selected_value)
    return by_track


def first_tempo(mid: mido.MidiFile) -> int:
    for track in mid.tracks:
        for msg in track:
            if msg.is_meta and msg.type == "set_tempo":
                return int(msg.tempo)
    return 500_000


def ticks_per_ms(mid: mido.MidiFile) -> float:
    return mid.ticks_per_beat * 1000 / first_tempo(mid)


def iter_note_dicts(
    track: mido.MidiTrack,
    *,
    track_index: int | None = None,
    include_note_off_index: bool = False,
) -> tuple[dict[str, int], ...]:
    pending: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    tick = 0
    notes: list[dict[str, int]] = []
    for msg_index, msg in enumerate(track):
        tick += msg.time
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            pending.setdefault((msg.channel, msg.note), []).append((
                tick,
                msg.velocity,
                msg_index,
            ))
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            stack = pending.get((msg.channel, msg.note))
            if not stack:
                continue
            start_tick, velocity, note_on_index = stack.pop(0)
            note = {
                "channel": int(msg.channel),
                "pitch": int(msg.note),
                "start": int(start_tick),
                "end": int(tick),
                "duration": int(tick - start_tick),
                "velocity": int(velocity),
                "note_on_index": int(note_on_index),
            }
            if track_index is not None:
                note["track_index"] = int(track_index)
            if include_note_off_index:
                note["note_off_index"] = int(msg_index)
            notes.append(note)
    return tuple(notes)


def note_on_events(track: mido.MidiTrack) -> tuple[tuple[int, int], ...]:
    events: list[tuple[int, int]] = []
    tick = 0
    for msg_index, msg in enumerate(track):
        tick += msg.time
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0:
            events.append((msg_index, tick))
    return tuple(events)


def hand_starts(notes: tuple[dict[str, int], ...] | list[dict[str, int]], tick: int) -> int:
    return sum(
        1
        for note in notes
        if note["channel"] == 9
        and note["start"] == tick
        and note["pitch"] not in DRUM_HAND_FOOT_NOTES
    )


def overlaps_same_pitch(
    existing: tuple[dict[str, int], ...] | list[dict[str, int]],
    channel: int,
    pitch: int,
    start_tick: int,
    end_tick: int,
) -> bool:
    for note in existing:
        if note["channel"] != channel or note["pitch"] != pitch:
            continue
        if note["start"] == start_tick and note["end"] == end_tick:
            continue
        if note["start"] < end_tick and note["end"] > start_tick:
            return True
    return False


def rebuild_track(
    track: mido.MidiTrack,
    *,
    added_notes: tuple[Mapping[str, int], ...] | list[Mapping[str, int]] = (),
    note_by_index: Mapping[int, int] | None = None,
    velocity_by_index: Mapping[int, int] | None = None,
    absolute_tick_by_index: Mapping[int, int] | None = None,
) -> None:
    absolute: list[tuple[int, int, mido.Message | mido.MetaMessage]] = []
    tick = 0
    order = 0
    note_by_index = {} if note_by_index is None else note_by_index
    velocity_by_index = {} if velocity_by_index is None else velocity_by_index
    absolute_tick_by_index = (
        {} if absolute_tick_by_index is None else absolute_tick_by_index
    )

    for msg_index, msg in enumerate(track):
        tick += msg.time
        absolute_tick = absolute_tick_by_index.get(msg_index, tick)
        if not msg.is_meta and msg_index in note_by_index:
            msg = msg.copy(note=note_by_index[msg_index])
        elif not msg.is_meta and msg_index in velocity_by_index:
            msg = msg.copy(velocity=velocity_by_index[msg_index])
        else:
            msg = msg.copy()
        absolute.append((absolute_tick, order, msg))
        order += 1

    for note in added_notes:
        absolute.append((
            int(note["start"]),
            order,
            mido.Message(
                "note_on",
                channel=int(note["channel"]),
                note=int(note["pitch"]),
                velocity=int(note["velocity"]),
            ),
        ))
        order += 1
        absolute.append((
            int(note["end"]),
            order,
            mido.Message(
                "note_off",
                channel=int(note["channel"]),
                note=int(note["pitch"]),
                velocity=0,
            ),
        ))
        order += 1

    rebuilt = mido.MidiTrack()
    previous_tick = 0
    for absolute_tick, _order, msg in sorted(
        absolute,
        key=lambda item: (item[0], item[1]),
    ):
        rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
        previous_tick = absolute_tick
    track[:] = rebuilt
