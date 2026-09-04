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


def positive_density(context: Any) -> float | None:
    """Retorna densidade positiva do plano ou None se ausente/desligada.

    Usada por técnicas em que `density=0` (ou ausencia) significa NO-OP,
    nao "minimo de um".
    """
    density = context.parameters.get("density")
    if not isinstance(density, (int, float)) or isinstance(density, bool):
        return None
    value = float(density)
    return value if value > 0.0 else None


def manual_int_param(context: Any, technique: Technique, name: str) -> int:
    """Le parametro inteiro do manual, com erro explicito quando falta."""
    for param in technique.parameters:
        if param.name == name and isinstance(param.value, (int, float)):
            return int(param.value)
    raise ValueError(
        f"tecnica {context.canonical!r} precisa declarar {name} no manual"
    )


def structural_notes(
    track: mido.MidiTrack,
    *,
    skip_drum_channel: bool = False,
) -> list[dict[str, int]]:
    """Reconstroi notas estruturais (`channel`, `pitch`, `start`, `end`) da track.

    Pareia `note_on`/`note_off` por (canal, altura), descarta orfaos e — se
    `skip_drum_channel=True` — remove canal 9 (bateria).
    """
    entries: list[dict[str, int]] = []
    pending: dict[tuple[int, int], list[dict[str, int]]] = {}
    tick = 0
    for msg in track:
        tick += msg.time
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            key = (int(msg.channel), int(msg.note))
            entry: dict[str, int] = {
                "channel": key[0],
                "pitch": key[1],
                "start": int(tick),
                "end": None,  # type: ignore[assignment]
            }
            pending.setdefault(key, []).append(entry)
            entries.append(entry)
            continue
        if msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            stack = pending.get((int(msg.channel), int(msg.note)))
            if stack:
                stack.pop(0)["end"] = int(tick)
    return [
        e for e in entries
        if e["end"] is not None
        and (not skip_drum_channel or e["channel"] != 9)
    ]


def din_msgs_per_second_ceiling(canonical: str) -> int:
    """Teto fisico da porta DIN, sourced em `keys.pitch_bend`.

    Reusado por tecnicas de CC continuo cujos manuais deixam
    `eventos_por_segundo_recomendados` como lacuna sourced null; e limite
    da porta, nao da tecnica.
    """
    manual = build_index().get("keys.pitch_bend")
    if manual is None:
        raise ValueError(
            f"tecnica {canonical!r}: manual de keys.pitch_bend nao "
            "encontrado para derivar teto de eventos"
        )
    for param in manual.parameters:
        if param.name == "teto_mensagens_por_segundo_din" and isinstance(
            param.value, (int, float)
        ):
            return int(param.value)
    return 1042


def structural_candidates(
    structural: list[dict[str, int]],
    track_index: int,
) -> list[dict[str, int]]:
    """Anexa `track_index` a cada nota estrutural — formato de candidate."""
    return [
        {
            "track_index": track_index,
            "start": entry["start"],
            "pitch": entry["pitch"],
            "channel": entry["channel"],
            "end": entry["end"],
        }
        for entry in structural
    ]


def technique_setup(
    context: Any,
) -> tuple[float, Technique, Callable[[str], int]] | None:
    """Boilerplate compartilhado por tecnicas de CC continuo com `density`.

    Devolve `(density, technique, int_param)` ou `None` quando `density` esta
    ausente/zerada (NO-OP). `int_param(name)` le parametro inteiro do manual
    da tecnica com erro explicito se faltar.
    """
    density = positive_density(context)
    if density is None:
        return None
    technique = technique_from_manual(context)

    def _int_param(name: str) -> int:
        return manual_int_param(context, technique, name)

    return density, technique, _int_param


def cc_envelope_setup(
    context: Any,
    mid: mido.MidiFile,
    *,
    rng_key: str,
) -> tuple[float, Technique, Callable[[str], int], int, float, Any] | None:
    """Boilerplate completo para tecnicas de envelope de CC continuo.

    Devolve `(density, technique, int_param, teto_msgs, ticks_per_second, rng)`
    ou `None` para NO-OP (density ausente/zero ou `ticks_per_beat` invalido).
    `teto_msgs` sai de `din_msgs_per_second_ceiling`; `rng` de
    `context.rng(rng_key)`.
    """
    setup = technique_setup(context)
    if setup is None:
        return None
    density, technique, int_param = setup
    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return None
    ticks_per_second = ticks_per_beat * 1_000_000 / first_tempo(mid)
    teto_msgs = din_msgs_per_second_ceiling(context.canonical)
    return (
        density, technique, int_param, teto_msgs,
        ticks_per_second, context.rng(rng_key),
    )


