"""Elemento de baixo gerado do zero (issue #20).

Diferente de `plan.edits`, que so humaniza uma track de baixo ja existente
no MIDI de origem, este gerador produz uma linha nova quando o usuario nao
tem baixo no source ou quer substituir a que tem. A linha:

- Segue o campo harmonico do bar (raiz/terca/quinta do acorde vigente,
  via `analyze.Chord`) — nunca uma pitch class fora do acorde, para passar
  sem aviso pelo validador harmonico (`tools.validators.harmony`, modo
  `follow_chords`) que ja roda no pipeline de render.
- Segue as ancoras de kick do MIDI de origem (`analysis.kick_positions`)
  quando existem na secao — o baixo nao e independente do groove da
  bateria. Sem kick na secao (fallback), cai numa grade deterministica
  dirigida pelo eixo `densidade`.
- Tem contorno proprio: alterna raiz/quinta/terca/oitava por bar em vez de
  repetir so a tonica — a sequencia de graus muda a cada `BASS_CONTOUR_MUTATE_BARS`
  bars (mesmo espirito de `mutate_every_bars` do arp).
- E monofonico por construcao: onsets nunca se sobrepoem (cada nota
  termina antes ou junto do proximo onset), o que ja satisfaz a
  plausibilidade fisica de "um instrumento so" sem precisar do validador
  de tecnica de `tools/techniques/physical.py` (que so audita ORNAMENTOS
  novos, nao a track estrutural gerada aqui).

Escopo declarado fora desta rodada: notas de passagem cromaticas/diatonicas
que NAO pertencem ao acorde vigente. O validador harmonico atual
(`harmony: follow_chords`) trata qualquer pitch class fora do acorde do bar
como ERRO quando a nota comeca dentro daquele bar — nao existe conceito de
"nota de passagem justificavel" no validador hoje. Implementar isso exigiria
estender `tools/validators/harmony.py` (fora do escopo desta issue). Por
isso o contorno aqui e restrito a tons do acorde (raiz/terca/quinta/oitava),
que e harmonicamente correto e ja produz um contorno real (nao so tonica).
"""

from __future__ import annotations

import random

from ..analyze import Analysis, BarAnalysis, Chord
from ..constants import TIMING_JITTER_MS, VELOCITY_RANGES
from ..humanize import DURATION_ARTICULATIONS, DurationEngine, DurationRequest
from ..plan import PlanSection
from ..techniques.physical import _BASS_DEFAULT_TUNING
from .harmonic import _bars_in_section, _chord_degrees
from .rhythmic import (
    STEPS_PER_BAR,
    RhythmicLayer,
    RhythmicNote,
    _clamp_pitch_to_register,
    _empty_rhythmic_layers,
)

# --- vocabulario e constantes ------------------------------------------------

BASS_ROLES: tuple[str, ...] = ("bass",)
"""Role atendido por `generate_bass`. Vocabulario fechado."""

DEFAULT_BASS_REGISTER: tuple[int, int] = (28, 55)
"""CONVENCAO: cobre a afinacao padrao de baixo eletrico 4 cordas
(`tools.techniques.physical._BASS_DEFAULT_TUNING`, corda mais grave = 28,
E1) ate ~G3 (55) — registro tipico de linha de baixo sem invadir o
registro de guitarra. Configuravel por elemento."""

BASS_BASE_VELOCITY_BUCKET: str = "normal"
BASS_ANCHOR_VELOCITY_BUCKET: str = "accent"
"""Nota alinhada a kick (ancora ritmica) recebe o bucket mais forte —
reforca a ideia de que baixo e bateria tocam juntos."""

BASS_ARTICULATION_DEFAULT: str = "tight"
"""Default de articulacao — gate 0.60-0.82 (GATE_RATIOS['tight']), curto o
bastante para nunca encostar no proximo onset mesmo em fallback denso."""

BASS_CONTOUR_MUTATE_BARS: int = 4
"""CONVENCAO: o contorno (sequencia de graus do acorde) muda a cada 4
bars — mesmo numero usado por `RHYTHMIC_MUTATE_ALLOWED` no arp, para o
baixo tambem nao virar decoreba de um so padrao a musica inteira."""

_CONTOUR_CATALOG: tuple[tuple[int, ...], ...] = (
    (0,),                 # so raiz — usado em densidade muito baixa
    (0, 2),                # raiz - quinta (indice de grau, resolvido depois)
    (0, 1, 2),             # raiz - terca - quinta
    (0, 2, 3, 2),          # raiz - quinta - oitava - quinta
    (0, 1, 2, 3),          # raiz - terca - quinta - oitava
)
"""Cada entrada e uma sequencia de INDICES sobre os graus do acorde
(0=raiz, 1=terca, 2=quinta, 3=oitava — resolvidos por `_contour_pitch`).
Mais de uma opcao por tamanho garante que a mutacao troque alguma coisa,
nao so repita o mesmo contorno com seed diferente."""

