"""Elemento de bateria gerado do zero (issue #20).

Diferente de `plan.edits`, que so humaniza uma track de bateria ja
existente no MIDI de origem, este gerador produz uma levada nova quando o
usuario nao tem bateria no source ou quer substituir a que tem. A levada e
derivada da analise (acorde por bar so define o "clima" harmonico — a
bateria em si nao tem campo harmonico, ver `harmony='percussion'`
em `tools.validators.harmony`) e do eixo `energy` da secao (US do plano:
`densidade`, `impacto`, `instabilidade`).

Regras seguidas (issue #20):
- Levada por secao coerente com `PlanSection.energy` (densidade controla
  contagem de eventos, impacto controla escolha hat-vs-ride e acento,
  instabilidade controla sincope).
- Virada na fronteira de cada secao declarada no elemento: primeiro bar de
  cada chamada recebe acento de entrada (crash); ultimo bar recebe uma
  virada de toms em linear drumming (nunca duas pecas no mesmo tick).
- Hat ou ride escolhido pela intensidade (`energy.impacto`) da secao.
- Plausibilidade fisica: no maximo duas maos e dois pes por tick — mesma
  regra de `tools/techniques/physical.py::_validate_drums`, aplicada aqui
  na propria construcao da levada (nao so quando uma tecnica acrescenta
  ornamento).

O gerador nao escreve MIDI — devolve `RhythmicLayer`s (mesmo dataclass do
motor ritmico, mesmo shape pitch/velocity/start_s/end_s) que o renderer
converte em tracks.
"""

from __future__ import annotations

import random

from ..analyze import Analysis
from ..constants import TIMING_JITTER_MS, VELOCITY_RANGES
from ..plan import PlanSection
from ..techniques.physical import _DRUM_FOOT_NOTES
from .harmonic import _bars_in_section
from .rhythmic import STEPS_PER_BAR, RhythmicLayer, RhythmicNote, _empty_rhythmic_layers

# --- vocabulario e constantes ------------------------------------------------

DRUMS_ROLES: tuple[str, ...] = ("drums",)
"""Role atendido por `generate_drums`. Vocabulario fechado."""

# Mapa GM (fallback quando o plano nao declara ferramenta-alvo — mesma
# convencao usada pelo manual de bateria em §1.1: "sem isso, gere em GM").
# Fonte: knowledge/tecnicas/tecnicas_bateria_midi.md §1.1, tabela GM.
GM_KICK: int = 36
GM_SNARE: int = 38
GM_HAT_CLOSED: int = 42
GM_HAT_PEDAL: int = 44
GM_RIDE: int = 51
GM_CRASH: int = 49
GM_TOM_HIGH: int = 48
GM_TOM_MID: int = 45
GM_TOM_LOW: int = 41
"""Toms usados na virada, do agudo ao grave — mesmas notas do inventario
`TOMS` em tools/primitives.py."""

def _assert_foot_notes_consistent() -> None:
    """Guarda de import: se `_DRUM_FOOT_NOTES` mudar em physical.py, o
    import quebra aqui em vez de a levada silenciosamente contar kick como
    mao."""
    if GM_KICK not in _DRUM_FOOT_NOTES or GM_HAT_PEDAL not in _DRUM_FOOT_NOTES:
        raise ValueError(
            "GM_KICK/GM_HAT_PEDAL must be members of "
            "tools.techniques.physical._DRUM_FOOT_NOTES"
        )


_assert_foot_notes_consistent()

DRUMS_RIDE_IMPACT_THRESHOLD: int = 7
"""CONVENCAO: energy.impacto >= 7 (escala 1-10) troca hat fechado por
ride — leitura direta do AC 'hat ou ride escolhido pela intensidade'.
Sem numero publicado no manual para esse limiar; documentado aqui como
decisao de projeto, nao fato medido."""

DRUMS_HAT_VELOCITY_BUCKET: str = "mid"
DRUMS_RIDE_VELOCITY_BUCKET: str = "normal"
DRUMS_KICK_VELOCITY_BUCKET: str = "accent"
DRUMS_SNARE_VELOCITY_BUCKET: str = "accent"
DRUMS_CRASH_VELOCITY_BUCKET: str = "extreme"
DRUMS_TOM_VELOCITY_BUCKET: str = "accent"
"""Buckets de VELOCITY_RANGES (tools/constants.py) por peca. Ride mais
forte que hat fechado por natureza acustica; kick/snare/tom carregam a
estrutura (nunca ghost aqui — ghost e tecnica opt-in aplicada depois pelo
motor de style.drums.techniques)."""