def iter_track_selections(
    mid: mido.MidiFile,
    *,
    density: float,
    rng: Any,
    skip_drum_channel: bool = True,
):
    """Itera (track, selected_candidates) por track de `mid`.

    Reduz o boilerplate `structural_notes -> structural_candidates ->
    select_by_density -> continue` replicado por tecnicas de CC continuo.
    Tracks sem notas estruturais ou sem selecao sao puladas silenciosamente.
    """
    for track_index, track in enumerate(mid.tracks):
        structural = structural_notes(track, skip_drum_channel=skip_drum_channel)
        if not structural:
            continue
        selected = select_by_density(
            structural_candidates(structural, track_index),
            density=density,
            rng=rng,
            sort_key=sort_by_track_start_pitch,
        )
        if selected:
            yield track, selected


def apply_symmetric_cc_envelope(
    track: mido.MidiTrack,
    selected: list[dict[str, int]],
    *,
    cc: int,
    rest_value: int,
    extreme_value: int,
    ticks_per_second: float,
    teto_msgs: int,
    steps_target: int = 8,
) -> None:
    """Emite envelope simetrico rest -> extreme -> rest por nota selecionada.

    Compartilhado por `keys.modulation` (rest=0, extreme=peak) e
    `keys.expression` (rest=127, extreme=valley). Passos limitados pelo teto
    fisico da DIN (`teto_msgs`) e pelo numero de ticks disponivel.
    """
    from ._track_rebuild import collect_absolute, sort_and_flush

    absolute = collect_absolute(track)
    order = len(absolute)

    for candidate in selected:
        start = candidate["start"]
        end = candidate["end"]
        channel = candidate["channel"]
        duration = end - start
        if duration <= 1:
            continue
        midpoint = start + duration // 2

        for low_tick, high_tick, low_value, high_value in (
            (start, midpoint, rest_value, extreme_value),
            (midpoint, end, extreme_value, rest_value),
        ):
            span_ticks = max(1, high_tick - low_tick)
            span_seconds = span_ticks / ticks_per_second
            max_by_rate = (
                max(2, int(span_seconds * teto_msgs))
                if span_seconds > 0
                else 2
            )
            max_by_ticks = max(2, span_ticks)
            n_steps = max(2, min(steps_target, max_by_rate, max_by_ticks))
            for i in range(1, n_steps + 1):
                frac = i / n_steps
                value = int(round(low_value + (high_value - low_value) * frac))
                value = max(0, min(127, value))
                step_tick = low_tick + int(round(span_ticks * frac))
                step_tick = max(low_tick, min(high_tick, step_tick))
                absolute.append((
                    step_tick, -2, order,
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=cc,
                        value=value,
                    ),
                ))
                order += 1

    sort_and_flush(absolute, track)


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


def manual_param_of(canonical: str, name: str) -> Any:
    """Le `value` de um parametro do manual de OUTRA tecnica.

    Existe pelo mesmo motivo de `din_msgs_per_second_ceiling`: numero fisico
    ja sourced num bloco vizinho do mesmo manual nao deve ser duplicado (nem
    hardcoded) no bloco que o consome. `guitar.vibrato`, por exemplo, precisa
    do range de pitch bend declarado em `guitar.bend` para converter cents em
    passos de roda.
    """
    manual = build_index().get(canonical)
    if manual is None:
        raise ValueError(
            f"manual de {canonical!r} nao encontrado para ler {name!r}"
        )
    for param in manual.parameters:
        if param.name == name and isinstance(param.value, (int, float)):
            return param.value
    raise ValueError(
        f"tecnica {canonical!r} precisa declarar {name} no manual"
    )


