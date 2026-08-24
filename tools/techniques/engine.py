"""Registro e despacho das tecnicas aplicaveis.

Este modulo e separado de `tools.techniques.index`: o indice le os manuais em
`knowledge/tecnicas/`, enquanto este registro declara quais tecnicas o motor
consegue aplicar e qual funcao executa cada uma. A populacao e explicita por
decorator; nao ha varredura dinamica de modulos, porque isso esconderia a
fronteira entre tecnica documentada e tecnica realmente implementada.
"""

from __future__ import annotations

import hashlib
import inspect
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import mido

from .index import Technique, TechniqueIndex, build_index

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
        seed: int,
        parameters: Mapping[str, Any] | None = None,
        tool: str | None = None,
        index: TechniqueIndex | None = None,
        **kwargs: Any,
    ) -> Any:
        """Despacha por nome canonico para a funcao registrada."""

        return self.apply_with_warnings(
            canonical,
            *args,
            seed=seed,
            parameters=parameters,
            tool=tool,
            index=index,
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
        result = technique.apply(*args, context=context, **kwargs)
        if before_humanize is not None:
            after_mid = _result_midi(result) or before_humanize.midi
            after = _MidiContentSnapshot.from_midi(after_mid)
            _validate_humanize_contract(
                technique.canonical,
                before_humanize.snapshot,
                after,
            )
        if before_technique is not None:
            after_mid = _result_midi(result) or before_technique.midi
            _drop_reapplied_notes(before_technique.snapshot, after_mid)
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

    @classmethod
    def from_midi(cls, mid: mido.MidiFile) -> _MidiContentSnapshot:
        events: list[tuple[int, int, int]] = []
        pitches: list[int] = []
        for track_index, track in enumerate(mid.tracks):
            for msg in track:
                if (
                    not msg.is_meta
                    and msg.type == "note_on"
                    and msg.velocity > 0
                ):
                    events.append((track_index, msg.channel, msg.note))
                    pitches.append(msg.note)
        return cls(
            note_on_count=len(events),
            pitch_multiset=tuple(sorted(pitches)),
            note_on_sequence=tuple(events),
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


@dataclass(frozen=True)
class _StructuralSnapshot:
    notes: dict[_StructuralKey, _StructuralNote]

    @classmethod
    def from_midi(cls, mid: mido.MidiFile) -> _StructuralSnapshot:
        pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
        collected: list[tuple[int, int, int, int, int, int]] = []
        for track_index, track in enumerate(mid.tracks):
            tick = 0
            pending.clear()
            for msg in track:
                tick += msg.time
                if msg.is_meta:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    pending.setdefault((msg.channel, msg.note), []).append(
                        (tick, msg.velocity)
                    )
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0
                ):
                    stack = pending.get((msg.channel, msg.note))
                    if not stack:
                        continue
                    start_tick, velocity = stack.pop(0)
                    collected.append((
                        track_index,
                        msg.channel,
                        msg.note,
                        start_tick,
                        tick,
                        velocity,
                    ))

        seen: dict[tuple[int, int, int, int], int] = {}
        notes: dict[_StructuralKey, _StructuralNote] = {}
        for track_index, channel, pitch, start_tick, end_tick, velocity in collected:
            occurrence_key = (track_index, channel, pitch, start_tick)
            occurrence = seen.get(occurrence_key, 0)
            seen[occurrence_key] = occurrence + 1
            key = _StructuralKey(
                track_index=track_index,
                channel=channel,
                pitch=pitch,
                start_tick=start_tick,
                occurrence=occurrence,
            )
            notes[key] = _StructuralNote(
                key=key,
                velocity=velocity,
                end_tick=end_tick,
            )
        return cls(notes=notes)

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


def _humanize_snapshot(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> _HumanizeBefore | None:
    mid = _first_midi((*args, *kwargs.values()))
    if mid is None:
        return None
    return _HumanizeBefore(
        midi=mid,
        snapshot=_MidiContentSnapshot.from_midi(mid),
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


def _indexed_notes(
    track_index: int,
    track: mido.MidiTrack,
) -> tuple[_IndexedNote, ...]:
    pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
    collected: list[tuple[_NoteIdentity, int, int]] = []
    tick = 0
    for msg_index, msg in enumerate(track):
        tick += msg.time
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            pending.setdefault((msg.channel, msg.note), []).append((tick, msg_index))
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            stack = pending.get((msg.channel, msg.note))
            if not stack:
                continue
            start_tick, note_on_index = stack.pop(0)
            collected.append((
                _NoteIdentity(
                    track_index=track_index,
                    channel=msg.channel,
                    pitch=msg.note,
                    start_tick=start_tick,
                    end_tick=tick,
                ),
                note_on_index,
                msg_index,
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

    return _REGISTRY.register(
        canonical,
        level,
        allow_structural_velocity_change=allow_structural_velocity_change,
        allow_structural_duration_change=allow_structural_duration_change,
    )


def get_technique(canonical: str) -> RegisteredTechnique:
    """Devolve uma tecnica registrada no registro global."""

    return _REGISTRY.get(canonical)


def apply_technique(
    canonical: str,
    *args: Any,
    seed: int,
    parameters: Mapping[str, Any] | None = None,
    tool: str | None = None,
    index: TechniqueIndex | None = None,
    **kwargs: Any,
) -> Any:
    """Despacha `canonical` para sua funcao registrada."""

    return _REGISTRY.apply(
        canonical,
        *args,
        seed=seed,
        parameters=parameters,
        tool=tool,
        index=index,
        **kwargs,
    )


def apply_technique_with_warnings(
    canonical: str,
    *args: Any,
    seed: int,
    parameters: Mapping[str, Any] | None = None,
    tool: str | None = None,
    index: TechniqueIndex | None = None,
    **kwargs: Any,
) -> TechniqueApplyResult:
    """Despacha `canonical` e devolve resultado com warnings do motor."""

    return _REGISTRY.apply_with_warnings(
        canonical,
        *args,
        seed=seed,
        parameters=parameters,
        tool=tool,
        index=index,
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


def _identity_apply(
    subject: Any = None,
    *_args: Any,
    context: TechniqueContext,
    **_kwargs: Any,
) -> Any:
    """Aplicador neutro ate os contratos de mutacao entrarem nas proximas US."""

    _ = context
    return subject


# Populacao explicita inicial. As implementacoes reais chegam nas historias de
# contrato dos niveis; estes registros ja estabelecem o despacho por nome.
register_technique("drums.microtiming", "humanize")(_identity_apply)
register_technique("drums.ghost_notes", "technique")(_identity_apply)
register_technique("bass.ghost_notes", "technique")(_identity_apply)

SUPPORTED_TECHNIQUES = tuple(t.canonical for t in registered_techniques())


__all__ = [
    "RegisteredTechnique",
    "SUPPORTED_TECHNIQUES",
    "TechniqueApply",
    "TechniqueApplyResult",
    "TechniqueContractError",
    "TechniqueContext",
    "TechniqueLevel",
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
