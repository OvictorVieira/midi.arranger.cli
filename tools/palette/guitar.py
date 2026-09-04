"""Elemento de guitarra gerado do zero (issue #19).

Diferente de `plan.edits`, que so humaniza uma track de guitarra ja
existente no MIDI de origem, este gerador produz uma linha nova: riff
ritmico ancorado no kick da bateria (`analysis.kick_positions`), no
idioma de power chord do manual (`knowledge/tecnicas/tecnicas_guitarra_midi.md`,
`guitar.power_chord`/`guitar.chord_voicing`) — raiz + quinta (+ oitava
quando o voicing cabe), NUNCA a terca empilhada no grave (o manual
documenta lama harmonica em alto ganho para isso).

Vozeamento fisicamente tocavel: cada voicing candidato passa por
`tools.techniques.physical.guitar_voicing_is_playable` (uma altura por
corda, casas pisadas dentro da janela de 6 casas do artigo academico
citado no manual) ANTES de ser aceito. Quando o voicing mais rico nao
cabe na mao, o gerador degrada para uma opcao mais simples (power chord
sem oitava, depois so a raiz) em vez de escrever um voicing impossivel —
"vozeamento de teclado num patch de guitarra" e exatamente o defeito que
esta funcao existe para nunca produzir.

Monofonico OU homofonico por construcao: todas as notas de um mesmo
"golpe" compartilham onset e duracao (e sao, portanto, fisicamente um
strum), e strums consecutivos nunca se sobrepoem — a mesma garantia que
`tools.palette.bass.generate_bass` da para o baixo.
"""

from __future__ import annotations

import random

from ..analyze import Analysis, BarAnalysis, Chord
from ..constants import TIMING_JITTER_MS, VELOCITY_RANGES
from ..humanize import DURATION_ARTICULATIONS, DurationEngine, DurationRequest
from ..plan import PlanSection
from ..rng import assert_traceable_seed
from ..style_profile import StyleProfile
from ..techniques.physical import _GUITAR_TUNINGS, guitar_voicing_is_playable
from .harmonic import _bars_in_section, _chord_degrees
from .rhythmic import (
    STEPS_PER_BAR,
    RhythmicLayer,
    RhythmicNote,
    _clamp_pitch_to_register,
    _empty_rhythmic_layers,
)

# --- vocabulario e constantes ------------------------------------------------

GUITAR_ROLES: tuple[str, ...] = ("guitar",)
"""Role atendido por `generate_guitar`. Vocabulario fechado."""

DEFAULT_GUITAR_TUNING: tuple[int, ...] = _GUITAR_TUNINGS["e_padrao"]
"""E padrao (E2-A2-D3-G3-B3-E4), a mesma tabela que
`tools/techniques/physical.py` ja usa para a plausibilidade fisica."""

DEFAULT_GUITAR_REGISTER: tuple[int, int] = (40, 76)
"""CONVENCAO: piso na corda mais grave da afinacao padrao (E2=40) ate uma
oitava e meia acima — cobre o registro tipico de riff ritmico sem invadir
o registro de solo. Configuravel por elemento; `register` do elemento
tambem funciona como piso fisico (nunca abaixo da afinacao declarada)."""

GUITAR_MAX_FRET: int = 24
"""Mesmo default fisico usado por `bass.string_selection`/`guitar.hammer_pull`."""

GUITAR_BASE_VELOCITY_BUCKET: str = "normal"
GUITAR_ANCHOR_VELOCITY_BUCKET: str = "accent"
"""Golpe alinhado a kick (ancora ritmica) recebe o bucket mais forte —
mesmo padrao de `tools.palette.bass`: guitarra ritmica e bateria tocam
juntas nos golpes de peso."""

GUITAR_ARTICULATION_DEFAULT: str = "tight"
"""Default de articulacao — gate curto, coerente com riff palm-muted
(`guitar.palm_mute`) mesmo quando a tecnica de palm mute nao esta
autorizada: o riff de metal moderno e curto por escrita, nao so por
tecnica de execucao aplicada depois."""