BASS_DENSITY_LOW: int = 3
BASS_DENSITY_HIGH: int = 7
"""Mesma particao (baixa/media/alta) usada em `tools/palette/drums.py`
para o eixo `densidade` — decisao de projeto documentada la, reaplicada
aqui para as duas familias lerem a mesma escala de forma consistente."""

BASS_FALLBACK_STEPS_LOW: tuple[int, ...] = (0,)
BASS_FALLBACK_STEPS_MID: tuple[int, ...] = (0, 8)
BASS_FALLBACK_STEPS_HIGH: tuple[int, ...] = (0, 4, 8, 12)
"""Grade de fallback (sem kick na secao) por bucket de densidade, em
STEPS_PER_BAR=16 — CONVENCAO, mesmo espirito do catalogo de motor/arp."""

MIN_NOTE_DURATION_S: float = 0.001
"""Piso de duracao — mesmo valor usado em tools/palette/rhythmic.py."""


def _energy_axis(section: PlanSection, axis: str, default: int = 5) -> int:
    energy = section.energy or {}
    value = energy.get(axis, default)
    if not isinstance(value, int):
        return default
    return max(1, min(10, value))


def _density_bucket(densidade: int) -> str:
    if densidade <= BASS_DENSITY_LOW:
        return "low"
    if densidade >= BASS_DENSITY_HIGH:
        return "high"
    return "mid"


def _fallback_steps(bucket: str) -> tuple[int, ...]:
    return {
        "low": BASS_FALLBACK_STEPS_LOW,
        "mid": BASS_FALLBACK_STEPS_MID,
        "high": BASS_FALLBACK_STEPS_HIGH,
    }[bucket]


def _kick_onsets_in_bar(analysis: Analysis, bar: BarAnalysis) -> list[float]:
    return sorted(
        t for t in analysis.kick_positions if bar.start <= t < bar.end
    )


BASS_CONNECTOR_FRACTION: float = 0.875
"""CONVENCAO: posicao do 'conector' dentro do bar (87.5% = ultimo 16avo),
uma nota que NAO esta presa a nenhum kick. Sem isso, em secao de baixa
densidade o baixo pode colar 100% dos onsets no kick e o validador de
artificialidade rejeita a track como 'sem conteudo ritmico proprio'
(`duplicates_source`) — a mesma nota tambem cumpre a AC de contorno
proprio, dando ao baixo uma voz que a bateria nao tem."""

BASS_CONNECTOR_MIN_GAP_S: float = 0.05
"""Distancia minima do conector a qualquer kick do bar — se a posicao
convencionada cair perto demais de um kick real, o conector e descartado
em vez de arriscar colidir/duplicar o onset."""


def _connector_onset(bar: BarAnalysis, existing: list[float]) -> float | None:
    """Onset candidato a 'conector' — perto do fim do bar, longe de
    qualquer onset ja escolhido. `None` quando a posicao convencionada
    cai perto demais de um onset existente (kick)."""
    candidate = bar.start + (bar.end - bar.start) * BASS_CONNECTOR_FRACTION
    if any(abs(candidate - t) < BASS_CONNECTOR_MIN_GAP_S for t in existing):
        return None
    return candidate


def _subsample_by_density(onsets: list[float], densidade: int) -> list[float]:
    """Reduz `onsets` (tipicamente ancoras de kick) para uma contagem
    proporcional a `densidade` (1-10). Sempre mantem o primeiro onset
    (ancora principal do bar); densidade alta mantem tudo."""
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


def _contour_for_bucket(bucket: str) -> tuple[tuple[int, ...], ...]:
    if bucket == "low":
        return (_CONTOUR_CATALOG[0], _CONTOUR_CATALOG[1])
    if bucket == "high":
        return (_CONTOUR_CATALOG[3], _CONTOUR_CATALOG[4])
    return (_CONTOUR_CATALOG[1], _CONTOUR_CATALOG[2])


