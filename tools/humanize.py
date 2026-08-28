"""Motores de humanizacao — velocity e microtiming.

Velocity (formula da secao 6 do spec):

    velocity = base_do_papel
             + acento_metrico
             + alinhamento_com_bateria
             + forma_da_frase
             + articulacao
             + golpe
             + jitter_pequeno

Cada componente e uma funcao pura testavel isoladamente. A `VelocityEngine`
combina os componentes com uma seed reprodutivel e aplica a regra de ferro
do spec: o componente aleatorio nunca pode ser maior que a soma das
intencoes musicais deterministicas (checagem de runtime, nao apenas teste).

Microtiming: offset em ms por `sync_role`, conforme TIMING_JITTER_MS. Ancoras
(primeiro tempo de secao, unisono de guitarra, entrada de breakdown) nunca
recebem offset independente. Vies direcional e por elemento (nunca global).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .constants import (
    LEGATO_OVERLAP_MS,
    SYNC_ROLES,
    TIMING_JITTER_MS,
)
from .style_profile import StyleProfile

# --- tabelas de contribuicao por dimensao musical ---------------------------

METRIC_ACCENT_BY_POSITION = {
    "downbeat": +6,   # tempo 1 do compasso
    "strong":   +3,   # outros tempos fortes (3 em 4/4)
    "weak":     -2,   # tempos fracos (2 e 4 em 4/4)
    "sub":      -4,   # subdivisoes (semicolcheias entre tempos)
}

ARTICULATION_ADJUST = {
    "ghost":     -18,
    "staccato":   -4,
    "tight":       0,
    "open":       +3,
    "sustained":  +2,
    "let_ring":   +1,
    "accent":     +8,
    "normal":      0,
}

STROKE_ADJUST = {
    "down":  +3,
    "up":    -3,
    "none":   0,
}

KICK_ALIGN_BONUS = 5
SNARE_ALIGN_BONUS = 3

MIN_VELOCITY = 1
MAX_VELOCITY = 127
DEFAULT_JITTER_AMPLITUDE = 3

# Teto de sanidade para `jitter_amplitude`. Acima disso o valor nao e uma
# escolha musical, e erro de programacao: nenhuma nota do vocabulario chega
# perto de 20 de intencao expressiva acumulada (o maximo teorico e
# 6 + 8 + 6 + 8 + 3 = 31, e so numa nota que e downbeat + kick + pico de frase
# + acento + golpe forte ao mesmo tempo). Configuracao acima do teto levanta
# na construcao, nao em runtime, para falhar cedo e uma vez so.
MAX_JITTER_AMPLITUDE = 20


# --- componentes puros ------------------------------------------------------

def base_velocity(role: str, *, profile: StyleProfile | None = None) -> int:
    """Base da formula — ponto medio de `velocity_ranges[role]`.

    Sem `profile`, le da `StyleProfile.default()`, que reproduz
    `constants.VELOCITY_RANGES` byte a byte. Perfil customizado troca as
    FAIXAS de entrada; a formula (ponto medio) nao muda."""
    ranges = (profile or StyleProfile.default()).velocity_ranges
    if role not in ranges:
        raise ValueError(f"unknown velocity role: {role!r}")
    lo, hi = ranges[role]
    return int((lo + hi) // 2)


def metric_accent(position: str) -> int:
    """Acento metrico por posicao no compasso."""
    if position not in METRIC_ACCENT_BY_POSITION:
        raise ValueError(f"unknown metric position: {position!r}")
    return METRIC_ACCENT_BY_POSITION[position]


def drum_alignment(*, aligned_with_kick: bool, aligned_with_snare: bool) -> int:
    """Bonus por alinhamento com bateria — kick pesa mais que snare."""
    total = 0
    if aligned_with_kick:
        total += KICK_ALIGN_BONUS
    if aligned_with_snare:
        total += SNARE_ALIGN_BONUS
    return total


def phrase_shape(position: float) -> int:
    """Arco de frase: -4 nas pontas, pico +6 no meio (sinusoide)."""
    if not 0.0 <= position <= 1.0:
        raise ValueError(f"phrase position must be in [0,1]; got {position}")
    return round(-4 + 10 * math.sin(math.pi * position))


def articulation_adjust(articulation: str) -> int:
    if articulation not in ARTICULATION_ADJUST:
        raise ValueError(f"unknown articulation: {articulation!r}")
    return ARTICULATION_ADJUST[articulation]


def stroke_adjust(stroke: str) -> int:
    if stroke not in STROKE_ADJUST:
        raise ValueError(f"unknown stroke: {stroke!r}")
    return STROKE_ADJUST[stroke]


def jitter(rng: random.Random, amplitude: int) -> int:
    """Sorteia inteiro uniforme em [-amplitude, amplitude]."""
    if amplitude < 0:
        raise ValueError(f"jitter amplitude must be >= 0; got {amplitude}")
    if amplitude == 0:
        return 0
    return rng.randint(-amplitude, amplitude)


# --- estrutura e engine -----------------------------------------------------

@dataclass(frozen=True)
class VelocityComponents:
    """Contribuicao numerica de cada componente da formula."""
    base: int
    metric_accent: int
    drum_align: int
    phrase_shape: int
    articulation: int
    stroke: int
    jitter: int

    @property
    def deterministic_intent(self) -> int:
        """Soma dos valores absolutos das intencoes musicais deterministicas.

        Nao inclui `base` (que e piso constante do papel) nem `jitter`
        (que e o proprio componente aleatorio). E este numero que o jitter
        nunca pode superar em amplitude.
        """
        return (
            abs(self.metric_accent)
            + abs(self.drum_align)
            + abs(self.phrase_shape)
            + abs(self.articulation)
            + abs(self.stroke)
        )

    @property
    def total(self) -> int:
        return (
            self.base
            + self.metric_accent
            + self.drum_align
            + self.phrase_shape
            + self.articulation
            + self.stroke
            + self.jitter
        )


@dataclass
class VelocityRequest:
    """Descreve uma nota candidata para calculo de velocity."""
    role: str
    metric_position: str
    aligned_with_kick: bool = False
    aligned_with_snare: bool = False
    phrase_position: float = 0.5
    articulation: str = "normal"
    stroke: str = "none"


class VelocityEngine:
    """Combina os componentes puros com seed reprodutivel.

    Regra de ferro (secao 6 do spec): ruido nunca pode dominar intencao.
    Ela e aplicada em duas camadas, deliberadamente:

    - **Construcao**: `jitter_amplitude` fora de [0, MAX_JITTER_AMPLITUDE]
      levanta `ValueError`. Isso pega erro de programacao — amplitude que
      nenhuma nota do vocabulario poderia justificar — e falha cedo, uma vez.
    - **Por nota**: o jitter efetivo e CLAMPADO para `intent - 1`, garantindo
      `jitter < intencao` sempre, sem nunca derrubar a geracao.

    O clamp e a camada correta para o caso normal porque uma nota pode
    legitimamente ter intencao baixa (subdivisao fraca, sem alinhamento com
    bateria, articulacao neutra, e arco de frase cruzando zero) — e nota assim
    existe aos montes num arpejo. Levantar ali derrubaria material valido; o
    comportamento musicalmente certo e dar pouca variacao aleatoria a uma nota
    que tem pouca intencao expressiva.
    """

    def __init__(
        self,
        *,
        seed: int,
        jitter_amplitude: int = DEFAULT_JITTER_AMPLITUDE,
        profile: StyleProfile | None = None,
    ) -> None:
        if jitter_amplitude < 0:
            raise ValueError(
                f"jitter_amplitude must be >= 0; got {jitter_amplitude}"
            )
        if jitter_amplitude > MAX_JITTER_AMPLITUDE:
            raise ValueError(
                f"jitter amplitude ({jitter_amplitude}) exceeds the sanity "
                f"ceiling ({MAX_JITTER_AMPLITUDE}); at that size the random "
                f"component would dominate the musical intent of every note."
            )
        self._rng = random.Random(seed)
        self._jitter_amplitude = jitter_amplitude
        self._profile = profile or StyleProfile.default()
        self._peaked_tracks: set[str] = set()

    @property
    def jitter_amplitude(self) -> int:
        return self._jitter_amplitude

    def compute(self, req: VelocityRequest, *, track: str) -> int:
        """Devolve a velocity final da nota, clampada em [1,127]."""
        # 1) Componentes deterministicos. O jitter efetivo e clampado para
        #    ficar ESTRITAMENTE abaixo da intencao musical acumulada desta
        #    nota — regra de ferro da secao 6 do spec. Nota de intencao baixa
        #    (ex.: subdivisao fraca com arco de frase cruzando zero) recebe
        #    jitter proporcionalmente baixo em vez de derrubar a geracao.
        det = self._deterministic_components(req)
        intent = det.deterministic_intent
        effective_amplitude = min(self._jitter_amplitude, max(0, intent - 1))

        # 2) Sortear jitter e compor. A RNG e consumida SEMPRE, com a mesma
        #    sequencia de chamadas, para que o clamp nao dessincronize o
        #    stream entre notas e quebre a reprodutibilidade por seed.
        j = jitter(self._rng, effective_amplitude)
        components = VelocityComponents(
            base=det.base,
            metric_accent=det.metric_accent,
            drum_align=det.drum_align,
            phrase_shape=det.phrase_shape,
            articulation=det.articulation,
            stroke=det.stroke,
            jitter=j,
        )
        v = max(MIN_VELOCITY, min(MAX_VELOCITY, components.total))

        # 3) Regra do 127 unico por track.
        if v == MAX_VELOCITY and track in self._peaked_tracks:
            v = MAX_VELOCITY - 1
        if v == MAX_VELOCITY:
            self._peaked_tracks.add(track)
        return v

    def _deterministic_components(self, req: VelocityRequest) -> VelocityComponents:
        return VelocityComponents(
            base=base_velocity(req.role, profile=self._profile),
            metric_accent=metric_accent(req.metric_position),
            drum_align=drum_alignment(
                aligned_with_kick=req.aligned_with_kick,
                aligned_with_snare=req.aligned_with_snare,
            ),
            phrase_shape=phrase_shape(req.phrase_position),
            articulation=articulation_adjust(req.articulation),
            stroke=stroke_adjust(req.stroke),
            jitter=0,
        )


# ============================================================================
# Motor de microtiming
# ============================================================================

# Mapeamento de sync_role para bucket de TIMING_JITTER_MS.
# - exact_anchor / guitar_unison caem no bucket 'anchor' (0, 3): sempre
#   dentro de +/-3ms, sem vies. Ancoras estruturais nao respiram.
# - kick_support cai em 'normal' (3, 8): fica proximo do beat, com leve vies
#   pra depois pra deixar o kick pousar antes.
# - anticipation e response caem em 'intermediate' (5, 12) mas com vies
#   direcional padrao invertido (early/late).
# - sustain_through cai em 'fill' (0, 15): "livre" — a nota respira sem
#   compromisso com o grid.
# - ghost_fill cai em 'intermediate' (5, 12): drift confortavel.
SYNC_ROLE_BUCKET: dict[str, str] = {
    "exact_anchor":    "anchor",
    "kick_support":    "normal",
    "guitar_unison":   "anchor",
    "anticipation":    "intermediate",
    "response":        "intermediate",
    "sustain_through": "fill",
    "ghost_fill":      "intermediate",
}

# Vies direcional padrao por sync_role, em ms. Negativo = adiantado.
# So anticipation e response tem vies != 0 por default (a semantica esta no
# nome do papel). Todos os outros comecam neutros e o elemento configura
# se quiser desviar.
DEFAULT_DIRECTIONAL_BIAS_MS: dict[str, int] = {
    "exact_anchor":    0,
    "kick_support":    0,
    "guitar_unison":   0,
    "anticipation":   -8,
    "response":       +8,
    "sustain_through": 0,
    "ghost_fill":      0,
}

# Limite absoluto do bucket 'anchor'. Nota ancora nunca ultrapassa.
ANCHOR_MAX_ABS_MS: int = TIMING_JITTER_MS["anchor"][1]


@dataclass(frozen=True)
class MicrotimingRequest:
    """Descreve uma nota candidata para calculo de offset.

    - `is_anchor` = True forca offset dentro de +/-ANCHOR_MAX_ABS_MS, sem
      vies. Marque como True: primeiro tempo de secao, unisono de guitarra,
      entrada de breakdown.
    - `directional_bias_ms=None` usa `DEFAULT_DIRECTIONAL_BIAS_MS[sync_role]`.
      Passar valor explicito por elemento — nunca por musica inteira.
    """
    sync_role: str
    is_anchor: bool = False
    directional_bias_ms: int | None = None


class _ProfiledEngine:
    """Base dos motores parametrizados por StyleProfile.

    Guarda RNG seedado + StyleProfile resolvido para `default()` quando o
    chamador nao passa nada. Existe para nao duplicar `__init__` em cada
    motor (Microtiming, Duration) — a fatoracao e trivial mas o jscpd cobra.
    """

    def __init__(self, *, seed: int, profile: StyleProfile | None = None) -> None:
        self._rng = random.Random(seed)
        self._profile = profile or StyleProfile.default()


class MicrotimingEngine(_ProfiledEngine):
    """Motor deterministico de microtiming.

    - Offset em ms por sync_role, seguindo TIMING_JITTER_MS.
    - Ancoras estruturais nunca recebem offset independente.
    - Vies direcional configurado por elemento; jamais global.
    - Mesma seed + mesma sequencia de requests => mesma sequencia de offsets.
    """

    def compute(self, req: MicrotimingRequest) -> int:
        """Devolve offset em ms (int arredondado) para a nota descrita."""
        if req.sync_role not in SYNC_ROLES:
            raise ValueError(f"unknown sync_role: {req.sync_role!r}")

        # Nota marcada como ancora: cai no bucket 'anchor' sem vies,
        # independente do sync_role e do directional_bias_ms configurado.
        # Ancora e ancora — nao respira.
        if req.is_anchor:
            return self._sample_anchor()

        bucket_name = SYNC_ROLE_BUCKET[req.sync_role]

        # exact_anchor e guitar_unison: bucket 'anchor' descarta bias e usa
        # uniforme dentro de +/-3ms, para respeitar o teto do bucket.
        if bucket_name == "anchor":
            return self._sample_anchor()

        bucket_bias_ms, bucket_sigma_ms = self._profile.timing_jitter_ms[bucket_name]
        directional = (
            req.directional_bias_ms
            if req.directional_bias_ms is not None
            else DEFAULT_DIRECTIONAL_BIAS_MS[req.sync_role]
        )
        noise = self._rng.gauss(0.0, float(bucket_sigma_ms))
        return int(round(bucket_bias_ms + directional + noise))

    def _sample_anchor(self) -> int:
        anchor_max = self._profile.timing_jitter_ms["anchor"][1]
        return int(round(self._rng.uniform(-anchor_max, anchor_max)))


# ============================================================================
# Motor de duracao (US-007)
# ============================================================================

# Articulacoes suportadas pelo motor de duracao. Superset das chaves de
# GATE_RATIOS: `let_ring` nao vive la porque nunca usa proporcao — alinha
# release ao compasso.
DURATION_ARTICULATIONS: tuple[str, ...] = (
    "ghost", "staccato", "tight", "open", "sustained", "let_ring",
)

# Articulacoes cujo release e alinhado a limite de compasso, nao a valor
# arbitrario tirado do gap.
BAR_ALIGNED_ARTICULATIONS: frozenset[str] = frozenset({"sustained", "let_ring"})

# Margem em ms subtraida do release para garantir que a nota nunca termine
# exatamente no onset da proxima. Casa numericamente com a mediana empirica
# de sustained em 174bpm (2 compassos = 2758.62ms; 2758.62 - 15 = 2743.62ms,
# secao 7 do spec).
DURATION_SAFETY_MS: float = 15.0

# Duracao usada quando nao ha proximo evento nem limite de compasso — ultima
# nota da track cai aqui.
DEFAULT_LAST_NOTE_MS: float = 250.0

MIN_DURATION_MS: float = 1.0


@dataclass(frozen=True)
class DurationRequest:
    """Descreve nota candidata ao motor de duracao.

    - `articulation`: uma de DURATION_ARTICULATIONS.
    - `gap_ms`: distancia (ms) do onset desta nota ate o onset da proxima
      no mesmo track. `None` se e a ultima nota do track.
    - `is_legato`: True faz a nota atravessar a proxima com overlap de
      LEGATO_OVERLAP_MS (5-25ms).
    - `bar_boundaries_ms`: offsets (ms, relativos a este onset) dos proximos
      limites de compasso, em ordem crescente. Usados apenas por
      sustained/let_ring para alinhar release.
    - `sustain_bars`: por quantos compassos sustained/let_ring deve sustentar.
    """
    articulation: str
    gap_ms: float | None = None
    is_legato: bool = False
    bar_boundaries_ms: tuple[float, ...] = ()
    sustain_bars: int = 2


class DurationEngine(_ProfiledEngine):
    """Motor deterministico de duracao.

    - Ghost/staccato/tight/open: duracao = gap * uniform(GATE_RATIOS[art]).
    - Sustained/let_ring: release alinhado a limite de compasso; fallback a
      proporcao sustained do gap quando sem limites informados.
    - Legato: overlap com a proxima entre 5 e 25ms (LEGATO_OVERLAP_MS).
    - Safety: nota nao-legato jamais termina no onset exato da proxima
      (garantido subtraindo DURATION_SAFETY_MS do gap).
    - Mesma seed + mesma sequencia de requests => mesma sequencia de duracoes.
    """

    def compute(self, req: DurationRequest) -> float:
        """Devolve a duracao em ms."""
        if req.articulation not in DURATION_ARTICULATIONS:
            raise ValueError(f"unknown articulation: {req.articulation!r}")

        if req.articulation in BAR_ALIGNED_ARTICULATIONS:
            duration = self._bar_aligned(req)
        else:
            duration = self._gap_based(req)

        if req.is_legato and req.gap_ms is not None:
            # Legato: cruza o onset seguinte com overlap intencional.
            duration = req.gap_ms + self._rng.uniform(*LEGATO_OVERLAP_MS)
        elif req.gap_ms is not None:
            # Jamais terminar no onset exato da proxima nota.
            duration = min(duration, req.gap_ms - DURATION_SAFETY_MS)

        return max(MIN_DURATION_MS, duration)

    def _bar_aligned(self, req: DurationRequest) -> float:
        if req.sustain_bars < 1:
            raise ValueError(
                f"sustain_bars must be >= 1; got {req.sustain_bars}"
            )
        if req.bar_boundaries_ms:
            idx = min(req.sustain_bars - 1, len(req.bar_boundaries_ms) - 1)
            return req.bar_boundaries_ms[idx] - DURATION_SAFETY_MS
        # Sem limites: fallback via proporcao sustained do gap.
        if req.gap_ms is not None:
            lo, hi = self._profile.gate_ratios["sustained"]
            return req.gap_ms * self._rng.uniform(lo, hi)
        return DEFAULT_LAST_NOTE_MS

    def _gap_based(self, req: DurationRequest) -> float:
        if req.gap_ms is None:
            return DEFAULT_LAST_NOTE_MS
        lo, hi = self._profile.gate_ratios[req.articulation]
        return req.gap_ms * self._rng.uniform(lo, hi)
