"""Issue #11 — StyleProfile plumbing nos GERADORES DE PALETA
(`tools/palette/*.py`).

`tools/humanize.py` e `tools/edits.py` ja aceitavam `StyleProfile` opcional
(US-002/US-003, ver `tests/test_style_profile_humanize.py` e
`tests/test_style_profile_monotonicity.py`). Este arquivo cobre a metade que
faltava: os GERADORES DE ELEMENTO (`generate_pad`, `generate_drums`,
`generate_bass`, `generate_keyboard`, `generate_strings`, `generate_drone`,
`generate_rhythmic`, `generate_motor`, `generate_shadow`, `generate_sub`,
`generate_sub_drop`) agora tambem aceitam `profile: StyleProfile | None`
keyword-only no fim da assinatura, com o MESMO contrato de retrocompatibilidade
que `humanize.py`/`edits.py` ja tinham: sem `profile`, cai em
`StyleProfile.default()` e o chamador antigo continua byte-identico.

Cobre:
- AC-05 (aplicacao efetiva): `timing_jitter_ms` e `velocity_ranges`
  customizados no `StyleProfile` mudam o MIDI gerado de verdade — nao sao
  aceitos e ignorados.
- AC-06 (monotonicidade): faixa de jitter mais larga produz desvio-padrao
  de onset maior, com a MESMA seed.
- AC-21 (rastreabilidade de runtime): `tools.rng.assert_traceable_seed` e a
  barreira que os geradores chamam na entrada; prova que ela dispara de
  verdade quando a seed nao e uma origem declarada (int explicito), tanto
  isolada quanto through um gerador real.
"""

from __future__ import annotations

import statistics

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.constants import TIMING_JITTER_MS, VELOCITY_RANGES
from tools.palette.bass import generate_bass
from tools.palette.drums import GM_HAT_CLOSED, generate_drums
from tools.palette.harmonic import PAD_BASE_VELOCITY_BUCKET, generate_pad
from tools.plan import PlanSection
from tools.rng import assert_traceable_seed
from tools.style_profile import StyleProfile

BAR_S = 2.0


def _bar(index: int, chord: Chord | None) -> BarAnalysis:
    return BarAnalysis(index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=chord)