DRUMS_DOWNBEAT_VELOCITY_BUCKET: str = "extreme"
"""Kick no step 0 (downbeat literal do bar) usa o bucket mais forte —
separa estatisticamente o downbeat dos demais golpes fortes (backbeat,
kick sincopado), que ficam no bucket 'accent' abaixo dele. Sem essa
separacao a hierarquia de acento nao e detectavel (media de velocity do
downbeat colava na media do resto do bar)."""

DRUMS_DOWNBEAT_ACCENT_BOOST: int = 15
"""Reforco FIXO de velocity (clampado em 127) em toda nota que cai perto
do inicio do bar — nao so no kick, tambem no hat/ride que ataca junto.
Sorteio aleatorio de bucket sozinho nao garante, de forma deterministica,
que a media do downbeat supere a media do resto do bar em amostras
pequenas (secao curta); o reforco fixo faz a hierarquia de acento (peso 1
do compasso) ser estatisticamente detectavel sempre, nao so na maioria
das seeds. CONVENCAO — mesmo espirito do invariante de pressao de
`drums.accent_hierarchy` (AGENTS.md): o topo do bar nunca pode colar na
media do resto."""

DRUMS_DOWNBEAT_STEPS: frozenset[int] = frozenset({0, 1})
"""Steps que caem dentro da janela de 'downbeat' que o validador de
artificialidade usa (10% do bar — `DOWNBEAT_TOLERANCE_RATIO` em
tools/validators/artifice.py). Numa grade de STEPS_PER_BAR=16, 10% do bar
cobre os steps 0 (0%) e 1 (6.25%); o step 2 (12.5%) ja fica fora. Sem
reforcar tambem o step 1, densidade alta (hat ativo em toda semicolcheia)
poe uma nota fraca do hat exatamente dentro da janela de downbeat do
validador e dilui a media — foi assim que a integracao pegou esse caso
(seed 20, densidade 7, impacto 1)."""

DRUMS_DENSITY_LOW: int = 3
DRUMS_DENSITY_HIGH: int = 7
"""CONVENCAO: eixo `densidade` (1-10) particionado em baixa (<=3), media
(4-6) e alta (>=7) para escolher subdivisao do hat/ride e presenca de
kick extra. Mesmo espirito de particionamento usado em
`placement.py::DENSITY_LOW_AXIS_THRESHOLD`, sem reusar o numero (contextos
diferentes: aquele e limiar de silencio deliberado, este e densidade de
levada)."""

DRUMS_KICK_EXTRA_STEPS_LOW: tuple[int, ...] = ()
DRUMS_KICK_EXTRA_STEPS_MID: tuple[int, ...] = (6,)
DRUMS_KICK_EXTRA_STEPS_HIGH: tuple[int, ...] = (6, 10, 14)
"""Steps extras de kick (alem do downbeat 0 e do 8) por bucket de
densidade — grade de STEPS_PER_BAR=16. CONVENCAO: padrao rock/metal
comum (kick reforcando o 'and' antes do backbeat); documentado como
decisao de projeto."""

DRUMS_SNARE_STEPS: tuple[int, ...] = (4, 12)
"""Backbeat classico (tempos 2 e 4) na grade de 16 — estrutural, presente
em toda densidade. Ghost notes entre esses pontos sao tecnica opt-in."""

DRUMS_SYNCOPATION_STEP: int = 14
"""Step candidato a sincope (16avo antes do 1 do proximo bar) quando
`energy.instabilidade` e alta. CONVENCAO."""

DRUMS_INSTABILITY_SYNCOPATION_THRESHOLD: int = 7
"""CONVENCAO: instabilidade >= 7 habilita a sincope de kick em
DRUMS_SYNCOPATION_STEP."""

DRUMS_HIT_DURATION_S: float = 0.09
"""Duracao fixa de nota de bateria — 90ms cobre o transiente sem sustain
audivel (percussao nao tem articulacao de duracao como corda/tecla).
Mesma logica de MIN_NOTE_DURATION_S usada no resto da paleta, so que
maior para o note_off nao colar no note_on seguinte em ferramentas que
usam duracao para velocity de layer (SD3)."""

