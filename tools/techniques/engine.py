"""Registro e despacho das tecnicas aplicaveis.

Este modulo e separado de `tools.techniques.index`: o indice le os manuais em
`knowledge/tecnicas/`, enquanto este registro declara quais tecnicas o motor
consegue aplicar e qual funcao executa cada uma. A populacao e explicita por
decorator; nao ha varredura dinamica de modulos, porque isso esconderia a
fronteira entre tecnica documentada e tecnica realmente implementada.
"""

from __future__ import annotations

import dis
import hashlib
import inspect
import random
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Any, Literal

import mido

from .index import Technique, TechniqueIndex, build_index
from .notes import _collect_notes
from .physical import TechniquePhysicalError, validate_physical_plausibility

TechniqueLevel = Literal["humanize", "technique"]
TechniqueApply = Callable[..., Any]

_VALID_LEVELS: frozenset[str] = frozenset({"humanize", "technique"})


class TechniqueRegistrationError(ValueError):
    """Registro invalido de tecnica aplicavel."""


class UnknownTechniqueError(LookupError):
    """Tecnica nao registrada para aplicacao pelo motor."""

    def __init__(self, canonical: str, available: tuple[str, ...]) -> None:
        self.canonical = canonical
        self.available = available
        listing = ", ".join(available) if available else "(nenhuma tecnica registrada)"
        super().__init__(
            f"tecnica aplicavel desconhecida {canonical!r}; disponiveis: {listing}"
        )


class TechniqueContractError(ValueError):
    """Violacao do contrato runtime de uma tecnica aplicavel."""


class TechniqueRecipeError(ValueError):
    """Falha ao resolver receita MIDI documentada para uma tecnica."""


@dataclass(frozen=True)
class TechniqueContext:
    """Contexto explicito e imutavel de uma aplicacao de tecnica.

    A seed entra no despacho e qualquer componente pseudoaleatorio deve derivar
    dela via `rng()`. Assim a funcao aplicadora nao precisa ler estado global,
    relogio nem `random` de modulo para variar uma execucao.
    """

    seed: int
    canonical: str
    parameters: Mapping[str, Any] = MappingProxyType({})
    tool: str = "generic"
    requested_tool: str | None = None
    recipe: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.seed, int):
            raise TechniqueRegistrationError("seed da tecnica precisa ser int")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "recipe", MappingProxyType(dict(self.recipe)))

    def derived_seed(self, purpose: str = "") -> int:
        """Deriva uma seed estavel para um subcomponente da tecnica."""

        payload = f"{self.seed}|{self.canonical}|{purpose}".encode()
        return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")

    def rng(self, purpose: str = "") -> random.Random:
        """Cria um RNG local e reprodutivel a partir da seed do contexto."""

        return random.Random(self.derived_seed(purpose))


@dataclass(frozen=True)
class RegisteredTechnique:
    """Tecnica que o motor sabe despachar."""

    canonical: str
    level: TechniqueLevel
    apply: TechniqueApply
    allow_structural_pitch_change: bool = False
    allow_structural_velocity_change: bool = False
    allow_structural_duration_change: bool = False


@dataclass(frozen=True)
class TechniqueApplyResult:
    """Resultado de aplicacao com warnings no formato do envelope das tools."""

    result: Any
    warnings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _ResolvedRecipe:
    tool: str
    requested_tool: str | None
    recipe: Mapping[str, Any]
    warnings: tuple[dict[str, Any], ...] = ()


