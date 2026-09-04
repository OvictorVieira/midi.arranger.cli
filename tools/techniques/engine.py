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

from . import errors as _errors
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


# `TechniqueRecipeError` mora em `tools/techniques/errors.py`, nao aqui, e
# `engine.py` NUNCA importa o nome solto (nem re-exporta) — ver o docstring
# daquele modulo para o motivo: qualquer aplicador registrado neste modulo
# que precise da excecao so fica de fato autocontido se "TechniqueRecipeError"
# nunca virar uma global do proprio `engine.py`. Codigo deste modulo que
# precisar dela usa `_errors.TechniqueRecipeError` (qualificado); quem
# importa a excecao de fora usa `tools.techniques.errors` ou
# `tools.techniques` (re-exportada la, nao aqui).


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
            _assert_all_notes_closed(after_mid, technique.canonical)
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
        raise _errors.TechniqueRecipeError(
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
        raise _errors.TechniqueRecipeError(
            f"tecnica {technique.canonical!r} nao tem receita para "
            f"tool={tool_target!r} nem fallback generic; disponiveis: "
            f"{sorted(technique.tools.keys())!r}"
        )
    raise _errors.TechniqueRecipeError(
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


def _assert_all_notes_closed(mid: mido.MidiFile, canonical: str) -> None:
    """Rejeita `note_on` sem `note_off` correspondente na saida de uma tecnica.

    `_StructuralSnapshot` deriva de `_collect_notes` (`tools/techniques/notes.py`),
    que so grava pares completos e silenciosamente ignora `note_on` aberto no
    fim da track -- por design, para os outros consumidores desse indice que
    so querem notas fechadas. Mas isso faz uma nota presa desaparecer da
    contagem: um aplicador com `allow_structural_pitch_change=True` que troca
    o pitch de uma nota E acrescenta um `note_on` extra sem fechamento nunca
    aparece em `after_shape - before_shape`, porque o evento nem chega a
    entrar no snapshot. `note_off` e `note_on` com velocity 0 sao
    equivalentes (mesmo par fechado do contrato `humanize`); qualquer nota
    que sobra aberta, ou `note_off` sem `note_on` correspondente, e violacao
    do contrato `technique`, com ou sem a excecao de pitch estrutural.
    """

    for track in mid.tracks:
        open_counts: dict[tuple[int, int], int] = {}
        for msg in track:
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                open_counts[key] = open_counts.get(key, 0) + 1
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if open_counts.get(key, 0) <= 0:
                    raise TechniqueContractError(
                        f"contrato technique violado por {canonical}: "
                        "note_off orfao encontrado"
                    )
                open_counts[key] -= 1
        if any(count > 0 for count in open_counts.values()):
            raise TechniqueContractError(
                f"contrato technique violado por {canonical}: note_on sem "
                "note_off correspondente"
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
        extra = after_shape - before_shape
        if extra:
            raise TechniqueContractError(
                f"contrato technique violado por {technique.canonical}: troca "
                "de articulacao acrescentou nota estrutural em vez de trocar "
                "pitch 1-para-1"
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
    # `ticks_per_bar` so serve de FALLBACK — suposicao de 4/4 usada apenas
    # quando `context.parameters["bars"]` nao chega (chamada direta da
    # tecnica fora do pipeline de `tools.render`, ex. testes unitarios do
    # motor). Quando `bars` chega, o agrupamento por compasso usa as
    # fronteiras REAIS de `analysis.bars` (achado do Codex no PR #107): em
    # 3/4, 5/4 ou com troca de compasso no meio da musica, um bucket fixo de
    # `ticks_per_beat*4` pode atravessar um compasso real (a cota do
    # `max_per_bar` estoura) ou partir um compasso real em dois buckets (a
    # densidade da secao errada e aplicada a metade dele). Nunca usada para
    # a decisao de ONDE (regras de posicao mais abaixo, que continuam
    # baseadas no intervalo de groove entre backbeats).
    ticks_per_bar = max_groove_interval
    rng = context.rng("positions")
    velocity_rng = context.rng("velocity")

    # --- issue #45: densidade por secao, nunca por constante fixa ----------
    # O manual (tecnicas_bateria_midi.md §2.3) declara a lacuna: "Densidade
    # por genero: [NAO VERIFICADO — sem fonte; derive do perfil de estilo
    # pesquisado]" — nenhum numero quantitativo de ghost/compasso e publicado
    # em fonte nenhuma, entao este bloco nao inventa um. O que existe e o
    # eixo `densidade` (0-10) de cada `plan.sections[].energy`
    # (`tools/plan.py` ENERGY_AXES) — QUANTOS ghosts por compasso deriva
    # DAI, nunca de um numero fixo aplicado igual em toda a musica. A regra
    # de ONDE (backbeats, semicolcheias, `violates_position_rules` abaixo)
    # continua intocada — sao decisoes separadas.
    #
    # O defeito medido na issue (86% dos compassos com ghost, mediana 4,
    # maximo 9; chorus com MAIS ghost que verse) vinha de tratar a antiga
    # `density` como fracao do total de candidatos do ARQUIVO INTEIRO: quase
    # qualquer fracao > 0 saturava as regras de posicao (que ja limitam a no
    # maximo 2 candidatos por intervalo entre backbeats), entao a musica
    # inteira convergia para quase-maximo independente da secao.
    #
    # Mapeamento eixo -> cota por compasso, tudo CONVENCAO do motor (nao do
    # manual, que nao declara numero nenhum aqui):
    energy_axis_max = 10  # mesma escala 0-10 de tools.plan.ENERGY_MAX/MIN
    default_section_densidade = 5  # meio da escala 0-10; usado so quando o
    # tick do candidato cai fora de toda janela de secao declarada (cauda
    # antes da 1a secao ou depois da ultima) ou quando nenhuma informacao de
    # secao chegou ate aqui (chamada direta da tecnica, fora do pipeline de
    # `tools.render`) — fecha a lacuna do manual sem inventar numero de
    # genero.
    max_per_bar = 3  # teto por compasso. O material da issue chegava a
    # 9/compasso (atulhamento em qualquer leitura); o proprio manual
    # recomenda "uma ghost isolada ou um par" por intervalo entre backbeats
    # (§2.3, passo 5), e um compasso 4/4 tipico tem dois desses intervalos —
    # um teto de 3 ja fica acima desse conselho e ainda evita o atulhamento
    # medido.
    kind_density_multiplier = {"chorus": 0.5, "breakdown": 0.5}
    # Refrao quer peso/clareza — ghost e textura de groove estavel
    # (verso/intro), nao coisa de topo de energia; breakdown do manual de
    # secoes (`tools/sections.py`) segue o sentido de metal (parte pesada),
    # mesma logica de "menos textura, mais peso". Fora dessas duas, cota
    # cheia (multiplicador 1.0) — inclui verse, intro, pre, bridge,
    # interlude, outro e a ausencia de `kind` conhecido.
    default_kind_multiplier = 1.0

    # `style.<familia>.techniques[].density` (0.0-1.0, `tools/plan.py`)
    # continua aceita como OVERRIDE explicito e direto da fracao por
    # compasso — mesmo contrato que as demais tecnicas deste motor ja
    # davam a `density` (`density<=0.0` desliga por completo, escala
    # monotonica). Quando o plano NAO declara `density`, a fracao passa a
    # ser 100% derivada do eixo `densidade` da secao (o caminho default
    # descrito abaixo) — e isso, no caminho sem override, que fecha o
    # defeito da issue #45 (quantidade vinha de uma constante fixa por
    # musica inteira, nunca da secao).
    density_param = context.parameters.get("density")
    explicit_density = density_param is not None
    explicit_fraction = (
        max(0.0, min(1.0, float(density_param))) if explicit_density else 0.0
    )

    # `sections` chega em `context.parameters` — nao em `style.parameters`
    # (schema fechado a numero/par, `tools/style_schema.py`) — exatamente
    # como `tuning` ja chega por um canal separado (ver
    # `render._style_technique_parameters`): e o render que sabe converter
    # `plan.sections[].energy` em janelas de tick, esta funcao so consome.
    sections_param = context.parameters.get("sections")
    windows = (
        tuple(sections_param) if isinstance(sections_param, (list, tuple)) else ()
    )

    # Fronteiras REAIS de compasso (`analysis.bars`, o mesmo mapa de downbeat
    # que `_section_energy_windows` ja usa) — canal irmao de `sections`,
    # tambem via `context.parameters` (nunca `style.parameters`, schema
    # fechado a numero/par). Corrige o achado do Codex no PR #107: agrupar
    # candidatos por `ticks_per_beat*4` so vale em 4/4 constante.
    bars_param = context.parameters.get("bars")
    real_bars = (
        tuple(bars_param) if isinstance(bars_param, (list, tuple)) else ()
    )

    def bar_start_for_tick(tick):
        """Tick de inicio do compasso REAL que contem `tick`.

        Usa `real_bars` (fronteiras vindas de `analysis.bars`) quando
        disponivel — cobre qualquer compasso, mesmo com troca de compasso no
        meio da musica. Cai no bucket `ticks_per_beat*4` (suposicao 4/4) so
        quando nao ha `bars` no contexto (chamada direta do motor, fora do
        pipeline de `tools.render`) ou quando o tick cai fora de toda janela
        de `real_bars` (cauda antes do 1o/depois do ultimo bar analisado)."""
        for bar in real_bars:
            if not isinstance(bar, dict):
                continue
            start = bar.get("start_tick")
            end = bar.get("end_tick")
            if isinstance(start, int) and isinstance(end, int) and start <= tick < end:
                return start
        return (tick // ticks_per_bar) * ticks_per_bar

    def section_for_tick(tick):
        for window in windows:
            if not isinstance(window, dict):
                continue
            start = window.get("start_tick")
            end = window.get("end_tick")
            if isinstance(start, int) and isinstance(end, int) and start <= tick < end:
                return window
        return None

    def bar_fraction(bar_start_tick):
        if explicit_density:
            return explicit_fraction
        window = section_for_tick(bar_start_tick)
        densidade_axis = default_section_densidade
        kind = None
        if window is not None and isinstance(window.get("densidade"), int):
            densidade_axis = window["densidade"]
            kind = window.get("kind")
        multiplier = kind_density_multiplier.get(kind, default_kind_multiplier)
        return max(0.0, min(1.0, (densidade_axis / energy_axis_max) * multiplier))

    def bar_target(bar_start_tick):
        fraction = bar_fraction(bar_start_tick)
        if fraction <= 0.0:
            return 0
        # Duas decisoes, nao uma so. (1) ATIVACAO: o compasso participa desta
        # passada com probabilidade `fraction` — sorteio seedado, nao
        # `round()`/`floor()` deterministico. Sem isso, um compasso elegivel
        # (com candidatos disponiveis) SEMPRE ganhava ghost sempre que a
        # fracao era positiva — nenhum baterista real toca ghost em todo
        # compasso elegivel de uma secao inteira, e era exatamente esse o
        # defeito medido na issue (86% dos compassos com ghost). (2) COTA:
        # so decide QUANTAS depois de decidir SE — escalada pela mesma
        # fracao, com piso 1 (senao "ativo" e "zero ghosts" virariam a
        # mesma coisa) e teto `max_per_bar`.
        if rng.random() >= fraction:
            return 0
        return max(1, min(max_per_bar, round(fraction * max_per_bar)))

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

    # Cota por compasso (`bar_counts`/`bar_targets`) e compartilhada por
    # TODA a chamada — nao reiniciada a cada track fisica. Achado do Codex
    # no PR #107: um `edit.track` cujo nome bate com MULTIPLAS tracks
    # fisicas do MIDI de origem e uma UNIDADE so (AGENTS.md: "nomes
    # repetidos de DAW sao tratados como uma unidade"), e cada chamada desta
    # funcao ja e uma unica unidade de edicao — mas o loop abaixo chama
    # `select_candidates` uma vez POR TRACK FISICA. Sem cota compartilhada,
    # duas tracks fisicas com backbeat no MESMO compasso podiam somar o
    # dobro do teto anunciado (`max_per_bar`) nesse compasso, porque cada
    # track reiniciava `bar_counts`/`bar_targets` do zero. `interval_counts`
    # continua por track: as regras de ONDE dentro de um intervalo entre
    # backbeats (`violates_position_rules`) sao sobre candidatos da MESMA
    # track fisica, que e onde o intervalo existe fisicamente.
    #
    # Segunda rodada do Codex no PR #107: o fix acima so cobre "multiplas
    # tracks fisicas DENTRO de uma so chamada desta funcao" — mas
    # `tools.render.render()` chama esta funcao (via `_run_style_pipeline`)
    # SEPARADAMENTE por edit de bateria distinta em `plan.edits[]` e mais
    # uma vez por elemento de bateria GERADO, cada chamada com seu proprio
    # `mid.tracks`. Duas edits de bateria (nomes de track diferentes) com
    # backbeat no mesmo compasso, ou uma edit de bateria mais um elemento
    # gerado, cada um caindo em chamada SEPARADA desta funcao, cada qual
    # recriando `bar_counts`/`bar_targets` do zero, podiam somar mais que
    # `max_per_bar` ghosts no mesmo compasso no ARQUIVO FINAL (o que um
    # ouvinte de fato escuta junto) — a cota local so protegia uma unidade
    # de edicao por vez, nunca o render inteiro.
    #
    # `context.parameters["drum_bar_quota"]` (canal separado de
    # `style.parameters`, mesmo padrao ja usado por `sections`/`bars`/
    # `tuning`) e um dict MUTAVEL criado UMA VEZ por chamada de
    # `tools.render.render()` (nunca global/modulo — resetado a cada
    # render, preserva determinismo entre renders separados) e repassado a
    # TODO despacho de tecnica de bateria dessa chamada. Quando presente,
    # `bar_counts`/`bar_targets` mutam DENTRO dele em vez de dicts locais
    # novos — a cota entao e compartilhada nao so entre tracks fisicas de
    # uma chamada, mas entre TODAS as chamadas desta funcao dentro do mesmo
    # `render()`. Chamada direta da tecnica fora do pipeline de
    # `tools.render` (testes unitarios do motor, por exemplo) nao passa
    # esse parametro e cai no dict local de sempre — retrocompatibilidade
    # preservada, e a funcao continua autocontida (o estado compartilhado
    # chega por `context.parameters`, nunca por global/nonlocal capturado).
    quota_state = context.parameters.get("drum_bar_quota")
    if isinstance(quota_state, dict):
        bar_counts: dict[int, int] = quota_state.setdefault("counts", {})
        bar_targets: dict[int, int] = quota_state.setdefault("targets", {})
    else:
        bar_counts = {}
        bar_targets = {}

    def select_candidates(candidates):
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        selected = []
        interval_counts = {}
        for candidate in shuffled:
            # Chave de agrupamento e o TICK DE INICIO DO COMPASSO REAL (nao
            # mais `tick // ticks_per_bar`) — em 3/4, 5/4 ou com troca de
            # compasso no meio da musica, o bucket fixo de `ticks_per_beat*4`
            # nao identifica compasso nenhum de verdade.
            bar_index = bar_start_for_tick(candidate["tick"])
            target = bar_targets.get(bar_index)
            if target is None:
                target = bar_target(bar_index)
                bar_targets[bar_index] = target
            # Teto do COMPASSO checado ANTES de acrescentar — nao mais um
            # teto global do arquivo inteiro. Checar depois deixava
            # `target == 0` passar sempre por uma nota: a primeira candidata
            # do compasso entrava e so entao o loop parava, entao um
            # compasso com densidade zerada (breakdown pesado, por exemplo)
            # ainda ganhava uma ghost.
            if bar_counts.get(bar_index, 0) >= target:
                continue
            if violates_position_rules(candidate, selected, interval_counts):
                continue
            selected.append(candidate)
            bar_counts[bar_index] = bar_counts.get(bar_index, 0) + 1
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
        # Contador de posicao GEOMETRICO — avanca a cada semicolcheia
        # candidata CONSIDERADA (elegivel ou nao), nunca a cada uma
        # ACRESCENTADA a `candidates`. Achado do Codex no PR #107 (P1,
        # idempotencia quebrada): a versao anterior usava `len(candidates)`
        # (a contagem de candidatas ja ELEGIVEIS) para escolher o pitch —
        # isso acopla o pitch de uma semicolcheia ao conjunto de notas JA
        # PRESENTES no MIDI. Na reaplicacao, uma ghost do passe anterior
        # muda a elegibilidade de OUTRAS semicolcheias na mesma passada
        # (via `overlaps_same_pitch`/`note_exists`, que leem `existing`), o
        # que desloca `len(candidates)` e, com ele, o pitch de toda
        # semicolcheia seguinte — a mesma seed entao sorteia sobre um
        # candidato de pitch DIFERENTE do passe anterior, e o
        # deduplicador central (`TechniqueRegistry.apply`) nao reconhece a
        # assinatura nova como a mesma ghost. `candidate_index`, em vez
        # disso, e uma funcao pura da GEOMETRIA (par de backbeat + offset em
        # semicolcheias) — nunca do que ja existe no MIDI — entao o alvo
        # (tick, pitch) de cada semicolcheia e IDENTICO em toda reaplicacao,
        # e so a elegibilidade (que ja se auto-exime pra ghost com a MESMA
        # assinatura exata, ver `overlaps_same_pitch`) decide se ela entra
        # em `candidates`.
        candidate_index = 0
        for current, following in zip(backbeats, backbeats[1:], strict=False):
            if following - current > max_groove_interval:
                continue
            tick = current + sixteenth
            while tick < following:
                if tick != following - sixteenth:
                    channel = 9
                    pitch = int(notes[candidate_index % len(notes)])
                    candidate_index += 1
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
        sai <= teto suave. No caso comum a saida cabe entre `soft_ceiling+1`
        e `hard_ceiling`, mas o piso `original - pressure_max_drop` e
        aplicado DEPOIS do teto duro e pode ultrapassa-lo quando o plano
        configurar um `pressure_max_drop` pequeno — a promessa do parametro
        manda sobre o teto pratico. Esta invariante e o que impede a
        inversao que motivou a remocao original.
      - Notas ja em faixa suave/ghost na origem NAO sao empurradas para cima.
      - `density=0` desliga (via `density_disabled`), como as demais tecnicas.
    """

    from ._fill_detection import (
        FILL_MAX_GAP_BEATS,
        FILL_MIN_DENSITY_PER_BEAT,
        FILL_MIN_NOTES,
        FILL_MIN_PIECE_VARIETY,
        fill_windows,
        piece_family,
    )
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

    def _float(name: str, default: float) -> float:
        rng = _range(name)
        if rng is None:
            return default
        return (rng[0] + rng[1]) / 2

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
    pressure_max_drop = _mid("pressure_max_drop", 15)

    # `fill_*`: resolvidos igual as camadas acima, senao um plano que
    # sobrescreva esses quatro parametros (declarados no manual, validados
    # na faixa e repassados via context.parameters) seria aceito e
    # silenciosamente ignorado pela deteccao de virada — o "parametro
    # mentiroso" que o AGENTS.md proibe.
    fill_max_gap_beats = _float("fill_max_gap_beats", FILL_MAX_GAP_BEATS)
    fill_min_notes = _mid("fill_min_notes", FILL_MIN_NOTES)
    fill_min_density_per_beat = _float(
        "fill_min_density_per_beat", FILL_MIN_DENSITY_PER_BEAT
    )
    fill_min_piece_variety = _mid("fill_min_piece_variety", FILL_MIN_PIECE_VARIETY)

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
        windows = fill_windows(
            drum_notes,
            ticks_per_beat=ticks_per_beat,
            max_gap_beats=fill_max_gap_beats,
            min_notes=fill_min_notes,
            min_density_per_beat=fill_min_density_per_beat,
            min_piece_variety=fill_min_piece_variety,
        )

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

            # `hard_ceiling` primeiro: e o teto pratico do manual (~115) no
            # caso comum. A INVARIANTE DE PRESSAO vem depois e pode
            # ultrapassar esse teto quando `pressure_max_drop` configurado
            # exige um piso mais alto — a promessa de nao rebaixar mais que
            # `pressure_max_drop` pontos manda sobre o teto pratico, senao o
            # parametro seria aceito e ignorado (achado do Codex no PR #59:
            # `pressure_max_drop=0` sobre origem 127 tinha que devolver 127,
            # mas o clamp de hard_ceiling aplicado por ultimo derrubava para
            # 115).
            target = max(1, min(hard_ceiling, target))
            if original > soft_ceiling:
                floor = max(soft_ceiling + 1, original - pressure_max_drop)
                target = max(floor, min(target, original))
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


@register_technique(
    "bass.palm_mute",
    "technique",
    allow_structural_velocity_change=True,
    allow_structural_duration_change=True,
)
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
      - Receita MODO BASS emite CC9 antes de cada nota escolhida e CC9=0 no
        fim dela. O usuario configura MUTING=CC9 na pagina CONTROL; sem isso
        o MIDI continua correto, mas o plugin nao associa a automacao.
      - Determinismo por seed atraves de `context.rng()`.
    """

    import mido as _mido

    from ._param_range import load_range_resolver

    # Tecnica DESLIGADA (density ausente ou <= 0) e no-op antes de consultar
    # os ranges do manual.
    density_raw = context.parameters.get("density")
    if not isinstance(density_raw, (int, float)) or isinstance(density_raw, bool):
        return mid
    density = float(density_raw)
    if density <= 0.0:
        return mid

    technique, _range = load_range_resolver(context)

    velocity_range = _range("velocity") or (60.0, 100.0)
    velocity_lo = max(1, int(velocity_range[0]))
    velocity_hi = max(velocity_lo, int(velocity_range[1]))

    gate_range = _range("gate_pct") or (25.0, 50.0)
    gate_lo = max(1.0, float(gate_range[0]))
    gate_hi = max(gate_lo, float(gate_range[1]))

    muting_cc: int | None = None
    muting_lo = muting_hi = 0
    if context.tool == "modo_bass":
        # Import local de modulo DIFERENTE do proprio (`errors.py`, nao
        # `engine.py`): `engine.py` nunca importa TechniqueRecipeError solto
        # no proprio escopo, entao isto e dependencia de verdade em outro
        # modulo, nao evasao de `inspect.getclosurevars` (ver comentario
        # acima de `TechniqueContractError` em engine.py).
        from .errors import TechniqueRecipeError

        cc_value = context.recipe.get("cc")
        if not isinstance(cc_value, int) or isinstance(cc_value, bool):
            raise TechniqueRecipeError(
                f"tecnica {context.canonical!r}: receita modo_bass sem CC de muting"
            )
        amount_range = _range("amount") or (18.0, 35.0)
        muting_cc = cc_value
        muting_lo = max(1, min(127, int(amount_range[0])))
        muting_hi = max(muting_lo, min(127, int(amount_range[1])))

    if mid.ticks_per_beat <= 0:
        return mid

    selection_rng = context.rng("selection")
    velocity_rng = context.rng("velocity")
    gate_rng = context.rng("gate")
    muting_rng = context.rng("muting")

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
        control_events: list[tuple[int, int, mido.Message]] = []
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
            if muting_cc is not None:
                # No MODO o proprio CC9 produz o abafamento. Nao encurtamos
                # nem recalculamos velocity por cima dele: alem de somar um
                # segundo efeito sem necessidade, isso mudaria o alvo na
                # reaplicacao e impediria o dedup central dos mesmos CCs.
                amount = muting_rng.randint(muting_lo, muting_hi)
                control_events.append((
                    start_tick,
                    -2,
                    _mido.Message(
                        "control_change", channel=_channel,
                        control=muting_cc, value=amount,
                    ),
                ))
                control_events.append((
                    end_tick,
                    -3,
                    _mido.Message(
                        "control_change", channel=_channel,
                        control=muting_cc, value=0,
                    ),
                ))
                continue
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

        if control_events:
            absolute_events: list[tuple[int, int, int, mido.Message]] = []
            tick = 0
            for order, msg in enumerate(track):
                tick += msg.time
                absolute_events.append((tick, 0, order, msg))
            offset = len(absolute_events)
            absolute_events.extend(
                (event_tick, bias, offset + order, msg)
                for order, (event_tick, bias, msg) in enumerate(control_events)
            )
            absolute_events.sort(key=lambda item: (item[0], item[1], item[2]))
            rebuilt = _mido.MidiTrack()
            previous_tick = 0
            for event_tick, _bias, _order, msg in absolute_events:
                rebuilt.append(msg.copy(time=event_tick - previous_tick))
                previous_tick = event_tick
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
    """Estilo de ataque do baixo — dedo, palheta ou slap com ou sem keyswitch.

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
      - Idempotente (caminho com keyswitch): mesma seed insere keyswitches
        nas mesmas posicoes e o dedup do dispatch descarta a duplicata.
      - `density` explicita <= 0 DESLIGA a tecnica inteira (achado do Codex
        na PR #93): sem essa checagem, `tool="modo_bass"` inseria o
        keyswitch mesmo com `density=0.0` declarado no plano, porque nada
        aqui olhava `density`. Ausencia de `density` continua aplicando
        normalmente quando `style` esta declarado — comportamento anterior
        preservado, so o "0.0 explicito" ganhou sentido de desligar.

    Caminho GENERIC (sem keyswitch — issue #57):
      - `style=palheta/picked` alterna downstroke/upstroke por DELTA RELATIVO
        direto na velocity das notas estruturais, mesma formula e mesmos
        numeros sourced do manual (`picked_downstroke_velocity`,
        `picked_upstroke_velocity`) do caminho MODO BASS — preserva a
        invariante de pressao (nao inverte a intencao de dinamica da
        origem) porque o deslocamento e sempre relativo ao valor escrito
        pelo usuario, nunca um alvo absoluto.
      - Idempotente por MARCADOR (`meta text` dedicado por track, gravado so
        depois da alternancia): sem keyswitch nao ha pitch reservado para
        servir de assinatura "ja processado" como no caminho com keyswitch;
        um delta relativo sem marcador dobraria a cada reaplicacao. Ver
        comentario no ramo `elif`/generic mais abaixo para o desenho
        completo.
      - `style=dedo/fingered` e `style=slap` continuam NO-OP no generic: o
        manual (secao 5.7) nao declara faixa sourced de index/middle picking
        nem reproduz a heuristica de registro/corda que o plugin usa para
        escolher entre thumb e pop — sem fonte, sem numero inventado.
    """

    import mido as _mido

    from ._param_range import load_range_resolver
    from ._track_rebuild import (
        collect_absolute as _collect_absolute,
    )
    from ._track_rebuild import (
        sort_and_flush as _sort_and_flush,
    )

    density_raw = context.parameters.get("density")
    if (
        isinstance(density_raw, (int, float))
        and not isinstance(density_raw, bool)
        and density_raw <= 0.0
    ):
        return mid

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
    has_keyswitch = isinstance(style_ks, int) and not isinstance(style_ks, bool)
    is_picked = style in {"palheta", "picked"}

    if not has_keyswitch and not is_picked:
        # Receita generic nao tem keyswitch. Para dedo/fingered o manual
        # (secao 5.7) nao declara faixa sourced de velocity/timing para
        # index/middle picking; para slap, a escolha entre thumb e pop e do
        # PLUGIN, pelo registro e pela corda — replicar essa decisao sem
        # fonte seria numero inventado, a mesma categoria ja rejeitada em
        # `_identity_apply`. Os dois ficam NO-OP no generic ate haver fonte
        # (issue #57) — documentado, nao bug.
        return mid

    if not has_keyswitch and is_picked and "upstroke_atraso_ms" in context.parameters:
        # Achado do Codex na PR #104, segunda rodada: so corrigir o texto da
        # receita `generic` no manual nao bastava — `plan.validate` ainda
        # aceitava `style.bass.parameters.upstroke_atraso_ms` (declarado
        # pelo aplicador do proprio `bass.attack_style`, sem saber qual
        # `tool` vai reger cada track) e o motor continuava resolvendo o
        # valor em `atraso_ms`/`atraso_ticks` sem usa-lo aqui embaixo —
        # parametro validado e depois ignorado e "parametro mentiroso"
        # (AGENTS.md). `plan.validate` nao pode recusar isso: o mesmo
        # `style.bass.parameters` vale para tracks com `tool` diferentes,
        # resolvido soo em render. Falha aqui, no despacho, quando o `tool`
        # de fato resolvido e generic — mesmo padrao de
        # `TechniqueRecipeError` ja usado pelo restante deste modulo para
        # receita incompativel com o tool-alvo.
        from .errors import TechniqueRecipeError

        raise TechniqueRecipeError(
            f"tecnica {context.canonical!r}: upstroke_atraso_ms nao tem "
            "efeito com tool='generic' — sem keyswitch reservado, aplicar "
            "o atraso na propria nota estrutural violaria o contrato de "
            "posicao do nivel technique (start_tick e identidade "
            "estrutural, sem excecao registrada para bass.attack_style). "
            "Declare upstroke_atraso_ms so quando a track for renderizada "
            "com tool='modo_bass', ou remova o parametro do plano."
        )

    forcar_primeiro = recipe.get("keyswitch_forcar_primeiro") if recipe else None
    forcar_segundo = recipe.get("keyswitch_forcar_segundo") if recipe else None

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

        if has_keyswitch:
            # IDEMPOTENCIA (caminho MODO BASS): o keyswitch de estilo e a
            # assinatura do que ja foi aplicado. Se ele ja esta na track, a
            # alternancia tambem ja esta — reaplicar deslocaria a velocity de
            # novo e a linha afundaria a cada render. A checagem tem que vir
            # ANTES de qualquer escrita.
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
            continue

        # CAMINHO GENERIC (sem keyswitch) — so alcancavel com is_picked=True,
        # porque dedo/slap sem keyswitch ja saiu da funcao mais acima.
        #
        # DESENHO DO MARCADOR DE IDEMPOTENCIA (issue #57): sem keyswitch nao
        # ha pitch reservado para servir de assinatura "ja processado", como
        # o caminho MODO BASS usa acima. Um delta relativo aplicado direto na
        # velocity SEM marcador nao e idempotente por si so: reaplicar sobre
        # a propria saida dobraria o deslocamento a cada render (a mesma nota
        # ganharia +half_delta duas vezes). A solucao espelha o padrao do
        # keyswitch — grava uma ASSINATURA OBSERVAVEL na track antes de
        # qualquer escrita, e reaplicacao futura le essa assinatura para
        # pular a track inteira — so que aqui a assinatura e um evento
        # `meta text` dedicado, em vez de nota, porque nao ha pitch reservado
        # para carregar o papel. O literal fica local (nao vira constante de
        # modulo) de proposito: o registro global desta tecnica precisa
        # continuar autocontido, sem capturar estado de modulo (o teste
        # `test_registered_techniques_do_not_capture_global_or_nonlocal_state`
        # falharia). O marcador nao e nota estrutural (fica fora de
        # `_StructuralSnapshot` e de `_MidiContentSnapshot`), entao nao conta
        # contra o contrato `technique` nem contra o dedup central de
        # ornamentos — a checagem e feita aqui, autocontida, no mesmo estilo
        # do `already_applied` do caminho com keyswitch.
        # Achado do Codex na PR #104, sexta rodada: a assinatura so incluia
        # `style`, entao reaplicar com `picked_downstroke_velocity`/
        # `picked_upstroke_velocity` DIFERENTES (ex.: contraste invertido)
        # numa track ja marcada pulava a track inteira so porque `style`
        # batia — os parametros novos, aceitos e validados, nunca chegavam
        # a comandar o resultado. A assinatura agora inclui os valores
        # EFETIVOS que determinam o resultado (`downstroke_vel`/
        # `upstroke_vel`, ja resolvidos por `_midrange` — parametro do
        # plano > receita > manual), entao configuracao diferente NUNCA
        # bate com um marcador anterior.
        marker_text = (
            f"midi-arranger:bass.attack_style:generic:{style}:"
            f"{downstroke_vel}:{upstroke_vel}"
        )
        already_applied_generic = any(
            msg.is_meta and msg.type == "text" and msg.text == marker_text
            for msg in track
        )
        if already_applied_generic:
            continue

        # Mesma alternancia relativa do caminho MODO BASS (nunca absoluta) —
        # preserva a invariante de pressao: nota que a origem escreveu alta
        # nao pode virar baixa so por causa do downstroke/upstroke.
        #
        # Calcula os novos valores ANTES de escrever qualquer coisa: se toda
        # nota ja esta saturada no clamp [1, 127] (ex.: velocity 127
        # alternando com 1), o shift nao muda nada de audivel. Gravar o
        # marcador mesmo assim faria `_midi_bytes` enxergar bytes diferentes
        # (o meta text em si) e `_run_style_pipeline` reportar a tecnica como
        # aplicada ao usuario sem nenhuma nota ter mudado — achado do Codex
        # na PR #104. Sem mudanca real, nao ha nada pra tornar idempotente:
        # pula a track inteira, sem marcador e sem escrita.
        # Achado do Codex na PR #104, segunda rodada: shift fixo por
        # posicao pode INVERTER a ordem que a origem escreveu entre notas
        # vizinhas quando a diferenca original e menor que o dobro do
        # shift — ex.: origem [90, 100] (diferenca 10) com half_delta=8
        # virava [98, 92], e a nota que a origem escreveu MAIS FORTE saia
        # mais fraca. Mesmo defeito, categoria, que ja tirou
        # `drums.accent_hierarchy` do motor uma vez.
        #
        # Achado do Codex na PR #104, terceira rodada: limitar so pelo
        # vizinho IMEDIATO nao basta. Duas notas de MESMA paridade (mesmo
        # sinal de shift, ex. duas "down") so preservam a ordem original
        # entre si se receberem a MESMA magnitude — capping por vizinho
        # deixava cada nota com uma magnitude diferente dependendo de QUEM
        # estava do lado, e duas "down" com magnitudes diferentes podiam
        # inverter a ordem entre ELAS MESMAS mesmo sem serem vizinhas
        # (origem [90, 90, 91]: nota 0 sem vizinho de gap>0 ficava com
        # magnitude cheia, nota 2 ficava presa a 0 pelo gap de 1 com a
        # nota 1 no meio — 90+cheio > 91+0, invertendo nota 0 acima da 2).
        #
        # Achado do Codex na PR #104, quarta rodada, achado 1: uma UNICA
        # magnitude pra track inteira preservava a ordem, mas jogava fora o
        # SINAL do contraste pedido — `abs(downstroke_vel - upstroke_vel)`
        # sempre fazia "down" mais forte, mesmo quando o plano pede
        # `picked_downstroke_velocity < picked_upstroke_velocity` (contraste
        # invertido, valido nas duas faixas do manual). O sinal do shift por
        # posicao agora segue o sinal PEDIDO (`direction`), nao um "down
        # sempre sobe" fixo.
        #
        # Achado do Codex na PR #104, quarta rodada, achado 2: uma unica
        # magnitude GLOBAL tambem tem o problema oposto — um UNICO par de
        # paridade oposta com diferenca pequena em QUALQUER lugar da track
        # zerava a magnitude (e a tecnica inteira) mesmo quando so aquele
        # par precisava de ajuste. "constrain the affected values or
        # passage instead of zeroing the technique across the entire
        # track."
        #
        # As duas exigencias juntas — nunca inverter NENHUM par (nao so
        # vizinhos) E nao apagar a diferenciacao onde nao ha conflito — sao
        # exatamente o problema que REGRESSAO ISOTONICA resolve: dado um
        # alvo por nota (velocity original + shift pedido) e uma ordem que
        # os resultados tem que respeitar (a mesma ordem da velocity
        # original), a regressao isotonica devolve a sequencia mais proxima
        # possivel dos alvos que nunca viola essa ordem — e so "agrupa"
        # (poola) as notas estritamente necessarias pra resolver um
        # conflito local, deixando o resto da track com a diferenciacao
        # plena pedida. E o mesmo principio matematico do "piso" em
        # `drums.accent_hierarchy` (nunca inverte a intencao da origem),
        # so que aqui resolvido de forma otima em vez de um piso fixo.
        # Achado do Codex na PR #104, quinta rodada, achado 1: dividir o
        # contraste pedido em uma metade UNICA (`abs(diff) // 2`) descartava
        # o resto pra diferenca IMPAR — `picked_downstroke_velocity=86` e
        # `picked_upstroke_velocity=85` (diferenca 1) davam `half_delta=0`,
        # entao um contraste explicitamente pedido (nao-zero) virava shift
        # zero. Agora o contraste total e distribuido SEM descartar resto:
        # `down_shift + up_shift == abs(diff)` sempre — o lado "down" recebe
        # o arredondamento pra cima quando a diferenca e impar (convencao
        # arbitraria, mas simetrica quando a diferenca e par).
        # Achado do Codex na PR #104, sexta rodada: ate aqui so a
        # DIFERENCA entre downstroke_vel/upstroke_vel comandava o
        # resultado — (85, 70) e (100, 85) tem a mesma diferenca (15) e
        # geravam alvo IDENTICO, entao o NIVEL absoluto pedido (dois
        # parametros aceitos e validados independentemente) nunca
        # comandava nada. Mas o design de 5 rodadas anteriores tambem
        # estabeleceu, com evidencia repetida, que sobrescrever a
        # velocity com um alvo ABSOLUTO destroi a dinamica que a origem
        # escreveu (mesmo defeito que ja tirou drums.accent_hierarchy do
        # motor). A saida que respeita as duas exigencias: o nivel
        # absoluto pedido entra como um DESLOCAMENTO UNIFORME (mesmo
        # valor somado a TODO alvo, down e up) — desvio de UM UNICO grau
        # de liberdade em relacao ao baseline do manual (a media dos
        # defaults de fallback), nao um alvo absoluto por nota. Shift
        # uniforme nunca muda a diferenca relativa entre dois alvos
        # (`(a+K) - (b+K) == a-b`), entao a garantia de nao-inversao da
        # regressao isotonica abaixo continua valendo — o nivel move a
        # track inteira pra cima/baixo junto, nunca troca a ordem escrita
        # pela origem.
        # NAO usar `_range()` aqui: ele consulta `context.parameters`
        # primeiro (mesma precedencia de `_midrange`), entao um override
        # do plano vazaria pro "baseline" e cancelaria o proprio bias que
        # este calculo tenta capturar. O baseline tem que ser o range CRU
        # do MANUAL (nunca o override do plano), lido direto de
        # `technique.parameters` — mesmo indice que `_range`/`_midrange`
        # usam por baixo, so que sem a precedencia de `context.parameters`
        # /`context.recipe` por cima.
        def _manual_midpoint(name: str, fallback: tuple[float, float]) -> float:
            for param in technique.parameters:
                if param.name != name:
                    continue
                if param.range is not None:
                    return (param.range[0] + param.range[1]) / 2
                if param.value is not None:
                    return float(param.value)
            return (fallback[0] + fallback[1]) / 2

        default_mean = (
            _manual_midpoint("picked_downstroke_velocity", (85.0, 120.0))
            + _manual_midpoint("picked_upstroke_velocity", (70.0, 100.0))
        ) / 2
        requested_mean = (downstroke_vel + upstroke_vel) / 2
        level_bias = requested_mean - default_mean

        original_velocities = [msg.velocity for _start, msg in structural]
        total_delta = abs(downstroke_vel - upstroke_vel)
        direction = 1 if downstroke_vel >= upstroke_vel else -1
        down_shift = total_delta - total_delta // 2
        up_shift = total_delta // 2

        targets = []
        for idx, velocity in enumerate(original_velocities):
            shift = direction * down_shift if idx % 2 == 0 else -direction * up_shift
            targets.append(float(velocity) + shift + level_bias)

        # Achado do Codex na PR #104, quinta rodada, achado 2: representar
        # cada grupo de velocity original EMPATADA so pela MEDIA dos alvos
        # dos membros nao garante a ordem entre os MEMBROS individuais —
        # so garante entre as medias. Origem [80, 80, 81, 81] com alvos
        # [88, 72, 89, 73]: as medias (80 e 81) ja saiam ordenadas (nenhum
        # merge de grupo acontecia), mas o membro individual da nota 0
        # (alvo 88, grupo 80) saia MAIS FORTE que o da nota 3 (alvo 73,
        # grupo 81) — inversao entre INDIVIDUOS que a comparacao por media
        # nao pegava.
        #
        # A correcao correta e' regressao isotonica de verdade sobre CADA
        # NOTA individual (nao sobre medias de grupo) — a garantia de PAVA
        # ("orig[i] antes de orig[j] na ordem de processamento implica
        # novo[i] <= novo[j]") vale pra QUALQUER ordem de processamento que
        # respeite a ordem exigida pela velocity original; so o DESEMPATE
        # entre notas empatadas e livre (elas nao tem ordem nenhuma pra
        # preservar entre si). Desempatar pelo proprio ALVO (nao por
        # indice) maximiza a diferenciacao preservada dentro do empate, mas
        # QUALQUER desempate seria igualmente correto — a garantia de nao
        # inverter vale antes de qualquer escolha de desempate.
        order = sorted(
            range(len(structural)),
            key=lambda i: (original_velocities[i], targets[i], i),
        )
        sorted_targets = [targets[i] for i in order]

        # Pool Adjacent Violators (PAVA) sobre os alvos individuais, na
        # ordem acima. Pilha de blocos (valor medio, peso) — cada novo alvo
        # que violar a ordem com o bloco anterior e fundido (media
        # ponderada) ate a pilha voltar a ser nao-decrescente. So funde o
        # que precisa: um alvo que ja bate com a ordem nunca e tocado, e um
        # empate cujos membros ficaram em blocos separados continua
        # diferenciado.
        stack: list[list[float]] = []
        for value in sorted_targets:
            stack.append([value, 1.0])
            while len(stack) > 1 and stack[-2][0] > stack[-1][0]:
                v2, w2 = stack.pop()
                v1, w1 = stack.pop()
                merged_w = w1 + w2
                stack.append([(v1 * w1 + v2 * w2) / merged_w, merged_w])

        isotonic_sorted: list[float] = []
        for value, weight in stack:
            isotonic_sorted.extend([value] * int(round(weight)))

        new_velocities = [0] * len(structural)
        for position, original_index in enumerate(order):
            resolved = int(round(isotonic_sorted[position]))
            new_velocities[original_index] = max(1, min(127, resolved))

        if all(
            new == msg.velocity
            for new, (_start, msg) in zip(new_velocities, structural, strict=True)
        ):
            continue

        for (_start, msg), new_velocity in zip(structural, new_velocities, strict=True):
            msg.velocity = new_velocity

        absolute = _collect_absolute(track)
        absolute.append((
            0, 0, len(absolute),
            _mido.MetaMessage("text", text=marker_text, time=0),
        ))
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


# Mapeamento posicao->keyswitch para as configuracoes de baixo realistas
# (4/5/6 cordas). O manual (secao 5.9, `bass.string_selection`) declara os
# seis keyswitches nomeados por CORDA (C/A/B/D/E/G), mas nao diz
# explicitamente em que ordem eles correspondem a um baixo de N cordas —
# isso e derivado aqui da convencao real de afinacao de baixo (4 cordas:
# E-A-D-G; 5 cordas: B-E-A-D-G; 6 cordas: B-E-A-D-G-C, grave para agudo),
# NAO da pitch real da afinacao declarada (que pode estar em drop): o
# keyswitch endereca a CORDA FISICA do instrumento modelado pelo plugin,
# nao o nome da nota que ela soa quando destafinada. Documentado aqui como
# inferencia explicita porque o manual (`verified: false` neste bloco) nao
# confirma isso por medicao — se a numeracao de corda do MODO BASS do
# usuario divergir dessa convencao padrao, o resultado sai errado e precisa
# de correcao manual.
@register_technique("bass.string_selection", "technique")
def _apply_bass_string_selection(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Forca a corda em que cada nota estrutural do baixo soa, via keyswitch.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le os seis `keyswitch_corda_*` do bloco `parameters` geral do manual
        (nao variam por ferramenta — `tools.modo_bass` so tem nota
        qualitativa sobre LATCH). Gate por `context.tool == "modo_bass"`:
        qualquer outra ferramenta (`generic` incluido) e NO-OP, documentado
        no proprio manual ("declare que a intencao de corda nao pode ser
        honrada nesta ferramenta").
      - Afinacao vem de `context.parameters["tuning"]` (mesmo canal que
        `tools/techniques/physical.py` ja le); sem declaracao, cai no
        default fisico de 4 cordas (`_BASS_DEFAULT_TUNING`), mesma
        convencao que o resto do motor usa.
      - Corda de cada nota estrutural: a MAIS GRAVE que alcanca aquele pitch
        dentro de `max_fret` (default 24) — regra do manual: "em drop
        tuning o riff mora na corda mais grave", escolha por TIMBRE, nunca
        por economia de mao.
      - Notas estruturais consecutivas na MESMA corda viram um run so; o
        keyswitch e emitido UMA VEZ por run e mantido pressionado (par
        note_on/note_off) ate o inicio do proximo run — o manual documenta
        que o LATCH do MODO BASS vem DESLIGADO de fabrica (keyswitch
        momentaneo, vale so enquanto segurado), entao segurar a nota e
        obrigatorio, nao um evento pontual.
      - Keyswitch nao colide com nota musical: os seis pitches (0-19) ficam
        muito abaixo do piso de qualquer afinacao real de baixo e saem do
        contrato estrutural via `_keyswitch_pitches_from_recipe`
        (tools/techniques/physical.py), o mesmo mecanismo generico que ja
        protege `bass.attack_style` — por isso os seis valores vem SEMPRE do
        manual (`manual_value`), nunca de override em `context.parameters`:
        um valor arbitrario ali divergiria do que a excecao fisica generica
        reconhece via `context.recipe`, e um numero abaixo do piso de
        afinacao seria rejeitado como se fosse nota impossivel.
      - Idempotente: recalcula o mesmo conjunto de runs a cada chamada
        (filtrando pitches de keyswitch do material estrutural) e deixa o
        dedup central por assinatura exata (track/canal/pitch/inicio/fim)
        descartar a repeticao — nao pula a track inteira so por ja ter
        ALGUM keyswitch (isso deixaria de forcar corda no resto da track se
        so um trecho tivesse keyswitch previo).
      - `density` explicita <= 0 DESLIGA a tecnica inteira, mesmo padrao de
        `bass.attack_style` (achado do Codex na PR #94): `_run_style_pipeline`
        ja pula o despacho nesse caso quando a chamada vem do render, mas
        quem chama `apply_technique`/`apply_technique_with_warnings`
        diretamente (testes, uso futuro fora do pipeline) precisa da mesma
        garantia aqui dentro — density=0.0 nunca pode inserir keyswitch.
        Ausencia de `density` continua aplicando normalmente.
    """

    import mido as _mido

    from ._track_rebuild import (
        collect_absolute as _collect_absolute,
    )
    from ._track_rebuild import (
        sort_and_flush as _sort_and_flush,
    )
    from .physical import _BASS_DEFAULT_TUNING

    density_raw = context.parameters.get("density")
    if (
        isinstance(density_raw, (int, float))
        and not isinstance(density_raw, bool)
        and density_raw <= 0.0
    ):
        return mid

    # Ao contrario de `bass.attack_style`, os seis `keyswitch_corda_*` vivem
    # no bloco `parameters` GERAL do manual (numero e o mesmo pitch fisico
    # de teclado do plugin, nao varia por ferramenta) — `tools.modo_bass`
    # so carrega uma nota qualitativa sobre LATCH. `tools.generic` e quem
    # diz que a intencao de corda nao pode ser honrada nessa ferramenta;
    # por isso o gate certo e `context.tool`, nao `context.recipe`.
    if context.tool != "modo_bass":
        return mid

    from ._helpers import iter_note_dicts, manual_value, technique_from_manual

    # `TechniqueRecipeError` mora em `tools/techniques/errors.py`, um modulo
    # DIFERENTE de `engine.py` (onde este aplicador esta definido) — e
    # `engine.py` nunca importa o nome solto no proprio escopo de modulo
    # (ver comentario acima de `TechniqueContractError`). Por isso este
    # import local e uma dependencia de verdade num modulo externo, mesmo
    # padrao ja usado para `iter_note_dicts` acima, em vez de uma captura
    # disfarcada do global do proprio modulo.
    from .errors import TechniqueRecipeError

    technique = technique_from_manual(context)
    tuning_raw = context.parameters.get("tuning")
    tuning = tuple(tuning_raw) if tuning_raw else _BASS_DEFAULT_TUNING

    string_order_by_count: dict[int, tuple[str, ...]] = {
        4: ("keyswitch_corda_E", "keyswitch_corda_A", "keyswitch_corda_D", "keyswitch_corda_G"),
        5: ("keyswitch_corda_B", "keyswitch_corda_E", "keyswitch_corda_A", "keyswitch_corda_D", "keyswitch_corda_G"),
        6: ("keyswitch_corda_B", "keyswitch_corda_E", "keyswitch_corda_A", "keyswitch_corda_D", "keyswitch_corda_G", "keyswitch_corda_C"),
    }
    string_order = string_order_by_count.get(len(tuning))
    if string_order is None:
        return mid

    keyswitch_by_string: list[int] = []
    for key in string_order:
        value = manual_value(context, technique, key)
        if not isinstance(value, int) or isinstance(value, bool):
            return mid
        keyswitch_by_string.append(value)
    keyswitch_pitches = set(keyswitch_by_string)

    def _positive_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if value > 0 else None

    max_fret_raw = context.parameters.get("max_fret")
    if max_fret_raw is None:
        max_fret = 24
    else:
        max_fret_value = _positive_number(max_fret_raw)
        if max_fret_value is None and (
            isinstance(max_fret_raw, (list, tuple)) and len(max_fret_raw) == 2
        ):
            lo = _positive_number(max_fret_raw[0])
            hi = _positive_number(max_fret_raw[1])
            # `style.parameters` aceita par [min, max] pra qualquer
            # parametro; max_fret e um limite fisico unico (nao uma faixa
            # pra sortear), por isso resolve pro PONTO MEDIO — mesma
            # convencao de `_midrange` em `bass.attack_style` pra
            # transformar range em valor estrutural unico e deterministico.
            if lo is not None and hi is not None:
                max_fret_value = (lo + hi) / 2
        if max_fret_value is None:
            # Declarado mas invalido (0, negativo, tipo errado, par
            # invalido): rejeita explicitamente em vez de cair no default
            # 24 em silencio — um limite fisico declarado errado nao pode
            # virar "nao declarado" (achado do Codex na PR).
            raise TechniqueRecipeError(
                f"tecnica {context.canonical!r}: style.bass.parameters."
                f"max_fret declarado invalido (precisa ser numero positivo "
                f"ou par [min, max] positivo), got {max_fret_raw!r}"
            )
        max_fret = int(round(max_fret_value))

    for track in mid.tracks:
        # So exclui pitch de KEYSWITCH por valor exato — nao por
        # `pitch >= floor`. Um filtro por piso deixaria passar em silencio
        # uma nota estrutural genuina ABAIXO da afinacao declarada (achado
        # do Codex na PR #94): o loop de atribuicao logo abaixo e quem
        # precisa ver essa nota, pra falhar explicito em vez dela nunca
        # chegar la.
        structural = sorted(
            (
                (note["start"], note["end"], note["channel"], note["pitch"])
                for note in iter_note_dicts(track)
                if note["pitch"] not in keyswitch_pitches
            ),
            key=lambda item: item[0],
        )
        if not structural:
            continue

        assignments: list[tuple[int, int, int, int]] = []
        for start, end, channel, pitch in structural:
            string_index = None
            for idx, open_pitch in enumerate(tuning):
                if open_pitch <= pitch <= open_pitch + max_fret:
                    string_index = idx
                    break
            if string_index is None:
                # Nota estrutural fora do alcance de TODAS as cordas da
                # afinacao declarada dentro de `max_fret`: falha explicita
                # em vez de descartar em silencio as atribuicoes ja
                # calculadas pras OUTRAS notas da track (achado do Codex na
                # PR #94) — antes disso, uma unica nota impossivel de tocar
                # apagava a tecnica da track inteira sem aviso nenhum.
                raise TechniqueRecipeError(
                    f"tecnica {context.canonical!r}: nota estrutural pitch "
                    f"{pitch} (canal {channel}, tick {start}) esta fora do "
                    f"alcance de qualquer corda da afinacao {tuning!r} "
                    f"dentro de max_fret={max_fret}"
                )
            assignments.append((start, end, channel, string_index))

        # Agrupa por canal ANTES de formar run — runs sao independentes por
        # canal (o keyswitch e por canal). Formar run numa lista global
        # ordenada por tick faria uma nota de OUTRO canal, intercalada no
        # meio de duas notas do mesmo canal na mesma corda, quebrar o run
        # em dois — soltando e reacionando o mesmo keyswitch sem
        # necessidade (o note_on do proximo run pode ate ordenar antes do
        # note_off do anterior no mesmo tick, corrompendo o pareamento).
        by_channel: dict[int, list[tuple[int, int, int]]] = {}
        for start, end, channel, string_index in assignments:
            by_channel.setdefault(channel, []).append((start, end, string_index))

        runs_by_channel: dict[int, list[tuple[int, int, int]]] = {}
        for channel, channel_assignments in by_channel.items():
            channel_runs: list[tuple[int, int, int]] = []
            run_start, run_end, run_string = channel_assignments[0]
            for start, end, string_index in channel_assignments[1:]:
                if string_index == run_string:
                    run_end = max(run_end, end)
                elif start < run_end:
                    # Notas sobrepostas no MESMO canal pedindo cordas
                    # diferentes: o keyswitch e estado unico por canal, entao
                    # nao existe forma de manter as duas cordas "ligadas" ao
                    # mesmo tempo — soltar uma delas cedo corromperia a nota
                    # estrutural que ainda esta soando. Falha explicita em
                    # vez de emitir keyswitch conflitante em silencio.
                    raise TechniqueRecipeError(
                        f"tecnica {context.canonical!r}: notas sobrepostas no "
                        f"canal {channel} pedem cordas diferentes (corda "
                        f"{run_string} ate tick {run_end}, corda {string_index} "
                        f"comecando tick {start}) — impossivel manter os dois "
                        "keyswitches simultaneos num canal so; declare essas "
                        "notas em canais separados"
                    )
                else:
                    channel_runs.append((run_start, run_end, run_string))
                    run_start, run_end, run_string = start, end, string_index
            channel_runs.append((run_start, run_end, run_string))
            runs_by_channel[channel] = channel_runs

        absolute = _collect_absolute(track)
        order = len(absolute)
        for channel, channel_runs in runs_by_channel.items():
            for position, (r_start, r_end, string_index) in enumerate(channel_runs):
                ks_pitch = keyswitch_by_string[string_index]
                on_tick = max(0, r_start - 1)
                if position + 1 < len(channel_runs):
                    off_tick = max(on_tick + 1, channel_runs[position + 1][0] - 1)
                else:
                    off_tick = max(on_tick + 1, r_end)
                absolute.append((
                    on_tick, -2, order,
                    _mido.Message("note_on", channel=channel, note=ks_pitch, velocity=127),
                ))
                order += 1
                absolute.append((
                    off_tick, -1, order,
                    _mido.Message("note_off", channel=channel, note=ks_pitch, velocity=0),
                ))
                order += 1

        _sort_and_flush(absolute, track)

    return mid


@register_technique("keys.pitch_bend", "technique")
def _apply_keys_pitch_bend(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Pitch bend em teclas — RPN 0, LSB+MSB completos, curva monotonica.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le do manual (via `build_index`): `centro`, `passos_para_baixo`,
        `passos_para_cima`, `range_default_gm` e `teto_mensagens_por_segundo_din`.
        Nada de hardcode.
      - Sem `density` numerica positiva no plano: NO-OP. Bend geral e ausencia
        de intencao musical.
      - Emite RPN 0 (CC101=0, CC100=0, CC6=<semitons>, CC38=0) uma vez por canal
        envolvido, antes do primeiro bend da track; fecha com RPN Null
        (CC101=127, CC100=127) depois do ultimo bend da track.
      - Cada bend selecionado e curva MONOTONICA na direcao (interval > 0 sobe,
        interval < 0 desce) da cauda de A ate perto do ataque de B, com reset a
        centro no proprio B.start.
      - `mido.Message('pitchwheel', pitch=...)` grava LSB+MSB internamente;
        a resolucao usada e a de `passos_para_(cima|baixo)`, nao 128.
      - Densidade de eventos limitada por `teto_mensagens_por_segundo_din`.
      - Canal 9 (bateria) e ignorado: nota de kit GM e peca distinta, nao grau
        de escala — mesma convencao ja aplicada por keys.modulation,
        keys.expression e keys.damper_pedal.
      - Idempotente: reaplicar com a mesma seed produz eventos com a mesma
        assinatura (canal, tick, valor) e o dedup do dispatch central
        descarta duplicatas.
    """

    import mido as _mido

    from ._helpers import first_tempo, structural_notes, technique_setup
    from ._track_rebuild import collect_absolute, sort_and_flush

    setup = technique_setup(context)
    if setup is None:
        return mid
    density, _technique, _int_param = setup

    centro = _int_param("centro")
    passos_para_baixo = _int_param("passos_para_baixo")
    passos_para_cima = _int_param("passos_para_cima")
    teto_msgs = _int_param("teto_mensagens_por_segundo_din")
    if centro != 8192:
        raise ValueError(
            f"tecnica {context.canonical!r} espera centro=8192 (MIDI 1.0); "
            f"manual declarou {centro}"
        )

    range_raw = context.parameters.get("range")
    if isinstance(range_raw, (int, float)) and not isinstance(range_raw, bool):
        range_semitones = max(1, int(range_raw))
    else:
        range_semitones = _int_param("range_default_gm")

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    tempo_us = first_tempo(mid)
    ticks_per_second = ticks_per_beat * 1_000_000 / tempo_us
    max_gap_ticks = ticks_per_beat
    steps_target = 8

    select_rng = context.rng("pitch_bend_select")

    for track in mid.tracks:
        # Canal 9 (bateria) e ignorado: notas de kit GM sao pecas distintas,
        # nao graus de escala — tratar duas batidas consecutivas como um
        # intervalo de altura e emitir "glide" entre elas nao tem sentido
        # musical nenhum. Mesma convencao ja aplicada por keys.modulation,
        # keys.expression e keys.damper_pedal via `iter_track_selections`.
        structural = structural_notes(track, skip_drum_channel=True)
        if len(structural) < 2:
            continue

        by_channel: dict[int, list[dict]] = {}
        for entry in structural:
            by_channel.setdefault(entry["channel"], []).append(entry)
        for lst in by_channel.values():
            lst.sort(key=lambda item: (item["start"], item["end"]))

        pairs: list[tuple[dict, dict]] = []
        for lst in by_channel.values():
            for a, b in zip(lst, lst[1:], strict=False):
                interval = b["pitch"] - a["pitch"]
                if interval == 0 or abs(interval) > range_semitones:
                    continue
                if b["start"] <= a["start"]:
                    continue
                if b["start"] - a["end"] > max_gap_ticks:
                    continue
                pairs.append((a, b))

        pairs.sort(key=lambda pair: (pair[0]["start"], pair[0]["channel"]))
        selected: list[tuple[dict, dict]] = []
        if density >= 1.0:
            selected = list(pairs)
        else:
            for pair in pairs:
                if select_rng.random() < density:
                    selected.append(pair)
        if not selected:
            continue

        absolute = collect_absolute(track)
        order = len(absolute)

        first_bend_tick = min(a["start"] for a, _ in selected)
        last_reset_tick = max(b["start"] for _, b in selected)
        channels = sorted({a["channel"] for a, _ in selected})

        rpn_tick = max(0, first_bend_tick - 1)
        for channel in channels:
            for cc_num, cc_val in (
                (101, 0), (100, 0), (6, range_semitones), (38, 0),
            ):
                absolute.append((
                    rpn_tick, -3, order,
                    _mido.Message(
                        "control_change", channel=channel,
                        control=cc_num, value=cc_val,
                    ),
                ))
                order += 1

        for a, b in selected:
            interval = b["pitch"] - a["pitch"]
            if interval > 0:
                target_wheel = int(round(passos_para_cima * interval / range_semitones))
                target_wheel = max(0, min(passos_para_cima, target_wheel))
            else:
                target_wheel = -int(round(
                    passos_para_baixo * abs(interval) / range_semitones
                ))
                target_wheel = max(-passos_para_baixo, min(0, target_wheel))

            a_mid = a["start"] + max(1, (a["end"] - a["start"]) // 2)
            span_end = b["start"] - 1
            span_start = min(a_mid, span_end)
            span_ticks = max(1, span_end - span_start)
            span_seconds = span_ticks / ticks_per_second
            max_steps_by_rate = (
                max(2, int(span_seconds * teto_msgs)) if span_seconds > 0 else 2
            )
            max_steps_by_ticks = max(2, span_ticks)
            n_steps = max(2, min(steps_target, max_steps_by_rate, max_steps_by_ticks))

            for i in range(n_steps):
                frac = (i + 1) / n_steps
                pitch_value = int(round(target_wheel * frac))
                if target_wheel > 0:
                    pitch_value = max(0, min(target_wheel, pitch_value))
                else:
                    pitch_value = min(0, max(target_wheel, pitch_value))
                step_tick = span_start + int(round(span_ticks * frac))
                absolute.append((
                    step_tick, -2, order,
                    _mido.Message(
                        "pitchwheel", channel=a["channel"], pitch=pitch_value,
                    ),
                ))
                order += 1
            absolute.append((
                b["start"], -2, order,
                _mido.Message("pitchwheel", channel=a["channel"], pitch=0),
            ))
            order += 1

        null_tick = last_reset_tick + 1
        for channel in channels:
            for cc_num, cc_val in ((101, 127), (100, 127)):
                absolute.append((
                    null_tick, 5, order,
                    _mido.Message(
                        "control_change", channel=channel,
                        control=cc_num, value=cc_val,
                    ),
                ))
                order += 1

        sort_and_flush(absolute, track)

    return mid


@register_technique("keys.modulation", "technique")
def _apply_keys_modulation(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Modulation em teclas — CC1 com profundidade lida do manual, sem invencao.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le do manual `keys.modulation` (via `build_index`): `cc` (1), `cc_lsb`
        (33), `default` (0), `profundidade_default_cents` (50) e
        `teto_dls_cents` (1200). Nada de hardcode.
      - Sem `density` numerica positiva no plano: NO-OP. Modulation geral e
        ausencia de intencao musical.
      - `depth_cents` opcional do plano: > 0 e <= `profundidade_default_cents`
        (50) — profundidade maior exige RPN 5, fora do escopo desta rodada;
        maior que `teto_dls_cents` estoura a faixa fisica do instrumento. Erro
        explicito em ambos os casos.
      - Cadencia de eventos: `eventos_por_segundo_recomendados` de
        `keys.modulation` esta com `source: null` no manual (lacuna declarada).
        CONVENCAO: usar `teto_mensagens_por_segundo_din` de `keys.pitch_bend`
        como teto compartilhado — 1042 msg/s a 31.25 kBaud e limite fisico da
        porta DIN, valido para TODO CC. Mesmo `steps_target=8` de
        `keys.pitch_bend`, sem inventar cadencia recomendada.
      - CC33 (LSB) NAO e emitido: o envelope trabalha em passos inteiros de
        CC1 (0..127); LSB so ganharia bit adicional em transicoes fracionarias,
        que nao existem aqui. `cc_lsb` e lido para o assert de consistencia com
        o manual (garante que o numero esta la), nao para producao.
      - Canal 9 (bateria) e ignorado: o manual e explicito que canais de
        ritmo nao devem responder a CC1.
      - Envelope por nota selecionada: sobe de `default` (0) ate o pico
        (`round(127 * depth_cents / profundidade_default_cents)`) durante a
        primeira metade da nota e desce de volta a 0 na segunda metade. Assim
        CC1 volta a 0 no fim de todo trecho onde foi aplicado, sem deixar
        modulation grudada.
      - Determinismo por seed: selecao via `context.rng("modulation_select")`;
        envelope e deterministico por nota.
      - Idempotente: mesma seed produz eventos com a mesma assinatura
        (canal, tick, valor); o dedup do dispatch descarta duplicatas.
    """

    from ._helpers import (
        apply_symmetric_cc_envelope,
        cc_envelope_setup,
        iter_track_selections,
    )

    prepared = cc_envelope_setup(context, mid, rng_key="modulation_select")
    if prepared is None:
        return mid
    density, _technique, _int_param, teto_msgs, ticks_per_second, select_rng = prepared

    cc_mod = _int_param("cc")
    cc_lsb = _int_param("cc_lsb")
    default_cc = _int_param("default")
    profundidade_default_cents = _int_param("profundidade_default_cents")
    teto_dls_cents = _int_param("teto_dls_cents")
    if cc_mod != 1:
        raise ValueError(
            f"tecnica {context.canonical!r} espera cc=1 (MIDI 1.0); "
            f"manual declarou {cc_mod}"
        )
    if cc_lsb != 33:
        raise ValueError(
            f"tecnica {context.canonical!r} espera cc_lsb=33 (MIDI 1.0); "
            f"manual declarou {cc_lsb}"
        )
    if teto_dls_cents < profundidade_default_cents:
        raise ValueError(
            f"tecnica {context.canonical!r}: manual inconsistente — "
            f"teto_dls_cents ({teto_dls_cents}) < profundidade_default_cents "
            f"({profundidade_default_cents})"
        )

    depth_raw = context.parameters.get("depth_cents")
    if depth_raw is None:
        depth_cents = float(profundidade_default_cents)
    elif not isinstance(depth_raw, (int, float)) or isinstance(depth_raw, bool):
        raise ValueError(
            f"tecnica {context.canonical!r}: depth_cents precisa ser numero"
        )
    else:
        depth_cents = float(depth_raw)
        if depth_cents <= 0.0:
            return mid
        if depth_cents > teto_dls_cents:
            raise ValueError(
                f"tecnica {context.canonical!r}: depth_cents={depth_cents} "
                f"estoura teto_dls_cents={teto_dls_cents}"
            )
        if depth_cents > profundidade_default_cents:
            raise ValueError(
                f"tecnica {context.canonical!r}: depth_cents={depth_cents} "
                f"> profundidade_default_cents={profundidade_default_cents} "
                "exige RPN 5 (Modulation Depth Range), nao implementado "
                "nesta rodada"
            )

    peak_cc1 = int(round(127 * depth_cents / profundidade_default_cents))
    peak_cc1 = max(1, min(127, peak_cc1))

    # Teto compartilhado da porta DIN — lacuna declarada em `keys.modulation`
    # (`eventos_por_segundo_recomendados` sem source); reusamos o teto fisico
    # sourced de `keys.pitch_bend` porque e limite da porta, nao da tecnica.
    for track, selected in iter_track_selections(
        mid, density=density, rng=select_rng
    ):
        apply_symmetric_cc_envelope(
            track,
            selected,
            cc=cc_mod,
            rest_value=default_cc,
            extreme_value=peak_cc1,
            ticks_per_second=ticks_per_second,
            teto_msgs=teto_msgs,
        )

    return mid


@register_technique("keys.expression", "technique")
def _apply_keys_expression(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Expression em teclas — CC11 (dinamica), NUNCA CC7 (fader).

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le do manual `keys.expression` (via `build_index`): `cc_expression` (11),
        `cc_volume` (7), `default_cc11` (127), `cc11_lsb` (43). O `cc_volume` e
        lido SO para o assert de manual — nunca e emitido; um teste explicito
        garante isso. O `cc11_lsb` idem: passos inteiros de CC11 nao precisam
        de LSB, e emiti-lo seria inventar cadencia fracionaria.
      - Sem `density` numerica positiva no plano: NO-OP.
      - `default_cc11` (127) e o repouso. A curva SO se afasta do repouso
        DENTRO da nota e obrigatoriamente RETORNA a 127 no fim da nota. Nunca
        comeca nem termina fora do default sem retornar — teste explicito.
      - Direcao: 127 e o teto pratico do CC11, entao o unico movimento
        significativo e para BAIXO (dip). O manual documenta db_em_cc_64=-11.9
        e db_em_cc_96=-4.9 como valores sourced da curva quadratica do GM2;
        usamos CC11=64 como VALLEY default (dip de 63 abaixo do repouso).
      - Lacuna: `forma_temporal_recomendada_da_rampa` esta com `source: null`
        no manual (lacuna declarada). CONVENCAO: rampa linear simetrica
        (127 -> valley -> 127) — mesma forma canonica de `keys.modulation`,
        que ja e a curva mais barata de auditar em teste e nao introduz
        segunda ordem sem source. Se um dia a MMA normatizar forma, o
        aplicador troca a curva sem mexer no restante.
      - Cadencia de eventos: mesmo caso da modulation — reusamos o
        `teto_mensagens_por_segundo_din` de `keys.pitch_bend` (1042 msg/s,
        limite fisico da porta DIN) como teto compartilhado. Mesmo
        `steps_target=8`.
      - Canal 9 (bateria) e ignorado: expression e usada por keys/melodicos;
        aplicar em drum channel seria fora da familia da tecnica.
      - `depth` opcional do plano: se ausente, dip default (63) do repouso.
        Faixa aceita: 1..default_cc11 (127). Fora dessa faixa, ValueError
        explicito. `depth=0` (nao positivo) e NO-OP (equivale a densidade zero
        para esta nota).
      - Determinismo por seed; idempotente: mesma seed gera eventos com a
        mesma assinatura (canal, tick, valor); o dedup do dispatch descarta
        duplicatas.
    """

    from ._helpers import (
        apply_symmetric_cc_envelope,
        cc_envelope_setup,
        iter_track_selections,
    )

    prepared = cc_envelope_setup(context, mid, rng_key="expression_select")
    if prepared is None:
        return mid
    density, _technique, _int_param, teto_msgs, ticks_per_second, select_rng = prepared

    cc_expression = _int_param("cc_expression")
    cc_volume = _int_param("cc_volume")
    default_cc11 = _int_param("default_cc11")
    cc11_lsb = _int_param("cc11_lsb")
    if cc_expression != 11:
        raise ValueError(
            f"tecnica {context.canonical!r} espera cc_expression=11 (MIDI 1.0); "
            f"manual declarou {cc_expression}"
        )
    if cc_volume != 7:
        raise ValueError(
            f"tecnica {context.canonical!r} espera cc_volume=7 (MIDI 1.0); "
            f"manual declarou {cc_volume}"
        )
    if cc11_lsb != 43:
        raise ValueError(
            f"tecnica {context.canonical!r} espera cc11_lsb=43 (MIDI 1.0); "
            f"manual declarou {cc11_lsb}"
        )
    if default_cc11 != 127:
        raise ValueError(
            f"tecnica {context.canonical!r} espera default_cc11=127 (GM2); "
            f"manual declarou {default_cc11}"
        )

    depth_raw = context.parameters.get("depth")
    if depth_raw is None:
        # CONVENCAO: valley em CC11=64 (dip de 63) — unico valor sourced
        # da tabela GM2 (-11.9 dB) representando meio-curso auditivo.
        depth = 63
    elif not isinstance(depth_raw, (int, float)) or isinstance(depth_raw, bool):
        raise ValueError(
            f"tecnica {context.canonical!r}: depth precisa ser numero"
        )
    else:
        depth_num = float(depth_raw)
        if depth_num <= 0.0:
            return mid
        if depth_num > default_cc11:
            raise ValueError(
                f"tecnica {context.canonical!r}: depth={depth_num} "
                f"excede default_cc11={default_cc11}"
            )
        depth = int(round(depth_num))
        depth = max(1, min(default_cc11, depth))

    valley = max(0, default_cc11 - depth)

    for track, selected in iter_track_selections(
        mid, density=density, rng=select_rng
    ):
        apply_symmetric_cc_envelope(
            track,
            selected,
            cc=cc_expression,
            rest_value=default_cc11,
            extreme_value=valley,
            ticks_per_second=ticks_per_second,
            teto_msgs=teto_msgs,
        )

    return mid


@register_technique("keys.damper_pedal", "technique")
def _apply_keys_damper_pedal(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Damper pedal (CC64) binario — meio-pedal so com opt-in explicito.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le do manual `keys.damper_pedal` (via `build_index`): `cc` (64),
        `limiar_on_min` (64), `limiar_off_max` (63), `default` (0),
        `passos_maximos_de_meio_pedal` (128).
      - Sem `density` numerica positiva no plano: NO-OP. Pedal sem intencao
        declarada nunca e default.
      - Padrao humano documentado: CC64=127 a maior parte do tempo, queda a
        `default` (0) LOGO DEPOIS de cada mudanca harmonica selecionada, e
        repressao em seguida. Nunca simultaneo ao note-on nem antes dele — o
        manual e explicito que isso captura o acorde anterior. Aqui usamos
        `gap_ticks = max(1, ticks_per_beat // 64)` para garantir que a
        release/repress cai APOS a nota-alvo, mantendo o intervalo
        praticamente instantaneo.
      - Meio-pedal: `half_pedal_supported=True` no plano habilita
        `press_value` intermediario em `[limiar_on_min, 127]`. Sem opt-in,
        `press_value` diferente de 127 e erro explicito (o manual e
        categorico que half-damper nao e padronizado; receptor conforme le
        63 como OFF total e 64 como ON total). `press_value` fora da faixa
        legal, ou nao numerico, tambem e erro.
      - Canal 9 (bateria) e ignorado.
      - Selecao por densidade determina QUAIS onsets recebem o par
        release/press; a lista final e ordenada por `(start, channel, pitch)`
        para casar com o sort da fotografia (`sort_and_flush`).
      - Per-canal: primeiro onset selecionado emite apenas a PRESS (nao
        havia pedal para soltar); onsets subsequentes emitem release em
        `start+gap` e press em `start+2*gap`. Ao final, emitimos release
        (`default`) em `max(note_end)` daquele canal — nunca deixamos pedal
        pendurado no fim da track. `sort_and_flush` empurra `end_of_track`
        para o ultimo tick, garantindo que o CC64=0 final entra antes.
      - Determinismo por seed: selecao via `context.rng("damper_select")`.
      - Idempotente: reaplicar produz eventos com a mesma assinatura
        (canal, tick, valor); dedup do dispatch descarta duplicatas.
    """

    import mido as _mido

    from ._helpers import iter_track_selections, technique_setup
    from ._track_rebuild import collect_absolute, sort_and_flush

    setup = technique_setup(context)
    if setup is None:
        return mid
    density, _technique, _int_param = setup

    cc = _int_param("cc")
    limiar_on_min = _int_param("limiar_on_min")
    limiar_off_max = _int_param("limiar_off_max")
    default_off = _int_param("default")
    if cc != 64:
        raise ValueError(
            f"tecnica {context.canonical!r} espera cc=64 (MIDI 1.0); "
            f"manual declarou {cc}"
        )
    if limiar_on_min != 64 or limiar_off_max != 63:
        raise ValueError(
            f"tecnica {context.canonical!r}: manual inconsistente — "
            f"limiar_on_min={limiar_on_min}, limiar_off_max={limiar_off_max}"
        )
    if default_off > limiar_off_max:
        raise ValueError(
            f"tecnica {context.canonical!r}: default={default_off} deveria "
            f"cair na faixa OFF (<= {limiar_off_max})"
        )

    half_pedal_supported_raw = context.parameters.get("half_pedal_supported", False)
    if not isinstance(half_pedal_supported_raw, bool):
        raise ValueError(
            f"tecnica {context.canonical!r}: half_pedal_supported precisa ser bool"
        )
    half_pedal_supported = half_pedal_supported_raw

    press_raw = context.parameters.get("press_value")
    if press_raw is None:
        press_value = 127
    elif not isinstance(press_raw, (int, float)) or isinstance(press_raw, bool):
        raise ValueError(
            f"tecnica {context.canonical!r}: press_value precisa ser numero"
        )
    else:
        press_value = int(round(float(press_raw)))
        if press_value < limiar_on_min or press_value > 127:
            raise ValueError(
                f"tecnica {context.canonical!r}: press_value={press_value} fora "
                f"de [{limiar_on_min}, 127]"
            )
        if press_value != 127 and not half_pedal_supported:
            raise ValueError(
                f"tecnica {context.canonical!r}: press_value={press_value} "
                "exige half_pedal_supported=True; half-damper nao e padronizado "
                "e receptor conforme le como ON total"
            )

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    gap = max(1, ticks_per_beat // 64)

    select_rng = context.rng("damper_select")

    for track, selected in iter_track_selections(
        mid, density=density, rng=select_rng
    ):
        selected_sorted = sorted(
            selected, key=lambda c: (c["start"], c["channel"], c["pitch"])
        )

        absolute = collect_absolute(track)
        order = len(absolute)

        per_channel_state: dict[int, dict] = {}
        for cand in selected_sorted:
            channel = cand["channel"]
            start = cand["start"]
            end = cand["end"]
            state = per_channel_state.get(channel)
            if state is None:
                press_tick = start + gap
                per_channel_state[channel] = {"last_end": end}
            else:
                release_tick = start + gap
                press_tick = start + 2 * gap
                absolute.append((
                    release_tick, -2, order,
                    _mido.Message(
                        "control_change",
                        channel=channel,
                        control=cc,
                        value=default_off,
                    ),
                ))
                order += 1
                state["last_end"] = max(state["last_end"], end)

            absolute.append((
                press_tick, -2, order,
                _mido.Message(
                    "control_change",
                    channel=channel,
                    control=cc,
                    value=press_value,
                ),
            ))
            order += 1

            # Update last_end even for the first onset per-channel.
            state = per_channel_state[channel]
            state["last_end"] = max(state["last_end"], end)

        for channel, state in per_channel_state.items():
            absolute.append((
                state["last_end"], -2, order,
                _mido.Message(
                    "control_change",
                    channel=channel,
                    control=cc,
                    value=default_off,
                ),
            ))
            order += 1

        sort_and_flush(absolute, track)

    return mid



@register_technique(
    "guitar.palm_mute",
    "technique",
    allow_structural_velocity_change=True,
    allow_structural_duration_change=True,
)
def _apply_guitar_palm_mute(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Palm mute / chug na guitarra — profundidade por velocity, gate curto.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `velocity` e `gate_pct` do manual via `build_index()` (CONVENCAO,
        `knowledge/tecnicas/tecnicas_guitarra_midi.md#1`).
      - Precedencia `context.parameters` > receita > `range` do manual.
      - `density` ausente ou <= 0 DESLIGA — mute geral e ausencia de intencao
        musical, nunca default (mute continuo na track inteira nao e
        "seguro", e ausencia de decisao musical).
      - Ample e Shreddage 3 modulam a PROFUNDIDADE do mute pela VELOCITY, nao
        por keyswitch fixo (manual §1): a nota selecionada sai numa faixa de
        velocity mais baixa em vez de trocar de articulacao travada.
      - Encurta a duracao pelo `gate_pct` do manual — chug e nota curta.
      - Quando a receita da ferramenta declara `keyswitch` (Ample/Ample
        Metal selecionam a articulacao Mute por keyswitch), o keyswitch e
        segurado (par note_on/note_off) do inicio ao fim de cada nota
        mutada — ferramentas sem `keyswitch` na receita (generic,
        shreddage3, musiclab_reallpc) so mudam velocity/gate.
      - Determinismo por seed via `context.rng()`.
    """

    import mido as _mido

    from ._param_range import load_range_resolver

    density_raw = context.parameters.get("density")
    if not isinstance(density_raw, (int, float)) or isinstance(density_raw, bool):
        return mid
    density = float(density_raw)
    if density <= 0.0:
        return mid

    _technique, _range = load_range_resolver(context)

    velocity_range = _range("velocity") or (30.0, 70.0)
    velocity_lo = max(1, int(velocity_range[0]))
    velocity_hi = max(velocity_lo, int(velocity_range[1]))

    gate_range = _range("gate_pct") or (25.0, 50.0)
    gate_lo = max(1.0, float(gate_range[0]))
    gate_hi = max(gate_lo, float(gate_range[1]))

    keyswitch_pitch: int | None = None
    ks_raw = context.recipe.get("keyswitch") if context.recipe else None
    if isinstance(ks_raw, int) and not isinstance(ks_raw, bool):
        keyswitch_pitch = ks_raw

    if mid.ticks_per_beat <= 0:
        return mid

    selection_rng = context.rng("selection")
    velocity_rng = context.rng("velocity")
    gate_rng = context.rng("gate")

    for track in mid.tracks:
        pairs = list(_iter_note_pairs(track))
        if not pairs:
            continue

        indices = list(range(len(pairs)))
        selection_rng.shuffle(indices)
        wanted = max(1, min(len(pairs), int(round(len(pairs) * density))))
        selected = set(indices[:wanted])
        if not selected:
            continue

        new_velocity_by_idx: dict[int, int] = {}
        new_end_by_idx: dict[int, int] = {}
        keyswitch_events: list[tuple[int, int, mido.Message]] = []
        for pair_index, pair in enumerate(pairs):
            if pair_index not in selected:
                continue
            (
                channel, _pitch, start_tick, end_tick, _orig_vel,
                note_on_index, note_off_index,
            ) = pair
            duration = max(1, end_tick - start_tick)
            gate_pct = gate_rng.uniform(gate_lo, gate_hi)
            new_duration = max(1, int(round(duration * gate_pct / 100.0)))
            new_end = start_tick + min(duration, new_duration)
            new_velocity_by_idx[note_on_index] = velocity_rng.randint(velocity_lo, velocity_hi)
            new_end_by_idx[note_off_index] = new_end

            if keyswitch_pitch is not None:
                keyswitch_events.append((
                    max(0, start_tick - 1), -2,
                    _mido.Message(
                        "note_on", channel=channel,
                        note=keyswitch_pitch, velocity=127,
                    ),
                ))
                keyswitch_events.append((
                    new_end, 4,
                    _mido.Message(
                        "note_off", channel=channel,
                        note=keyswitch_pitch, velocity=0,
                    ),
                ))

        if not new_velocity_by_idx:
            continue

        absolute: list[tuple[int, int, int, mido.Message]] = []
        tick = 0
        for msg_index, msg in enumerate(track):
            tick += msg.time
            abs_tick = new_end_by_idx.get(msg_index, tick)
            if msg_index in new_velocity_by_idx and not msg.is_meta:
                msg = msg.copy(velocity=new_velocity_by_idx[msg_index])
            absolute.append((abs_tick, 0, msg_index, msg))
        order = len(absolute)
        for event_tick, bias, ks_msg in keyswitch_events:
            absolute.append((event_tick, bias, order, ks_msg))
            order += 1

        absolute.sort(key=lambda item: (item[0], item[1], item[2]))
        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _bias, _order, msg in absolute:
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    return mid


@register_technique("guitar.dead_notes", "technique")
def _apply_guitar_dead_notes(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Dead notes entre chugs — transiente da palheta contra corda abafada.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `velocity` e `gate_pct` do manual via `build_index()` (CONVENCAO,
        `knowledge/tecnicas/tecnicas_guitarra_midi.md#10`).
      - Precedencia `context.parameters` > receita > `range` do manual.
      - `density = 0.0` DESLIGA — teto checado ANTES de acrescentar
        candidato (mesmo cuidado de `bass.ghost_notes`/`drums.ghost_notes`).
      - Nao semeia em silencio: intervalo entre notas estruturais > 1
        compasso (`ticks_per_beat*4`) e borda de pausa, nao groove — a mao
        da palheta nao continua tocando sobre um silencio de arranjo.
      - Dead note herda o pitch da nota estrutural anterior (mesma corda em
        que a mao ja esta) — nunca inventa altura.
      - Idempotente: reaplicar com a mesma seed dispara o dedup central do
        dispatch (`_drop_reapplied_notes`).
    """

    import mido as _mido

    from ._param_range import load_range_resolver

    _technique, _range = load_range_resolver(context)

    velocity_range = _range("velocity") or (15.0, 35.0)
    velocity_lo = max(1, int(velocity_range[0]))
    velocity_hi = max(velocity_lo, int(velocity_range[1]))

    gate_range = _range("gate_pct") or (8.0, 20.0)
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

    position_rng = context.rng("positions")
    velocity_rng = context.rng("velocity")
    gate_rng = context.rng("gate")

    def target_count(size: int) -> int:
        if density is None:
            return size
        if density <= 0.0:
            return 0
        return max(1, min(size, int(round(size * density))))

    def overlaps_structural(existing, channel, pitch, start_tick, end_tick):
        for chan, pit, start, end in existing:
            if chan != channel or pit != pitch:
                continue
            if start < end_tick and end > start_tick:
                return True
        return False

    for track in mid.tracks:
        pairs = [
            (channel, pitch, start_tick, end_tick)
            for channel, pitch, start_tick, end_tick, _vel, _on, _off
            in _iter_note_pairs(track)
        ]
        if len(pairs) < 2:
            continue

        by_channel: dict[int, list[tuple[int, int, int, int]]] = {}
        for pair in pairs:
            by_channel.setdefault(pair[0], []).append(pair)
        for lst in by_channel.values():
            lst.sort(key=lambda item: (item[2], item[3]))

        candidates: list[dict[str, int]] = []
        for channel_pairs in by_channel.values():
            for current, following in zip(
                channel_pairs, channel_pairs[1:], strict=False,
            ):
                cur_channel, cur_pitch, cur_start, _cur_end = current
                _n_channel, _n_pitch, next_start, _n_end = following
                if next_start - cur_start > max_groove_interval:
                    continue
                tick = cur_start + sixteenth
                while tick < next_start:
                    sixteenth_in_beat = (tick % ticks_per_beat) // sixteenth
                    if sixteenth_in_beat in (1, 3):
                        candidates.append({
                            "tick": tick,
                            "channel": cur_channel,
                            "pitch": cur_pitch,
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
                pairs, candidate["channel"], candidate["pitch"],
                candidate["tick"], end_tick,
            ):
                continue
            candidate["end_tick"] = end_tick
            candidate["velocity"] = velocity_rng.randint(velocity_lo, velocity_hi)
            selected.append(candidate)
            seen_slots.add(slot)

        if not selected:
            continue

        selected.sort(key=lambda item: item["tick"])
        events: list[tuple[int, int, mido.Message]] = []
        for candidate in selected:
            channel = candidate["channel"]
            events.append((
                candidate["tick"], 1,
                _mido.Message(
                    "note_on", channel=channel,
                    note=candidate["pitch"], velocity=candidate["velocity"],
                ),
            ))
            events.append((
                candidate["end_tick"], 3,
                _mido.Message(
                    "note_off", channel=channel,
                    note=candidate["pitch"], velocity=0,
                ),
            ))

        absolute: list[tuple[int, int, int, mido.Message]] = []
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
            absolute, key=lambda item: (item[0], item[1], item[2]),
        ):
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    return mid


@register_technique(
    "guitar.pinch_harmonic",
    "technique",
    allow_structural_velocity_change=True,
)
def _apply_guitar_pinch_harmonic(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Pinch harmonic — velocity 127 sequestra a articulacao em Ample.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `ample_velocity_gatilho` (127) e `ample_teto_de_acento_normal`
        (126) do manual — numeros com fonte real (`§3`, Ample Settings &
        CPC), nao CONVENCAO.
      - So tem realizacao honesta em `tool: ample` ou `tool: ample_metal`:
        a UNICA receita `generic` do manual exige transpor a nota escrita
        pelo intervalo do parcial harmonico, o que e mudanca de pitch
        estrutural — proibida fora da excecao de bateria
        (`drums.articulation_diff`). Em qualquer outra ferramenta a tecnica
        falha explicito em vez de aplicar um acento que nao e pinch
        harmonic nenhum.
      - `density` ausente ou <= 0 DESLIGA.
      - Seleciona notas estruturais abaixo do gatilho e sobe a velocity
        para exatamente 127 — nunca mexe em pitch, posicao ou duracao.
      - Idempotente: reaplicar so pode subir velocity ja em 127 para 127
        (no-op observavel), nunca oscila.
    """

    import mido as _mido

    from ._helpers import manual_value, technique_from_manual
    from .errors import TechniqueRecipeError

    density_raw = context.parameters.get("density")
    if not isinstance(density_raw, (int, float)) or isinstance(density_raw, bool):
        return mid
    density = float(density_raw)
    if density <= 0.0:
        return mid

    if context.tool not in {"ample", "ample_metal"}:
        raise TechniqueRecipeError(
            f"tecnica {context.canonical!r}: pinch harmonic so tem "
            "realizacao honesta num plugin que dispara o harmonico pela "
            "velocity 127 (ample/ample_metal); a unica receita generic do "
            "manual exigiria transpor a nota estrutural pelo intervalo do "
            "parcial, mudanca de pitch estrutural proibida fora da "
            "excecao de bateria — declare tool=ample ou tool=ample_metal "
            f"no elemento/edit para usar esta tecnica (recebido: {context.tool!r})"
        )

    technique = technique_from_manual(context)
    gatilho = manual_value(context, technique, "ample_velocity_gatilho")
    teto_acento = manual_value(context, technique, "ample_teto_de_acento_normal")
    if not isinstance(gatilho, int) or isinstance(gatilho, bool) or gatilho != 127:
        raise ValueError(
            f"tecnica {context.canonical!r} espera "
            f"ample_velocity_gatilho=127; manual declarou {gatilho!r}"
        )
    if (
        not isinstance(teto_acento, int)
        or isinstance(teto_acento, bool)
        or teto_acento != 126
    ):
        raise ValueError(
            f"tecnica {context.canonical!r} espera "
            f"ample_teto_de_acento_normal=126; manual declarou {teto_acento!r}"
        )

    select_rng = context.rng("pinch_select")

    for track in mid.tracks:
        pairs = list(_iter_note_pairs(track))
        candidates = [
            index for index, pair in enumerate(pairs) if pair[4] < gatilho
        ]
        if not candidates:
            continue
        shuffled = list(candidates)
        select_rng.shuffle(shuffled)
        wanted = max(1, min(len(candidates), int(round(len(candidates) * density))))
        selected = set(shuffled[:wanted])
        if not selected:
            continue

        new_velocity_by_idx: dict[int, int] = {
            pairs[index][5]: gatilho for index in selected
        }

        absolute: list[tuple[int, int, mido.Message]] = []
        tick = 0
        for msg_index, msg in enumerate(track):
            tick += msg.time
            if msg_index in new_velocity_by_idx and not msg.is_meta:
                msg = msg.copy(velocity=new_velocity_by_idx[msg_index])
            absolute.append((tick, msg_index, msg))

        rebuilt = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _index, msg in sorted(
            absolute, key=lambda item: (item[0], item[1]),
        ):
            rebuilt.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        track[:] = rebuilt

    return mid


@register_technique(
    "guitar.hammer_pull",
    "technique",
    allow_structural_velocity_change=True,
    allow_structural_duration_change=True,
)
def _apply_guitar_hammer_pull(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Hammer-on/pull-off de guitarra — ligado sem reataque, MESMA corda.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `reducao_de_velocity_percentual` e `overlap_ms` do manual
        (CONVENCAO, `knowledge/tecnicas/tecnicas_guitarra_midi.md#7`).
      - Precedencia `context.parameters` > receita > `range` do manual.
      - RESTRICAO FISICA do manual: hammer-on/pull-off so existem na MESMA
        corda. Par de notas so vira candidato quando existe pelo menos uma
        corda (na afinacao de `context.parameters["tuning"]`, default E
        padrao) que alcanca as DUAS alturas dentro de `max_fret` — usa a
        mesma tabela de afinacoes de `tools/techniques/physical.py`.
      - So aplica entre notas adjacentes fisicamente ligaveis: mesmo canal,
        intervalo em semitons dentro do limite de ligado (<= 4), separacao
        temporal curta (<= metade de uma batida).
      - Ligada (segunda nota) sai mais fraca por `reducao_de_velocity_percentual`
        (reducao percentual sobre a velocity original, direcao documentada
        pela Shreddage); primeira estende note_off para sobrepor a segunda
        por `overlap_ms` — sobreposicao e o que dispara o legato num
        instrumento sampleado.
      - Nao altera pitch nem posicao (start_tick) de nota estrutural.
      - Receita com `keyswitch` declarado insere o par note_on/note_off do
        keyswitch em volta da ligadura; sem keyswitch, sobreposicao E a
        unica assinatura de idempotencia disponivel (mesma logica de
        `bass.hammer_pull`).
      - Determinismo por seed via `context.rng()`.
    """

    import mido as _mido

    from ._param_range import load_range_resolver
    from ._track_rebuild import sort_and_flush as _sort_and_flush
    from .physical import _GUITAR_TUNINGS

    _technique, _resolve_range = load_range_resolver(context)
    recipe = context.recipe

    def _range(name: str, fallback: tuple[float, float]) -> tuple[float, float]:
        return _resolve_range(name) or fallback

    density_raw = context.parameters.get("density")
    if isinstance(density_raw, (int, float)) and not isinstance(density_raw, bool):
        density = float(density_raw)
    else:
        density = 0.0
    if density <= 0.0:
        return mid

    reduction_pct_range = _range("reducao_de_velocity_percentual", (15.0, 35.0))
    overlap_range = _range("overlap_ms", (5.0, 15.0))

    tuning_raw = context.parameters.get("tuning")
    if isinstance(tuning_raw, str):
        tuning = _GUITAR_TUNINGS.get(tuning_raw, _GUITAR_TUNINGS["e_padrao"])
    elif isinstance(tuning_raw, (list, tuple)) and tuning_raw:
        tuning = tuple(int(p) for p in tuning_raw)
    else:
        tuning = _GUITAR_TUNINGS["e_padrao"]

    max_fret_raw = context.parameters.get("max_fret")
    max_fret = (
        int(max_fret_raw)
        if isinstance(max_fret_raw, int) and not isinstance(max_fret_raw, bool)
        else 24
    )

    def strings_for(pitch: int) -> set[int]:
        return {
            s for s, open_pitch in enumerate(tuning)
            if open_pitch <= pitch <= open_pitch + max_fret
        }

    keyswitch_pitch: int | None = None
    ks_raw = recipe.get("keyswitch") if recipe else None
    if isinstance(ks_raw, int) and not isinstance(ks_raw, bool):
        keyswitch_pitch = ks_raw

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    max_gap_ticks = ticks_per_beat // 2
    max_interval_semitones = 4

    rng = context.rng("hammer_pull")

    for track in mid.tracks:
        structural: list[dict] = []
        pending: dict[tuple[int, int], list[dict]] = {}
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                entry = {
                    "channel": msg.channel, "pitch": msg.note,
                    "start": tick, "end": None,
                    "on_msg": msg, "off_msg": None,
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
                if not (strings_for(a["pitch"]) & strings_for(b["pitch"])):
                    # Restricao fisica do manual: hammer-on/pull-off so
                    # existem quando as duas alturas alcancam a MESMA corda.
                    continue
                if a["end"] > b["start"] and keyswitch_pitch is None:
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

            reduction_pct = rng.uniform(reduction_pct_range[0], reduction_pct_range[1])
            b["on_msg"].velocity = max(
                1, min(127, int(round(b["on_msg"].velocity * (1.0 - reduction_pct / 100.0)))),
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


@register_technique("guitar.double_tracking", "technique")
def _apply_guitar_double_tracking(
    mid: mido.MidiFile,
    *,
    context: TechniqueContext,
) -> mido.MidiFile:
    """Double tracking real — segunda track com offsets, nunca uma copia.

    Regras que fazem esta tecnica NAO virar `_identity_apply`:
      - Le `offset_de_timing_convencao_ms`, `offset_de_velocity_convencao`
        e `detune_convencao_cents` do manual (CONVENCAO,
        `knowledge/tecnicas/tecnicas_guitarra_midi.md#13`).
      - `density` ausente ou <= 0 DESLIGA — dobrar a track inteira sem
        pedido explicito nao e default seguro.
      - NUNCA duplica a track MIDI 1-para-1: cada nota da track original
        nasce numa SEGUNDA track, num canal novo, com offset de timing e de
        velocity sorteados por nota (`context.rng`) e um detune constante
        de canal via pitch bend — a copia identica soaria coerente em fase
        (manual §13), o oposto do que double tracking real produz.
      - Marca a track nova com `meta text
        guitar_double_tracking_of=<indice da track original>` no tick 0,
        junto do `track_name` `<original> (Double)` — o proprio marcador e
        o mecanismo de idempotencia: reaplicar detecta que a origem ja tem
        par e nao cria uma terceira track, e nunca dobra uma track que ela
        mesma e um double (evita cadeia de doubles).
      - A track original nunca e tocada — permanece nota a nota identica.
      - Detune fica bem abaixo de 1 semitom (100 cents), dentro do range
        default de pitch bend (±2 semitons), sem precisar de RPN.
    """

    import mido as _mido

    from ._helpers import first_tempo
    from ._param_range import load_range_resolver
    from .notes import _track_name

    density_raw = context.parameters.get("density")
    if not isinstance(density_raw, (int, float)) or isinstance(density_raw, bool):
        return mid
    if float(density_raw) <= 0.0:
        return mid

    _technique, _range = load_range_resolver(context)

    timing_range = _range("offset_de_timing_convencao_ms") or (8.0, 20.0)
    velocity_range = _range("offset_de_velocity_convencao") or (-10.0, 10.0)
    detune_range = _range("detune_convencao_cents") or (3.0, 10.0)

    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat <= 0:
        return mid
    ticks_per_ms = ticks_per_beat * 1_000_000 / first_tempo(mid) / 1000.0

    marker_prefix = "guitar_double_tracking_of="

    existing_marked_sources: set[int] = set()
    for existing_track in mid.tracks:
        for msg in existing_track:
            if (
                msg.is_meta and msg.type == "text"
                and isinstance(msg.text, str)
                and msg.text.startswith(marker_prefix)
            ):
                try:
                    existing_marked_sources.add(int(msg.text[len(marker_prefix):]))
                except ValueError:
                    continue

    timing_rng = context.rng("double_timing")
    velocity_rng = context.rng("double_velocity")
    detune_rng = context.rng("double_detune")

    new_tracks: list[mido.MidiTrack] = []
    original_track_count = len(mid.tracks)
    for track_index in range(original_track_count):
        if track_index in existing_marked_sources:
            continue
        track = mid.tracks[track_index]
        is_double_track = any(
            msg.is_meta and msg.type == "text"
            and isinstance(msg.text, str)
            and msg.text.startswith(marker_prefix)
            for msg in track
        )
        if is_double_track:
            continue

        pairs = list(_iter_note_pairs(track))
        if not pairs:
            continue

        original_channels = sorted({pair[0] for pair in pairs})
        new_channel = (original_channels[0] + 1) % 16
        if new_channel == 9:
            new_channel = (new_channel + 1) % 16
        if new_channel in original_channels:
            for candidate_channel in range(16):
                if candidate_channel != 9 and candidate_channel not in original_channels:
                    new_channel = candidate_channel
                    break

        timing_offset_ms = timing_rng.uniform(timing_range[0], timing_range[1])
        timing_offset_ticks = int(round(timing_offset_ms * ticks_per_ms))
        velocity_offset = int(round(
            velocity_rng.uniform(velocity_range[0], velocity_range[1])
        ))
        detune_sign = 1 if detune_rng.random() < 0.5 else -1
        detune_cents = detune_sign * detune_rng.uniform(
            detune_range[0], detune_range[1],
        )
        pitch_bend_value = int(round(8192 * (detune_cents / 100.0) / 2.0))
        pitch_bend_value = max(-8192, min(8191, pitch_bend_value))

        source_name = _track_name(track) or f"Track {track_index}"

        events: list[tuple[int, int, mido.Message | mido.MetaMessage]] = [
            (0, 0, _mido.MetaMessage(
                "track_name", name=f"{source_name} (Double)"[:127],
            )),
            (0, 1, _mido.MetaMessage(
                "text", text=f"{marker_prefix}{track_index}",
            )),
            (0, 2, _mido.Message(
                "pitchwheel", channel=new_channel, pitch=pitch_bend_value,
            )),
        ]
        for _channel, pitch, start, end, velocity, _on_idx, _off_idx in pairs:
            new_start = max(0, start + timing_offset_ticks)
            new_end = max(new_start + 1, end + timing_offset_ticks)
            new_velocity = max(1, min(127, velocity + velocity_offset))
            events.append((
                new_start, 3,
                _mido.Message(
                    "note_on", channel=new_channel,
                    note=pitch, velocity=new_velocity,
                ),
            ))
            events.append((
                new_end, 4,
                _mido.Message(
                    "note_off", channel=new_channel, note=pitch, velocity=0,
                ),
            ))

        events.sort(key=lambda item: (item[0], item[1]))
        new_track = _mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _bias, msg in events:
            new_track.append(msg.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        new_track.append(_mido.MetaMessage("end_of_track", time=0))
        new_tracks.append(new_track)

    if new_tracks:
        mid.tracks.extend(new_tracks)

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