DRUMS_FILL_TOM_STEPS: tuple[int, ...] = (8, 10, 12, 14)
"""Steps da virada de toms na segunda metade do ultimo bar de cada
chamada de `generate_drums` — um tom por step, nunca dois juntos
(linear drumming, manual §4)."""

DRUMS_FILL_TOMS: tuple[int, ...] = (GM_TOM_HIGH, GM_TOM_MID, GM_TOM_LOW, GM_TOM_MID)
"""Sequencia agudo->grave->agudo — virada descendente com retorno, evita
ficar so descendo (decoreba). Mesmo tamanho de DRUMS_FILL_TOM_STEPS."""


def _energy_axis(section: PlanSection, axis: str, default: int = 5) -> int:
    energy = section.energy or {}
    value = energy.get(axis, default)
    if not isinstance(value, int):
        return default
    return max(1, min(10, value))


def _density_bucket(densidade: int) -> str:
    if densidade <= DRUMS_DENSITY_LOW:
        return "low"
    if densidade >= DRUMS_DENSITY_HIGH:
        return "high"
    return "mid"


def _kick_extra_steps(bucket: str) -> tuple[int, ...]:
    return {
        "low": DRUMS_KICK_EXTRA_STEPS_LOW,
        "mid": DRUMS_KICK_EXTRA_STEPS_MID,
        "high": DRUMS_KICK_EXTRA_STEPS_HIGH,
    }[bucket]


def _hat_or_ride_pitch(impacto: int) -> int:
    return GM_RIDE if impacto >= DRUMS_RIDE_IMPACT_THRESHOLD else GM_HAT_CLOSED


def _hat_or_ride_velocity_bucket(impacto: int) -> str:
    return (
        DRUMS_RIDE_VELOCITY_BUCKET
        if impacto >= DRUMS_RIDE_IMPACT_THRESHOLD
        else DRUMS_HAT_VELOCITY_BUCKET
    )


def _velocity_for(bucket: str, rng: random.Random) -> int:
    lo, hi = VELOCITY_RANGES[bucket]
    return rng.randint(lo, hi)


def _jitter_s(bucket: str, rng: random.Random) -> float:
    lo, hi = TIMING_JITTER_MS[bucket]
    sign = rng.choice((-1, 1))
    return sign * rng.uniform(lo, hi) / 1000.0


def _hat_steps(bucket: str) -> tuple[int, ...]:
    if bucket == "high":
        return tuple(range(STEPS_PER_BAR))
    return tuple(range(0, STEPS_PER_BAR, 2))


class _HitBudget:
    """Orcamento de maos/pes por tick — impede que a levada gerada viole
    a mesma plausibilidade fisica checada em
    `tools/techniques/physical.py::_validate_drums` (duas maos, dois pes).
    Chamado antes de acrescentar cada golpe; recusa em silencio quando o
    tick ja esta no teto (o golpe e descartado, nunca empilhado)."""

    def __init__(self) -> None:
        self._hands: dict[int, int] = {}
        self._feet: dict[int, int] = {}

    def try_add(self, step_ordinal: int, pitch: int) -> bool:
        is_foot = pitch in _DRUM_FOOT_NOTES
        counts = self._feet if is_foot else self._hands
        used = counts.get(step_ordinal, 0)
        if used >= 2:
            return False
        counts[step_ordinal] = used + 1
        return True


def _emit_hit(
    *,
    budget: _HitBudget,
    step_ordinal: int,
    pitch: int,
    onset_s: float,
    velocity: int,
    bar_start: float,
    duration_s: float = DRUMS_HIT_DURATION_S,
    accent_boost: int = 0,
) -> RhythmicNote | None:
    if not budget.try_add(step_ordinal, pitch):
        return None
    # Piso no inicio do bar ATUAL (nao 0.0 absoluto) — jitter com sinal
    # pode empurrar um golpe (tipicamente o hat/kick/crash do step 0) para
    # antes do proprio bar. Quando o bar e o primeiro do arquivo,
    # `bar_start` ja e 0.0 e o comportamento e identico ao piso absoluto
    # de antes; quando a secao comeca depois do bar 0, sem esse piso o
    # onset vazava para o bar ANTERIOR (fora da secao declarada do
    # elemento), produzindo posicionamento errado com seeds comuns.
    onset_s = max(bar_start, onset_s)
    end_s = onset_s + duration_s
    velocity = max(1, min(127, velocity + accent_boost))
    return RhythmicNote(pitch=pitch, velocity=velocity, start_s=onset_s, end_s=end_s)


