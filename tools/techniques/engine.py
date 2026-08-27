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
    pending: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    tick = 0
    for msg_index, msg in enumerate(track):
        tick += msg.time
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            pending.setdefault((msg.channel, msg.note), []).append(
                (tick, msg.velocity, msg_index),
            )
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            stack = pending.get((msg.channel, msg.note))
            if not stack:
                continue
            start_tick, velocity, note_on_index = stack.pop(0)
            yield (
                msg.channel,
                msg.note,
                start_tick,
                tick,
                velocity,
                note_on_index,
                msg_index,
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
    import mido as _mido

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    recipe = dict(context.recipe)
    if not recipe:
        recipe = dict(technique.tools.get(context.tool) or technique.tools["generic"])

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

    def read_notes(track_index, track):
        return [
            {
                "track_index": track_index,
                "channel": channel,
                "pitch": pitch,
                "start": start_tick,
                "end": end_tick,
                "velocity": velocity,
            }
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

    def overlaps_same_pitch(existing, channel, pitch, start_tick, end_tick):
        # FIFO pairing entre note_on/note_off do MIDI e agnostica a "ornamento":
        # inserir uma ghost cuja janela cruze com uma nota estrutural na mesma
        # (channel, pitch) reembaralha o pareamento e muda a duracao da nota
        # estrutural — o que viola o contrato do nivel technique. Identidade
        # exata e ignorada para preservar idempotencia (o ornamento anterior
        # apenas se reconstroi no mesmo lugar).
        for note in existing:
            if note["channel"] != channel or note["pitch"] != pitch:
                continue
            if note["start"] == start_tick and note["end"] == end_tick:
                continue
            if note["start"] < end_tick and note["end"] > start_tick:
                return True
        return False

    def target_count(size):
        if size <= 0:
            return 0
        if isinstance(density, (int, float)):
            requested = float(density)
            # `density=0.0` significa ZERO ghost. Um `max(1, ...)` aqui fazia
            # densidade zero ainda acrescentar uma nota, o que torna o
            # parametro mentiroso: o plano pede para desligar a tecnica e ela
            # continua escrevendo no MIDI. Piso de 1 so vale para densidade
            # positiva que arredonda para baixo — ai o pedido foi "pouco",
            # nao "nenhum".
            if requested <= 0.0:
                return 0
            return max(1, min(size, int(round(size * requested))))
        # Sem `density` explicita, aponte para o teto do manual (2 ghosts por
        # intervalo entre backbeats) e deixe as regras de posicao — nao um cap
        # global — decidir quem entra. Um cap global de 2 aqui zera a densidade
        # sobre uma levada inteira: 158 compassos so ganhariam duas ghosts.
        return size

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
        wanted = target_count(len(shuffled))
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

    def insert_note(track, channel, pitch, velocity, start_tick, end_tick):
        absolute = []
        tick = 0
        order = 0
        for msg in track:
            tick += msg.time
            absolute.append((tick, order, msg))
            order += 1
        absolute.append((
            start_tick,
            order,
            _mido.Message(
                "note_on",
                channel=channel,
                note=pitch,
                velocity=velocity,
            ),
        ))
        absolute.append((
            end_tick,
            order + 1,
            _mido.Message("note_off", channel=channel, note=pitch, velocity=0),
        ))

        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    for track_index, track in enumerate(mid.tracks):
        existing = read_notes(track_index, track)
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
            insert_note(
                track,
                channel=candidate["channel"],
                pitch=candidate["pitch"],
                velocity=velocity,
                start_tick=candidate["tick"],
                end_tick=candidate["tick"] + gate,
            )
    return mid


@register_technique("drums.flam", "technique")
def _apply_drums_flam(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    import mido as _mido

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    recipe = dict(context.recipe)
    if not recipe:
        recipe = dict(technique.tools.get(context.tool) or technique.tools["generic"])

    params = {param.name: param for param in technique.parameters}

    def parameter_value(name, fallback=None):
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
        parameter = params.get(name)
        if parameter is None:
            return fallback
        if parameter.value is not None:
            return float(parameter.value)
        if parameter.range is not None:
            return (float(parameter.range[0]) + float(parameter.range[1])) / 2
        return fallback

    gap_ms = parameter_value("gap_ms")
    ratio = parameter_value("grace_velocity_ratio")
    ceiling_ms = parameter_value("reading_ceiling_ms")
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

    tempo = 500_000
    for track in mid.tracks:
        absolute_tick = 0
        for msg in track:
            absolute_tick += msg.time
            if msg.is_meta and msg.type == "set_tempo":
                tempo = msg.tempo
                break
        if tempo != 500_000:
            break
    ticks_per_ms = mid.ticks_per_beat * 1000 / tempo
    gap_ticks = max(1, int(round(gap_ms * ticks_per_ms)))
    density = context.parameters.get("density")

    def read_notes(track_index, track):
        return [
            {
                "track_index": track_index,
                "channel": channel,
                "pitch": pitch,
                "start": start_tick,
                "end": end_tick,
                "velocity": velocity,
            }
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

    def overlaps_same_pitch(existing, channel, pitch, start_tick, end_tick):
        for note in existing:
            if note["channel"] != channel or note["pitch"] != pitch:
                continue
            if note["start"] == start_tick and note["end"] == end_tick:
                continue
            if note["start"] < end_tick and note["end"] > start_tick:
                return True
        return False

    def simultaneous_hands(existing, channel, tick):
        foot_notes = {35, 36, 44}
        return sum(
            1
            for note in existing
            if note["channel"] == channel
            and note["start"] == tick
            and note["pitch"] not in foot_notes
        )

    def select_targets(candidates):
        if not candidates:
            return []
        if isinstance(density, (int, float)):
            requested = float(density)
            if requested <= 0.0:
                return []
            wanted = max(1, min(len(candidates), int(round(len(candidates) * requested))))
        else:
            wanted = len(candidates)
        shuffled = list(candidates)
        context.rng("targets").shuffle(shuffled)
        return sorted(shuffled[:wanted], key=lambda item: (
            item["track_index"],
            item["start"],
            item["pitch"],
        ))

    def insert_note(track, channel, pitch, velocity, start_tick, end_tick):
        absolute = []
        tick = 0
        order = 0
        for msg in track:
            tick += msg.time
            absolute.append((tick, order, msg))
            order += 1
        absolute.append((
            start_tick,
            order,
            _mido.Message(
                "note_on",
                channel=channel,
                note=pitch,
                velocity=velocity,
            ),
        ))
        absolute.append((
            end_tick,
            order + 1,
            _mido.Message("note_off", channel=channel, note=pitch, velocity=0),
        ))

        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    candidates = []
    for track_index, track in enumerate(mid.tracks):
        existing = read_notes(track_index, track)
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

    for candidate in select_targets(candidates):
        insert_note(
            mid.tracks[candidate["track_index"]],
            channel=candidate["channel"],
            pitch=candidate["pitch"],
            velocity=candidate["velocity"],
            start_tick=candidate["start"],
            end_tick=candidate["end"],
        )
    return mid


@register_technique("drums.accented_roll", "humanize")
def _apply_drums_accented_roll(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    import mido as _mido

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    recipe = dict(context.recipe)
    if not recipe:
        recipe = dict(technique.tools.get(context.tool) or technique.tools["generic"])

    params = {param.name: param for param in technique.parameters}

    def parameter_value(name, fallback=None):
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
        parameter = params.get(name)
        if parameter is None:
            return fallback
        if isinstance(parameter.value, (int, float)):
            return float(parameter.value)
        if parameter.range is not None:
            return (float(parameter.range[0]) + float(parameter.range[1])) / 2
        return fallback

    density = context.parameters.get("density")
    if isinstance(density, (int, float)) and float(density) <= 0.0:
        return mid

    accent_velocity = parameter_value("velocity_acento")
    soft_velocity = parameter_value("velocity_suave")
    dominant_delta = parameter_value("delta_mao_dominante")
    pre_accent_delta = parameter_value("delta_lift_pre_acento")
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

    def note_pairs(track):
        pending: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        tick = 0
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
                yield {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start": start_tick,
                    "end": tick,
                    "velocity": velocity,
                    "note_on_index": note_on_index,
                }

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

        if original_velocity >= top_pressure_floor:
            target = max(target, top_pressure_floor)
        if original_velocity >= top_pressure_floor and target <= low_layer_ceiling:
            target = top_pressure_floor
        return max(1, min(126, target))

    def rebuild_track(track, velocity_by_index):
        rebuilt = _mido.MidiTrack()
        for msg_index, msg in enumerate(track):
            if msg_index in velocity_by_index:
                rebuilt.append(msg.copy(velocity=velocity_by_index[msg_index]))
            else:
                rebuilt.append(msg.copy())
        track[:] = rebuilt

    for track in mid.tracks:
        notes = [
            note for note in note_pairs(track)
            if note["channel"] == 9
            and (target_note_set is None or note["pitch"] in target_note_set)
        ]
        if not notes:
            continue

        velocity_by_index = {}
        for sequence in roll_sequences(sorted(notes, key=lambda item: item["start"])):
            for position, note in enumerate(sequence):
                velocity_by_index[note["note_on_index"]] = contour_velocity(
                    position,
                    note["velocity"],
                )
        if velocity_by_index:
            rebuild_track(track, velocity_by_index)

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
    import mido as _mido

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    recipe = dict(context.recipe)
    if not recipe:
        available = sorted(technique.tools.keys())
        raise ValueError(
            f"tecnica {context.canonical!r} exige ferramenta-alvo; "
            f"receitas disponiveis: {available!r}"
        )

    def notes_for(name):
        values = recipe.get(name)
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(note, int) for note in values)
        ):
            raise ValueError(
                f"tecnica {context.canonical!r} precisa declarar {name} "
                "como lista de MIDI ints"
            )
        return tuple(int(note) for note in values)

    hat_tip = notes_for("hat_tip")
    hat_edge = notes_for("hat_edge")
    ride_bow_tip = notes_for("ride_bow_tip")
    ride_bow_shank = notes_for("ride_bow_shank")
    ride_bell = notes_for("ride_bell")
    snare_center = notes_for("snare_center")
    snare_rimshot = notes_for("snare_rimshot")

    density = context.parameters.get("density")
    if isinstance(density, (int, float)) and float(density) <= 0.0:
        return mid

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    bar_ticks = ticks_per_beat * 4

    def note_pairs(track):
        pending: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        tick = 0
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
                yield {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start": start_tick,
                    "velocity": velocity,
                    "note_on_index": note_on_index,
                    "note_off_index": msg_index,
                }

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

    def rebuild_track(track, pitch_by_index):
        rebuilt = _mido.MidiTrack()
        for msg_index, msg in enumerate(track):
            pitch = pitch_by_index.get(msg_index)
            if pitch is not None and not msg.is_meta:
                rebuilt.append(msg.copy(note=pitch))
            else:
                rebuilt.append(msg.copy())
        track[:] = rebuilt

    for track in mid.tracks:
        pitch_by_index = {}
        for note in note_pairs(track):
            if note["channel"] != 9:
                continue
            replacement = replacement_for(note)
            if replacement == note["pitch"]:
                continue
            pitch_by_index[note["note_on_index"]] = replacement
            pitch_by_index[note["note_off_index"]] = replacement
        if pitch_by_index:
            rebuild_track(track, pitch_by_index)

    return mid


@register_technique("drums.microtiming", "humanize")
def _apply_drums_microtiming(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    import math as _math

    import mido as _mido

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    recipe = dict(context.recipe)
    if not recipe:
        recipe = dict(technique.tools.get(context.tool) or technique.tools["generic"])

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

    params = {param.name: param for param in technique.parameters}

    def parameter_value(name, fallback=None):
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
        parameter = params.get(name)
        if parameter is None:
            return fallback
        if parameter.value is not None:
            return float(parameter.value)
        if parameter.range is not None:
            return (float(parameter.range[0]) + float(parameter.range[1])) / 2
        return fallback

    density = context.parameters.get("density")
    if isinstance(density, (int, float)) and float(density) <= 0.0:
        return mid

    sigma_ms = parameter_value("hihat_timing_sigma_ms")
    autocorr = parameter_value("hihat_autocorr_lag1")
    perception_ms = parameter_value("perception_threshold_ms")
    musical_hi_ms = parameter_value("musical_range_ms")
    sloppy_ms = parameter_value("sloppy_threshold_ms")
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

    tempo = 500_000
    for track in mid.tracks:
        absolute_tick = 0
        for msg in track:
            absolute_tick += msg.time
            if msg.is_meta and msg.type == "set_tempo":
                tempo = msg.tempo
                break
        if tempo != 500_000:
            break
    ticks_per_ms = mid.ticks_per_beat * 1000 / tempo
    max_abs_ticks = max(1, int(_math.floor(max_abs_ms * ticks_per_ms)))
    perception_ticks = max(1, int(round(float(perception_ms) * ticks_per_ms)))

    def note_pairs(track):
        pending = {}
        tick = 0
        for msg_index, msg in enumerate(track):
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault((msg.channel, msg.note), []).append(
                    (tick, msg_index)
                )
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if not stack:
                    continue
                start_tick, note_on_index = stack.pop(0)
                yield {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start": start_tick,
                    "end": tick,
                    "note_on_index": note_on_index,
                    "note_off_index": msg_index,
                }

    def note_on_events(track):
        events = []
        tick = 0
        for msg_index, msg in enumerate(track):
            tick += msg.time
            if (
                not msg.is_meta
                and msg.type == "note_on"
                and msg.velocity > 0
            ):
                events.append((msg_index, tick))
        return events

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

    def rebuild_track(track, shifts):
        absolute = []
        tick = 0
        for msg_index, msg in enumerate(track):
            tick += msg.time
            absolute.append((tick + shifts.get(msg_index, 0), msg_index, msg))

        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _msg_index, msg in sorted(
            absolute,
            key=lambda item: (item[0], item[1]),
        ):
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    rng = context.rng("hihat-offsets")
    previous_series_ms = 0.0
    scale = _math.sqrt(max(0.0, 1.0 - autocorr * autocorr))

    for track in mid.tracks:
        pairs = list(note_pairs(track))
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
            raw_ticks = int(round(series_ms * ticks_per_ms))
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
            rebuild_track(track, shifts)

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
