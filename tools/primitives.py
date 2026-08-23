"""Primitivos musicais vendorizados.

Origem: `common/logicpro/humanize.py` do repositório
`OvictorVieira/ai.skills`, commit `f8a65674ea7d9ef85a02f350ad1f1e7a064f118c`.

Cópia fiel: o comportamento é idêntico ao da origem. Nomes com prefixo `_`
lá viraram API pública aqui (sem prefixo, com type hints e docstrings) —
essa é a única mudança em relação ao original.
"""

from __future__ import annotations

from dataclasses import dataclass

import pretty_midi

# --- constantes de percussão ---------------------------------------------

KICKS: set[int] = {35, 36}
SNARES: set[int] = {37, 38, 40}
HATS_CLOSED: set[int] = {42, 44}
HATS_OPEN: set[int] = {46}
TOMS: set[int] = {41, 43, 45, 47, 48, 50}
CYMBALS: set[int] = {49, 51, 52, 53, 55, 57, 59}


# --- estimador de tom ----------------------------------------------------

def _estimate_key(pm: pretty_midi.PrettyMIDI) -> str:
    """Krumhansl-Schmuckler-ish key finder from pitch-class histogram."""
    hist = [0.0] * 12
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            hist[n.pitch % 12] += (n.end - n.start)
    if sum(hist) == 0:
        return ""

    major = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
             2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
    minor = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
             2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
    names = ["C", "C#", "D", "D#", "E", "F",
             "F#", "G", "G#", "A", "A#", "B"]

    best = ("", -1.0)
    for i in range(12):
        for prof, tag in ((major, "major"), (minor, "minor")):
            rot = prof[-i:] + prof[:-i] if i else prof
            score = sum(hist[j] * rot[j] for j in range(12))
            if score > best[1]:
                best = (f"{names[i]} {tag}", score)
    return best[0]


def key_root(pm: pretty_midi.PrettyMIDI) -> int:
    """Return the pitch class (0..11) of the estimated key root.

    C=0, C#=1, ..., B=11. Returns 0 when no key can be estimated
    (e.g. the file contains only drums)."""
    est = _estimate_key(pm)
    names = ["C", "C#", "D", "D#", "E", "F",
             "F#", "G", "G#", "A", "A#", "B"]
    if not est:
        return 0
    root = est.split()[0]
    return names.index(root)


def chord_root(pcs: list[int]) -> int:
    """Pick a root from a set of pitch classes: prefer the lowest interval
    pattern that looks like a triad root (i.e. the pc with a 3rd and 5th
    above it in the set). Falls back to the lowest pc."""
    if not pcs:
        return 0
    for pc in pcs:
        has_third = ((pc + 3) % 12 in pcs) or ((pc + 4) % 12 in pcs)
        has_fifth = (pc + 7) % 12 in pcs
        if has_third and has_fifth:
            return pc
    return pcs[0]


# --- bars ---------------------------------------------------------------

@dataclass
class Bar:
    """Uma barra do arranjo, com contagens agregadas por instrumento."""

    idx: int
    start: float
    end: float
    kicks: int = 0
    snares: int = 0
    hats: int = 0
    cymbals: int = 0
    toms: int = 0
    guitar_notes: int = 0
    guitar_avg_pitch: float = 0.0
    guitar_min_pitch: int = 127
    bass_notes: int = 0
    label: str = ""


def bars_from(pm: pretty_midi.PrettyMIDI) -> list[Bar]:
    """Split the song into bars using the MIDI's own downbeat map.

    Falls back to a synthetic 4/4 grid at the estimated tempo if the file
    has no time signature (some Songsterr exports)."""
    downbeats = list(pm.get_downbeats())
    end = pm.get_end_time()
    if not downbeats or downbeats[-1] < end - 0.5:
        tempo = pm.estimate_tempo() or 120
        bar_len = 4 * 60.0 / tempo
        if not downbeats:
            downbeats = [i * bar_len for i in range(int(end / bar_len) + 1)]
        else:
            last = downbeats[-1]
            while last + bar_len < end:
                last += bar_len
                downbeats.append(last)
    edges = downbeats + [end]
    return [Bar(idx=i, start=edges[i], end=edges[i + 1])
            for i in range(len(edges) - 1)]


def fill_bar_features(bars: list[Bar], pm: pretty_midi.PrettyMIDI) -> None:
    """Aggregate per-instrument note counts into each bar."""
    def bar_of(t: float) -> int:
        for b in bars:
            if b.start <= t < b.end:
                return b.idx
        return len(bars) - 1

    for inst in pm.instruments:
        name = (inst.name or "").lower()
        if inst.is_drum:
            for n in inst.notes:
                b = bars[bar_of(n.start)]
                if n.pitch in KICKS:
                    b.kicks += 1
                elif n.pitch in SNARES:
                    b.snares += 1
                elif n.pitch in HATS_CLOSED or n.pitch in HATS_OPEN:
                    b.hats += 1
                elif n.pitch in TOMS:
                    b.toms += 1
                elif n.pitch in CYMBALS:
                    b.cymbals += 1
        else:
            is_bass = "bass" in name or (
                inst.notes and
                sum(n.pitch for n in inst.notes) / len(inst.notes) < 40
                and "guitar" not in name
            )
            for n in inst.notes:
                b = bars[bar_of(n.start)]
                if is_bass:
                    b.bass_notes += 1
                else:
                    b.guitar_notes += 1
                    b.guitar_min_pitch = min(b.guitar_min_pitch, n.pitch)

    for b in bars:
        gnotes = [n for i in pm.instruments if not i.is_drum
                  and "bass" not in (i.name or "").lower()
                  for n in i.notes
                  if b.start <= n.start < b.end]
        b.guitar_avg_pitch = (sum(n.pitch for n in gnotes) / len(gnotes)
                              if gnotes else 0)
        if b.guitar_min_pitch == 127:
            b.guitar_min_pitch = 0