class TechniqueRegistry:
    """Registro deterministico de tecnicas aplicaveis."""

    def __init__(self) -> None:
        self._items: dict[str, RegisteredTechnique] = {}

    def register(
        self,
        canonical: str,
        level: TechniqueLevel,
        *,
        allow_structural_pitch_change: bool = False,
        allow_structural_velocity_change: bool = False,
        allow_structural_duration_change: bool = False,
    ) -> Callable[[TechniqueApply], TechniqueApply]:
        """Registra `func` como aplicadora de `canonical`.

        O decorator devolve a propria funcao para preservar o uso normal em
        testes e em futuros modulos de implementacao.
        """

        _validate_canonical(canonical)
        _validate_level(level)

        def decorator(func: TechniqueApply) -> TechniqueApply:
            if not callable(func):
                raise TechniqueRegistrationError(
                    f"funcao de aplicacao de {canonical!r} nao e chamavel"
                )
            _validate_apply_accepts_context(canonical, func)
            if canonical in self._items:
                raise TechniqueRegistrationError(
                    f"tecnica aplicavel duplicada: {canonical!r}"
                )
            self._items[canonical] = RegisteredTechnique(
                canonical=canonical,
                level=level,
                apply=func,
                allow_structural_pitch_change=allow_structural_pitch_change,
                allow_structural_velocity_change=allow_structural_velocity_change,
                allow_structural_duration_change=allow_structural_duration_change,
            )
            return func

        return decorator

    def get(self, canonical: str) -> RegisteredTechnique:
        """Devolve a tecnica registrada ou falha com a lista de disponiveis."""

        try:
            return self._items[canonical]
        except KeyError:
            raise UnknownTechniqueError(canonical, self.supported()) from None

    def apply(
        self,
        canonical: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Despacha por nome canonico para a funcao registrada."""

        return self.apply_with_warnings(
            canonical,
            *args,
            **kwargs,
        ).result

    def apply_with_warnings(
        self,
        canonical: str,
        *args: Any,
        seed: int,
        parameters: Mapping[str, Any] | None = None,
        tool: str | None = None,
        index: TechniqueIndex | None = None,
        **kwargs: Any,
    ) -> TechniqueApplyResult:
        """Despacha e devolve warnings estruturados emitidos pelo motor."""

        technique = self.get(canonical)
        resolved = (
            _resolve_recipe(canonical, tool, index)
            if tool is not None or index is not None
            else _ResolvedRecipe(tool="generic", requested_tool=None, recipe={})
        )
        context = TechniqueContext(
            seed=seed,
            canonical=canonical,
            parameters={} if parameters is None else parameters,
            tool=resolved.tool,
            requested_tool=resolved.requested_tool,
            recipe=resolved.recipe,
        )
        before_humanize = (
            _humanize_snapshot(args, kwargs) if technique.level == "humanize" else None
        )
        before_technique = (
            _technique_snapshot(args, kwargs) if technique.level == "technique" else None
        )
        apply_args = args
        apply_kwargs = kwargs
        working_mid = None
        if before_technique is not None:
            working_mid = _clone_midi(before_technique.midi)
            apply_args, apply_kwargs = _replace_first_midi(
                args, kwargs, working_mid
            )

        result = technique.apply(*apply_args, context=context, **apply_kwargs)
        if before_humanize is not None:
            after_mid = _result_midi(result) or before_humanize.midi
            after = _MidiContentSnapshot.from_midi(
                after_mid,
                canonical=technique.canonical,
            )
            _validate_humanize_contract(
                technique.canonical,
                before_humanize.snapshot,
                after,
            )
        if before_technique is not None:
            after_mid = _result_midi(result) or working_mid or before_technique.midi
            _drop_reapplied_notes(before_technique.snapshot, after_mid)
            _drop_reapplied_continuous_events(before_technique.snapshot, after_mid)
            validate_physical_plausibility(
                technique.canonical,
                before_technique.midi,
                after_mid,
                context.parameters,
                context.recipe,
            )
            after = _StructuralSnapshot.from_midi(after_mid)
            _validate_technique_contract(
                technique,
                before_technique.snapshot,
                after,
            )
        return TechniqueApplyResult(result=result, warnings=resolved.warnings)

    def registered(self) -> tuple[RegisteredTechnique, ...]:
        """Tecnicas registradas em ordem estavel por nome canonico."""

        return tuple(self._items[name] for name in self.supported())

    def supported(self) -> tuple[str, ...]:
        """Nomes canonicos suportados, derivados do registro."""

        return tuple(sorted(self._items))


def _validate_canonical(canonical: str) -> None:
    if not isinstance(canonical, str) or not canonical.strip():
        raise TechniqueRegistrationError("nome canonico precisa ser string nao vazia")
    parts = canonical.split(".")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise TechniqueRegistrationError(
            f"nome canonico {canonical!r} precisa seguir '<familia>.<nome>'"
        )


def _validate_level(level: str) -> None:
    if level not in _VALID_LEVELS:
        raise TechniqueRegistrationError(
            f"nivel {level!r} invalido; use um de {sorted(_VALID_LEVELS)!r}"
        )


def _validate_apply_accepts_context(canonical: str, func: TechniqueApply) -> None:
    signature = inspect.signature(func)
    context = signature.parameters.get("context")
    if context is None:
        raise TechniqueRegistrationError(
            f"funcao de aplicacao de {canonical!r} precisa receber parametro "
            "explicito 'context'"
        )
    if context.kind not in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }:
        raise TechniqueRegistrationError(
            f"parametro 'context' de {canonical!r} precisa aceitar keyword"
        )


def _resolve_recipe(
    canonical: str,
    tool_target: str | None,
    index: TechniqueIndex | None,
) -> _ResolvedRecipe:
    idx = index if index is not None else build_index()
    technique = idx.get(canonical)
    if technique is None:
        raise TechniqueRecipeError(
            f"tecnica {canonical!r} nao existe no indice dos manuais"
        )
    return _recipe_for_tool(technique, tool_target)


def _recipe_for_tool(
    technique: Technique,
    tool_target: str | None,
) -> _ResolvedRecipe:
    if tool_target and tool_target in technique.tools:
        return _ResolvedRecipe(
            tool=tool_target,
            requested_tool=tool_target,
            recipe=technique.tools[tool_target],
        )

    generic = technique.tools.get("generic")
    if generic is not None:
        warning = ()
        if tool_target:
            warning = ({
                "code": "W_NO_TOOL_RECIPE",
                "message": (
                    f"tecnica {technique.canonical!r} nao tem receita para "
                    f"tool={tool_target!r}; usando fallback generico. "
                    f"Disponiveis: {sorted(technique.tools.keys())!r}"
                ),
                "path": "tool",
            },)
        return _ResolvedRecipe(
            tool="generic",
            requested_tool=tool_target,
            recipe=generic,
            warnings=warning,
        )

    if tool_target:
        raise TechniqueRecipeError(
            f"tecnica {technique.canonical!r} nao tem receita para "
            f"tool={tool_target!r} nem fallback generic; disponiveis: "
            f"{sorted(technique.tools.keys())!r}"
        )
    raise TechniqueRecipeError(
        f"tecnica {technique.canonical!r} nao tem receita generic; declare "
        f"uma ferramenta-alvo com receita disponivel: {sorted(technique.tools.keys())!r}"
    )


@dataclass(frozen=True)
class _HumanizeBefore:
    midi: mido.MidiFile
    snapshot: _MidiContentSnapshot


@dataclass(frozen=True)
class _TechniqueBefore:
    midi: mido.MidiFile
    snapshot: _StructuralSnapshot


@dataclass(frozen=True)
class _MidiContentSnapshot:
    note_on_count: int
    pitch_multiset: tuple[int, ...]
    note_on_sequence: tuple[tuple[int, int, int], ...]
    note_pairs: tuple[tuple[int, int, int], ...]

    @classmethod
    def from_midi(
        cls,
        mid: mido.MidiFile,
        *,
        canonical: str,
    ) -> _MidiContentSnapshot:
        events: list[tuple[int, int, int]] = []
        pairs: list[tuple[int, int, int]] = []
        pitches: list[int] = []
        for track_index, track in enumerate(mid.tracks):
            pending: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
            for msg in track:
                if msg.is_meta:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    event = (track_index, msg.channel, msg.note)
                    events.append(event)
                    pitches.append(msg.note)
                    pending.setdefault((msg.channel, msg.note), []).append(event)
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0
                ):
                    key = (msg.channel, msg.note)
                    stack = pending.get(key)
                    if not stack:
                        raise TechniqueContractError(
                            f"contrato humanize violado por {canonical}: "
                            "note_off orfao encontrado"
                        )
                    pairs.append(stack.pop(0))
            unclosed = [
                event
                for stack in pending.values()
                for event in stack
            ]
            if unclosed:
                raise TechniqueContractError(
                    f"contrato humanize violado por {canonical}: note_on sem "
                    "note_off correspondente"
                )
        return cls(
            note_on_count=len(events),
            pitch_multiset=tuple(sorted(pitches)),
            note_on_sequence=tuple(events),
            note_pairs=tuple(pairs),
        )


@dataclass(frozen=True, order=True)
class _StructuralKey:
    track_index: int
    channel: int
    pitch: int
    start_tick: int
    occurrence: int


@dataclass(frozen=True)
class _StructuralNote:
    key: _StructuralKey
    velocity: int
    end_tick: int


@dataclass(frozen=True, order=True)
class _NoteIdentity:
    track_index: int
    channel: int
    pitch: int
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class _IndexedNote:
    identity: _NoteIdentity
    occurrence: int
    note_on_index: int
    note_off_index: int


@dataclass(frozen=True, order=True)
class _ContinuousEventIdentity:
    track_index: int
    message_type: str
    channel: int
    tick: int
    number: int
    value: int


@dataclass(frozen=True)
class _IndexedContinuousEvent:
    identity: _ContinuousEventIdentity
    occurrence: int
    message_index: int


@dataclass(frozen=True)
class _StructuralSnapshot:
    notes: dict[_StructuralKey, _StructuralNote]
    continuous_events: dict[_ContinuousEventIdentity, int]

    @classmethod
    def from_midi(cls, mid: mido.MidiFile) -> _StructuralSnapshot:
        seen: dict[tuple[int, int, int, int], int] = {}
        notes: dict[_StructuralKey, _StructuralNote] = {}
        for raw in _collect_notes(mid):
            occurrence_key = (
                raw.track_index,
                raw.channel,
                raw.pitch,
                raw.start_tick,
            )
            occurrence = seen.get(occurrence_key, 0)
            seen[occurrence_key] = occurrence + 1
            key = _StructuralKey(
                track_index=raw.track_index,
                channel=raw.channel,
                pitch=raw.pitch,
                start_tick=raw.start_tick,
                occurrence=occurrence,
            )
            notes[key] = _StructuralNote(
                key=key,
                velocity=raw.velocity,
                end_tick=raw.end_tick,
            )
        return cls(
            notes=notes,
            continuous_events=_continuous_event_counts(mid),
        )

    def identity_counts(self) -> dict[_NoteIdentity, int]:
        counts: dict[_NoteIdentity, int] = {}
        for note in self.notes.values():
            identity = _NoteIdentity(
                track_index=note.key.track_index,
                channel=note.key.channel,
                pitch=note.key.pitch,
                start_tick=note.key.start_tick,
                end_tick=note.end_tick,
            )
            counts[identity] = counts.get(identity, 0) + 1
        return counts

    def continuous_event_counts(self) -> dict[_ContinuousEventIdentity, int]:
        return dict(self.continuous_events)


def _humanize_snapshot(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> _HumanizeBefore | None:
    mid = _first_midi((*args, *kwargs.values()))
    if mid is None:
        return None
    return _HumanizeBefore(
        midi=mid,
        snapshot=_MidiContentSnapshot.from_midi(
            mid,
            canonical="entrada",
        ),
    )


def _technique_snapshot(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> _TechniqueBefore | None:
    mid = _first_midi((*args, *kwargs.values()))
    if mid is None:
        return None
    return _TechniqueBefore(
        midi=mid,
        snapshot=_StructuralSnapshot.from_midi(mid),
    )


def _result_midi(result: Any) -> mido.MidiFile | None:
    if isinstance(result, mido.MidiFile):
        return result
    return None


def _first_midi(values: tuple[Any, ...]) -> mido.MidiFile | None:
    for value in values:
        if isinstance(value, mido.MidiFile):
            return value
    return None


def _clone_midi(mid: mido.MidiFile) -> mido.MidiFile:
    buffer = BytesIO()
    mid.save(file=buffer)
    buffer.seek(0)
    return mido.MidiFile(file=buffer)


def _replace_first_midi(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    replacement: mido.MidiFile,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    new_args = list(args)
    for index, value in enumerate(new_args):
        if isinstance(value, mido.MidiFile):
            new_args[index] = replacement
            return tuple(new_args), kwargs

    new_kwargs = dict(kwargs)
    for key, value in new_kwargs.items():
        if isinstance(value, mido.MidiFile):
            new_kwargs[key] = replacement
            return args, new_kwargs
    return args, kwargs


def _validate_humanize_contract(
    canonical: str,
    before: _MidiContentSnapshot,
    after: _MidiContentSnapshot,
) -> None:
    """Garante que `humanize` so muda execucao, nunca conteudo musical."""

    if after.note_on_count != before.note_on_count:
        raise TechniqueContractError(
            f"contrato humanize violado por {canonical}: contagem de note_on "
            f"mudou de {before.note_on_count} para {after.note_on_count}"
        )
    if after.pitch_multiset != before.pitch_multiset:
        raise TechniqueContractError(
            f"contrato humanize violado por {canonical}: multiconjunto de "
            "pitches mudou"
        )
    if after.note_on_sequence != before.note_on_sequence:
        raise TechniqueContractError(
            f"contrato humanize violado por {canonical}: ordem dos note_on "
            "por track/canal/altura mudou"
        )
    if after.note_pairs != before.note_pairs:
        raise TechniqueContractError(
            f"contrato humanize violado por {canonical}: pareamento de "
            "note_on/note_off mudou"
        )


def _validate_technique_contract(
    technique: RegisteredTechnique,
    before: _StructuralSnapshot,
    after: _StructuralSnapshot,
) -> None:
    """Garante que `technique` ornamenta sem deslocar material estrutural."""

    if technique.allow_structural_pitch_change:
        before_shape = _structural_shape_counts(
            before,
            include_velocity=not technique.allow_structural_velocity_change,
            include_duration=not technique.allow_structural_duration_change,
        )
        after_shape = _structural_shape_counts(
            after,
            include_velocity=not technique.allow_structural_velocity_change,
            include_duration=not technique.allow_structural_duration_change,
        )
        missing = before_shape - after_shape
        if missing:
            raise TechniqueContractError(
                f"contrato technique violado por {technique.canonical}: troca "
                "de articulacao removeu nota estrutural ou alterou posicao"
            )
        return

    for key, before_note in before.notes.items():
        after_note = after.notes.get(key)
        if after_note is None:
            raise TechniqueContractError(
                f"contrato technique violado por {technique.canonical}: nota "
                "estrutural perdeu pitch ou posicao original"
            )
        if (
            not technique.allow_structural_velocity_change
            and after_note.velocity != before_note.velocity
        ):
            raise TechniqueContractError(
                f"contrato technique violado por {technique.canonical}: "
                "velocity de nota estrutural mudou sem permissao declarada"
            )
        if (
            not technique.allow_structural_duration_change
            and after_note.end_tick != before_note.end_tick
        ):
            raise TechniqueContractError(
                f"contrato technique violado por {technique.canonical}: "
                "duracao de nota estrutural mudou sem permissao declarada"
            )


def _structural_shape_counts(
    snapshot: _StructuralSnapshot,
    *,
    include_velocity: bool,
    include_duration: bool,
) -> Counter[tuple[int, int, int, int | None, int | None]]:
    shapes: Counter[tuple[int, int, int, int | None, int | None]] = Counter()
    for note in snapshot.notes.values():
        shapes[(
            note.key.track_index,
            note.key.channel,
            note.key.start_tick,
            note.end_tick if include_duration else None,
            note.velocity if include_velocity else None,
        )] += 1
    return shapes


def _drop_reapplied_notes(
    before: _StructuralSnapshot,
    mid: mido.MidiFile,
) -> None:
    """Remove notas extras que uma reaplicacao tentou empilhar.

    A classificacao ornamental e derivada, nao persistida no MIDI. Para manter
    idempotencia tambem depois de salvar/recarregar, o despacho descarta notas
    acrescentadas pela aplicacao atual quando a mesma assinatura em ticks ja
    existia antes dela.
    """

    before_counts = before.identity_counts()
    if not before_counts:
        return

    for track_index, track in enumerate(mid.tracks):
        remove_indices: set[int] = set()
        for note in _indexed_notes(track_index, track):
            before_count = before_counts.get(note.identity, 0)
            if before_count and note.occurrence >= before_count:
                remove_indices.add(note.note_on_index)
                remove_indices.add(note.note_off_index)
        if remove_indices:
            _remove_track_messages(track, remove_indices)


def _drop_reapplied_continuous_events(
    before: _StructuralSnapshot,
    mid: mido.MidiFile,
) -> None:
    """Remove CC e pitch bend extras que uma reaplicacao tentou empilhar."""

    before_counts = before.continuous_event_counts()
    if not before_counts:
        return

    for track_index, track in enumerate(mid.tracks):
        remove_indices: set[int] = set()
        for event in _indexed_continuous_events(track_index, track):
            before_count = before_counts.get(event.identity, 0)
            if before_count and event.occurrence >= before_count:
                remove_indices.add(event.message_index)
        if remove_indices:
            _remove_track_messages(track, remove_indices)


def _iter_note_pairs(track: mido.MidiTrack):
    """Pareia `note_on`/`note_off` da mesma `(channel, pitch)` em FIFO.

    Emite `(channel, pitch, start_tick, end_tick, velocity, note_on_index,
    note_off_index)`. `note_on` com velocity 0 conta como `note_off`. Mesmo
    pareamento que `mido` grava na reconstrucao — nao troque por dict simples.
    """
    from ._helpers import iter_note_dicts

    for note in iter_note_dicts(track, include_note_off_index=True):
        yield (
            note["channel"],
            note["pitch"],
            note["start"],
            note["end"],
            note["velocity"],
            note["note_on_index"],
            note["note_off_index"],
        )


def _indexed_notes(
    track_index: int,
    track: mido.MidiTrack,
) -> tuple[_IndexedNote, ...]:
    collected: list[tuple[_NoteIdentity, int, int]] = []
    for (
        channel,
        pitch,
        start_tick,
        end_tick,
        _velocity,
        note_on_index,
        note_off_index,
    ) in _iter_note_pairs(track):
        collected.append((
            _NoteIdentity(
                track_index=track_index,
                channel=channel,
                pitch=pitch,
                start_tick=start_tick,
                end_tick=end_tick,
            ),
            note_on_index,
            note_off_index,
        ))

    seen: dict[_NoteIdentity, int] = {}
    indexed: list[_IndexedNote] = []
    for identity, note_on_index, note_off_index in collected:
        occurrence = seen.get(identity, 0)
        seen[identity] = occurrence + 1
        indexed.append(_IndexedNote(
            identity=identity,
            occurrence=occurrence,
            note_on_index=note_on_index,
            note_off_index=note_off_index,
        ))
    return tuple(indexed)


def _continuous_event_counts(
    mid: mido.MidiFile,
) -> dict[_ContinuousEventIdentity, int]:
    counts: dict[_ContinuousEventIdentity, int] = {}
    for track_index, track in enumerate(mid.tracks):
        for event in _indexed_continuous_events(track_index, track):
            counts[event.identity] = counts.get(event.identity, 0) + 1
    return counts


def _indexed_continuous_events(
    track_index: int,
    track: mido.MidiTrack,
) -> tuple[_IndexedContinuousEvent, ...]:
    collected: list[tuple[_ContinuousEventIdentity, int]] = []
    tick = 0
    for msg_index, msg in enumerate(track):
        tick += msg.time
        if msg.is_meta:
            continue
        if msg.type == "control_change":
            collected.append((
                _ContinuousEventIdentity(
                    track_index=track_index,
                    message_type=msg.type,
                    channel=msg.channel,
                    tick=tick,
                    number=msg.control,
                    value=msg.value,
                ),
                msg_index,
            ))
        elif msg.type == "pitchwheel":
            collected.append((
                _ContinuousEventIdentity(
                    track_index=track_index,
                    message_type=msg.type,
                    channel=msg.channel,
                    tick=tick,
                    number=0,
                    value=msg.pitch,
                ),
                msg_index,
            ))

    seen: dict[_ContinuousEventIdentity, int] = {}
    indexed: list[_IndexedContinuousEvent] = []
    for identity, msg_index in collected:
        occurrence = seen.get(identity, 0)
        seen[identity] = occurrence + 1
        indexed.append(_IndexedContinuousEvent(
            identity=identity,
            occurrence=occurrence,
            message_index=msg_index,
        ))
    return tuple(indexed)


def _remove_track_messages(
    track: mido.MidiTrack,
    remove_indices: set[int],
) -> None:
    absolute: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    tick = 0
    for msg in track:
        tick += msg.time
        absolute.append((tick, msg))

    rebuilt = mido.MidiTrack()
    previous_tick = 0
    for index, (absolute_tick, msg) in enumerate(absolute):
        if index in remove_indices:
            continue
        rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
        previous_tick = absolute_tick

    track[:] = rebuilt


_REGISTRY = TechniqueRegistry()


def register_technique(
    canonical: str,
    level: TechniqueLevel,
    *,
    allow_structural_pitch_change: bool = False,
    allow_structural_velocity_change: bool = False,
    allow_structural_duration_change: bool = False,
) -> Callable[[TechniqueApply], TechniqueApply]:
    """Decorator para registrar uma tecnica no registro global."""

    register = _REGISTRY.register(
        canonical,
        level,
        allow_structural_pitch_change=allow_structural_pitch_change,
        allow_structural_velocity_change=allow_structural_velocity_change,
        allow_structural_duration_change=allow_structural_duration_change,
    )

    def decorator(func: TechniqueApply) -> TechniqueApply:
        _validate_global_apply_is_not_identity_stub(canonical, func)
        return register(func)

    return decorator


def get_technique(canonical: str) -> RegisteredTechnique:
    """Devolve uma tecnica registrada no registro global."""

    return _REGISTRY.get(canonical)


def apply_technique(
    canonical: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Despacha `canonical` para sua funcao registrada."""

    return _REGISTRY.apply(
        canonical,
        *args,
        **kwargs,
    )


def apply_technique_with_warnings(
    canonical: str,
    *args: Any,
    **kwargs: Any,
) -> TechniqueApplyResult:
    """Despacha `canonical` e devolve resultado com warnings do motor."""

    return _REGISTRY.apply_with_warnings(
        canonical,
        *args,
        **kwargs,
    )


def registered_techniques() -> tuple[RegisteredTechnique, ...]:
    """Snapshot ordenado das tecnicas registradas."""

    return _REGISTRY.registered()


def validate_registry_against_index(index: TechniqueIndex | None = None) -> None:
    """Garante que toda tecnica aplicavel possui manual versionado."""

    idx = index if index is not None else build_index()
    missing = [
        technique.canonical
        for technique in registered_techniques()
        if idx.get(technique.canonical) is None
    ]
    if missing:
        raise TechniqueRegistrationError(
            "tecnicas aplicaveis sem manual em knowledge/tecnicas/: "
            + ", ".join(sorted(missing))
        )


_IGNORED_IDENTITY_OPS = frozenset({
    "CACHE",
    "COPY_FREE_VARS",
    "EXTENDED_ARG",
    "NOP",
    "RESUME",
})
_LOAD_FAST_OPS = frozenset({"LOAD_FAST", "LOAD_FAST_BORROW"})


def _validate_global_apply_is_not_identity_stub(
    canonical: str,
    func: TechniqueApply,
) -> None:
    if _returns_first_argument_unchanged(func):
        raise TechniqueRegistrationError(
            f"tecnica aplicavel {canonical!r} usa aplicador neutro; "
            "registre somente tecnicas com implementacao real"
        )


def _returns_first_argument_unchanged(func: TechniqueApply) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False

    subject_name = None
    for parameter in signature.parameters.values():
        if parameter.name == "context":
            continue
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            subject_name = parameter.name
            break
    if subject_name is None:
        return False

    instructions = [
        instruction
        for instruction in dis.get_instructions(func)
        if instruction.opname not in _IGNORED_IDENTITY_OPS
    ]
    while (
        len(instructions) >= 2
        and instructions[0].opname in _LOAD_FAST_OPS
        and instructions[0].argval == "context"
        and instructions[1].opname == "STORE_FAST"
    ):
        instructions = instructions[2:]

    return (
        len(instructions) == 2
        and instructions[0].opname in _LOAD_FAST_OPS
        and instructions[0].argval == subject_name
        and instructions[1].opname == "RETURN_VALUE"
    )


@register_technique("drums.ghost_notes", "technique")
def _apply_drums_ghost_notes(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    from ._helpers import (
        iter_note_dicts,
        overlaps_same_pitch,
        rebuild_track,
        recipe_from_context,
        target_count,
        technique_from_manual,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique)

    notes = recipe.get("notes")
    if (
        not isinstance(notes, list)
        or not notes
        or not all(isinstance(note, int) for note in notes)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar notes na receita"
        )

    # Precedencia: `style.<familia>.parameters` do plano > receita da tool no
    # manual > `range` do parametro no manual. O que o plano declara tem que
    # COMANDAR o resultado; parametro aceito pelo schema, validado contra a
    # faixa do manual e depois ignorado na aplicacao e parametro mentiroso.
    # A faixa do plano ja foi validada contra o manual em `plan.validate` —
    # valor fora da faixa e erro la, nunca clamp silencioso aqui.
    velocity_range = context.parameters.get("velocity")
    if velocity_range is None:
        velocity_range = recipe.get("velocity")
    if velocity_range is None:
        params = {param.name: param for param in technique.parameters}
        velocity_param = params.get("velocity")
        velocity_range = None if velocity_param is None else velocity_param.range
    if (
        not isinstance(velocity_range, (list, tuple))
        or len(velocity_range) != 2
        or not all(isinstance(value, (int, float)) for value in velocity_range)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar velocity [min, max]"
        )
    velocity_lo = int(velocity_range[0])
    velocity_hi = int(velocity_range[1])
    snare_notes = {
        note
        for tool_recipe in technique.tools.values()
        for note in tool_recipe.get("notes", [])
        if isinstance(note, int)
    }

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    sixteenth = max(1, ticks_per_beat // 4)
    gate = max(1, sixteenth // 2)
    # Ghost note mora DENTRO de uma levada, entre dois backbeats que de fato
    # se seguem. Backbeats consecutivos separados por mais que um compasso
    # (4 tempos em 4/4, o que ja cobre meio-tempo com caixa so no 3) nao sao
    # um intervalo de groove: sao a borda de um break. Emparelhar por cima
    # do break faz o loop varrer semicolcheias por cima do silencio e semear
    # ghost no vazio — foi o que apareceu nos compassos 53-54 de DEIXE IR,
    # com a nota estrutural mais proxima a 18 tempos de distancia.
    max_groove_interval = ticks_per_beat * 4
    rng = context.rng("positions")
    velocity_rng = context.rng("velocity")
    density = context.parameters.get("density")

    def simultaneous_count_at(existing, channel, tick):
        return sum(
            1
            for note in existing
            if note["channel"] == channel
            and note["start"] == tick
        )

    def note_exists(existing, channel, pitch, tick):
        return any(
            note["channel"] == channel
            and note["pitch"] == pitch
            and note["start"] == tick
            for note in existing
        )

    def violates_position_rules(candidate, selected, interval_counts):
        tick = candidate["tick"]
        interval_start = candidate["interval_start"]
        selected_ticks = {item["tick"] for item in selected}
        if interval_counts.get(interval_start, 0) >= 2:
            return True
        if (
            tick == interval_start + sixteenth
            and interval_start + 2 * sixteenth in selected_ticks
        ):
            return True
        if (
            tick == interval_start + 2 * sixteenth
            and interval_start + sixteenth in selected_ticks
        ):
            return True
        triples = (
            (tick - 2 * sixteenth, tick - sixteenth),
            (tick - sixteenth, tick + sixteenth),
            (tick + sixteenth, tick + 2 * sixteenth),
        )
        return any(a in selected_ticks and b in selected_ticks for a, b in triples)

    def select_candidates(candidates):
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        selected = []
        interval_counts = {}
        wanted = target_count(len(shuffled), density)
        for candidate in shuffled:
            # Teto checado ANTES de acrescentar. Checar depois deixava
            # `wanted == 0` passar sempre por uma nota: a primeira candidata
            # entrava e so entao o loop parava, entao `density=0.0` ainda
            # escrevia uma ghost no MIDI.
            if len(selected) >= wanted:
                break
            if violates_position_rules(candidate, selected, interval_counts):
                continue
            selected.append(candidate)
            interval_start = candidate["interval_start"]
            interval_counts[interval_start] = interval_counts.get(interval_start, 0) + 1
        return sorted(selected, key=lambda item: item["tick"])

    for track_index, track in enumerate(mid.tracks):
        existing = list(iter_note_dicts(track, track_index=track_index))
        backbeats = sorted({
            note["start"]
            for note in existing
            if note["channel"] == 9
            and note["pitch"] in snare_notes
            and note["velocity"] > velocity_hi
        })
        if len(backbeats) < 2:
            continue

        candidates = []
        for current, following in zip(backbeats, backbeats[1:], strict=False):
            if following - current > max_groove_interval:
                continue
            tick = current + sixteenth
            while tick < following:
                if tick != following - sixteenth:
                    channel = 9
                    pitch = int(notes[len(candidates) % len(notes)])
                    end_tick = tick + gate
                    if not overlaps_same_pitch(
                        existing, channel, pitch, tick, end_tick
                    ) and (
                        note_exists(existing, channel, pitch, tick)
                        or simultaneous_count_at(existing, channel, tick) < 2
                    ):
                        candidates.append({
                            "tick": tick,
                            "interval_start": current,
                            "channel": channel,
                            "pitch": pitch,
                        })
                tick += sixteenth

        for candidate in select_candidates(candidates):
            velocity = velocity_rng.randint(velocity_lo, velocity_hi)
            rebuild_track(
                track,
                added_notes=({
                    "channel": candidate["channel"],
                    "pitch": candidate["pitch"],
                    "velocity": velocity,
                    "start": candidate["tick"],
                    "end": candidate["tick"] + gate,
                },),
            )
    return mid


@register_technique("drums.flam", "technique")
def _apply_drums_flam(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    from ._helpers import (
        DRUM_HAND_FOOT_NOTES,
        iter_note_dicts,
        overlaps_same_pitch,
        parameter_value,
        rebuild_track,
        recipe_from_context,
        select_by_density,
        technique_from_manual,
        ticks_per_ms,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique)

    gap_ms = parameter_value(context, technique, "gap_ms")
    ratio = parameter_value(context, technique, "grace_velocity_ratio")
    ceiling_ms = parameter_value(context, technique, "reading_ceiling_ms")
    if gap_ms is None or ratio is None or ceiling_ms is None:
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar gap_ms, "
            "grace_velocity_ratio e reading_ceiling_ms no manual ou no plano"
        )
    if gap_ms <= 0 or ceiling_ms <= 0 or gap_ms > ceiling_ms:
        return mid
    ratio = max(0.0, min(1.0, ratio))

    snare_main_notes = recipe.get("notes_main")
    if (
        not isinstance(snare_main_notes, list)
        or not snare_main_notes
        or not all(isinstance(note, int) for note in snare_main_notes)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar notes_main na receita"
        )
    tom_notes = recipe.get("tom_notes")
    if (
        not isinstance(tom_notes, list)
        or not tom_notes
        or not all(isinstance(note, int) for note in tom_notes)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar tom_notes na receita"
        )

    if context.tool == "superior_drummer":
        grace_notes = recipe.get("notes")
    else:
        grace_notes = recipe.get("notes_grace")
    if (
        not isinstance(grace_notes, list)
        or not grace_notes
        or not all(isinstance(note, int) for note in grace_notes)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar notes/notes_grace "
            "na receita"
        )

    gap_ticks = max(1, int(round(gap_ms * ticks_per_ms(mid))))
    density = context.parameters.get("density")

    def simultaneous_hands(existing, channel, tick):
        return sum(
            1
            for note in existing
            if note["channel"] == channel
            and note["start"] == tick
            and note["pitch"] not in DRUM_HAND_FOOT_NOTES
        )

    candidates = []
    for track_index, track in enumerate(mid.tracks):
        existing = list(iter_note_dicts(track, track_index=track_index))
        by_start: dict[tuple[int, int], list[dict[str, int]]] = {}
        for note in existing:
            if note["channel"] != 9:
                continue
            by_start.setdefault((note["channel"], note["start"]), []).append(note)

        for note in existing:
            if note["channel"] != 9:
                continue
            is_snare = note["pitch"] in snare_main_notes and note["velocity"] > 45
            simultaneous_toms = [
                item for item in by_start.get((note["channel"], note["start"]), [])
                if item["pitch"] in tom_notes
            ]
            is_tom_flam = (
                note["pitch"] in tom_notes
                and len(simultaneous_toms) >= 2
                and note is not simultaneous_toms[0]
            )
            if not (is_snare or is_tom_flam):
                continue

            grace_start = note["start"] - gap_ticks
            if grace_start < 0:
                continue
            grace_end = note["start"]
            grace_pitch = (
                int(grace_notes[len(candidates) % len(grace_notes)])
                if is_snare
                else int(note["pitch"])
            )
            if simultaneous_hands(existing, note["channel"], grace_start) >= 2:
                continue
            if overlaps_same_pitch(
                existing,
                int(note["channel"]),
                grace_pitch,
                grace_start,
                grace_end,
            ):
                continue
            candidates.append({
                "track_index": track_index,
                "channel": int(note["channel"]),
                "pitch": grace_pitch,
                "start": grace_start,
                "end": grace_end,
                "velocity": max(1, min(126, int(round(note["velocity"] * ratio)))),
            })

    for candidate in select_by_density(
        candidates,
        density=density,
        rng=context.rng("targets"),
        sort_key=lambda item: (
            item["track_index"],
            item["start"],
            item["pitch"],
        ),
    ):
        rebuild_track(
            mid.tracks[candidate["track_index"]],
            added_notes=(candidate,),
        )
    return mid


@register_technique("drums.accent_hierarchy", "humanize")
def _apply_drums_accent_hierarchy(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Redistribui velocity em camadas (acento/primario/normal/suave/ghost).

    Reimplementacao pos-issue #50. A versao antiga decidia camada so pela
    posicao metrica e rebaixava a virada inteira porque virada e feita de
    contratempo. Aqui:

      - Detectamos virada via `tools.techniques._fill_detection.fill_windows`,
        que reusa o padrao de agrupamento por gap de `drums.accented_roll` e
        aplica os quatro criterios da CONVENCAO documentada no bloco
        `accent_hierarchy` do manual.
      - Dentro de virada, tom/caixa/prato leem "acento de virada" (105-120,
        exatamente a camada da tabela 2.2 que o motor antigo nunca usou).
      - Fora de virada, backbeat/downbeat mapeiam para acento/primario e o
        resto para a camada correspondente.
      - Camadas vem do manual via `load_range_resolver`, com precedencia
        `context.parameters` > `context.recipe` > manual.
      - INVARIANTE DE PRESSAO: nota com velocity de origem > teto suave nunca
        sai <= teto suave. Somada ao teto duro (~115), a saida cabe entre
        `soft_ceiling+1` e `hard_ceiling`. Esta invariante e o que impede a
        inversao que motivou a remocao original.
      - Notas ja em faixa suave/ghost na origem NAO sao empurradas para cima.
      - `density=0` desliga (via `density_disabled`), como as demais tecnicas.
    """

    from ._fill_detection import fill_windows, piece_family
    from ._helpers import density_disabled, iter_note_dicts, rebuild_track
    from ._param_range import load_range_resolver

    if density_disabled(context):
        return mid

    _, _range = load_range_resolver(context)

    def _mid(name: str, default: int) -> int:
        rng = _range(name)
        if rng is None:
            return default
        return int(round((rng[0] + rng[1]) / 2))

    def _lo(name: str, default: int) -> int:
        rng = _range(name)
        if rng is None:
            return default
        return int(round(rng[0]))

    def _hi(name: str, default: int) -> int:
        rng = _range(name)
        if rng is None:
            return default
        return int(round(rng[1]))

    accent_mid = _mid("accent", 112)
    accent_hi = _hi("accent", 120)
    primary_mid = _mid("primary", 107)
    primary_hi = _hi("primary", 115)
    normal_mid = _mid("normal", 90)
    soft_mid = _mid("soft", 67)
    ghost_mid = _mid("ghost", 32)
    soft_ceiling = _hi("soft", 79)
    normal_floor = _lo("normal", 80)
    hard_ceiling = _mid("hard_ceiling", 115)

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    sixteenth = max(1, ticks_per_beat // 4)

    for track in mid.tracks:
        drum_notes = [
            note for note in iter_note_dicts(track)
            if int(note["channel"]) == 9
        ]
        if not drum_notes:
            continue
        windows = fill_windows(drum_notes, ticks_per_beat=ticks_per_beat)

        velocity_by_index: dict[int, int] = {}
        for note in drum_notes:
            original = int(note["velocity"])
            if original <= normal_floor:
                continue
            family = piece_family(int(note["pitch"]))
            position = round(int(note["start"]) / sixteenth) % 16
            on_backbeat = position in (4, 12)
            on_downbeat = position in (0, 8)
            note_tick = int(note["start"])
            is_fill = any(
                start <= note_tick <= end for start, end in windows
            )

            if is_fill:
                if family in ("tom", "snare", "cymbal"):
                    target = accent_hi
                elif family == "kick":
                    target = primary_hi
                elif family == "hihat":
                    target = normal_mid
                else:
                    target = normal_mid
            else:
                if family == "snare":
                    target = accent_mid if on_backbeat else ghost_mid
                elif family == "kick":
                    target = primary_mid if on_downbeat else normal_mid
                elif family == "hihat":
                    target = normal_mid if (on_backbeat or on_downbeat) else soft_mid
                elif family == "tom":
                    target = soft_mid
                elif family == "cymbal":
                    target = accent_mid if on_downbeat else primary_mid
                else:
                    target = normal_mid

            if original > soft_ceiling:
                target = max(soft_ceiling + 1, min(target, original))
            target = max(1, min(hard_ceiling, target))
            if target != original:
                velocity_by_index[int(note["note_on_index"])] = target

        if velocity_by_index:
            rebuild_track(track, velocity_by_index=velocity_by_index)

    return mid


@register_technique("drums.accented_roll", "humanize")
def _apply_drums_accented_roll(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    from ._helpers import (
        density_disabled,
        iter_note_dicts,
        parameter_value,
        rebuild_track,
        recipe_from_context,
        select_by_density,
        sort_by_track_start_pitch,
        technique_from_manual,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique)

    if density_disabled(context):
        return mid

    density = context.parameters.get("density")
    select_rng = context.rng("accented_roll_density")

    accent_velocity = parameter_value(context, technique, "velocity_acento")
    soft_velocity = parameter_value(context, technique, "velocity_suave")
    dominant_delta = parameter_value(context, technique, "delta_mao_dominante")
    pre_accent_delta = parameter_value(context, technique, "delta_lift_pre_acento")
    if (
        accent_velocity is None
        or soft_velocity is None
        or dominant_delta is None
        or pre_accent_delta is None
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar velocity_acento, "
            "velocity_suave, delta_mao_dominante e delta_lift_pre_acento"
        )

    target_notes = recipe.get("notes")
    if target_notes is not None and (
        not isinstance(target_notes, list)
        or not target_notes
        or not all(isinstance(note, int) for note in target_notes)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar notes como lista "
            "de inteiros na receita"
        )
    target_note_set = None if target_notes is None else set(target_notes)

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    max_gap_ticks = max(1, ticks_per_beat // 4)
    top_pressure_floor = 105
    low_layer_ceiling = 79

    def roll_sequences(notes):
        sequences = []
        current = []
        previous = None
        for note in notes:
            if (
                previous is not None
                and note["start"] - previous["start"] <= max_gap_ticks
                and note["channel"] == previous["channel"]
            ):
                current.append(note)
            else:
                if len(current) >= 4:
                    sequences.append(current)
                current = [note]
            previous = note
        if len(current) >= 4:
            sequences.append(current)
        return sequences

    def contour_velocity(position, original_velocity):
        cycle_pos = position % 4
        cycle_index = position // 4
        if cycle_pos == 0:
            target = int(round(float(accent_velocity) - (cycle_index % 2) * 2))
        else:
            target = float(soft_velocity)
            if cycle_pos == 2:
                target += float(dominant_delta)
            if cycle_pos == 3:
                target += float(pre_accent_delta)
            target = int(round(target))

        # INVARIANTE DE PRESSAO: nota que a origem escreveu ACIMA da faixa
        # suave nunca pode sair NA faixa suave ou abaixo dela. O gate
        # antigo comparava contra `top_pressure_floor` (105) em vez de
        # `low_layer_ceiling` (79) — uma nota de origem 104 passava direto
        # pelo `>= 105` e saia esmagada a 55. E o mesmo defeito que tirou
        # `drums.accent_hierarchy` do motor, uma oitava acima.
        if original_velocity > low_layer_ceiling and target <= low_layer_ceiling:
            target = min(top_pressure_floor, max(target, original_velocity))
        return max(1, min(126, target))

    for track_index, track in enumerate(mid.tracks):
        notes = [
            note for note in iter_note_dicts(track)
            if note["channel"] == 9
            and (target_note_set is None or note["pitch"] in target_note_set)
        ]
        if not notes:
            continue

        sequences = roll_sequences(sorted(notes, key=lambda item: item["start"]))
        # `density` seleciona QUAIS rulos recebem o contorno humano — nao
        # nota a nota dentro de um rulo, o que embaralharia a sequencia
        # posicional que da sentido a mao dominante/lift pre-acento.
        # Sequencia nao selecionada mantem a velocity da origem intacta.
        candidates = [
            {
                "track_index": track_index,
                "start": sequence[0]["start"],
                "pitch": sequence[0]["pitch"],
                "sequence": sequence,
            }
            for sequence in sequences
        ]
        selected = select_by_density(
            candidates,
            density=density,
            rng=select_rng,
            sort_key=sort_by_track_start_pitch,
        )

        velocity_by_index = {}
        for candidate in selected:
            for position, note in enumerate(candidate["sequence"]):
                velocity_by_index[note["note_on_index"]] = contour_velocity(
                    position,
                    note["velocity"],
                )
        if velocity_by_index:
            rebuild_track(track, velocity_by_index=velocity_by_index)

    return mid


@register_technique(
    "drums.articulation_diff",
    "technique",
    allow_structural_pitch_change=True,
)
def _apply_drums_articulation_diff(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    from ._helpers import (
        density_disabled,
        iter_note_dicts,
        notes_for,
        rebuild_track,
        recipe_from_context,
        select_by_density,
        sort_by_track_start_pitch,
        technique_from_manual,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique, require_explicit_tool=True)

    density = context.parameters.get("density")
    select_rng = context.rng("articulation_diff_density")

    hat_tip = notes_for(recipe, "hat_tip", context.canonical)
    hat_edge = notes_for(recipe, "hat_edge", context.canonical)
    ride_bow_tip = notes_for(recipe, "ride_bow_tip", context.canonical)
    ride_bow_shank = notes_for(recipe, "ride_bow_shank", context.canonical)
    ride_bell = notes_for(recipe, "ride_bell", context.canonical)
    snare_center = notes_for(recipe, "snare_center", context.canonical)
    snare_rimshot = notes_for(recipe, "snare_rimshot", context.canonical)

    if density_disabled(context):
        return mid

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    bar_ticks = ticks_per_beat * 4

    def replacement_for(note):
        pitch = note["pitch"]
        start = note["start"]
        velocity = note["velocity"]
        beat_in_bar = start % bar_ticks
        beat_position = beat_in_bar // ticks_per_beat

        if pitch in hat_tip or pitch in hat_edge:
            if start % ticks_per_beat == 0:
                return hat_edge[0]
            return hat_tip[0]

        if pitch in ride_bow_tip or pitch in ride_bow_shank or pitch in ride_bell:
            if beat_in_bar == 0:
                return ride_bell[0]
            if start % ticks_per_beat == 0 and beat_position in {2, 3}:
                return ride_bow_shank[0]
            return ride_bow_tip[0]

        if pitch in snare_center or pitch in snare_rimshot:
            if start % ticks_per_beat == 0 and beat_position in {1, 3} and velocity >= 90:
                return snare_rimshot[0]
            return snare_center[0]

        return pitch

    for track_index, track in enumerate(mid.tracks):
        candidates = []
        for note in iter_note_dicts(track, include_note_off_index=True):
            if note["channel"] != 9:
                continue
            replacement = replacement_for(note)
            if replacement == note["pitch"]:
                continue
            candidates.append({
                "track_index": track_index,
                "start": note["start"],
                "pitch": note["pitch"],
                "note_on_index": note["note_on_index"],
                "note_off_index": note["note_off_index"],
                "replacement": replacement,
            })

        # `density` seleciona QUAIS batidas trocam de articulacao; as nao
        # selecionadas mantem a nota original. Sem isso, `density` era
        # aceito pelo schema e ignorado — 0.1 e 1.0 produziam saida
        # identica.
        selected = select_by_density(
            candidates,
            density=density,
            rng=select_rng,
            sort_key=sort_by_track_start_pitch,
        )

        pitch_by_index = {}
        for candidate in selected:
            pitch_by_index[candidate["note_on_index"]] = candidate["replacement"]
            pitch_by_index[candidate["note_off_index"]] = candidate["replacement"]
        if pitch_by_index:
            rebuild_track(track, note_by_index=pitch_by_index)

    return mid


@register_technique(
    "drums.buzz_roll",
    "technique",
)
def _apply_drums_buzz_roll(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    from ._helpers import (
        density_disabled,
        hand_starts,
        iter_note_dicts,
        manual_value,
        notes_for,
        positive_float,
        rebuild_track,
        recipe_from_context,
        selected_by_track,
        technique_from_manual,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique, require_explicit_tool=True)

    grid = manual_value(context, technique, "grid")
    if grid != "32nd/64th":
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar grid='32nd/64th'"
        )

    ramp = manual_value(context, technique, "velocity_ramp")
    if not isinstance(ramp, dict):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar velocity_ramp "
            "como objeto no manual"
        )

    if ramp.get("shape") != "linear":
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar velocity_ramp.shape='linear'"
        )
    start_ratio = positive_float(
        ramp, "start_ratio", context.canonical, location="em velocity_ramp"
    )
    end_ratio = positive_float(
        ramp, "end_ratio", context.canonical, location="em velocity_ramp"
    )
    gate_ratio = positive_float(
        ramp, "gate_ratio", context.canonical, location="em velocity_ramp"
    )
    window_beats = positive_float(
        ramp, "window_beats", context.canonical, location="em velocity_ramp"
    )
    if start_ratio > end_ratio:
        return mid

    target_notes = notes_for(recipe, "notes", context.canonical)
    density = context.parameters.get("density")
    if density_disabled(context):
        return mid

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    grid_ticks = (
        max(1, int(round(ticks_per_beat * 4 / 32))),
        max(1, int(round(ticks_per_beat * 4 / 64))),
    )
    roll_window_ticks = max(grid_ticks[0], int(round(ticks_per_beat * window_beats)))
    target_velocity_floor = 90
    # Mesma convencao de borda de pausa usada em `drums.ghost_notes`: mais
    # de um compasso sem atividade e silencio estrutural, nao groove. Sem
    # isso o rufo nasce do nada antes de uma caixa isolada depois de uma
    # pausa longa.
    max_silence_ticks = ticks_per_beat * 4

    def build_ornaments(note, existing):
        end_tick = int(note["start"])
        start_tick = max(0, end_tick - roll_window_ticks)
        if end_tick - start_tick < grid_ticks[0] * 2:
            return ()

        # Nao semear em silencio: precisa haver atividade de bateria ate
        # `max_silence_ticks` antes da janela do rufo.
        has_recent_activity = any(
            other is not note
            and other["channel"] == 9
            and other["end"] <= start_tick
            and start_tick - other["end"] <= max_silence_ticks
            for other in existing
        )
        if start_tick > 0 and not has_recent_activity:
            return ()

        # Nao sobrepor nota estrutural existente na mesma pitch: sobrepor
        # reembaralha o pareamento FIFO note_on/note_off e o contrato
        # explode com "duracao de nota estrutural mudou". Rufo so ocupa
        # espaco realmente vazio na mesma pitch.
        if any(
            other is not note
            and other["channel"] == note["channel"]
            and other["pitch"] == note["pitch"]
            and other["start"] < end_tick
            and other["end"] > start_tick
            for other in existing
        ):
            return ()

        ticks = []
        tick = start_tick
        step_index = 0
        while tick < end_tick:
            step = grid_ticks[step_index % len(grid_ticks)]
            next_tick = min(end_tick, tick + step)
            if next_tick >= end_tick:
                break
            ticks.append((tick, next_tick, step))
            tick = next_tick
            step_index += 1

        if len(ticks) < 3:
            return ()

        ornaments = []
        for position, (start, next_tick, step) in enumerate(ticks):
            if hand_starts(existing, start) >= 2:
                continue
            ratio_span = end_ratio - start_ratio
            ramp_position = position / max(1, len(ticks) - 1)
            velocity_ratio = start_ratio + ratio_span * ramp_position
            velocity = max(1, min(126, int(round(note["velocity"] * velocity_ratio))))
            ornaments.append({
                "track_index": int(note["track_index"]),
                "channel": int(note["channel"]),
                "pitch": int(note["pitch"]),
                "start": int(start),
                "end": int(min(next_tick, start + max(1, int(round(step * gate_ratio))))),
                "velocity": velocity,
            })
        return tuple(ornaments)

    candidates = []
    target_note_set = set(target_notes)
    for track_index, track in enumerate(mid.tracks):
        existing = iter_note_dicts(track, track_index=track_index)
        for note in existing:
            if (
                note["channel"] != 9
                or note["pitch"] not in target_note_set
                or note["velocity"] < target_velocity_floor
                or note["start"] <= 0
            ):
                continue
            ornaments = build_ornaments(note, existing)
            if ornaments:
                candidates.append({
                    "track_index": track_index,
                    "start": int(note["start"]),
                    "pitch": int(note["pitch"]),
                    "ornaments": ornaments,
                })

    by_track = selected_by_track(
        candidates,
        density=density,
        rng=context.rng("targets"),
        value=lambda candidate: candidate["ornaments"],
    )

    for track_index, ornaments in by_track.items():
        rebuild_track(mid.tracks[track_index], added_notes=ornaments)

    return mid


@register_technique(
    "drums.cymbal_choke",
    "technique",
    allow_structural_duration_change=True,
)
def _apply_drums_cymbal_choke(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    from ._helpers import (
        density_disabled,
        iter_note_dicts,
        notes_for,
        positive_float,
        rebuild_track,
        recipe_from_context,
        selected_by_track,
        technique_from_manual,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique, require_explicit_tool=True)

    target_notes = notes_for(recipe, "target_notes", context.canonical)
    choke_notes = notes_for(recipe, "notes", context.canonical)

    choke_after_beats = positive_float(
        recipe, "choke_after_beats", context.canonical
    )
    short_ceiling_beats = positive_float(
        recipe, "short_ceiling_beats", context.canonical
    )

    density = context.parameters.get("density")
    if density_disabled(context):
        return mid

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    choke_after_ticks = max(1, int(round(ticks_per_beat * choke_after_beats)))
    short_ceiling_ticks = max(1, int(round(ticks_per_beat * short_ceiling_beats)))
    choke_gate_ticks = max(1, min(choke_after_ticks, ticks_per_beat // 16))

    candidates = []
    target_note_set = set(target_notes)
    for track_index, track in enumerate(mid.tracks):
        for note in iter_note_dicts(
            track,
            track_index=track_index,
            include_note_off_index=True,
        ):
            if (
                note["channel"] != 9
                or note["pitch"] not in target_note_set
                or note["duration"] <= short_ceiling_ticks
            ):
                continue
            choke_start = note["start"] + choke_after_ticks
            if choke_start >= note["end"]:
                continue
            candidates.append({
                "track_index": track_index,
                "channel": int(note["channel"]),
                "pitch": int(note["pitch"]),
                "start": int(note["start"]),
                "choke_start": int(choke_start),
                "choke_end": int(choke_start + choke_gate_ticks),
                "velocity": max(1, min(126, int(note["velocity"]))),
                "note_off_index": int(note["note_off_index"]),
            })

    by_track = selected_by_track(
        candidates,
        density=density,
        rng=context.rng("targets"),
        value=lambda candidate: candidate,
    )

    for track_index, track_candidates in by_track.items():
        end_by_index = {
            candidate["note_off_index"]: candidate["choke_start"]
            for candidate in track_candidates
        }
        chokes = [
            {
                "channel": candidate["channel"],
                "pitch": choke_notes[position % len(choke_notes)],
                "velocity": candidate["velocity"],
                "start": candidate["choke_start"],
                "end": candidate["choke_end"],
            }
            for position, candidate in enumerate(track_candidates)
        ]
        rebuild_track(
            mid.tracks[track_index],
            absolute_tick_by_index=end_by_index,
            added_notes=chokes,
        )

    return mid


@register_technique("drums.microtiming", "humanize")
def _apply_drums_microtiming(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    import math as _math

    from ._helpers import (
        density_disabled,
        iter_note_dicts,
        note_on_events,
        parameter_value,
        rebuild_track,
        recipe_from_context,
        technique_from_manual,
        ticks_per_ms,
    )

    technique = technique_from_manual(context)
    recipe = recipe_from_context(context, technique)

    hihat_notes = recipe.get("hihat_notes")
    if (
        not isinstance(hihat_notes, list)
        or not hihat_notes
        or not all(isinstance(note, int) for note in hihat_notes)
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar hihat_notes na receita"
        )
    hihat_note_set = set(hihat_notes)

    if density_disabled(context):
        return mid

    sigma_ms = parameter_value(context, technique, "hihat_timing_sigma_ms")
    autocorr = parameter_value(context, technique, "hihat_autocorr_lag1")
    perception_ms = parameter_value(context, technique, "perception_threshold_ms")
    musical_hi_ms = parameter_value(context, technique, "musical_range_ms")
    sloppy_ms = parameter_value(context, technique, "sloppy_threshold_ms")
    if (
        sigma_ms is None
        or autocorr is None
        or perception_ms is None
        or musical_hi_ms is None
        or sloppy_ms is None
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} precisa declarar "
            "hihat_timing_sigma_ms, hihat_autocorr_lag1, "
            "perception_threshold_ms, musical_range_ms e sloppy_threshold_ms"
        )
    if sigma_ms <= 0 or sloppy_ms <= 0:
        return mid
    autocorr = max(-0.95, min(0.95, float(autocorr)))
    max_abs_ms = min(float(sloppy_ms), float(musical_hi_ms))
    if max_abs_ms <= 0:
        return mid

    ticks_ms = ticks_per_ms(mid)
    max_abs_ticks = max(1, int(_math.floor(max_abs_ms * ticks_ms)))
    perception_ticks = max(1, int(round(float(perception_ms) * ticks_ms)))

    def clamp_offset(offset, current_tick, previous_tick, next_tick):
        lower = -max_abs_ticks
        upper = max_abs_ticks
        if current_tick + lower < 0:
            lower = -current_tick
        if previous_tick is not None:
            lower = max(lower, previous_tick - current_tick)
        if next_tick is not None:
            upper = min(upper, next_tick - current_tick)
        if lower > upper:
            return 0
        clamped = max(lower, min(upper, offset))
        if (
            clamped != 0
            and abs(clamped) < perception_ticks
            and lower <= (perception_ticks if clamped > 0 else -perception_ticks) <= upper
        ):
            return perception_ticks if clamped > 0 else -perception_ticks
        return clamped

    rng = context.rng("hihat-offsets")
    previous_series_ms = 0.0
    scale = _math.sqrt(max(0.0, 1.0 - autocorr * autocorr))

    for track in mid.tracks:
        pairs = list(iter_note_dicts(track, include_note_off_index=True))
        if not pairs:
            continue
        ons = note_on_events(track)
        previous_by_index = {}
        next_by_index = {}
        for pos, (msg_index, _tick) in enumerate(ons):
            previous_by_index[msg_index] = ons[pos - 1][1] if pos > 0 else None
            next_by_index[msg_index] = ons[pos + 1][1] if pos + 1 < len(ons) else None

        shifts = {}
        for pair in pairs:
            if pair["channel"] != 9 or pair["pitch"] not in hihat_note_set:
                continue
            innovation = rng.gauss(0.0, float(sigma_ms)) * scale
            series_ms = autocorr * previous_series_ms + innovation
            previous_series_ms = series_ms
            raw_ticks = int(round(series_ms * ticks_ms))
            if raw_ticks == 0:
                raw_ticks = 1 if series_ms >= 0 else -1
            offset = clamp_offset(
                raw_ticks,
                pair["start"],
                previous_by_index[pair["note_on_index"]],
                next_by_index[pair["note_on_index"]],
            )
            if offset == 0:
                continue
            shifts[pair["note_on_index"]] = offset
            shifts[pair["note_off_index"]] = offset

        if shifts:
            absolute_tick_by_index = {}
            tick = 0
            for msg_index, msg in enumerate(track):
                tick += msg.time
                absolute_tick_by_index[msg_index] = tick + shifts.get(msg_index, 0)
            rebuild_track(track, absolute_tick_by_index=absolute_tick_by_index)

    return mid


@register_technique("bass.velocity_contour", "humanize")
def _apply_bass_velocity_contour(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Contorno dinamico para linha de baixo.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le os numeros do manual pelo indice dentro da aplicacao.
      - Precedencia `context.parameters` > receita > `range` do manual.
      - INVARIANTE DE PRESSAO: nota da origem no topo (>= P75) nao pode sair na
        faixa mais baixa (< P25) da propria origem — ordem por posicao original.
      - A mediana de velocity da saida nunca cai abaixo da mediana da origem.
      - Determinismo por seed atraves de `context.rng()`.
    """

    from ._param_range import load_range_resolver

    _, _range = load_range_resolver(context)

    def _as_int(name: str, default: int) -> int:
        rng = _range(name) or (default, default)
        return int(round((rng[0] + rng[1]) / 2))

    span_tipico = max(1, _as_int("span_tipico", 40))
    accent = max(2, span_tipico // 6)
    jitter_hi = max(1, span_tipico // 10)

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid

    rng = context.rng("contour")

    for track in mid.tracks:
        note_positions: list[tuple[mido.Message, int]] = []
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                note_positions.append((msg, tick))

        if not note_positions:
            continue

        original = [msg.velocity for msg, _ in note_positions]
        sorted_orig = sorted(original)
        n = len(sorted_orig)
        p25 = sorted_orig[max(0, (n - 1) // 4)]
        p75 = sorted_orig[min(n - 1, (3 * (n - 1)) // 4)]
        median_orig = sorted_orig[n // 2]

        proposed: list[int] = []
        for msg, tick_pos in note_positions:
            beat_index = tick_pos // ticks_per_beat
            beat_offset = tick_pos % ticks_per_beat
            if beat_offset == 0 and beat_index % 4 in (0, 2):
                delta = +accent
            elif beat_offset == 0:
                delta = +max(1, accent // 2)
            else:
                delta = -max(1, accent // 3)
            delta += rng.randint(0, jitter_hi)
            proposed.append(max(1, min(127, msg.velocity + delta)))

        sorted_new = sorted(proposed)
        median_new = sorted_new[n // 2]
        if median_new < median_orig:
            offset = median_orig - median_new
            proposed = [min(127, v + offset) for v in proposed]

        for (msg, _), original_vel, new_vel in zip(
            note_positions, original, proposed, strict=True,
        ):
            if original_vel >= p75 and new_vel < p25:
                new_vel = max(new_vel, p25)
            msg.velocity = new_vel

    return mid


@register_technique("bass.ghost_notes", "technique")
def _apply_bass_ghost_notes(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Dead notes entre notas estruturais do baixo.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `velocity`, `velocity_relativa_pct` e `gate_pct` pelo indice.
      - Precedencia `context.parameters` > receita > `range` do manual.
      - `density = 0.0` DESLIGA — teto checado ANTES de acrescentar candidato.
      - Nao semeia em silencio: intervalo entre notas estruturais > 1 compasso
        e borda de pausa, nao groove.
      - Ghost herda pitch da nota estrutural anterior (mesma corda que a mao ja
        esta) — nunca inventa altura.
      - Idempotente: reaplicar com a mesma seed dispara o dedup do dispatch.
      - Receita `modo_bass` insere keyswitch A#-1 (10) simultaneo ao ghost;
        keyswitch fica fora da regiao tocavel do baixo e o validador fisico
        ignora pitches declarados como keyswitch na receita.
    """

    import mido as _mido

    from ._param_range import load_range_resolver

    technique, _range = load_range_resolver(context)

    velocity_range = _range("velocity") or (25.0, 50.0)
    velocity_lo = max(1, int(velocity_range[0]))
    velocity_hi = max(velocity_lo, int(velocity_range[1]))

    relative_range = _range("velocity_relativa_pct") or (20.0, 40.0)
    relative_hi = max(1.0, float(relative_range[1]))

    gate_range = _range("gate_pct") or (10.0, 25.0)
    gate_lo = max(1.0, float(gate_range[0]))
    gate_hi = max(gate_lo, float(gate_range[1]))

    density_raw = context.parameters.get("density")
    density: float | None
    if isinstance(density_raw, (int, float)) and not isinstance(density_raw, bool):
        density = float(density_raw)
    else:
        density = None

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    sixteenth = max(1, ticks_per_beat // 4)
    max_groove_interval = ticks_per_beat * 4

    keyswitch_pitch: int | None = None
    ks_raw = context.recipe.get("keyswitch") if context.recipe else None
    if isinstance(ks_raw, int):
        keyswitch_pitch = ks_raw

    position_rng = context.rng("positions")
    velocity_rng = context.rng("velocity")
    gate_rng = context.rng("gate")

    def target_count(size: int) -> int:
        if density is None:
            return size
        if density <= 0.0:
            return 0
        return max(1, min(size, int(round(size * density))))

    def read_pairs(track):
        return [
            (channel, pitch, start_tick, end_tick, velocity)
            for (
                channel,
                pitch,
                start_tick,
                end_tick,
                velocity,
                _note_on_index,
                _note_off_index,
            ) in _iter_note_pairs(track)
        ]

    def overlaps_structural(existing, channel, pitch, start_tick, end_tick):
        for chan, pit, start, end, _vel in existing:
            if chan != channel or pit != pitch:
                continue
            if start < end_tick and end > start_tick:
                return True
        return False

    def insert_events(track, events):
        # events: list of (tick, order_bias, mido.Message)
        absolute = []
        tick = 0
        order = 0
        for msg in track:
            tick += msg.time
            absolute.append((tick, 0, order, msg))
            order += 1
        for candidate_tick, bias, msg in events:
            absolute.append((candidate_tick, bias, order, msg))
            order += 1

        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _bias, _order, msg in sorted(
            absolute, key=lambda item: (item[0], item[1], item[2])
        ):
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    for track in mid.tracks:
        pairs = read_pairs(track)
        if len(pairs) < 2:
            continue

        by_channel: dict[int, list[tuple[int, int, int, int, int]]] = {}
        for pair in pairs:
            by_channel.setdefault(pair[0], []).append(pair)
        for channel_pairs in by_channel.values():
            channel_pairs.sort(key=lambda item: (item[2], item[3]))

        candidates: list[dict[str, int]] = []
        for channel_pairs in by_channel.values():
            for current, following in zip(
                channel_pairs, channel_pairs[1:], strict=False,
            ):
                _chan, cur_pitch, cur_start, cur_end, cur_vel = current
                _n_chan, _n_pitch, next_start, _n_end, _n_vel = following
                if next_start - cur_start > max_groove_interval:
                    continue
                tick = cur_start + sixteenth
                while tick < next_start:
                    sixteenth_in_beat = (tick % ticks_per_beat) // sixteenth
                    if sixteenth_in_beat in (1, 3):
                        candidates.append({
                            "tick": tick,
                            "channel": current[0],
                            "pitch": cur_pitch,
                            "reference_velocity": cur_vel,
                        })
                    tick += sixteenth

        if not candidates:
            continue

        shuffled = list(candidates)
        position_rng.shuffle(shuffled)
        wanted = target_count(len(shuffled))
        selected: list[dict[str, int]] = []
        seen_slots: set[tuple[int, int]] = set()
        for candidate in shuffled:
            if len(selected) >= wanted:
                break
            slot = (candidate["channel"], candidate["tick"])
            if slot in seen_slots:
                continue
            gate_pct = gate_rng.uniform(gate_lo, gate_hi)
            duration = max(1, int(round(sixteenth * gate_pct / 100.0)))
            end_tick = candidate["tick"] + duration
            if overlaps_structural(
                pairs,
                candidate["channel"],
                candidate["pitch"],
                candidate["tick"],
                end_tick,
            ):
                continue
            candidate["end_tick"] = end_tick
            candidate["velocity"] = min(
                velocity_hi,
                max(
                    velocity_lo,
                    min(
                        velocity_rng.randint(velocity_lo, velocity_hi),
                        int(round(candidate["reference_velocity"] * relative_hi / 100.0)),
                    ),
                ),
            )
            selected.append(candidate)
            seen_slots.add(slot)

        if not selected:
            continue

        selected.sort(key=lambda item: item["tick"])
        events = []
        for candidate in selected:
            channel = candidate["channel"]
            events.append((
                candidate["tick"],
                1,
                _mido.Message(
                    "note_on",
                    channel=channel,
                    note=candidate["pitch"],
                    velocity=candidate["velocity"],
                ),
            ))
            events.append((
                candidate["end_tick"],
                3,
                _mido.Message(
                    "note_off",
                    channel=channel,
                    note=candidate["pitch"],
                    velocity=0,
                ),
            ))
            if keyswitch_pitch is not None:
                events.append((
                    candidate["tick"],
                    0,
                    _mido.Message(
                        "note_on",
                        channel=channel,
                        note=keyswitch_pitch,
                        velocity=127,
                    ),
                ))
                events.append((
                    candidate["end_tick"],
                    4,
                    _mido.Message(
                        "note_off",
                        channel=channel,
                        note=keyswitch_pitch,
                        velocity=0,
                    ),
                ))
        insert_events(track, events)

    return mid


@register_technique("bass.palm_mute", "humanize")
def _apply_bass_palm_mute(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Palm mute na linha de baixo — abafamento pontual, nao geral.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `velocity` e `gate_pct` do manual via `build_index()`.
      - Precedencia `context.parameters` > receita > `range` do manual.
      - Aplica so onde o plano pedir por `density` explicita. `density` ausente
        ou <= 0 significa DESLIGAR — a tecnica nunca abafa a linha inteira por
        default; palm mute geral e ausencia de intencao musical, nao "seguro".
      - INVARIANTE DE PRESSAO: nota da origem no topo (>= P75) nao pode sair na
        faixa mais baixa (< P25) da propria origem, mesmo abafada.
      - Encurta pelo `gate_pct` do manual sobre a duracao original — nao inventa
        numero.
      - Determinismo por seed atraves de `context.rng()`.
    """

    import mido as _mido

    from ._param_range import load_range_resolver

    technique, _range = load_range_resolver(context)

    # O manual e explicito: "no MODO BASS mute NAO e um estilo separado: e
    # uma quantidade continua aplicada por cima do estilo ativo... NAO
    # EXISTE CC DE FABRICA. O plano precisa declarar qual CC o usuario
    # atribuiu, ou a tecnica nao sai." A receita `modo_bass` carrega
    # `cc: null` de proposito.
    #
    # Sem esta guarda, pedir `tool=modo_bass` sem declarar o CC caia no
    # comportamento generic (so encurta duracao e ajusta velocity) SEM
    # avisar — o usuario acharia que ganhou o palm mute continuo do plugin
    # e recebeu outra coisa. FALHA ALTA em vez de fallback silencioso.
    #
    # A emissao real da curva de CC continua fora do escopo desta correcao:
    # o manual pede "desenhe uma curva", nao um valor unico, e a FORMA dessa
    # curva nao tem fonte — inventa-la aqui repetiria o erro que motivou a
    # issue #53. Rastreado separadamente.
    if context.tool == "modo_bass" and context.recipe.get("cc") is None:
        cc_param = context.parameters.get("cc")
        if not isinstance(cc_param, int) or isinstance(cc_param, bool) or not (0 <= cc_param <= 127):
            raise ValueError(
                f"tecnica {context.canonical!r} com tool='modo_bass' precisa "
                "de style.bass.parameters.cc (0-127) declarado no plano — o "
                "MODO BASS nao tem CC de fabrica para palm mute"
            )

    velocity_range = _range("velocity") or (60.0, 100.0)
    velocity_lo = max(1, int(velocity_range[0]))
    velocity_hi = max(velocity_lo, int(velocity_range[1]))

    gate_range = _range("gate_pct") or (25.0, 50.0)
    gate_lo = max(1.0, float(gate_range[0]))
    gate_hi = max(gate_lo, float(gate_range[1]))

    density_raw = context.parameters.get("density")
    if not isinstance(density_raw, (int, float)) or isinstance(density_raw, bool):
        return mid
    density = float(density_raw)
    if density <= 0.0:
        return mid

    if mid.ticks_per_beat <= 0:
        return mid

    selection_rng = context.rng("selection")
    velocity_rng = context.rng("velocity")
    gate_rng = context.rng("gate")

    def collect_pairs(track):
        return list(_iter_note_pairs(track))

    for track in mid.tracks:
        pairs = collect_pairs(track)
        if not pairs:
            continue

        originals = [pair[4] for pair in pairs]
        sorted_orig = sorted(originals)
        n = len(sorted_orig)
        p25 = sorted_orig[max(0, (n - 1) // 4)]
        p75 = sorted_orig[min(n - 1, (3 * (n - 1)) // 4)]

        indices = list(range(len(pairs)))
        selection_rng.shuffle(indices)
        wanted = max(1, min(len(pairs), int(round(len(pairs) * density))))
        selected = set(indices[:wanted])

        new_velocity_by_msg: dict[int, int] = {}
        new_end_tick_by_msg: dict[int, int] = {}
        for pair_index, pair in enumerate(pairs):
            if pair_index not in selected:
                continue
            (
                _channel,
                _pitch,
                start_tick,
                end_tick,
                original_velocity,
                note_on_index,
                note_off_index,
            ) = pair
            duration = max(1, end_tick - start_tick)
            gate_pct = gate_rng.uniform(gate_lo, gate_hi)
            new_duration = max(1, int(round(duration * gate_pct / 100.0)))
            new_end_tick = start_tick + min(duration, new_duration)

            proposed = velocity_rng.randint(velocity_lo, velocity_hi)
            if original_velocity >= p75 and proposed < p25:
                proposed = p25
            proposed = max(1, min(127, proposed))

            new_velocity_by_msg[note_on_index] = proposed
            new_end_tick_by_msg[note_off_index] = new_end_tick

        absolute = []
        tick = 0
        for msg_index, msg in enumerate(track):
            tick += msg.time
            absolute_tick = new_end_tick_by_msg.get(msg_index, tick)
            absolute.append((absolute_tick, msg_index, msg))

        absolute.sort(key=lambda item: (item[0], item[1]))

        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, msg_index, msg in absolute:
            delta = absolute_tick - previous_tick
            if msg_index in new_velocity_by_msg:
                rebuilt.append(
                    msg.copy(time=delta, velocity=new_velocity_by_msg[msg_index])
                )
            else:
                rebuilt.append(msg.copy(time=delta))
            previous_tick = absolute_tick
        track[:] = rebuilt

    return mid


@register_technique(
    "bass.attack_style",
    "technique",
    allow_structural_velocity_change=True,
)
def _apply_bass_attack_style(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Estilo de ataque do baixo — dedo, palheta ou slap com keyswitch.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `keyswitch_dedo` (13), `keyswitch_palheta` (15), `keyswitch_slap` (18)
        e `keyswitch_forcar_primeiro`/`_segundo` (1/3) do MODO BASS pelo indice.
      - `context.parameters["style"]` COMANDA: `dedo`, `palheta` ou `slap`.
        Sem `style` ou estilo desconhecido: NO-OP (nunca reescreve a linha por
        default — estilo geral e ausencia de intencao musical).
      - Picked (palheta) alterna downstroke/upstroke DETERMINISTICAMENTE pela
        posicao na sequencia (par=down, impar=up); velocities lidas de
        `picked_downstroke_velocity` e `picked_upstroke_velocity` do manual.
      - `upstroke_atraso_ms` reposiciona so o keyswitch do upstroke, nunca a
        nota estrutural.
      - Keyswitch nao colide com nota musical: pitches 1, 3, 13, 15, 18 sao
        muito abaixo do floor do baixo (~28) e ficam fora do contrato
        estrutural via `_keyswitch_pitches_from_recipe`.
      - Idempotente: mesma seed insere keyswitches nas mesmas posicoes e o
        dedup do dispatch descarta a duplicata.
    """

    import mido as _mido

    from ._param_range import load_range_resolver
    from ._track_rebuild import (
        collect_absolute as _collect_absolute,
    )
    from ._track_rebuild import (
        sort_and_flush as _sort_and_flush,
    )

    technique, _range = load_range_resolver(context)
    recipe = context.recipe

    style_raw = context.parameters.get("style")
    if not isinstance(style_raw, str):
        return mid
    style = style_raw.strip().lower()
    style_key = {
        "dedo": "keyswitch_dedo",
        "fingered": "keyswitch_dedo",
        "palheta": "keyswitch_palheta",
        "picked": "keyswitch_palheta",
        "slap": "keyswitch_slap",
    }.get(style)
    if style_key is None:
        return mid

    style_ks = recipe.get(style_key) if recipe else None
    if not isinstance(style_ks, int) or isinstance(style_ks, bool):
        # Receita generic nao tem keyswitch — no-op.
        return mid

    forcar_primeiro = recipe.get("keyswitch_forcar_primeiro") if recipe else None
    forcar_segundo = recipe.get("keyswitch_forcar_segundo") if recipe else None
    is_picked = style in {"palheta", "picked"}

    # Pitches declarados como keyswitch na receita ficam fora do material
    # estrutural: evitam que uma reaplicacao trate keyswitch previamente
    # inserido como nota da linha e reembaralhe velocity.
    keyswitch_pitches: set[int] = set()
    for key, value in (recipe or {}).items():
        if key != "keyswitch" and not key.startswith("keyswitch_"):
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            keyswitch_pitches.add(value)

    def _midrange(name: str, fallback: tuple[float, float]) -> int:
        rng = _range(name) or fallback
        return int(round((rng[0] + rng[1]) / 2))

    downstroke_vel = max(1, min(127, _midrange("picked_downstroke_velocity", (85, 120))))
    upstroke_vel = max(1, min(127, _midrange("picked_upstroke_velocity", (70, 100))))
    atraso_ms = max(0, _midrange("upstroke_atraso_ms", (0, 8)))

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    # 120 BPM como baseline: 1 beat = 500 ms, entao 1 ms ~ ticks_per_beat/500.
    atraso_ticks = int(round(atraso_ms * ticks_per_beat / 500))

    for track in mid.tracks:
        # Coleta note_on estruturais com tick absoluto para localizar os
        # pontos de insercao e alterar velocity in-place.
        structural: list[tuple[int, mido.Message]] = []
        tick = 0
        for msg in track:
            tick += msg.time
            if (
                not msg.is_meta
                and msg.type == "note_on"
                and msg.velocity > 0
                and msg.note not in keyswitch_pitches
            ):
                structural.append((tick, msg))
        if not structural:
            continue

        channel = structural[0][1].channel

        # IDEMPOTENCIA: o keyswitch de estilo e a assinatura do que ja foi
        # aplicado. Se ele ja esta na track, a alternancia tambem ja esta —
        # reaplicar deslocaria a velocity de novo e a linha afundaria a cada
        # render. A checagem tem que vir ANTES de qualquer escrita.
        already_applied = any(
            not msg.is_meta
            and msg.type == "note_on"
            and msg.velocity > 0
            and msg.note == style_ks
            for msg in track
        )
        if already_applied:
            continue

        # Coleta absoluta de todas as mensagens da track — vamos reconstruir.
        absolute = _collect_absolute(track)
        order = len(absolute)

        # Keyswitch de estilo, uma vez por track, antes da primeira nota.
        first_tick = structural[0][0]
        style_tick = max(0, first_tick - 1)
        absolute.append((
            style_tick, -2, order,
            _mido.Message("note_on", channel=channel, note=style_ks, velocity=127),
        ))
        order += 1
        absolute.append((
            style_tick, -1, order,
            _mido.Message("note_off", channel=channel, note=style_ks, velocity=0),
        ))
        order += 1

        if is_picked:
            # Alternancia deterministica por posicao: even=down, odd=up.
            #
            # O DELTA e relativo a velocity da ORIGEM, nunca valor absoluto.
            # Sobrescrever com o alvo do manual destruia a dinamica escrita
            # pelo usuario: uma nota em 127 saia em 85 (upstroke), abaixo do
            # piso da propria origem. E o mesmo defeito que tirou
            # `drums.accent_hierarchy` do motor — tecnica nao pode inverter a
            # intencao da origem. Os numeros do manual definem a DIFERENCA
            # entre golpe para baixo e para cima; e essa diferenca que
            # caracteriza a alternancia, nao o valor absoluto.
            half_delta = max(1, abs(downstroke_vel - upstroke_vel) // 2)
            for idx, (_start, msg) in enumerate(structural):
                shift = half_delta if idx % 2 == 0 else -half_delta
                msg.velocity = max(1, min(127, msg.velocity + shift))

            if isinstance(forcar_primeiro, int) and isinstance(forcar_segundo, int) \
                    and not isinstance(forcar_primeiro, bool) \
                    and not isinstance(forcar_segundo, bool):
                for idx, (start_tick, msg) in enumerate(structural):
                    is_upstroke = idx % 2 == 1
                    ks_pitch = forcar_segundo if is_upstroke else forcar_primeiro
                    delay = atraso_ticks if is_upstroke else 0
                    ks_tick = max(0, start_tick - 1 - delay)
                    absolute.append((
                        ks_tick, -2, order,
                        _mido.Message(
                            "note_on", channel=msg.channel, note=ks_pitch,
                            velocity=127,
                        ),
                    ))
                    order += 1
                    absolute.append((
                        ks_tick, -1, order,
                        _mido.Message(
                            "note_off", channel=msg.channel, note=ks_pitch,
                            velocity=0,
                        ),
                    ))
                    order += 1

        _sort_and_flush(absolute, track)

    return mid


@register_technique(
    "bass.hammer_pull",
    "technique",
    allow_structural_velocity_change=True,
    allow_structural_duration_change=True,
)
def _apply_bass_hammer_pull(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Hammer-on e pull-off do baixo — ligado sem reataque.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `velocity_relativa` e `overlap_ms` do manual pelo indice.
      - Precedencia `context.parameters` > receita > `range` do manual.
      - So aplica entre notas adjacentes fisicamente ligaveis: mesmo canal,
        intervalo em semitons dentro do limite de ligado (<= 4), separacao
        temporal curta (<= metade de um beat) e ambas alcancaveis na mesma
        corda pela afinacao declarada.
      - Ligada (segunda nota) sai mais fraca por `velocity_relativa`
        (delta negativo); primeira estende note_off para sobrepor a segunda
        por `overlap_ms` — sobreposicao e o que dispara o legato no MODO BASS.
      - Nao altera pitch nem posicao (start_tick) de nota estrutural.
      - Receita `modo_bass` insere keyswitch C0 (12), segurado do inicio da
        primeira ao fim da segunda; keyswitch fica fora da regiao tocavel
        via `_keyswitch_pitches_from_recipe`.
      - Idempotente na receita com keyswitch: se ja ha keyswitch do canal
        pendurado sobre a primeira nota da ligadura, pulamos o par.
    """

    import mido as _mido

    from ._param_range import load_range_resolver
    from ._track_rebuild import sort_and_flush as _sort_and_flush

    technique, _resolve_range = load_range_resolver(context)
    recipe = context.recipe

    def _range(name: str, fallback: tuple[float, float]) -> tuple[float, float]:
        return _resolve_range(name) or fallback

    density_raw = context.parameters.get("density")
    if isinstance(density_raw, (int, float)) and not isinstance(density_raw, bool):
        density = float(density_raw)
    else:
        density = 0.0
    if density <= 0.0:
        # Ligado geral e ausencia de intencao musical: sem `density` positiva,
        # a tecnica e NO-OP — mantem a linha original intocada por default.
        return mid

    velocity_rel_range = _range("velocity_relativa", (-30.0, -15.0))
    overlap_range = _range("overlap_ms", (10.0, 40.0))

    keyswitch_pitch: int | None = None
    ks_raw = recipe.get("keyswitch") if recipe else None
    if isinstance(ks_raw, int) and not isinstance(ks_raw, bool):
        keyswitch_pitch = ks_raw

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid

    # Ligadura so entre notas proximas: gap <= meia batida (colcheia).
    max_gap_ticks = ticks_per_beat // 2
    # Hammer-on/pull-off em baixo: ate um terco maior (4 semitons).
    max_interval_semitones = 4

    rng = context.rng("hammer_pull")

    for track in mid.tracks:
        # Pareia note_on/note_off preservando referencia das mensagens.
        structural: list[dict] = []
        pending: dict[tuple[int, int], list[dict]] = {}
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                entry = {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start": tick,
                    "end": None,
                    "on_msg": msg,
                    "off_msg": None,
                }
                structural.append(entry)
                pending.setdefault((msg.channel, msg.note), []).append(entry)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if stack:
                    entry = stack.pop(0)
                    entry["end"] = tick
                    entry["off_msg"] = msg

        structural = [e for e in structural if e["end"] is not None]
        if len(structural) < 2:
            continue

        # Keyswitches ja presentes por canal, com intervalo de vigencia
        # (para idempotencia quando a receita usa keyswitch).
        existing_keyswitches: dict[int, list[tuple[int, int]]] = {}
        if keyswitch_pitch is not None:
            ks_pending: dict[int, list[int]] = {}
            ktick = 0
            for msg in track:
                ktick += msg.time
                if msg.is_meta:
                    continue
                # Cheque o TIPO antes de tocar em `.note`: `control_change`,
                # `pitchwheel` e afins nao tem esse atributo, e MIDI real de
                # baixo carrega CC (let ring, expressao) no meio das notas.
                # Ler `.note` cedo levantava AttributeError na primeira track
                # de verdade.
                if msg.type not in ("note_on", "note_off"):
                    continue
                if msg.note != keyswitch_pitch:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    ks_pending.setdefault(msg.channel, []).append(ktick)
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0
                ):
                    stack = ks_pending.get(msg.channel)
                    if stack:
                        start = stack.pop(0)
                        existing_keyswitches.setdefault(msg.channel, []).append(
                            (start, ktick)
                        )

        by_channel: dict[int, list[dict]] = {}
        for entry in structural:
            by_channel.setdefault(entry["channel"], []).append(entry)
        for lst in by_channel.values():
            lst.sort(key=lambda e: (e["start"], e["end"]))

        candidate_pairs: list[tuple[dict, dict]] = []
        for lst in by_channel.values():
            for a, b in zip(lst, lst[1:], strict=False):
                interval = abs(b["pitch"] - a["pitch"])
                if interval == 0 or interval > max_interval_semitones:
                    continue
                gap = b["start"] - a["end"]
                if gap > max_gap_ticks:
                    continue
                if b["start"] <= a["start"]:
                    continue
                if a["end"] > b["start"] and keyswitch_pitch is None:
                    # IDEMPOTENCIA no caminho `generic`: sem keyswitch para
                    # reconhecer o que ja foi feito, a sobreposicao e a
                    # UNICA assinatura disponivel. Sem esta guarda, reaplicar
                    # `velocity_relativa` a cada passada afundava a segunda
                    # nota a cada render (100 -> 71 -> 42).
                    #
                    # No MODO BASS a sobreposicao pode ser NATURAL — baixo
                    # tocado por gente as vezes ja liga notas sem que a
                    # tecnica tenha passado por ali. Nesse caminho a
                    # idempotencia real e a checagem de keyswitch logo
                    # abaixo, entao overlap sozinho nao basta para pular.
                    continue
                if keyswitch_pitch is not None and any(
                    lo <= a["start"] <= hi
                    for lo, hi in existing_keyswitches.get(a["channel"], ())
                ):
                    continue
                candidate_pairs.append((a, b))

        candidate_pairs.sort(key=lambda pair: (pair[0]["start"], pair[0]["channel"]))
        select_rng = context.rng("select")
        ligatures: list[tuple[dict, dict]] = []
        if density >= 1.0:
            ligatures = list(candidate_pairs)
        else:
            for pair in candidate_pairs:
                if select_rng.random() < density:
                    ligatures.append(pair)

        if not ligatures:
            continue

        events_to_insert: list[tuple[int, int, mido.Message]] = []
        new_end_by_id: dict[int, int] = {}
        for a, b in ligatures:
            overlap_ms = rng.uniform(overlap_range[0], overlap_range[1])
            overlap_ticks = max(1, int(round(overlap_ms * ticks_per_beat / 500.0)))
            new_end = b["start"] + overlap_ticks
            if new_end > a["end"] and a["off_msg"] is not None:
                new_end_by_id[id(a["off_msg"])] = new_end
                a["end"] = new_end

            vel_delta = rng.uniform(velocity_rel_range[0], velocity_rel_range[1])
            b["on_msg"].velocity = max(
                1, min(127, int(round(b["on_msg"].velocity + vel_delta))),
            )

            if keyswitch_pitch is not None:
                ks_start_tick = max(0, a["start"] - 1)
                ks_end_tick = b["end"] if b["end"] is not None else b["start"] + 1
                events_to_insert.append((
                    ks_start_tick, -2,
                    _mido.Message(
                        "note_on", channel=a["channel"],
                        note=keyswitch_pitch, velocity=127,
                    ),
                ))
                events_to_insert.append((
                    ks_end_tick, 4,
                    _mido.Message(
                        "note_off", channel=a["channel"],
                        note=keyswitch_pitch, velocity=0,
                    ),
                ))

        # Reconstroi a track: reposiciona note_off estendidos e injeta keyswitches.
        absolute: list[tuple[int, int, int, mido.Message]] = []
        tick = 0
        order = 0
        for msg in track:
            tick += msg.time
            abs_tick = new_end_by_id.get(id(msg), tick)
            absolute.append((abs_tick, 0, order, msg))
            order += 1
        for abs_tick, bias, msg in events_to_insert:
            absolute.append((abs_tick, bias, order, msg))
            order += 1

        _sort_and_flush(absolute, track)

    return mid


@register_technique("bass.let_ring", "technique")
def _apply_bass_let_ring(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Let-ring do baixo — sustentacao via CC declarado no manual.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `cc` do manual pelo indice; precedencia parameters > recipe > manual.
      - Sem `density` (ou `density<=0`) => NO-OP. Let-ring geral e ausencia de
        intencao musical, nao default seguro.
      - Agrupa notas estruturais por canal em runs (gap entre notas
        consecutivas <= 1 compasso). `density` seleciona a fracao dos runs
        que recebe pedal.
      - Emite CC de liga (127) e desliga (0) em pares por run — nunca deixa
        CC pendurado no fim da track.
      - Nao altera nota estrutural (nivel technique sem flags de mudanca).
      - Idempotencia: reaplicar com a mesma seed produz eventos com mesma
        assinatura (canal, tick, cc, valor) e o dedup do dispatch central
        (`_drop_reapplied_continuous_events`) descarta as duplicatas.
    """

    import mido as _mido

    from ._param_range import load_range_resolver
    from ._track_rebuild import (
        collect_absolute as _collect_absolute,
    )
    from ._track_rebuild import (
        sort_and_flush as _sort_and_flush,
    )

    technique, _range = load_range_resolver(context)

    cc_range = _range("cc")
    cc_number = int(cc_range[0]) if cc_range else None
    if cc_number is None or not (0 <= cc_number <= 127):
        return mid

    density_raw = context.parameters.get("density")
    if not isinstance(density_raw, (int, float)) or isinstance(density_raw, bool):
        return mid
    density = float(density_raw)
    if density <= 0.0:
        return mid

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    max_gap_ticks = ticks_per_beat * 4

    select_rng = context.rng("let_ring_select")

    def collect_pairs(track):
        return [
            (channel, pitch, start_tick, end_tick)
            for channel, pitch, start_tick, end_tick, _vel, _on_idx, _off_idx
            in _iter_note_pairs(track)
        ]

    def insert_events(track, events):
        absolute = _collect_absolute(track)
        order = len(absolute)
        for event_tick, bias, msg in events:
            absolute.append((event_tick, bias, order, msg))
            order += 1
        _sort_and_flush(absolute, track)

    for track in mid.tracks:
        pairs = collect_pairs(track)
        if not pairs:
            continue

        by_channel: dict[int, list[tuple[int, int, int]]] = {}
        for channel, _pitch, start, end in pairs:
            by_channel.setdefault(channel, []).append((start, end, _pitch))
        for lst in by_channel.values():
            lst.sort(key=lambda item: (item[0], item[1]))

        runs: list[tuple[int, int, int]] = []
        for channel, lst in by_channel.items():
            run_start = lst[0][0]
            run_end = lst[0][1]
            for start, end, _pitch in lst[1:]:
                gap = start - run_end
                if gap > max_gap_ticks:
                    runs.append((channel, run_start, run_end))
                    run_start = start
                    run_end = end
                else:
                    if end > run_end:
                        run_end = end
            runs.append((channel, run_start, run_end))

        selected: list[tuple[int, int, int]] = []
        if density >= 1.0:
            selected = list(runs)
        else:
            for run in runs:
                if select_rng.random() < density:
                    selected.append(run)

        if not selected:
            continue

        events: list[tuple[int, int, mido.Message]] = []
        for channel, run_start, run_end in selected:
            events.append((
                run_start, -1,
                _mido.Message(
                    "control_change", channel=channel,
                    control=cc_number, value=127,
                ),
            ))
            events.append((
                run_end, 5,
                _mido.Message(
                    "control_change", channel=channel,
                    control=cc_number, value=0,
                ),
            ))
        insert_events(track, events)

    return mid

SUPPORTED_TECHNIQUES = tuple(t.canonical for t in registered_techniques())


__all__ = [
    "RegisteredTechnique",
    "SUPPORTED_TECHNIQUES",
    "TechniqueApply",
    "TechniqueApplyResult",
    "TechniqueContractError",
    "TechniqueContext",
    "TechniqueLevel",
    "TechniquePhysicalError",
    "TechniqueRecipeError",
    "TechniqueRegistrationError",
    "TechniqueRegistry",
    "UnknownTechniqueError",
    "apply_technique",
    "apply_technique_with_warnings",
    "get_technique",
    "register_technique",
    "registered_techniques",
    "validate_registry_against_index",
]
