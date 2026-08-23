"""Constantes numericas da base de conhecimento.

Todos os valores vem da secao 6 de tasks/midi-arranger-spec.md, cuja fonte e
knoledgebase/base_conhecimento_midi_realista_modo_bass.md.
"""

VELOCITY_RANGES = {
    "ghost":     (20, 50),
    "tied_soft": (50, 75),
    "mid":       (70, 90),
    "normal":    (82, 105),
    "accent":    (102, 118),
    "extreme":   (115, 125),
}

GATE_RATIOS = {
    "ghost":     (0.15, 0.35),
    "staccato":  (0.35, 0.65),
    "tight":     (0.60, 0.82),
    "open":      (0.78, 0.95),
    "sustained": (0.90, 1.00),
}

TIMING_JITTER_MS = {
    "anchor":       (0, 3),
    "normal":       (3, 8),
    "intermediate": (5, 12),
    "fill":         (0, 15),
}

LEGATO_OVERLAP_MS = (5, 25)
SLIDE_OVERLAP_MS = (10, 50)

SYNC_ROLES = (
    "exact_anchor",
    "kick_support",
    "guitar_unison",
    "anticipation",
    "response",
    "sustain_through",
    "ghost_fill",
)

REGISTER_BANDS = {
    "sub":  (0, 35),
    "low":  (36, 47),
    "mid":  (48, 71),
    "high": (72, 127),
}

TRIGGER_NOTE = 60