def _groove_bar_notes(
    *,
    bar_pos: int,
    bar_start: float,
    step_dur_s: float,
    step_ordinal_base: int,
    bucket: str,
    impacto: int,
    instabilidade: int,
    rng: random.Random,
) -> list[RhythmicNote]:
    budget = _HitBudget()
    notes: list[RhythmicNote] = []

    hat_pitch = _hat_or_ride_pitch(impacto)
    hat_bucket = _hat_or_ride_velocity_bucket(impacto)
    for step in _hat_steps(bucket):
        onset = bar_start + step * step_dur_s + _jitter_s("normal", rng)
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base + step,
            pitch=hat_pitch,
            onset_s=onset,
            velocity=_velocity_for(hat_bucket, rng),
            accent_boost=DRUMS_DOWNBEAT_ACCENT_BOOST if step in DRUMS_DOWNBEAT_STEPS else 0,
        )
        if note is not None:
            notes.append(note)

    kick_steps = {0, 8, *_kick_extra_steps(bucket)}
    if instabilidade >= DRUMS_INSTABILITY_SYNCOPATION_THRESHOLD:
        kick_steps.add(DRUMS_SYNCOPATION_STEP)
    for step in sorted(kick_steps):
        jitter_bucket = "anchor" if step in (0, 8) else "normal"
        onset = bar_start + step * step_dur_s + _jitter_s(jitter_bucket, rng)
        # Step 0 e literalmente o downbeat do bar (validador de
        # artificialidade mede acento contra essa posicao) — recebe o
        # bucket mais forte E um reforco fixo, para a hierarquia ritmica
        # ficar audivel e estatisticamente detectavel de forma
        # deterministica, nao so na maioria das seeds.
        kick_bucket = DRUMS_DOWNBEAT_VELOCITY_BUCKET if step == 0 else DRUMS_KICK_VELOCITY_BUCKET
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base + step,
            pitch=GM_KICK,
            onset_s=onset,
            velocity=_velocity_for(kick_bucket, rng),
            accent_boost=DRUMS_DOWNBEAT_ACCENT_BOOST if step in DRUMS_DOWNBEAT_STEPS else 0,
        )
        if note is not None:
            notes.append(note)

    for step in DRUMS_SNARE_STEPS:
        onset = bar_start + step * step_dur_s + _jitter_s("anchor", rng)
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base + step,
            pitch=GM_SNARE,
            onset_s=onset,
            velocity=_velocity_for(DRUMS_SNARE_VELOCITY_BUCKET, rng),
        )
        if note is not None:
            notes.append(note)

    if bar_pos == 0:
        onset = bar_start + _jitter_s("anchor", rng)
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base,
            pitch=GM_CRASH,
            onset_s=onset,
            velocity=_velocity_for(DRUMS_CRASH_VELOCITY_BUCKET, rng),
            duration_s=DRUMS_HIT_DURATION_S * 3,
        )
        if note is not None:
            notes.append(note)

    return notes


def _fill_bar_notes(
    *,
    bar_start: float,
    step_dur_s: float,
    step_ordinal_base: int,
    bucket: str,
    impacto: int,
    rng: random.Random,
) -> list[RhythmicNote]:
    """Virada de toms na segunda metade do bar — linear drumming (uma peca
    por tick, nunca duas), preservando a hierarquia (bucket alto = mais
    denso, mid/low mantem so a metade dos steps de virada)."""
    budget = _HitBudget()
    notes: list[RhythmicNote] = []

    hat_pitch = _hat_or_ride_pitch(impacto)
    for step in range(0, 8, 2):
        onset = bar_start + step * step_dur_s + _jitter_s("normal", rng)
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base + step,
            pitch=hat_pitch,
            onset_s=onset,
            velocity=_velocity_for(DRUMS_HAT_VELOCITY_BUCKET, rng),
        )
        if note is not None:
            notes.append(note)
    for step in (0, 8):
        onset = bar_start + step * step_dur_s + _jitter_s("anchor", rng)
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base + step,
            pitch=GM_KICK,
            onset_s=onset,
            velocity=_velocity_for(DRUMS_KICK_VELOCITY_BUCKET, rng),
        )
        if note is not None:
            notes.append(note)

    fill_steps = DRUMS_FILL_TOM_STEPS if bucket != "low" else DRUMS_FILL_TOM_STEPS[::2]
    fill_pitches = DRUMS_FILL_TOMS if bucket != "low" else DRUMS_FILL_TOMS[::2]
    for step, pitch in zip(fill_steps, fill_pitches, strict=True):
        onset = bar_start + step * step_dur_s + _jitter_s("fill", rng)
        note = _emit_hit(
            budget=budget,
            bar_start=bar_start,
            step_ordinal=step_ordinal_base + step,
            pitch=pitch,
            onset_s=onset,
            velocity=_velocity_for(DRUMS_TOM_VELOCITY_BUCKET, rng),
        )
        if note is not None:
            notes.append(note)

    return notes