GUITAR_DENSITY_LOW: int = 3
GUITAR_DENSITY_HIGH: int = 7
"""Mesma particao de `tools.palette.bass`/`tools.palette.drums` para o
eixo `densidade` — as tres familias leem a mesma escala."""

GUITAR_FALLBACK_STEPS_LOW: tuple[int, ...] = (0,)
GUITAR_FALLBACK_STEPS_MID: tuple[int, ...] = (0, 8)
GUITAR_FALLBACK_STEPS_HIGH: tuple[int, ...] = (0, 4, 8, 12)
"""Grade de fallback (sem kick na secao), em STEPS_PER_BAR=16 — CONVENCAO,
mesmo espirito de `tools.palette.bass`."""

MIN_NOTE_DURATION_S: float = 0.001


def _energy_axis(section: PlanSection, axis: str, default: int = 5) -> int:
    energy = section.energy or {}
    value = energy.get(axis, default)
    if not isinstance(value, int):
        return default
    return max(1, min(10, value))


def _density_bucket(densidade: int) -> str:
    if densidade <= GUITAR_DENSITY_LOW:
        return "low"
    if densidade >= GUITAR_DENSITY_HIGH:
        return "high"
    return "mid"


def _fallback_steps(bucket: str) -> tuple[int, ...]:
    return {
        "low": GUITAR_FALLBACK_STEPS_LOW,
        "mid": GUITAR_FALLBACK_STEPS_MID,
        "high": GUITAR_FALLBACK_STEPS_HIGH,
    }[bucket]


def _kick_onsets_in_bar(analysis: Analysis, bar: BarAnalysis) -> list[float]:
    return sorted(
        t for t in analysis.kick_positions if bar.start <= t < bar.end
    )


def _subsample_by_density(onsets: list[float], densidade: int) -> list[float]:
    if not onsets:
        return []
    frac = densidade / 10.0
    wanted = max(1, round(len(onsets) * frac))
    wanted = min(wanted, len(onsets))
    if wanted >= len(onsets):
        return onsets
    step = len(onsets) / wanted
    indices = sorted({int(i * step) for i in range(wanted)})
    return [onsets[i] for i in indices]


def _power_chord_pitches(chord: Chord, register: tuple[int, int]) -> tuple[int, int, int]:
    """(raiz, quinta, oitava) do acorde vigente, cada um clampado a
    `register` na propria oitava mais proxima do piso. Nunca inclui a
    terca — o manual e explicito que terca empilhada no grave produz lama
    harmonica em patch de alto ganho (`guitar.power_chord`)."""

    degs = _chord_degrees(chord)  # [0,4,7] maior / [0,3,7] menor / [0,7] power
    fifth = degs[-1]
    lo, _hi = register
    root_base = lo + ((chord.root - lo) % 12)
    root = _clamp_pitch_to_register(root_base, register)
    if root is None:
        root = max(lo, min(register[1], root_base))
    fifth_pitch = _clamp_pitch_to_register(root_base + fifth, register)
    if fifth_pitch is None:
        fifth_pitch = min(register[1], root + fifth)
    octave_pitch = _clamp_pitch_to_register(root_base + 12, register)
    if octave_pitch is None:
        octave_pitch = min(register[1], root + 12)
    return (root, fifth_pitch, octave_pitch)


def _pick_playable_voicing(
    chord: Chord,
    register: tuple[int, int],
    tuning: tuple[int, ...],
    max_fret: int,
) -> tuple[int, ...]:
    """Degrada de power chord+oitava ate raiz solo ate achar um voicing
    fisicamente tocavel na afinacao declarada — NUNCA escreve um voicing
    que a mao nao alcanca (a checagem reusa
    `tools.techniques.physical.guitar_voicing_is_playable`, a mesma janela
    de 6 casas do artigo academico citado no manual)."""

    root, fifth, octave = _power_chord_pitches(chord, register)
    candidates = ((root, fifth, octave), (root, fifth), (root,))
    for candidate in candidates:
        if guitar_voicing_is_playable(candidate, tuning, max_fret=max_fret):
            return candidate
    # Nenhum candidato coube (afinacao/registro incompativeis) — a raiz
    # sozinha sempre cabe em alguma corda se `register` respeita o piso da
    # afinacao (garantido pelo chamador via `_effective_register`); se nem
    # isso couber, falha explicito em vez de escrever nota impossivel.
    raise ValueError(
        f"guitar voicing for chord root {chord.root} is not playable in "
        f"tuning {tuning!r} within max_fret={max_fret} at register {register!r}"
    )


