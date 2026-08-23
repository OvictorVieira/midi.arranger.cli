"""Integracao gerador x validadores (US-001).

Rede de seguranca da paleta. Para cada papel gerado (pad, piano, rhodes,
strings, drone, arp, rhythmic_machine, motor, shadow) monta um plano minimo
valido, renderiza sobre um MIDI sintetico e passa a saida por TODOS os
validadores (harmonia, placement, colisao, persona, artificialidade).

Erro em qualquer validador = falha do papel. Roles com anti-padroes ainda
nao corrigidos entram marcados como `xfail(strict=True)` apontando a story
que os endereca: quando a US correspondente corrigir o gerador, o xfail vira
`XPASS` e a story seguinte tem que remover o marker. US-008 fecha exigindo
o teste inteiro verde sem xfail nem skip.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pretty_midi
import pytest

from tools.plan import (
    ArrangementPlan,
    Element,
    PlanSection,
    SourceMidi,
)
from tools.render import render
from tools.validators.harmony import has_errors as harmony_has_errors
from tools.validators.persona import has_errors as persona_has_errors
from tools.validators.placement import has_errors as placement_has_errors

# --- fixtures ---------------------------------------------------------------

def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_base(tmp_path: Path, *, with_guitar: bool, name: str) -> Path:
    """Source com piano + bass e, opcionalmente, guitarra syncopada.

    Guitarra syncopada (offbeats "e" e "a", nunca no downbeat) para que
    tracks que ATACAM no downbeat (pad/piano/rhodes/etc) nao caiam
    acidentalmente na janela do `duplicates_source`. Shadow depende de
    frases de guitarra para dobrar, entao usa esse source; o restante da
    paleta usa o source sem guitarra."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.extend([piano, bass])
    if with_guitar:
        g1 = pretty_midi.Instrument(program=27, name="Guitar 1")
        # Frase de 4 notas por compasso, mas nos offbeats — nao competem
        # com o downbeat onde pad/piano/rhodes/drone/motor batem.
        offsets = (0.25, 0.5, 0.75, 1.25)
        for bar in range(8):
            start = bar * bar_len
            for i, off in enumerate(offsets):
                g1.notes.append(pretty_midi.Note(
                    velocity=100, pitch=40 + i,
                    start=start + off * beat_len,
                    end=start + (off + 0.4) * beat_len,
                ))
        pm.instruments.append(g1)
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _source_default(tmp_path: Path) -> Path:
    return _source_base(tmp_path, with_guitar=False, name="source.mid")


def _source_with_guitar(tmp_path: Path) -> Path:
    return _source_base(tmp_path, with_guitar=True, name="source_guit.mid")


_ROLES_REQUIRING_GUITAR: frozenset[str] = frozenset({"shadow"})


# --- catalogo de papeis -----------------------------------------------------

_INSTRUMENT_OMNI = {
    "plugin": "Omnisphere", "preset": "Palette Patch", "verified": True,
}

# Cada entrada define o minimo suficiente para o renderer aceitar o elemento.
# `route` bate com a paleta persona (ver ROUTE_PALETTES) para que o
# _check_route_palette nao alarme.
_ROLE_SPECS: dict[str, dict] = {
    "pad": {
        "route": "cinematica_emocional",
        "register": [48, 71],
        "articulation": "sustained",
        "harmony": "follow_chords",
        "sync_role": "sustain_through",
        "pattern": None,
    },
    "piano": {
        "route": "cinematica_emocional",
        "register": [60, 84],
        "articulation": "tight",
        "harmony": "follow_chords",
        "sync_role": "sustain_through",
        "pattern": None,
    },
    "rhodes": {
        "route": "cinematica_emocional",
        "register": [60, 84],
        "articulation": "open",
        "harmony": "follow_chords",
        "sync_role": "sustain_through",
        "pattern": None,
    },
    "strings": {
        "route": "cinematica_emocional",
        "register": [48, 84],
        "articulation": "sustained",
        "harmony": "follow_chords",
        "sync_role": "sustain_through",
        "pattern": None,
        "layers": 3,
    },
    "drone": {
        "route": "cinematica_emocional",
        "register": [36, 47],
        "articulation": "sustained",
        "harmony": "pedal",
        "sync_role": "sustain_through",
        "pattern": {
            "pedal": True, "pedal_pitch": "tonic",
            "filter_cycle_bars": 4, "modulation_bars": 16,
        },
    },
    "arp": {
        "route": "hook_eletronico_pesado",
        "register": [72, 96],
        "articulation": "staccato",
        "harmony": "follow_chords",
        "sync_role": "response",
        "pattern": {"pattern_bars": 1, "interlock": False},
    },
    "rhythmic_machine": {
        "route": "hook_eletronico_pesado",
        "register": [72, 96],
        "articulation": "staccato",
        "harmony": "follow_chords",
        "sync_role": "response",
        "pattern": {"pattern_bars": 1, "interlock": False},
    },
    "motor": {
        "route": "hook_eletronico_pesado",
        "register": [48, 71],
        "articulation": "staccato",
        "harmony": "follow_chords",
        "sync_role": "sustain_through",
        "pattern": {"subdivision": "sixteenth"},
    },
    "shadow": {
        "route": "hook_eletronico_pesado",
        "register": [48, 84],
        "articulation": "sustained",
        "harmony": "free",
        "sync_role": "response",
        "pattern": {"octave_shift": 12, "tail_notes": 2},
    },
}


