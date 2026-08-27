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
    allow_structural_velocity_change: bool = False,
    allow_structural_duration_change: bool = False,
) -> Callable[[TechniqueApply], TechniqueApply]:
    """Decorator para registrar uma tecnica no registro global."""

    register = _REGISTRY.register(
        canonical,
        level,
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

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    params_by_name = {p.name: p for p in technique.parameters}

    def _pick(name: str) -> Any:
        if name in context.parameters:
            return context.parameters[name]
        if name in context.recipe:
            return context.recipe[name]
        param = params_by_name.get(name)
        if param is None:
            return None
        if param.value is not None:
            return param.value
        return param.range

    def _as_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, (list, tuple)) and len(value) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        ):
            lo, hi = float(value[0]), float(value[1])
            return int(round((lo + hi) / 2))
        return default

    span_tipico = max(1, _as_int(_pick("span_tipico"), 40))
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

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    params_by_name = {p.name: p for p in technique.parameters}

    def _range(name: str) -> tuple[float, float] | None:
        if name in context.parameters:
            value = context.parameters[name]
        elif name in context.recipe:
            value = context.recipe[name]
        else:
            param = params_by_name.get(name)
            if param is None:
                return None
            if param.range is not None:
                value = param.range
            elif param.value is not None:
                value = (param.value, param.value)
            else:
                return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value), float(value))
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        ):
            return (float(value[0]), float(value[1]))
        return None

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
        if size <= 0:
            return 0
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

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    params_by_name = {p.name: p for p in technique.parameters}

    def _range(name: str) -> tuple[float, float] | None:
        if name in context.parameters:
            value = context.parameters[name]
        elif name in context.recipe:
            value = context.recipe[name]
        else:
            param = params_by_name.get(name)
            if param is None:
                return None
            if param.range is not None:
                value = param.range
            elif param.value is not None:
                value = (param.value, param.value)
            else:
                return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value), float(value))
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
            )
        ):
            return (float(value[0]), float(value[1]))
        return None

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
        pending: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        collected: list[tuple[int, int, int, int, int, int, int]] = []
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
                collected.append((
                    msg.channel,
                    msg.note,
                    start_tick,
                    tick,
                    velocity,
                    note_on_index,
                    msg_index,
                ))
        return collected

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

        if not new_velocity_by_msg:
            continue

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

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    params_by_name = {p.name: p for p in technique.parameters}
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
        if not isinstance(key, str):
            continue
        if key != "keyswitch" and not key.startswith("keyswitch_"):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            keyswitch_pitches.add(value)

    def _midrange(name: str, fallback: tuple[float, float]) -> int:
        if name in context.parameters:
            value = context.parameters[name]
        elif name in recipe:
            value = recipe[name]
        else:
            param = params_by_name.get(name)
            if param is None:
                value = fallback
            elif param.value is not None:
                value = param.value
            elif param.range is not None:
                value = param.range
            else:
                value = fallback
        if isinstance(value, bool):
            return int(round((fallback[0] + fallback[1]) / 2))
        if isinstance(value, (int, float)):
            return int(round(float(value)))
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
            )
        ):
            return int(round((float(value[0]) + float(value[1])) / 2))
        return int(round((fallback[0] + fallback[1]) / 2))

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

        # Coleta absoluta de todas as mensagens da track — vamos reconstruir.
        absolute: list[tuple[int, int, int, mido.Message | mido.MetaMessage]] = []
        tick = 0
        order = 0
        for msg in track:
            tick += msg.time
            absolute.append((tick, 0, order, msg))
            order += 1

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
            for idx, (_start, msg) in enumerate(structural):
                target = downstroke_vel if idx % 2 == 0 else upstroke_vel
                msg.velocity = target

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

        absolute.sort(key=lambda item: (item[0], item[1], item[2]))
        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _bias, _order, msg in absolute:
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

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