def _velocity_for(
    bucket: str, rng: random.Random, velocity_ranges=VELOCITY_RANGES,
) -> int:
    lo, hi = velocity_ranges[bucket]
    return rng.randint(int(lo), int(hi))


def _jitter_s(
    bucket: str, rng: random.Random, timing_jitter_ms=TIMING_JITTER_MS,
) -> float:
    lo, hi = timing_jitter_ms[bucket]
    sign = rng.choice((-1, 1))
    return sign * rng.uniform(lo, hi) / 1000.0


def _effective_register(
    register: tuple[int, int], tuning: tuple[int, ...],
) -> tuple[int, int]:
    floor = min(tuning)
    lo = max(register[0], floor)
    hi = max(register[1], lo)
    return (lo, hi)


def generate_guitar(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "guitar",
    register: tuple[int, int] = DEFAULT_GUITAR_REGISTER,
    tuning: tuple[int, ...] = DEFAULT_GUITAR_TUNING,
    max_fret: int = GUITAR_MAX_FRET,
    layers: int = 1,
    articulation: str = GUITAR_ARTICULATION_DEFAULT,
    dynamics: dict | None = None,
    seed: int = 0,
    profile: StyleProfile | None = None,
) -> list[RhythmicLayer]:
    """Gera o riff de guitarra ritmica de uma secao, do zero.

    Onsets seguem `analysis.kick_positions` dentro da secao quando
    existem (subamostrados por `energy.densidade`, mesmo mecanismo de
    `tools.palette.bass`); sem kick no bar, cai numa grade deterministica
    pelo mesmo eixo. Cada golpe e um voicing power-chord (raiz+quinta[+
    oitava]) do acorde vigente, degradado ate caber fisicamente na
    afinacao declarada — nunca terca empilhada no grave, nunca voicing
    que a mao nao alcanca. Golpes nunca se sobrepoem.

    Args:
      analysis: saida de analise da rodada 1 (acordes + kick_positions).
      section: secao alvo, com `energy.densidade` controlando contagem de
        golpes por bar.
      role: 'guitar'.
      register: (low, high) MIDI — piso e sempre elevado ate a corda mais
        grave de `tuning` se `register[0]` estiver abaixo dela.
      tuning: cordas soltas grave->agudo, MIDI absoluto. Default E padrao
        6 cordas (`tools.techniques.physical._GUITAR_TUNINGS`).
      max_fret: alcance de casas usado na checagem de tocabilidade.
      layers: numero de camadas (tracks); tipicamente 1.
      articulation: mapeada para gate (GATE_RATIOS). Default 'tight'.
      dynamics: reservado (nao consumido nesta rodada).
      seed: seed deterministica.
      profile: `StyleProfile` opcional — mesma retrocompatibilidade de
        `tools.palette.bass.generate_bass`.

    Raises:
      ValueError: layers < 1, role != 'guitar', ou nenhum voicing tocavel
        existe para algum acorde dentro de `register`/`tuning`/`max_fret`.
    """
    assert_traceable_seed(seed, source="palette.guitar.generate_guitar")
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in GUITAR_ROLES:
        raise ValueError(f"role must be one of {list(GUITAR_ROLES)}; got {role!r}")

    resolved_profile = profile or StyleProfile.default()
    bars = _bars_in_section(section, analysis)
    if not bars:
        return _empty_rhythmic_layers(layers)

    densidade = _energy_axis(section, "densidade")
    bucket = _density_bucket(densidade)
    section_end_s = bars[-1].end
    reg = _effective_register(register, tuning)

    result: list[RhythmicLayer] = []
    for layer_idx in range(layers):
        layer_rng = random.Random(seed + (layer_idx + 1) * 1_000_003)
        notes: list[RhythmicNote] = []
        for bar in bars:
            if bar.chord is None:
                continue
            voicing = _pick_playable_voicing(bar.chord, reg, tuning, max_fret)

            kicks = _kick_onsets_in_bar(analysis, bar)
            if kicks:
                onsets = _subsample_by_density(kicks, densidade)
                anchored = True
            else:
                step_dur_s = (bar.end - bar.start) / STEPS_PER_BAR
                onsets = [
                    bar.start + step * step_dur_s
                    for step in _fallback_steps(bucket)
                ]
                anchored = False

            for onset in onsets:
                start_s = max(
                    bar.start, 0.0,
                    onset + _jitter_s(
                        "anchor" if anchored else "normal", layer_rng,
                        timing_jitter_ms=resolved_profile.timing_jitter_ms,
                    ),
                )
                start_s = min(start_s, bar.end - 1e-6)
                bucket_v = (
                    GUITAR_ANCHOR_VELOCITY_BUCKET if anchored
                    else GUITAR_BASE_VELOCITY_BUCKET
                )
                velocity = _velocity_for(
                    bucket_v, layer_rng,
                    velocity_ranges=resolved_profile.velocity_ranges,
                )
                for pitch in voicing:
                    notes.append(RhythmicNote(
                        pitch=pitch, velocity=velocity,
                        start_s=start_s, end_s=start_s,  # end_s resolvido abaixo
                    ))

        notes.sort(key=lambda n: n.start_s)
        notes = _resolve_durations(
            notes, articulation=articulation,
            fallback_end_s=section_end_s,
            seed=seed + (layer_idx + 1) * 1_000_009,
            profile=resolved_profile,
        )
        result.append(RhythmicLayer(index=layer_idx, notes=tuple(notes)))
    return result


