"""Classificacao interna de notas estruturais e ornamentais.

O motor nao grava essa marcacao no MIDI. Metadados customizados nao sao
garantidos no round-trip por DAW/biblioteca, e as tracks do source precisam
continuar byte-identicas quando nao sao alvo de edicao. Em vez disso, a
classificacao e derivada: dado o MIDI de origem e as notas que o plano+seed
fazem os geradores e tecnicas criarem, o motor recompoe as assinaturas em
ticks e classifica o MIDI final sem depender de metadado persistido.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

import mido
import pretty_midi

NoteOrigin = Literal["source", "generator", "technique"]
NoteRole = Literal["structural", "ornamental"]


class MidiNoteLike(Protocol):
    """Forma minima das notas emitidas pelas paletas."""

    pitch: int
    velocity: int
    start_s: float
    end_s: float


class UnclassifiedNoteError(ValueError):
    """Nota encontrada no MIDI final sem origem derivavel."""


@dataclass(frozen=True, order=True)
class NoteSignature:
    """Identidade derivavel de uma nota dentro de uma track fisica."""

    track_index: int
    channel: int
    pitch: int
    start_tick: int
    end_tick: int
    occurrence: int = 0


@dataclass(frozen=True)
class ExpectedNote:
    """Nota que o motor espera existir apos recomputar plano+seed."""

    signature: NoteSignature
    origin: NoteOrigin
    velocity: int
    track_name: str = ""

    @property
    def role(self) -> NoteRole:
        """Contrato central: source/gerador sao estruturais; tecnica ornamenta."""

        if self.origin == "technique":
            return "ornamental"
        return "structural"


@dataclass(frozen=True)
class ClassifiedNote:
    """Nota observada no MIDI final com classificacao derivada."""

    signature: NoteSignature
    origin: NoteOrigin
    role: NoteRole
    velocity: int
    track_name: str = ""


@dataclass(frozen=True)
class _RawNote:
    track_index: int
    track_name: str
    channel: int
    pitch: int
    velocity: int
    start_tick: int
    end_tick: int
    occurrence: int = 0

    @property
    def signature(self) -> NoteSignature:
        return NoteSignature(
            track_index=self.track_index,
            channel=self.channel,
            pitch=self.pitch,
            start_tick=self.start_tick,
            end_tick=self.end_tick,
            occurrence=self.occurrence,
        )


def source_note_expectations(mid: mido.MidiFile) -> tuple[ExpectedNote, ...]:
    """Marca toda nota vinda do MIDI de origem como estrutural."""

    return tuple(
        ExpectedNote(
            signature=raw.signature,
            origin="source",
            velocity=raw.velocity,
            track_name=raw.track_name,
        )
        for raw in _collect_notes(mid)
    )


def generator_note_expectations(
    notes: Iterable[MidiNoteLike],
    pm: pretty_midi.PrettyMIDI,
    *,
    track_index: int,
    track_name: str,
    channel: int,
) -> tuple[ExpectedNote, ...]:
    """Converte notas de paleta em expectativas estruturais.

    As notas de paleta sao conteudo criado pelo gerador; por contrato elas
    entram no mesmo grupo protegido das notas do MIDI de origem.
    """

    return _note_like_expectations(
        notes,
        pm,
        track_index=track_index,
        track_name=track_name,
        channel=channel,
        origin="generator",
    )


def technique_note_expectations(
    notes: Iterable[MidiNoteLike],
    pm: pretty_midi.PrettyMIDI,
    *,
    track_index: int,
    track_name: str,
    channel: int,
) -> tuple[ExpectedNote, ...]:
    """Converte notas acrescentadas por tecnica em expectativas ornamentais."""

    return _note_like_expectations(
        notes,
        pm,
        track_index=track_index,
        track_name=track_name,
        channel=channel,
        origin="technique",
    )


def derive_note_classification(
    final_mid: mido.MidiFile,
    *,
    source_mid: mido.MidiFile,
    generated_notes: Iterable[ExpectedNote] = (),
    technique_notes: Iterable[ExpectedNote] = (),
) -> tuple[ClassifiedNote, ...]:
    """Classifica as notas observadas no MIDI final por derivacao.

    `generated_notes` e `technique_notes` devem ser recomputadas pelo chamador
    a partir do plano, seed e MIDI de origem. A funcao entao casa as
    assinaturas resultantes contra o MIDI final, inclusive depois de salvar e
    recarregar o arquivo.
    """

    expected = _expectation_map((
        *source_note_expectations(source_mid),
        *tuple(generated_notes),
        *tuple(technique_notes),
    ))

    classified: list[ClassifiedNote] = []
    missing: list[NoteSignature] = []
    for raw in _collect_notes(final_mid):
        exp = expected.get(raw.signature)
        if exp is None:
            missing.append(raw.signature)
            continue
        classified.append(ClassifiedNote(
            signature=raw.signature,
            origin=exp.origin,
            role=exp.role,
            velocity=raw.velocity,
            track_name=raw.track_name or exp.track_name,
        ))

    if missing:
        preview = ", ".join(str(sig) for sig in missing[:3])
        suffix = "" if len(missing) <= 3 else f" (+{len(missing) - 3})"
        raise UnclassifiedNoteError(
            f"notas sem classificacao derivavel: {preview}{suffix}"
        )
    return tuple(classified)


def _note_like_expectations(
    notes: Iterable[MidiNoteLike],
    pm: pretty_midi.PrettyMIDI,
    *,
    track_index: int,
    track_name: str,
    channel: int,
    origin: NoteOrigin,
) -> tuple[ExpectedNote, ...]:
    raw_notes = [
        _raw_note_from_note_like(
            note,
            pm,
            track_index=track_index,
            track_name=track_name,
            channel=channel,
        )
        for note in notes
    ]
    return tuple(
        ExpectedNote(
            signature=raw.signature,
            origin=origin,
            velocity=raw.velocity,
            track_name=raw.track_name,
        )
        for raw in _with_occurrences(raw_notes)
    )


def _expectation_map(notes: Iterable[ExpectedNote]) -> dict[NoteSignature, ExpectedNote]:
    out: dict[NoteSignature, ExpectedNote] = {}
    duplicates: list[NoteSignature] = []
    for note in _with_expected_occurrences(notes):
        if note.signature in out:
            duplicates.append(note.signature)
        out[note.signature] = note
    if duplicates:
        preview = ", ".join(str(sig) for sig in duplicates[:3])
        suffix = "" if len(duplicates) <= 3 else f" (+{len(duplicates) - 3})"
        raise ValueError(f"assinaturas de nota duplicadas: {preview}{suffix}")
    return out


def _with_expected_occurrences(
    notes: Iterable[ExpectedNote],
) -> tuple[ExpectedNote, ...]:
    seen: dict[tuple[int, int, int, int, int], int] = defaultdict(int)
    out: list[ExpectedNote] = []
    for note in notes:
        sig = note.signature
        key = (
            sig.track_index,
            sig.channel,
            sig.pitch,
            sig.start_tick,
            sig.end_tick,
        )
        occurrence = seen[key]
        seen[key] += 1
        out.append(ExpectedNote(
            signature=NoteSignature(
                track_index=sig.track_index,
                channel=sig.channel,
                pitch=sig.pitch,
                start_tick=sig.start_tick,
                end_tick=sig.end_tick,
                occurrence=occurrence,
            ),
            origin=note.origin,
            velocity=note.velocity,
            track_name=note.track_name,
        ))
    return tuple(out)


def _raw_note_from_note_like(
    note: MidiNoteLike,
    pm: pretty_midi.PrettyMIDI,
    *,
    track_index: int,
    track_name: str,
    channel: int,
) -> _RawNote:
    start_tick = int(round(pm.time_to_tick(float(note.start_s))))
    end_tick = int(round(pm.time_to_tick(float(note.end_s))))
    if end_tick <= start_tick:
        end_tick = start_tick + 1
    return _RawNote(
        track_index=track_index,
        track_name=track_name,
        channel=channel,
        pitch=int(note.pitch),
        velocity=int(note.velocity),
        start_tick=start_tick,
        end_tick=end_tick,
    )


def _collect_notes(mid: mido.MidiFile) -> tuple[_RawNote, ...]:
    raw_notes: list[_RawNote] = []
    for track_index, track in enumerate(mid.tracks):
        track_name = _track_name(track)
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes[(msg.channel, msg.note)].append((tick, msg.velocity))
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = open_notes.get((msg.channel, msg.note))
                if not stack:
                    continue
                start_tick, velocity = stack.pop(0)
                raw_notes.append(_RawNote(
                    track_index=track_index,
                    track_name=track_name,
                    channel=msg.channel,
                    pitch=msg.note,
                    velocity=velocity,
                    start_tick=start_tick,
                    end_tick=tick,
                ))
    return tuple(_with_occurrences(raw_notes))


def _with_occurrences(raw_notes: Iterable[_RawNote]) -> tuple[_RawNote, ...]:
    seen: dict[tuple[int, int, int, int, int], int] = defaultdict(int)
    out: list[_RawNote] = []
    for raw in raw_notes:
        key = (
            raw.track_index,
            raw.channel,
            raw.pitch,
            raw.start_tick,
            raw.end_tick,
        )
        occurrence = seen[key]
        seen[key] += 1
        out.append(_RawNote(
            track_index=raw.track_index,
            track_name=raw.track_name,
            channel=raw.channel,
            pitch=raw.pitch,
            velocity=raw.velocity,
            start_tick=raw.start_tick,
            end_tick=raw.end_tick,
            occurrence=occurrence,
        ))
    return tuple(out)


def _track_name(track: mido.MidiTrack) -> str:
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            return str(msg.name)
    return ""


__all__ = [
    "ClassifiedNote",
    "ExpectedNote",
    "MidiNoteLike",
    "NoteOrigin",
    "NoteRole",
    "NoteSignature",
    "UnclassifiedNoteError",
    "derive_note_classification",
    "generator_note_expectations",
    "source_note_expectations",
    "technique_note_expectations",
]