def isolated_notes(
    notes: tuple[dict[str, int], ...] | list[dict[str, int]],
    *,
    skip_drum_channel: bool = True,
) -> list[dict[str, int]]:
    """Notas que soam SOZINHAS no canal delas, com `prev_end` anexado.

    Pitch bend e mensagem de CANAL: bend ou vibrato escrito enquanto outra
    nota do mesmo canal soa desafina o acorde inteiro — o manual de guitarra
    e explicito que bend dentro de acorde em canal unico e impossivel e que
    vibrato de canal em power chord esta errado, porque o guitarrista vibra
    UMA corda. `prev_end` e o fim da ultima nota anterior do mesmo canal (ou
    `None`), para o aplicador saber se ha espaco antes do ataque.
    """
    by_channel: dict[int, list[dict[str, int]]] = {}
    for note in notes:
        if skip_drum_channel and note["channel"] == 9:
            continue
        by_channel.setdefault(note["channel"], []).append(note)

    out: list[dict[str, int]] = []
    for group in by_channel.values():
        ordered = sorted(group, key=lambda n: (n["start"], n["end"], n["pitch"]))
        for position, note in enumerate(ordered):
            if any(
                other["start"] < note["end"] and other["end"] > note["start"]
                for index, other in enumerate(ordered)
                if index != position
            ):
                continue
            entry = dict(note)
            entry["prev_end"] = max(
                (other["end"] for other in ordered[:position]),
                default=-1,
            )
            out.append(entry)
    return sorted(out, key=lambda n: (n["start"], n["pitch"]))


def isolated_notes_by_file(
    mid: mido.MidiFile,
    *,
    skip_drum_channel: bool = True,
) -> dict[int, list[dict[str, int]]]:
    """`isolated_notes` avaliado no escopo do CANAL do arquivo INTEIRO.

    Pitch bend e mensagem de canal, e canal nao pertence a uma track: duas
    tracks no mesmo canal soam juntas no mesmo sintetizador. Avaliar isolamento
    track a track deixava passar exatamente o power chord que o manual proibe —
    `_render_guitar_element` da o mesmo `GUITAR_CHANNEL` a todas as layers, e
    `_apply_style_techniques_to_edit_tracks` junta num so `MidiFile` todas as
    tracks fisicas com o mesmo nome de DAW.

    Devolve `{indice_da_track: [notas isoladas daquela track]}`, com `prev_end`
    medido tambem no canal inteiro.
    """
    todas: list[dict[str, int]] = []
    for track_index, track in enumerate(mid.tracks):
        for note in iter_note_dicts(track, track_index=track_index):
            todas.append(dict(note))

    out: dict[int, list[dict[str, int]]] = {
        track_index: [] for track_index in range(len(mid.tracks))
    }
    for note in isolated_notes(todas, skip_drum_channel=skip_drum_channel):
        out[note["track_index"]].append(note)
    for notes in out.values():
        notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def select_by_stable_density(
    candidates: list[dict[str, Any]],
    *,
    density: Any,
    context: Any,
    purpose: str,
    identity: Callable[[dict[str, Any]], tuple[Any, ...]],
    sort_key: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
    """Selecao por densidade em que a decisao NAO depende do pool.

    `select_by_density` sorteia um subconjunto do pool: se o pool encolhe entre
    duas passadas — e encolhe, porque tecnica aplicada tira o alvo da lista de
    candidatos — o resorteio pega o RESTO INTOCADO e a reaplicacao converge
    para `density=1.0`. Aqui cada candidato decide sozinho, a partir da seed do
    contexto e da propria identidade, entao candidato recusado na primeira
    passada continua recusado em todas as seguintes.

    A contrapartida e que a contagem selecionada e binomial em torno de
    `len(candidates) * density` em vez de exata; `density=1.0` (e qualquer
    densidade nao numerica) continua levando o pool inteiro.
    """
    if not candidates:
        return []
    if not isinstance(density, (int, float)):
        return sorted(candidates, key=sort_key)
    requested = float(density)
    if requested <= 0.0:
        return []
    if requested >= 1.0:
        return sorted(candidates, key=sort_key)
    chosen = [
        candidate
        for candidate in candidates
        if context.rng(f"{purpose}:{identity(candidate)}").random() < requested
    ]
    return sorted(chosen, key=sort_key)


def simultaneous_chords(
    notes: tuple[dict[str, int], ...] | list[dict[str, int]],
    *,
    min_size: int = 2,
    skip_drum_channel: bool = True,
) -> list[list[dict[str, int]]]:
    """Agrupa notas que comecam no MESMO tick e canal, em ordem de escrita.

    Cada grupo sai ordenado por `note_on_index` — a ordem em que os eventos
    aparecem na track, que e justamente a ordem que o contrato `humanize`
    proibe mudar.
    """
    groups: dict[tuple[int, int], list[dict[str, int]]] = {}
    for note in notes:
        if skip_drum_channel and note["channel"] == 9:
            continue
        groups.setdefault((note["channel"], note["start"]), []).append(note)
    return [
        sorted(groups[key], key=lambda n: n["note_on_index"])
        for key in sorted(groups)
        if len(groups[key]) >= min_size
    ]