def _resolve_durations(
    notes: list[RhythmicNote],
    *,
    articulation: str,
    fallback_end_s: float,
    seed: int,
    profile: StyleProfile | None = None,
) -> list[RhythmicNote]:
    """Preenche `end_s` a partir do gap ate o PROXIMO onset DISTINTO —
    notas do mesmo golpe (mesmo `start_s`, um voicing inteiro) compartilham
    a mesma duracao, e golpes consecutivos nunca se sobrepoem."""
    if not notes:
        return notes
    art = articulation if articulation in DURATION_ARTICULATIONS else GUITAR_ARTICULATION_DEFAULT
    engine = DurationEngine(seed=seed, profile=profile)

    distinct_starts = sorted({n.start_s for n in notes})
    next_start_by_start: dict[float, float] = {}
    for i, start in enumerate(distinct_starts):
        next_start_by_start[start] = (
            distinct_starts[i + 1] if i + 1 < len(distinct_starts) else fallback_end_s
        )

    duration_by_start: dict[float, float] = {}
    result: list[RhythmicNote] = []
    for note in notes:
        if note.start_s not in duration_by_start:
            next_start = next_start_by_start[note.start_s]
            gap_s = next_start - note.start_s
            if gap_s <= 0:
                dur_s = MIN_NOTE_DURATION_S
            else:
                gap_ms = gap_s * 1000.0
                dur_ms = engine.compute(
                    DurationRequest(articulation=art, gap_ms=gap_ms),
                )
                dur_s = min(gap_s, max(MIN_NOTE_DURATION_S, dur_ms / 1000.0))
            duration_by_start[note.start_s] = dur_s
        dur_s = duration_by_start[note.start_s]
        result.append(RhythmicNote(
            pitch=note.pitch, velocity=note.velocity,
            start_s=note.start_s, end_s=note.start_s + dur_s,
        ))
    return result


__all__ = [
    "DEFAULT_GUITAR_REGISTER",
    "DEFAULT_GUITAR_TUNING",
    "GUITAR_MAX_FRET",
    "GUITAR_ROLES",
    "generate_guitar",
]