def _analysis(n_bars: int = 8, chord: Chord | None = None) -> Analysis:
    chord = chord or Chord(root=0, quality="major")
    return Analysis(
        key_root=0,
        bars=[_bar(i, chord) for i in range(n_bars)],
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(*, start_bar=0, end_bar=8, **energy_overrides) -> PlanSection:
    energy = {"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 5}
    energy.update(energy_overrides)
    return PlanSection(
        label="MAIN", kind="verse", start_bar=start_bar, end_bar=end_bar,
        source="marker", energy=energy,
    )


# --- AC-05: perfil comanda o resultado, nao e aceito-e-ignorado -------------

def test_generate_pad_uses_profile_velocity_range():
    """Bucket degenerado (lo == hi) no perfil forca uma velocity EXATA —
    prova mais forte de que o parametro comanda o resultado (nao so
    influencia estatisticamente)."""
    profile = StyleProfile(
        velocity_ranges={**dict(VELOCITY_RANGES), PAD_BASE_VELOCITY_BUCKET: (30, 30)},
    )
    layers = generate_pad(
        _analysis(), _section(),
        layers=1, dynamics={"shape": "hold"}, seed=7, profile=profile,
    )
    notes = layers[0].notes
    assert notes, "fixture deve gerar pelo menos uma nota"
    assert all(n.velocity == 30 for n in notes)

    # Regressao: sem profile, a velocity NAO e 30 (prova que o teste acima
    # de fato exercitou o override, nao um valor que ja sairia default).
    default_layers = generate_pad(
        _analysis(), _section(), layers=1, dynamics={"shape": "hold"}, seed=7,
    )
    assert not all(n.velocity == 30 for n in default_layers[0].notes)


def test_generate_drums_uses_profile_velocity_range():
    profile = StyleProfile(velocity_ranges={**dict(VELOCITY_RANGES), "accent": (100, 100)})
    layers = generate_drums(_analysis(), _section(), layers=1, seed=3, profile=profile)
    notes = layers[0].notes
    kicks = [n for n in notes if n.velocity == 100]
    assert kicks, "kick/downbeat usa bucket 'accent' — deve refletir o override"


def test_generate_bass_timing_bias_moves_measured_offset():
    """AC-05 tabela literal do objetivo.md: um vies de timing declarado no
    perfil produz um offset medio MEDIDO no MIDI gerado, dentro de
    tolerancia — nao apenas aceito e ignorado.

    `bass._jitter_s` sorteia sinal +-1 e amplitude uniform(lo, hi); um
    bucket degenerado (lo == hi == X) faz o offset absoluto de toda nota
    cair EXATAMENTE em X ms (a menos do clamp de piso em bar.start). A
    fixture nao declara `kick_positions`, entao o baixo cai no fallback
    (sem ancora de kick) — todo onset usa o bucket 'normal', nao 'anchor'
    (ver `jitter_bucket = "normal" if (is_connector or not anchored) ...`
    em `tools/palette/bass.py`)."""
    bias_ms = 8.0
    profile = StyleProfile(
        timing_jitter_ms={**dict(TIMING_JITTER_MS), "normal": (bias_ms, bias_ms)},
    )
    analysis = _analysis(n_bars=8)
    section = _section(start_bar=1, end_bar=7)  # evita o piso do bar 0
    layers = generate_bass(analysis, section, layers=1, seed=11, profile=profile)
    notes = layers[0].notes
    assert notes

    # Sem kick na secao (fixture nao declara `kick_positions`), o baixo
    # cai na grade de fallback (steps de 16avos) — o onset ideal de cada
    # nota e o step de 16avos mais proximo. O offset medido e
    # |start_s - step_mais_proximo|, e deve bater bias_ms/1000 dentro de
    # folga de arredondamento de ponto flutuante.
    # Step 0 de cada bar tem um piso explicito em `bar.start` (o gerador
    # nunca deixa o onset vazar para o bar anterior) — quando o sinal do
    # jitter sorteia negativo NESSE step, o clamp zera o offset. Isso e um
    # efeito de borda do piso, nao do perfil; medir so os steps que nao
    # colam no piso (offset != 0 exato) isola o que o perfil de fato
    # controla.
    step_dur_s = BAR_S / 16
    offsets_ms = [
        abs(n.start_s - round(n.start_s / step_dur_s) * step_dur_s) * 1000.0
        for n in notes
    ]
    offsets_ms = [o for o in offsets_ms if o > 1e-6]
    assert offsets_ms, "todas as notas colaram no piso — fixture ruim"
    mean_offset = statistics.mean(offsets_ms)
    assert mean_offset == pytest.approx(bias_ms, abs=1.0)


# --- AC-06: densidade/largura de faixa -> monotonicidade medida ------------

def test_generate_drums_wider_jitter_increases_measured_spread_monotonically():
    """Mesma seed, tres perfis com `timing_jitter_ms['normal']` cada vez
    mais largo (0 a densidade crescente) — desvio-padrao do offset dos
    hats em relacao ao grid ideal cresce monotonicamente."""

    def _measure(width_ms: float) -> float:
        profile = StyleProfile(
            timing_jitter_ms={**dict(TIMING_JITTER_MS), "normal": (0.0, width_ms)},
        )
        layers = generate_drums(_analysis(), _section(), layers=1, seed=42, profile=profile)
        hats = [n for n in layers[0].notes if n.pitch == GM_HAT_CLOSED]
        assert hats
        step_dur_s = BAR_S / 16
        offsets = []
        for n in hats:
            nearest_step = round(n.start_s / step_dur_s) * step_dur_s
            offsets.append(n.start_s - nearest_step)
        return statistics.pstdev(offsets)

    spreads = [_measure(w) for w in (0.5, 4.0, 12.0)]
    assert spreads[0] < spreads[1] < spreads[2], spreads


# --- AC-21: asserção de runtime de rastreabilidade de seed ------------------

def test_assert_traceable_seed_accepts_explicit_int():
    assert assert_traceable_seed(42, source="test") == 42
    assert assert_traceable_seed(0, source="test") == 0


@pytest.mark.parametrize("bad_seed", [None, "42", 3.14, [1], object()])
def test_assert_traceable_seed_rejects_non_int(bad_seed):
    with pytest.raises(AssertionError, match="AC-21"):
        assert_traceable_seed(bad_seed, source="test")


def test_assert_traceable_seed_rejects_bool():
    """bool e subclasse de int em Python — isinstance(True, int) e True.
    Sem a exclusao explicita, `seed=True`/`seed=False` passaria pela
    barreira como se fosse uma seed real, escondendo um bug de chamador
    (ex.: um `if`/flag confundido com a seed) atras de uma seed valida
    por acidente (True == 1)."""
    with pytest.raises(AssertionError, match="AC-21"):
        assert_traceable_seed(True, source="test")


def test_assert_traceable_seed_requires_source():
    with pytest.raises(AssertionError, match="AC-21"):
        assert_traceable_seed(1, source="")


def test_generate_pad_raises_when_seed_is_not_traceable():
    """A barreira dispara DENTRO do gerador real, antes de qualquer
    `random.Random` ser construido — nao so em teste isolado da funcao."""
    with pytest.raises(AssertionError, match="AC-21"):
        generate_pad(_analysis(), _section(), layers=1, seed="not-a-seed")  # type: ignore[arg-type]


def test_generate_bass_raises_when_seed_is_not_traceable():
    with pytest.raises(AssertionError, match="AC-21"):
        generate_bass(_analysis(), _section(), layers=1, seed=None)  # type: ignore[arg-type]


def test_generate_drums_raises_when_seed_is_not_traceable():
    with pytest.raises(AssertionError, match="AC-21"):
        generate_drums(_analysis(), _section(), layers=1, seed=1.5)  # type: ignore[arg-type]


# --- Regressao: chamador antigo (sem profile) continua byte-identico -------

def test_generate_pad_without_profile_matches_default_profile():
    a, s = _analysis(), _section()
    without = generate_pad(a, s, layers=2, dynamics={"shape": "hold"}, seed=99)
    with_default = generate_pad(
        a, s, layers=2, dynamics={"shape": "hold"}, seed=99, profile=StyleProfile.default(),
    )
    assert without == with_default


def test_generate_drums_without_profile_matches_default_profile():
    a, s = _analysis(), _section()
    without = generate_drums(a, s, layers=1, seed=99)
    with_default = generate_drums(a, s, layers=1, seed=99, profile=StyleProfile.default())
    assert without == with_default


def test_generate_bass_without_profile_matches_default_profile():
    a, s = _analysis(), _section()
    without = generate_bass(a, s, layers=1, seed=99)
    with_default = generate_bass(a, s, layers=1, seed=99, profile=StyleProfile.default())
    assert without == with_default