def _chord_tone_pitches(chord: Chord, register: tuple[int, int]) -> list[int]:
    """Raiz/terca/quinta/oitava do acorde dentro do registro — SEMPRE 4
    entradas, indice 0=raiz, 1=terca (ou 5J se power/unknown), 2=quinta,
    3=oitava, na mesma ordem que o catalogo de contorno espera (`_contour_pitch`
    indexa direto por grau, sem compactar a lista).

    Cada tom passa por `_clamp_pitch_to_register` (tools/palette/rhythmic.py),
    que transpoe em oitavas ate encaixar ou devolve `None` quando nenhuma
    oitava cabe — nao a subtracao unica de uma oitava do codigo anterior, que
    podia empurrar um tom para ABAIXO do registro (achado do Codex na PR #69:
    registro `[28, 35]` sobre acorde de Si maior produzia terca em 27, abaixo
    do piso de afinacao E1=28).

    Quando o tom pedido por um grau especifico nao cabe em NENHUMA oitava, so
    aquele grau e substituido — pelo tom, dentre os que couberam, cujo
    candidato bruto (antes do clamp) fica mais perto do candidato bruto do
    grau que faltou. Isso preserva o slot dos outros graus: filtrar a lista
    e reindexar (comportamento anterior, achado do Codex pos-#69/pos-#70)
    deslocava todo grau subsequente ao grau descartado, podendo fazer o
    contorno colapsar para um unico tom repetido mesmo quando outro grau
    pedido (ex.: quinta) cabia perfeitamente no registro.

    Raises:
      ValueError: registro impossivel para este acorde — nenhum dos
        quatro tons (raiz/terca/quinta/oitava) cabe em nenhuma oitava
        dentro de `register`.
    """
    degs = _chord_degrees(chord)  # [0,4,7] maior / [0,3,7] menor / [0,7] power
    lo, _hi = register
    root_base = lo + ((chord.root - lo) % 12)
    third = degs[1] if len(degs) == 3 else degs[-1]
    fifth = degs[-1]
    candidates = [root_base, root_base + third, root_base + fifth, root_base + 12]
    fitted = [_clamp_pitch_to_register(c, register) for c in candidates]
    available = [
        (c, p) for c, p in zip(candidates, fitted, strict=True) if p is not None
    ]
    if not available:
        raise ValueError(
            f"bass register {register} is impossible for chord root "
            f"{chord.root}: no chord tone fits within it"
        )
    resolved: list[int] = []
    for raw, fit in zip(candidates, fitted, strict=True):
        if fit is not None:
            resolved.append(fit)
        else:
            closest = min(available, key=lambda cp: abs(cp[0] - raw))
            resolved.append(closest[1])
    return resolved


def _contour_pitch(
    chord: Chord, register: tuple[int, int], degree_index: int,
) -> int:
    pitches = _chord_tone_pitches(chord, register)
    idx = degree_index % len(pitches)
    return pitches[idx]


def _pick_contour(
    catalog: tuple[tuple[int, ...], ...], rng: random.Random, last: int,
) -> int:
    if len(catalog) == 1:
        return 0
    candidates = [i for i in range(len(catalog)) if i != last]
    return rng.choice(candidates)


def _velocity_for(bucket: str, rng: random.Random) -> int:
    lo, hi = VELOCITY_RANGES[bucket]
    return rng.randint(lo, hi)


def _jitter_s(bucket: str, rng: random.Random) -> float:
    lo, hi = TIMING_JITTER_MS[bucket]
    sign = rng.choice((-1, 1))
    return sign * rng.uniform(lo, hi) / 1000.0


