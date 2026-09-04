"""Humanizacao opt-in de tracks existentes (FR-28, US-011).

Motor que aplica os engines de `humanize.py` (velocity/microtiming/duracao)
apenas em tracks NOMEADAS em `plan.edits`. Track fora dessa lista fica
byte-identica: `_clone_source_tracks` copiou verbatim e este modulo nao
toca nela.

O que muda por nota:

- **timing**: offset gaussiano em ms com bias/sigma do profile; ancoras
  estruturais (kick/snare de bateria em downbeat) sao clampadas para o
  bucket `anchor` (+-3ms, sem bias).
- **velocity**: opcional `phrase_shape` (arco senoidal por track) + jitter
  uniforme, ambos escalados por `intensity`.
- **duracao**: opcional, controlada por `gate_articulation` do profile
  (`GATE_RATIOS`). Blendada com a duracao original em razao a intensity
  para preservar articulacao humana ja gravada quando intensity < 1.

Pitches e ordem de notas NUNCA mudam. `intensity == 0.0` e no-op absoluto
por design (return imediato — nao consome RNG, saida identica ao source).

Determinismo: seed derivada de `sha256(plan.seed | track | profile)` — a
mesma edit em cima do mesmo source produz sempre a mesma saida.

Fontes das constantes dos profiles estao citadas linha a linha em
PROFILE_PARAMS.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

import mido
import pretty_midi

from .style_profile import StyleProfile

# --- vocabulario e parametros por profile -----------------------------------

@dataclass(frozen=True)
class ProfileParams:
    """Parametros do profile aplicados por nota.

    Cada campo tem sua fonte citada em PROFILE_PARAMS abaixo (secao 6/7 do
    spec ou `knoledgebase/base_conhecimento_midi_realista_modo_bass.md`).
    """
    bias_ms: float
    sigma_ms: float
    velocity_amplitude: int
    velocity_phrase_shape: bool
    gate_articulation: str | None
    anchor_pitches: frozenset[int]
    anchor_max_abs_ms: float


PROFILE_PARAMS: dict[str, ProfileParams] = {
    "bass": ProfileParams(
        # Bias -3 a -4ms: secao 7 do spec, tabela empirica ANCORA
        # ("Vies de baixo e hat: -3 a -4 ms (adiantado)").
        bias_ms=-3.5,
        # sigma no teto do bucket 'normal' de TIMING_JITTER_MS: (3, 8) —
        # baixo respira dentro da janela normal.
        sigma_ms=8.0,
        # Amplitude 6 = `phrase_shape` do spec (secao 6), pico +6 no meio
        # da frase. Baixo carrega arco de frase.
        velocity_amplitude=6,
        velocity_phrase_shape=True,
        # `articulacao por gap`: doc modo_bass secao 12 e AC do PRD. `tight`
        # e o gate default de baixo em regime normal (0.60-0.82 do gap).
        gate_articulation="tight",
        anchor_pitches=frozenset(),
        anchor_max_abs_ms=3.0,
    ),
    "drums": ProfileParams(
        # Bateria de estudio ancora no beat: bias neutro; a variacao
        # decisiva sai do bucket 'normal' + regra de ancora (kick/snare em
        # downbeat) — secao 7 do spec: bucket 'anchor' = (0, 3).
        bias_ms=0.0,
        sigma_ms=8.0,
        velocity_amplitude=4,
        velocity_phrase_shape=False,
        gate_articulation=None,
        # GM percussion: 35 acoustic bass drum, 36 bass drum 1,
        # 38 acoustic snare, 40 electric snare. Ancoras estruturais.
        anchor_pitches=frozenset({35, 36, 38, 40}),
        anchor_max_abs_ms=3.0,
    ),
    "keys": ProfileParams(
        # Neutro: TIMING_JITTER_MS['normal'] com bias 0, jitter de velocity
        # discreto (piano/rhodes ja carregam sua propria dinamica).
        bias_ms=0.0,
        sigma_ms=6.0,
        velocity_amplitude=3,
        velocity_phrase_shape=False,
        gate_articulation=None,
        anchor_pitches=frozenset(),
        anchor_max_abs_ms=3.0,
    ),
    "guitar": ProfileParams(
        # Sem bias direcional documentado (issue #19): ao contrario do
        # baixo/hat, nao ha numero medido de adiantamento/atraso para
        # guitarra ritmica no manual (`tecnicas_guitarra_midi.md`,
        # secao Lacunas). Neutro por honestidade, nao "seguro por default".
        bias_ms=0.0,
        # CONVENCAO: sigma no piso do bucket 'normal' de TIMING_JITTER_MS
        # (3, 8) — issue #19 pede timing de guitarra ritmica moderna
        # "apertado", e guitarra editada/quantizada em metal moderno e mais
        # firme que baixo tocado a mao; sem medicao publicada, o motor
        # escolhe o extremo apertado da faixa ja existente em vez de
        # inventar um numero novo fora dela.
        sigma_ms=3.0,
        # CONVENCAO: amplitude entre a de bateria (4) e a de baixo (6) —
        # riff de guitarra tem dinamica de picking, mas sem arco de frase
        # tao marcado quanto o baixo.
        velocity_amplitude=5,
        velocity_phrase_shape=False,
        # 'tight': riff palm-muted e nota curta por natureza
        # (`guitar.palm_mute`, `guitar.dead_notes`) mesmo antes de qualquer
        # tecnica ser autorizada.
        gate_articulation="tight",
        anchor_pitches=frozenset(),
        anchor_max_abs_ms=3.0,
    ),
    "generic": ProfileParams(
        # Fallback: mesma pegada de 'keys' — sem opinioes sobre pecas
        # especificas, apenas humaniza dentro do bucket 'normal'.
        bias_ms=0.0,
        sigma_ms=6.0,
        velocity_amplitude=3,
        velocity_phrase_shape=False,
        gate_articulation=None,
        anchor_pitches=frozenset(),
        anchor_max_abs_ms=3.0,
    ),
}


# --- relatorio -------------------------------------------------------------

@dataclass(frozen=True)
class EditReport:
    """Resumo de uma edit aplicada — entra em `RenderReport.edits`."""
    track: str
    profile: str
    intensity: float
    notes_touched: int
    mean_offset_ms: float
    tracks_matched: int = 1


# --- helpers ---------------------------------------------------------------

def _edit_seed(plan_seed: int, track: str, profile: str) -> int:
    """Seed determinstica por (plano, track, profile)."""
    payload = f"{plan_seed}|{track}|{profile}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def track_name(track: mido.MidiTrack) -> str | None:
    """Devolve o valor da meta `track_name` da track, ou None."""
    for msg in track:
        if msg.is_meta and msg.type == "track_name":
            return msg.name
    return None


def collect_track_names(tracks: list[mido.MidiTrack]) -> list[str]:
    """Nomes das tracks (posicional). Track sem nome vira string vazia."""
    return [track_name(t) or "" for t in tracks]


def _downbeat_ticks(pm: pretty_midi.PrettyMIDI) -> set[int]:
    """Set com o tick de cada downbeat estrutural. Tolera janela de +/-1
    tick para lidar com arredondamento de time_to_tick em MIDI muito
    denso — na pratica todos os downbeats sinteticos caem em tick exato."""
    downbeats = pm.get_downbeats()
    if len(downbeats) == 0:
        return set()
    return {int(round(pm.time_to_tick(float(t)))) for t in downbeats}


# --- aplicacao -------------------------------------------------------------

def apply_edit(
    track: mido.MidiTrack,
    profile: ProfileParams,
    intensity: float,
    seed: int,
    pm: pretty_midi.PrettyMIDI,
    *,
    style_profile: StyleProfile | None = None,
) -> tuple[int, float]:
    """Aplica humanizacao in-place na `track`. Devolve `(notes_touched,
    mean_offset_ms)`.

    `intensity == 0.0` retorna imediatamente sem consumir RNG e sem tocar
    na track. Isso mantem a saida byte-identica quando o usuario declara
    edit mas quer testar o efeito neutro."""
    if intensity <= 0.0:
        return (0, 0.0)

    resolved_style = style_profile or StyleProfile.default()
    gate_ratios = resolved_style.gate_ratios
    rng = random.Random(seed)

    # 1) absolute-tick view de todos os eventos, preservando a ordem
    #    original como desempate estavel.
    events: list[list] = []
    abs_tick = 0
    for i, msg in enumerate(track):
        abs_tick += msg.time
        events.append([abs_tick, i, msg])

    # 2) parear note_on (vel>0) com o note_off correspondente por pilha de
    #    (channel, pitch). `note_on vel=0` conta como off (MIDI running).
    #    Sobreposicao de mesma nota e canal e valida: o off fecha o on mais
    #    recente ainda aberto, sem sobrescrever onsets anteriores.
    open_notes: dict[tuple[int, int], list[int]] = {}
    pairs: list[tuple[int, int]] = []
    for idx, (_, _, msg) in enumerate(events):
        if msg.is_meta:
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            open_notes.setdefault((msg.channel, msg.note), []).append(idx)
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            key = (msg.channel, msg.note)
            if key in open_notes:
                on_idx = open_notes[key].pop()
                pairs.append((on_idx, idx))
                if not open_notes[key]:
                    del open_notes[key]

    if not pairs:
        return (0, 0.0)

    pairs.sort(key=lambda pair: events[pair[0]][1])
    downbeats = _downbeat_ticks(pm)
    n_pairs = len(pairs)
    # Gap de gate: distancia ate o proximo onset em qualquer canal da track.
    # Ordenado uma vez para busca linear tolerar tempos com tempo change.
    on_ticks_sorted = sorted({events[on_idx][0] for on_idx, _ in pairs})

    offsets_ms: list[float] = []
    previous_new_on_tick = 0
    for pair_idx, (on_idx, off_idx) in enumerate(pairs):
        on_tick = events[on_idx][0]
        off_tick = events[off_idx][0]
        on_msg = events[on_idx][2]
        pitch = on_msg.note
        original_velocity = on_msg.velocity

        # microtiming ---------------------------------------------------
        is_anchor = (
            pitch in profile.anchor_pitches and _tick_near(on_tick, downbeats)
        )
        if is_anchor:
            offset_ms = rng.uniform(
                -profile.anchor_max_abs_ms, profile.anchor_max_abs_ms,
            )
        else:
            offset_ms = rng.gauss(profile.bias_ms, profile.sigma_ms)
        offset_ms *= intensity

        # Converte offset em ticks via pretty_midi para respeitar mapa de
        # tempos — nao assume tempo constante. `max(0.0, ...)` no tempo em
        # segundos garante que o tick resultante nunca fica negativo.
        on_time_s = pm.tick_to_time(on_tick)
        new_time_s = max(0.0, on_time_s + offset_ms / 1000.0)
        new_on_tick = int(round(pm.time_to_tick(new_time_s)))
        new_on_tick = max(previous_new_on_tick, new_on_tick)
        previous_new_on_tick = new_on_tick
        actual_offset_ms = (pm.tick_to_time(new_on_tick) - on_time_s) * 1000.0
        offsets_ms.append(actual_offset_ms)

        # velocity ------------------------------------------------------
        dv = 0.0
        if profile.velocity_phrase_shape and n_pairs > 1:
            position = pair_idx / (n_pairs - 1)
            dv += -4.0 + 10.0 * math.sin(math.pi * position)
        if profile.velocity_amplitude > 0:
            dv += rng.uniform(
                -float(profile.velocity_amplitude),
                float(profile.velocity_amplitude),
            )
        dv *= intensity
        new_velocity = max(1, min(127, int(round(original_velocity + dv))))

        # duracao -------------------------------------------------------
        original_duration_ticks = off_tick - on_tick
        if profile.gate_articulation is not None:
            next_on_tick = None
            for t in on_ticks_sorted:
                if t > on_tick:
                    next_on_tick = t
                    break
            if next_on_tick is not None and next_on_tick > on_tick:
                gap_ticks = next_on_tick - on_tick
                lo, hi = gate_ratios[profile.gate_articulation]
                target_ratio = rng.uniform(lo, hi)
                original_ratio = original_duration_ticks / gap_ticks
                blended_ratio = (
                    original_ratio * (1.0 - intensity)
                    + target_ratio * intensity
                )
                new_duration_ticks = max(
                    1, int(round(gap_ticks * blended_ratio)),
                )
            else:
                new_duration_ticks = original_duration_ticks
        else:
            new_duration_ticks = original_duration_ticks

        new_off_tick = new_on_tick + new_duration_ticks
        if new_off_tick <= new_on_tick:
            new_off_tick = new_on_tick + 1

        # commit --------------------------------------------------------
        events[on_idx][0] = new_on_tick
        events[off_idx][0] = new_off_tick
        events[on_idx][2] = on_msg.copy(velocity=new_velocity)

    # 3) reordena por (tick, original_index) para preservar ordem estavel
    #    quando dois eventos caem no mesmo tick (ex.: meta + note_on).
    events.sort(key=lambda e: (e[0], e[1]))

    # 4) reescreve a track com novos deltas. `events` esta ordenado por
    #    tick crescente, entao `delta >= 0` sempre — dispensa guarda extra.
    track.clear()
    prev_tick = 0
    for abs_tick, _, msg in events:
        delta = abs_tick - prev_tick
        track.append(msg.copy(time=delta))
        prev_tick = abs_tick

    mean_offset = sum(offsets_ms) / len(offsets_ms) if offsets_ms else 0.0
    return (len(pairs), mean_offset)


def _tick_near(tick: int, downbeats: set[int], tol: int = 1) -> bool:
    """Downbeat com tolerancia de +/-`tol` ticks — pretty_midi arredonda em
    tempo change e o tick calculado pode variar 1 unidade do exato."""
    if tick in downbeats:
        return True
    for delta in range(1, tol + 1):
        if (tick - delta) in downbeats or (tick + delta) in downbeats:
            return True
    return False


def apply_edits(
    tracks: list[mido.MidiTrack],
    edits: list,
    plan_seed: int,
    pm: pretty_midi.PrettyMIDI,
    *,
    style_profile: StyleProfile | None = None,
) -> list[EditReport]:
    """Aplica cada edit nas tracks correspondentes (busca pelo nome exato).

    Se o MIDI tem varias tracks com o mesmo `track_name`, uma edit por nome
    atinge todas elas. `tracks` e modificada in-place. Track cujo nome nao
    aparece em `edits` sai intocada. Devolve a lista de `EditReport`, uma por
    edit declarada, agregando todas as tracks homonimas atingidas.

    Precondicao: `plan.py` ja validou que profile+intensity estao no
    vocabulario/range, e o renderer ja rodou `validate_edits_against_midi`
    para garantir que cada track existe no source. Aqui apenas mapeia e
    aplica.
    """
    reports: list[EditReport] = []
    name_to_indices: dict[str, list[int]] = {}
    for idx, tr in enumerate(tracks):
        name = track_name(tr)
        if name:
            name_to_indices.setdefault(name, []).append(idx)

    for ed in edits:
        profile = PROFILE_PARAMS[ed.profile]
        indices = name_to_indices[ed.track]
        total_touched = 0
        weighted_offset_sum = 0.0
        for match_ordinal, idx in enumerate(indices):
            seed_track = ed.track if match_ordinal == 0 else f"{ed.track}#{idx}"
            seed = _edit_seed(plan_seed, seed_track, ed.profile)
            touched, mean_offset = apply_edit(
                tracks[idx], profile, float(ed.intensity), seed, pm,
                style_profile=style_profile,
            )
            total_touched += touched
            weighted_offset_sum += mean_offset * touched
        mean_offset = (
            weighted_offset_sum / total_touched
            if total_touched
            else 0.0
        )
        reports.append(EditReport(
            track=ed.track,
            profile=ed.profile,
            intensity=float(ed.intensity),
            notes_touched=total_touched,
            mean_offset_ms=mean_offset,
            tracks_matched=len(indices),
        ))
    return reports


__all__ = [
    "EditReport",
    "PROFILE_PARAMS",
    "ProfileParams",
    "apply_edit",
    "apply_edits",
    "collect_track_names",
    "track_name",
]