def _build_plan(source: Path, role: str) -> ArrangementPlan:
    spec = _ROLE_SPECS[role]
    element = Element(
        id=f"{role}_main",
        role=role,
        sections=["MAIN"],
        register=list(spec["register"]),
        layers=spec.get("layers", 1),
        sync_role=spec["sync_role"],
        articulation=spec["articulation"],
        harmony=spec["harmony"],
        pattern=spec["pattern"],
        dynamics={"shape": "hold"},
        instrument=dict(_INSTRUMENT_OMNI),
        rationale=f"Integration coverage for {role}.",
    )
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(
            path=str(source), sha256=_sha256_bytes(source),
        ),
        route=spec["route"],
        sections=[
            PlanSection(
                label="MAIN",
                kind="chorus",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={
                    "densidade": 5, "impacto": 5, "largura": 5,
                    "altura": 5, "instabilidade": 3,
                },
            ),
        ],
        elements=[element],
    )


# --- teste parametrizado ----------------------------------------------------

_ROLES: tuple[str, ...] = tuple(_ROLE_SPECS.keys())

# Roles com anti-padroes ainda ativos. A key e o role, o value e a story
# que endereca a correcao. `strict=True`: quando o fix landar, o teste
# passa e o xfail vira `XPASS` — a story tem que remover o marker aqui
# alem de aplicar o fix. Zerar este dict e requisito da US-008.
_KNOWN_ARTIFICE_XFAILS: dict[str, str] = {
    "shadow": "US-008 (duplicates_source: decidir isencao musicalmente justificada)",
}


def _param(role: str):
    marks = []
    reason = _KNOWN_ARTIFICE_XFAILS.get(role)
    if reason is not None:
        marks.append(pytest.mark.xfail(strict=True, reason=reason))
    return pytest.param(role, marks=marks, id=role)


@pytest.mark.parametrize("role", [_param(r) for r in _ROLES])
def test_palette_role_passes_all_validators(tmp_path, role):
    """Cada papel renderiza e sai limpo em TODOS os validadores.

    US-001 documenta em `progress.txt` quais papeis falham hoje. Cada
    papel listado em `_KNOWN_ARTIFICE_XFAILS` esta esperando um fix; ao
    corrigir, remover o entry aqui na mesma story. US-008 fecha exigindo
    o dict vazio."""
    src = (
        _source_with_guitar(tmp_path)
        if role in _ROLES_REQUIRING_GUITAR
        else _source_default(tmp_path)
    )
    plan = _build_plan(src, role)
    out = tmp_path / f"{role}_out.mid"
    report = render(plan, out)

    problems: list[str] = []
    if harmony_has_errors(report.harmony_issues):
        problems.extend(
            f"harmony: {i.message}" for i in report.harmony_issues
            if i.severity == "error"
        )
    if placement_has_errors(report.placement_issues):
        problems.extend(
            f"placement: {i.message}" for i in report.placement_issues
            if i.severity == "error"
        )
    if persona_has_errors(report.persona_issues):
        problems.extend(
            f"persona: {i.message}" for i in report.persona_issues
            if i.severity == "error"
        )
    if report.artifice_issues:
        problems.extend(
            f"artifice[{i.pattern}]: {i.message}"
            for i in report.artifice_issues
        )
    if report.collision.relocations:
        problems.extend(
            f"collision: {r.element_id} relocated "
            f"{r.from_register}->{r.to_register}"
            for r in report.collision.relocations
        )

    assert not problems, (
        f"role {role!r} failed integration: "
        + "\n  - ".join([""] + problems)
    )


# --- guarda contra regressao silenciosa -------------------------------------

def test_all_target_roles_are_covered():
    """Se um papel novo entrar em SUPPORTED_ROLES, ele PRECISA aparecer aqui.
    Sem isso, um role adicionado depois passaria sem ser exercitado pelos
    validadores — exatamente a lacuna que originou esta rede de seguranca."""
    from tools.render import SUPPORTED_ROLES
    missing = sorted(set(SUPPORTED_ROLES) - set(_ROLES) - {"choir"})
    # 'choir' compartilha gerador com 'strings' (STRINGS_ROLES); cobrir
    # ambos duplicaria o mesmo caminho. O teste dedicado de choir vive em
    # tests/test_strings.py.
    assert not missing, (
        f"palette integration is missing roles: {missing}. Add specs to "
        f"_ROLE_SPECS or explicitly justify the skip here."
    )
