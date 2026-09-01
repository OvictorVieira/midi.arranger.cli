"""Leitura de secoes do MIDI.

Prioriza marcadores explicitos (meta-evento 'marker'). Sem marcadores, cai
no rotulador heuristico de tools.primitives.label_sections.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

import mido
import pretty_midi

from .primitives import bars_from as _bars_from
from .primitives import fill_bar_features as _fill_bar_features
from .primitives import label_sections as _label_sections

CANONICAL_KINDS = (
    "intro",
    "verse",
    "pre",
    "chorus",
    "breakdown",
    "bridge",
    "interlude",
    "outro",
)


# Ordem importa: grupos mais especificos (ex. 'pre-chorus', 'pre-drop')
# precisam casar antes dos grupos genericos que sua substring tocaria
# ('chorus', 'breakdown') — por isso 'pre' vem primeiro na tupla.
#
# Vocabulario de cue de producao, em ingles e portugues — nao especifico de
# uma musica, mesma convencao usada em DAW/tab/sessao de estudio comum:
#
# - 'pre': tensao crescente antes do proximo trecho — pre-chorus/pre-drop,
#   'build'/'build-up' (subida de energia) e 'riser' (efeito de subida
#   tipico de producao eletronica/hibrida) tem a mesma funcao formal.
# - 'interlude': trecho de transicao ou textura suspensa, nao conteudo
#   principal — 'hold' (pausa/fermata), 'transicao'/'transition',
#   'turnaround' e 'vamp' (groove repetido segurando o lugar) encaixam aqui.
# - 'breakdown': trecho pesado, geralmente riff grave com palm mute —
#   'quebra' (PT) e sinonimo direto; 'drop' aqui segue o sentido de metal/
#   metalcore (breakdown pesado), nao o sentido de EDM/pop (que seria mais
#   proximo de 'chorus') — a persona default deste projeto e produtor de
#   metal moderno, entao esse e o sentido mais provavel quando o genero nao
#   for informado de outra forma.
# - 'chorus': 'hook'/'gancho' (parte mais cantavel) e 'refrain' (sinonimo
#   em ingles) sao a mesma coisa formalmente.
# - 'verse': 'estrofe' e sinonimo em portugues.
# - 'bridge': 'solo' (desvio instrumental) cumpre a mesma funcao formal de
#   contraste dentro da musica.
# - 'outro': 'coda' e 'tag' sao sinonimos de encerramento.
_KIND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pre",       (r"pre[-\s_]?chorus", r"pre[-\s_]?refr[aã]o", r"^pre\b", r"^prechorus\b",
                    r"build[-\s_]?up", r"^build\b", r"riser",
                    r"pre[-\s_]?drop", r"^predrop\b")),
    ("interlude", (r"interlude", r"interl[uú]dio", r"^hold\b",
                    r"transi[cç][aã]o", r"transition", r"turnaround", r"vamp")),
    ("breakdown", (r"breakdown", r"break", r"quebra", r"drop")),
    ("bridge",    (r"bridge", r"ponte", r"^solo\b")),
    ("chorus",    (r"chorus", r"refr[aã]o", r"^hook\b", r"^gancho\b", r"refrain")),
    ("verse",     (r"verse", r"verso", r"estrofe")),
    ("intro",     (r"intro", r"introdu[cç][aã]o")),
    ("outro",     (r"outro", r"ending", r"final", r"^coda\b", r"^tag\b")),
)


def normalize_kind(label: str) -> str | None:
    """Mapeia rotulo livre para o vocabulario canonico do spec.

    Retorna None quando o rotulo nao casa com nenhuma familia conhecida —
    quem chama decide se levanta erro ou trata como generico.
    """
    if not label:
        return None
    s = label.strip().lower()
    for kind, patterns in _KIND_PATTERNS:
        for p in patterns:
            if re.search(p, s):
                return kind
    return None


@dataclass
class Section:
    label: str
    kind: str
    start_tick: int
    end_tick: int
    start_bar: int
    end_bar: int
    source: str  # "marker" ou "inferred"


def _collect_markers(mid: mido.MidiFile) -> list[tuple[int, str]]:
    """Coleta (tick_absoluto, texto) de todos os meta-eventos marker.

    Percorre todas as tracks porque nem sempre o marker esta na track 0.
    """
    markers: list[tuple[int, str]] = []
    for track in mid.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "marker":
                markers.append((t, msg.text))
    markers.sort(key=lambda x: x[0])
    return markers


def _bar_index_at_time(downbeats: list[float], t: float) -> int:
    """Indice do compasso (0-based) que contem o tempo t."""
    idx = bisect.bisect_right(downbeats, t + 1e-6) - 1
    return max(0, idx)


def read_sections(midi_path: str) -> list[Section]:
    """Le secoes de midi_path.

    Estrategia:
      1. Se houver marcadores, cada marker define uma secao. Fronteira final
         de cada secao = inicio da proxima (ou fim do arquivo).
      2. Sem marcadores, delega para tools.primitives.label_sections e
         marca cada secao como source='inferred'.
    """
    mid = mido.MidiFile(midi_path)
    pm = pretty_midi.PrettyMIDI(midi_path)
    markers = _collect_markers(mid)

    downbeats = list(pm.get_downbeats())
    end_time = pm.get_end_time()
    end_tick = int(round(pm.time_to_tick(end_time)))
    if not downbeats:
        downbeats = [0.0]
    total_bars = len(downbeats)

    if markers:
        sections: list[Section] = []
        for i, (tick, text) in enumerate(markers):
            next_tick = markers[i + 1][0] if i + 1 < len(markers) else end_tick
            t_start = pm.tick_to_time(tick)
            t_end = pm.tick_to_time(next_tick)
            start_bar = _bar_index_at_time(downbeats, t_start)
            end_bar = _bar_index_at_time(downbeats, t_end) if next_tick < end_tick else total_bars
            if end_bar <= start_bar:
                end_bar = start_bar + 1
            kind = normalize_kind(text) or ""
            sections.append(Section(
                label=text,
                kind=kind,
                start_tick=tick,
                end_tick=next_tick,
                start_bar=start_bar,
                end_bar=end_bar,
                source="marker",
            ))
        return sections

    # Fallback: rotula por heuristica.
    bars = _bars_from(pm)
    _fill_bar_features(bars, pm)
    _label_sections(bars)

    sections = []
    if not bars:
        return sections
    run_start = 0
    cur_label = bars[0].label
    for i in range(1, len(bars) + 1):
        if i == len(bars) or bars[i].label != cur_label:
            b_start = bars[run_start]
            b_end = bars[i - 1]
            start_tick = int(round(pm.time_to_tick(b_start.start)))
            end_tick_run = int(round(pm.time_to_tick(b_end.end)))
            sections.append(Section(
                label=cur_label,
                kind=cur_label,
                start_tick=start_tick,
                end_tick=end_tick_run,
                start_bar=run_start,
                end_bar=i,
                source="inferred",
            ))
            if i < len(bars):
                cur_label = bars[i].label
                run_start = i
    return sections


def format_section_map(sections: list[Section]) -> str:
    """Tabela textual do mapa de secoes com aviso quando houver inferencia.

    Colunas: secao (label), compasso inicial (1-based), compasso final
    (1-based, inclusivo), duracao em compassos e origem ('marker' ou
    'inferred'). Quando qualquer secao tiver source='inferred', anexa
    aviso pedindo confirmacao. Quando todas vierem de marcador, informa
    que nao ha necessidade de confirmacao.
    """
    if not sections:
        return "Nenhuma secao detectada."

    header = ("Secao", "Compasso inicial", "Compasso final", "Duracao (compassos)", "Origem")
    rows: list[tuple[str, str, str, str, str]] = [header]
    for s in sections:
        duration = max(1, s.end_bar - s.start_bar)
        rows.append((
            s.label or s.kind or "?",
            str(s.start_bar + 1),
            str(s.end_bar),
            str(duration),
            s.source,
        ))

    widths = [max(len(r[c]) for r in rows) for c in range(len(header))]
    lines = []
    for i, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row))
        lines.append(line.rstrip())
        if i == 0:
            lines.append("  ".join("-" * widths[c] for c in range(len(header))))

    table = "\n".join(lines)
    has_inferred = any(s.source == "inferred" for s in sections)
    if has_inferred:
        footer = (
            "\n\nAVISO: uma ou mais secoes foram inferidas por heuristica. "
            "Confirme o mapa antes de arranjar."
        )
    else:
        footer = "\n\nTodas as secoes vieram de marcador; nao ha necessidade de confirmacao."
    return table + footer
