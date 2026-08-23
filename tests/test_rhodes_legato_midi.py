"""Legato do rhodes medido na SAIDA MIDI FINAL (US-006).

O legato do rhodes solicitava overlap entre notas de mesma altura, mas MIDI
no mesmo canal nao expressa isso: `note_on(P), note_on(P), note_off(P)` faz
o primeiro note_off encerrar a segunda nota, truncando o segundo ataque
para a janela de 5-25ms em vez de alongar a primeira. O fix da US-006
substitui overlap por TOUCHING (end_s da primeira == start_s da segunda),
que a ordem de eventos em `_notes_to_track` (note_off antes de note_on no
mesmo tick) preserva limpo.

Este arquivo mede o contrato no arquivo MIDI escrito por `render`, nao na
estrutura intermediaria de `KeyboardNote`. Se algum gerador reintroduzir
overlap same-pitch/same-channel, ou se a ratio 12-47% do rhodes escapar
apos a serializacao, os testes aqui capturam.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import mido
import pretty_midi
import pytest

from tools.palette.harmonic import RHODES_LEGATO_RATIO_RANGE
from tools.plan import ArrangementPlan, Element, PlanSection, SourceMidi
from tools.render import render

# --- fixtures ---------------------------------------------------------------

def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _minimal_source(tmp_path: Path) -> Path:
    """Source estavel — 8 bars de C/E/G no piano + bass em semininas.

    Guitarra fica de fora: os papeis medidos aqui (rhodes, piano, pad,
    strings) atacam no downbeat, e guitarra no downbeat dispararia
    `duplicates_source` sem relacao com o contrato de legato."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.extend([piano, bass])
    dest = tmp_path / "source.mid"
    pm.write(str(dest))
    return dest


_INSTRUMENT = {"plugin": "Omnisphere", "preset": "Palette Patch", "verified": True}


def _build_rhodes_plan(source: Path, *, seed: int) -> ArrangementPlan:
    element = Element(
        id="rhodes_main",
        role="rhodes",
        sections=["MAIN"],
        register=[60, 84],
        layers=1,
        sync_role="sustain_through",
        articulation="open",
        harmony="follow_chords",
        pattern=None,
        dynamics={"shape": "hold"},
        instrument=dict(_INSTRUMENT),
        rationale="MIDI-level legato contract coverage.",
    )
    return ArrangementPlan(
        version=1,
        seed=seed,
        source_midi=SourceMidi(path=str(source), sha256=_sha256_bytes(source)),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="MAIN", kind="chorus", start_bar=0, end_bar=8,
                source="marker", protagonist="texture",
                energy={
                    "densidade": 5, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3,
                },
            ),
        ],
        elements=[element],
    )


def _build_piano_plan(source: Path) -> ArrangementPlan:
    plan = _build_rhodes_plan(source, seed=17)
    piano = plan.elements[0]
    plan.elements[0] = Element(
        id="piano_main", role="piano", sections=piano.sections,
        register=piano.register, layers=piano.layers,
        sync_role=piano.sync_role, articulation="tight",
        harmony=piano.harmony, pattern=piano.pattern,
        dynamics=piano.dynamics, instrument=piano.instrument,
        rationale="MIDI-level legato contract coverage.",
    )
    return plan


# --- helpers de leitura MIDI -----------------------------------------------

def _iter_generated_tracks(mid: mido.MidiFile, element_id: str):
    """Devolve tracks cujo `track_name` comeca por `element_id`.

    O `name_for_element` prefixa o nome do elemento no track_name; source
    tracks preservam o nome original ('Piano', 'Bass', ...). Isso evita
    medir contrato de legato em tracks copiadas do source."""
    for tr in mid.tracks:
        name = ""
        for msg in tr:
            if msg.type == "track_name":
                name = msg.name
                break
        if name.startswith(element_id):
            yield tr


def _track_note_events(track: mido.MidiTrack):
    """Yield `(pitch, channel, event, tick)` para cada note_on/note_off da
    track, onde `event` in {'on', 'off'}. Converte deltas para ticks
    absolutos. `note_on vel=0` conta como off (MIDI running status)."""
    tick = 0
    for msg in track:
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            yield (msg.note, msg.channel, "on", tick)
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            yield (msg.note, msg.channel, "off", tick)