def generate_bass(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "bass",
    register: tuple[int, int] = DEFAULT_BASS_REGISTER,
    layers: int = 1,
    articulation: str = BASS_ARTICULATION_DEFAULT,
    dynamics: dict | None = None,
    seed: int = 0,
) -> list[RhythmicLayer]:
    """Gera a linha de baixo de uma secao, do zero.

    Onsets seguem `analysis.kick_positions` dentro da secao quando
    existem (subamostrados por `energy.densidade`); sem kick no bar, cai
    numa grade deterministica pelo mesmo eixo. Pitches sao sempre tons do
    acorde vigente (raiz/terca/quinta/oitava) dentro de `register`, com o
    grau escolhido por um contorno que muda a cada
    `BASS_CONTOUR_MUTATE_BARS` bars — nunca so a tonica repetida. Notas
    nunca se sobrepoem (monofonico por construcao).

    Args:
      analysis: saida de analise da rodada 1 (acordes + kick_positions).
      section: secao alvo, com `energy.densidade` controlando contagem de
        notas por bar.
      role: 'bass'.
      register: (low, high) MIDI — default cobre E1-G3.
      layers: numero de camadas (tracks); tipicamente 1 (instrumento
        unico), camadas extras dobram a linha com jitter proprio.
      articulation: mapeada para gate (GATE_RATIOS). Default 'tight'.
      dynamics: reservado (nao consumido nesta rodada).
      seed: seed deterministica.

    Raises:
      ValueError: layers < 1 ou role != 'bass'.
    """
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in BASS_ROLES:
        raise ValueError(f"role must be one of {list(BASS_ROLES)}; got {role!r}")

    bars = _bars_in_section(section, analysis)
    if not bars:
        return _empty_rhythmic_layers(layers)

    densidade = _energy_axis(section, "densidade")
    bucket = _density_bucket(densidade)
    catalog = _contour_for_bucket(bucket)
    section_end_s = bars[-1].end
    floor = min(_BASS_DEFAULT_TUNING)
    lo_reg = max(register[0], floor)
    hi_reg = max(register[1], lo_reg)
    reg = (lo_reg, hi_reg)

    result: list[RhythmicLayer] = []
    for layer_idx in range(layers):
        layer_rng = random.Random(seed + (layer_idx + 1) * 1_000_003)
        notes: list[RhythmicNote] = []
        last_contour = -1
        for bar_pos, bar in enumerate(bars):
            if bar.chord is None:
                continue
            if bar_pos % BASS_CONTOUR_MUTATE_BARS == 0:
                last_contour = _pick_contour(catalog, layer_rng, last_contour)
            contour = catalog[last_contour]

            kicks = _kick_onsets_in_bar(analysis, bar)
            connector_index: int | None = None
            if kicks:
                onsets = _subsample_by_density(kicks, densidade)
                anchored = True
                connector = _connector_onset(bar, onsets)
                if connector is not None:
                    connector_index = len(onsets)
                    onsets = [*onsets, connector]
            else:
                step_dur_s = (bar.end - bar.start) / STEPS_PER_BAR
                onsets = [
                    bar.start + step * step_dur_s
                    for step in _fallback_steps(bucket)
                ]
                anchored = False

            for i, onset in enumerate(onsets):
                is_connector = i == connector_index
                degree_index = contour[i % len(contour)]
                pitch = _contour_pitch(bar.chord, reg, degree_index)
                # O conector nao esta preso ao kick — jitter 'normal' e
                # bucket de velocity mais suave o diferenciam como
                # identidade ritmica propria do baixo, nao eco da bateria
                # (AC: 'contorno proprio', e evita o anti-padrao
                # `duplicates_source` quando 100% dos onsets colam no
                # kick).
                jitter_bucket = "normal" if (is_connector or not anchored) else "anchor"
                # Piso em bar.start (nunca abaixo de 0.0) — jitter com
                # sinal nao pode empurrar o onset para o bar ANTERIOR
                # (o pitch foi escolhido pelo acorde deste bar; cruzar a
                # fronteira faz o validador harmonico classificar a nota
                # pelo acorde errado) nem para antes do tempo zero.
                start_s = max(bar.start, 0.0, onset + _jitter_s(jitter_bucket, layer_rng))
                start_s = min(start_s, bar.end - 1e-6)
                if is_connector:
                    bucket_v = BASS_BASE_VELOCITY_BUCKET
                elif anchored:
                    bucket_v = BASS_ANCHOR_VELOCITY_BUCKET
                else:
                    bucket_v = BASS_BASE_VELOCITY_BUCKET
                velocity = _velocity_for(bucket_v, layer_rng)
                notes.append(RhythmicNote(
                    pitch=pitch, velocity=velocity,
                    start_s=start_s, end_s=start_s,  # end_s resolvido abaixo
                ))

        notes.sort(key=lambda n: n.start_s)
        notes = _resolve_durations(
            notes, articulation=articulation,
            fallback_end_s=section_end_s,
            seed=seed + (layer_idx + 1) * 1_000_009,
        )
        result.append(RhythmicLayer(index=layer_idx, notes=tuple(notes)))
    return result


def _resolve_durations(
    notes: list[RhythmicNote],
    *,
    articulation: str,
    fallback_end_s: float,
    seed: int,
) -> list[RhythmicNote]:
    """Preenche `end_s` de cada nota a partir do gap real ate a proxima —
    garante monofonia por construcao (duracao nunca ultrapassa o gap)."""
    if not notes:
        return notes
    art = articulation if articulation in DURATION_ARTICULATIONS else BASS_ARTICULATION_DEFAULT
    engine = DurationEngine(seed=seed)
    result: list[RhythmicNote] = []
    for i, note in enumerate(notes):
        next_start = notes[i + 1].start_s if i + 1 < len(notes) else fallback_end_s
        gap_s = next_start - note.start_s
        if gap_s <= 0:
            # Dois onsets coincidentes (raro — arredondamento de jitter);
            # nota degenerada vira duracao minima em vez de negativa.
            dur_s = MIN_NOTE_DURATION_S
        else:
            gap_ms = gap_s * 1000.0
            dur_ms = engine.compute(
                DurationRequest(articulation=art, gap_ms=gap_ms),
            )
            dur_s = min(gap_s, max(MIN_NOTE_DURATION_S, dur_ms / 1000.0))
        result.append(RhythmicNote(
            pitch=note.pitch, velocity=note.velocity,
            start_s=note.start_s, end_s=note.start_s + dur_s,
        ))
    return result


__all__ = [
    "BASS_ROLES",
    "DEFAULT_BASS_REGISTER",
    "generate_bass",
]