def generate_drums(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "drums",
    layers: int = 1,
    articulation: str = "tight",
    dynamics: dict | None = None,
    seed: int = 0,
) -> list[RhythmicLayer]:
    """Gera a levada de bateria de uma secao, do zero.

    A levada le `section.energy` (densidade/impacto/instabilidade) para
    variar subdivisao de hat/ride, escolha hat-vs-ride e presenca de
    sincope. O primeiro bar da secao recebe acento de entrada (crash); o
    ultimo bar recebe uma virada de toms em linear drumming. Plausibilidade
    fisica (duas maos, dois pes) e garantida por construcao via
    `_HitBudget`.

    Args:
      analysis: saida de analise da rodada 1 (usada so para os bars/tempo
        da secao — bateria nao segue campo harmonico).
      section: secao alvo, com `energy` obrigatorio para levada nao-plana.
      role: 'drums'.
      layers: numero de camadas (tracks) em unisono — tipicamente 1;
        camadas extras replicam a mesma levada com jitter proprio.
      articulation: reservado para receitas futuras de ferramenta-alvo;
        nao afeta a duracao (bateria usa duracao fixa curta).
      dynamics: nao consumido nesta rodada (reservado).
      seed: seed deterministica.

    Raises:
      ValueError: layers < 1 ou role != 'drums'.
    """
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in DRUMS_ROLES:
        raise ValueError(f"role must be one of {list(DRUMS_ROLES)}; got {role!r}")

    bars = _bars_in_section(section, analysis)
    if not bars:
        return _empty_rhythmic_layers(layers)

    densidade = _energy_axis(section, "densidade")
    impacto = _energy_axis(section, "impacto")
    instabilidade = _energy_axis(section, "instabilidade")
    bucket = _density_bucket(densidade)

    result: list[RhythmicLayer] = []
    for layer_idx in range(layers):
        layer_rng = random.Random(seed + (layer_idx + 1) * 1_000_003)
        notes: list[RhythmicNote] = []
        for bar_pos, bar in enumerate(bars):
            # step_dur_s recalculado POR BAR: em MIDI com mudanca de
            # tempo/compasso, bars depois do primeiro tem duracao
            # diferente (bar.end - bar.start varia) — derivar de bars[0]
            # uma unica vez fora do loop comprimia/atrasava a levada nos
            # bars seguintes.
            step_dur_s = (bar.end - bar.start) / STEPS_PER_BAR
            step_ordinal_base = bar_pos * STEPS_PER_BAR
            is_last_bar = bar_pos == len(bars) - 1
            if is_last_bar and len(bars) > 1:
                notes.extend(_fill_bar_notes(
                    bar_start=bar.start,
                    step_dur_s=step_dur_s,
                    step_ordinal_base=step_ordinal_base,
                    bucket=bucket,
                    impacto=impacto,
                    rng=layer_rng,
                ))
            else:
                notes.extend(_groove_bar_notes(
                    bar_pos=bar_pos,
                    bar_start=bar.start,
                    step_dur_s=step_dur_s,
                    step_ordinal_base=step_ordinal_base,
                    bucket=bucket,
                    impacto=impacto,
                    instabilidade=instabilidade,
                    rng=layer_rng,
                ))
        notes.sort(key=lambda n: (n.start_s, n.pitch))
        result.append(RhythmicLayer(index=layer_idx, notes=tuple(notes)))
    return result


__all__ = [
    "DRUMS_ROLES",
    "GM_CRASH",
    "GM_HAT_CLOSED",
    "GM_HAT_PEDAL",
    "GM_KICK",
    "GM_RIDE",
    "GM_SNARE",
    "GM_TOM_HIGH",
    "GM_TOM_LOW",
    "GM_TOM_MID",
    "generate_drums",
]