def _paired_notes(track: mido.MidiTrack) -> list[tuple[int, int, int, int]]:
    """Pareia note_on/note_off da track. Devolve `(pitch, channel, start_tick,
    end_tick)`. Pareamento FIFO por (channel, pitch), como qualquer synth
    faz — nao ha nesting em MIDI 1.0. Notas sem par (dangling) sao
    ignoradas."""
    open_notes: dict[tuple[int, int], list[int]] = {}
    out: list[tuple[int, int, int, int]] = []
    for pitch, channel, evt, tick in _track_note_events(track):
        key = (channel, pitch)
        if evt == "on":
            open_notes.setdefault(key, []).append(tick)
        else:
            stack = open_notes.get(key)
            if stack:
                start = stack.pop(0)
                out.append((pitch, channel, start, tick))
    return out


# --- testes -----------------------------------------------------------------

@pytest.mark.parametrize(
    "plan_builder,element_id",
    [
        (lambda src: _build_rhodes_plan(src, seed=0), "rhodes_main"),
        (_build_piano_plan, "piano_main"),
    ],
    ids=["rhodes", "piano"],
)
def test_no_note_on_while_pitch_sounding_on_same_channel(
    tmp_path, plan_builder, element_id,
):
    """AC: nenhuma track gerada emite note_on de altura ja soando no mesmo
    canal.

    Rhodes e o alvo primario (legato same-pitch), mas piano herda o
    contrato porque compartilha `_enforce_same_pitch_contract`. A ordem
    de eventos em `_notes_to_track` (note_off antes de note_on no mesmo
    tick) mantem touching valido — este teste falha se o gerador voltar
    a produzir overlap same-pitch."""
    src = _minimal_source(tmp_path)
    plan = plan_builder(src)
    out = tmp_path / f"{element_id}_out.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    tracks = list(_iter_generated_tracks(mid, element_id))
    assert tracks, f"no generated track for {element_id!r}"

    for tr in tracks:
        sounding: set[tuple[int, int]] = set()
        offending: list[tuple[int, int, int]] = []
        for pitch, channel, evt, tick in _track_note_events(tr):
            key = (channel, pitch)
            if evt == "on":
                if key in sounding:
                    offending.append((tick, channel, pitch))
                sounding.add(key)
            else:
                sounding.discard(key)
        assert not offending, (
            f"note_on while pitch already sounding in {element_id!r}: "
            f"{offending[:5]!r}"
        )


def test_rhodes_legato_ratio_on_midi_output_stays_in_range(tmp_path_factory):
    """AC: 12 a 47% dos pares de mesma altura com overlap ou touching, medido
    na SAIDA MIDI FINAL.

    Roda varios seeds para confirmar que a trava do `_legato_pair_count`
    sobrevive a serializacao — nada de arredondamento em ticks poe a
    ratio fora do intervalo."""
    for seed in range(10):
        tmp = tmp_path_factory.mktemp(f"rhodes_legato_seed{seed}")
        src = _minimal_source(tmp)
        plan = _build_rhodes_plan(src, seed=seed)
        out = tmp / "out.mid"
        render(plan, out)

        mid = mido.MidiFile(str(out))
        tracks = list(_iter_generated_tracks(mid, "rhodes_main"))
        assert tracks, "no rhodes track in rendered MIDI"

        pairs_total = 0
        pairs_legato = 0
        for tr in tracks:
            notes = _paired_notes(tr)
            by_pitch: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for pitch, channel, start_tick, end_tick in notes:
                by_pitch.setdefault(
                    (channel, pitch), [],
                ).append((start_tick, end_tick))
            for lst in by_pitch.values():
                lst.sort()
                for (_, a_end), (b_start, _) in zip(
                    lst, lst[1:], strict=False,
                ):
                    pairs_total += 1
                    if a_end >= b_start:
                        pairs_legato += 1

        assert pairs_total > 0, f"no same-pitch pairs in rhodes MIDI (seed={seed})"
        ratio = pairs_legato / pairs_total
        lo, hi = RHODES_LEGATO_RATIO_RANGE
        assert lo - 1e-9 <= ratio <= hi + 1e-9, (
            f"seed={seed} ratio={ratio:.3f} outside [{lo}, {hi}] "
            f"({pairs_legato}/{pairs_total})"
        )