def label_sections(bars: list[Bar]) -> None:
    """Assign each bar to a section based on activity + register.

    Heuristic priority (checked per bar, in order):
      - Empty bars at ends → intro/outro.
      - Very low guitar register + cymbal-heavy + few notes → breakdown.
      - High guitar density + kicks + cymbals → chorus.
      - Rising density vs prior bar + no cymbal yet → pre.
      - Everything else → verse.

    Then smooth: single-bar sections between two identical labels get
    merged (a lone 'verse' between two 'chorus' bars becomes chorus).
    """
    if not bars:
        return
    total = len(bars)
    guitar_notes = [b.guitar_notes for b in bars]
    max_gn = max(guitar_notes) or 1

    for i, b in enumerate(bars):
        total_activity = b.kicks + b.snares + b.hats + b.cymbals + b.guitar_notes
        if total_activity < 2:
            b.label = "intro" if i < total / 2 else "outro"
            continue

        low_register = (0 < b.guitar_min_pitch <= 42)
        chuggy = b.guitar_notes < max_gn * 0.4
        heavy_cymbals = b.cymbals >= 2
        dense_guitar = b.guitar_notes >= max_gn * 0.65

        if low_register and (chuggy or heavy_cymbals) and b.kicks >= 3:
            b.label = "breakdown"
        elif dense_guitar and b.cymbals >= 1 and b.kicks >= 2:
            b.label = "chorus"
        elif (i > 0 and guitar_notes[i] > guitar_notes[i - 1] * 1.3
              and b.cymbals == 0):
            b.label = "pre"
        else:
            b.label = "verse"

    # Smoothing pass: kill single-bar islands.
    for i in range(1, len(bars) - 1):
        if (bars[i - 1].label == bars[i + 1].label
                and bars[i].label != bars[i - 1].label):
            bars[i].label = bars[i - 1].label

    # Second pass: collapse very short 'pre' runs into the following chorus.
    i = 0
    while i < len(bars):
        j = i
        while j < len(bars) and bars[j].label == bars[i].label:
            j += 1
        run_len = j - i
        if bars[i].label == "pre" and run_len == 1 and j < len(bars) \
                and bars[j].label == "chorus":
            bars[i].label = "chorus"
        i = j

    # Third pass: enforce minimum section length (4 bars — songs breathe
    # in 4/8/16-bar phrases, not 1-bar islands). Absorb any run below the
    # minimum into the LARGER of its two neighbours. Repeat until stable.
    min_bars = 4
    for _ in range(50):
        runs = []
        i = 0
        while i < len(bars):
            j = i
            while j < len(bars) and bars[j].label == bars[i].label:
                j += 1
            runs.append((i, j, bars[i].label))
            i = j

        target = None
        for k, (s, e, _lbl) in enumerate(runs):
            if (e - s) < min_bars:
                prev = runs[k - 1] if k > 0 else None
                nxt = runs[k + 1] if k + 1 < len(runs) else None
                if prev and nxt:
                    winner = prev[2] if (prev[1] - prev[0]) >= (nxt[1] - nxt[0]) else nxt[2]
                elif prev:
                    winner = prev[2]
                elif nxt:
                    winner = nxt[2]
                else:
                    continue
                target = (s, e, winner)
                break

        if target is None:
            break
        s, e, winner = target
        for k in range(s, e):
            bars[k].label = winner


def chordal_bars(rgtr, bar_bounds, *, chord_size: int = 3,
                 coverage: float = 0.15) -> set[int]:
    """Return the set of bar indices where the rhythm guitar plays a real
    chord (>= `chord_size` distinct pitches simultaneously) for at least
    `coverage` fraction of the bar. This is the "full-chord" gate the
    section labeler misses — post-hc choruses aren't the only chord-rich
    sections, and a bar that sounds like a refrão has to trigger the pad
    layers even if it's tagged 'verse' or 'breakdown'."""
    out: set[int] = set()
    for i, (bs, be) in enumerate(bar_bounds):
        # collect all note-onset/offset times in this bar
        in_bar = [n for n in rgtr.notes if n.start < be and n.end > bs]
        if len(in_bar) < chord_size:
            continue
        events = []
        for n in in_bar:
            events.append((max(n.start, bs), +1))
            events.append((min(n.end, be), -1))
        events.sort()
        active = 0
        chord_duration = 0.0
        prev_t = bs
        for t, delta in events:
            if active >= chord_size:
                chord_duration += t - prev_t
            active += delta
            prev_t = t
        if chord_duration >= (be - bs) * coverage:
            out.add(i)
    return out
