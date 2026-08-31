"""US-004 (AC-06) — monotonicidade medida do StyleProfile.

Prova, com seeds fixas, que mudar a FAIXA de `gate_ratios` ou
`timing_jitter_ms` no `StyleProfile` produz diferenca OBSERVAVEL e
MONOTONICA no resultado, sem tocar em formula alguma.

Duas frentes:

1. **MIDI de saida via `apply_edits`** — a unica constante do
   `StyleProfile` que `edits.py` propaga e `gate_ratios` (ver nota da
   US-003). O motor sorteia `target_ratio = uniform(lo, hi)` por nota
   estrutural do baixo, entao a metrica observavel e o **desvio-padrao
   das duracoes das notas do baixo em ticks**: faixa mais larga (hi-lo
   maior) => maior variancia entre notas com a MESMA seed. Nao e o
   "density" do motor de tecnicas (que a issue explicita nao existe
   nesse motor de humanizacao por profile); e a densidade estatistica
   da distribuicao de duracoes que a propria faixa impoe.

2. **Motor de microtiming em isolamento** — `MicrotimingEngine` sorteia
   `gauss(0, sigma)` por request; sigma maior em
   `timing_jitter_ms["normal"]` => maior desvio-padrao dos offsets ao
   longo de uma sequencia de requests com a mesma seed. `edits.py` nao
   propaga esse dicionario (usa `ProfileParams.sigma_ms` hardcoded), mas
   o pipeline do render passa por `humanize.py` em outras
   fases; a monotonicidade fica provada onde o profile de fato manda.
"""

from __future__ import annotations

import statistics

import mido
import pretty_midi

from tools.constants import GATE_RATIOS, TIMING_JITTER_MS
from tools.edits import apply_edits
from tools.humanize import MicrotimingEngine, MicrotimingRequest
from tools.plan import PlanEdit
from tools.style_profile import StyleProfile


def _build_bass_grid(tmp_path):
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.append(bass)
    dest = tmp_path / "grid_bass.mid"
    pm.write(str(dest))
    return dest


def _bass_durations(src, profile):
    mid = mido.MidiFile(str(src))
    pm = pretty_midi.PrettyMIDI(str(src))
    edits = [PlanEdit(track="Bass", profile="bass", intensity=1.0)]
    apply_edits(list(mid.tracks), edits, plan_seed=2026, pm=pm,
                style_profile=profile)
    for track in mid.tracks:
        if any(m.is_meta and m.type == "track_name" and m.name == "Bass"
               for m in track):
            abs_tick, open_ons, pairs = 0, {}, []
            for msg in track:
                abs_tick += msg.time
                if msg.is_meta:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    open_ons.setdefault((msg.channel, msg.note), []).append(
                        abs_tick,
                    )
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0
                ):
                    key = (msg.channel, msg.note)
                    if key in open_ons and open_ons[key]:
                        pairs.append(abs_tick - open_ons[key].pop())
            return pairs
    return []


def _profile_with_tight_range(lo, hi):
    ratios = dict(GATE_RATIOS)
    ratios["tight"] = (lo, hi)
    return StyleProfile(gate_ratios=ratios)


def test_gate_range_width_monotonically_increases_duration_spread(tmp_path):
    src = _build_bass_grid(tmp_path)
    # tres faixas com o MESMO centro (0.5) e larguras crescentes:
    # 0.02, 0.20, 0.80. Mesmo centro isola o efeito de largura da faixa
    # do efeito de deslocamento medio, entao a monotonicidade do
    # desvio-padrao das duracoes reflete APENAS o alargamento imposto
    # pelo perfil.
    narrow = _profile_with_tight_range(0.49, 0.51)
    medium = _profile_with_tight_range(0.40, 0.60)
    wide = _profile_with_tight_range(0.10, 0.90)

    dur_narrow = _bass_durations(src, narrow)
    dur_medium = _bass_durations(src, medium)
    dur_wide = _bass_durations(src, wide)

    assert len(dur_narrow) == len(dur_medium) == len(dur_wide) > 8
    stdev_narrow = statistics.pstdev(dur_narrow)
    stdev_medium = statistics.pstdev(dur_medium)
    stdev_wide = statistics.pstdev(dur_wide)

    assert stdev_narrow < stdev_medium < stdev_wide


def test_jitter_sigma_monotonically_increases_offset_spread():
    # Mesma seed, mesma sequencia de requests, so muda o sigma do bucket
    # 'normal' de timing_jitter_ms. gauss(0, sigma) => desvio-padrao da
    # amostra cresce com sigma. Metrica: pstdev dos offsets em ms.
    def _profile(sigma):
        jitter = dict(TIMING_JITTER_MS)
        jitter["normal"] = (0, sigma)
        return StyleProfile(timing_jitter_ms=jitter)

    reqs = [MicrotimingRequest(sync_role="kick_support") for _ in range(400)]

    def _offsets(sigma):
        eng = MicrotimingEngine(seed=2026, profile=_profile(sigma))
        return [eng.compute(r) for r in reqs]

    stdev_tight = statistics.pstdev(_offsets(1))
    stdev_medium = statistics.pstdev(_offsets(5))
    stdev_wide = statistics.pstdev(_offsets(15))

    assert stdev_tight < stdev_medium < stdev_wide
