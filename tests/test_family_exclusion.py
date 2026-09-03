"""Testes da issue #17 — veto de criacao de familia ausente.

Cobrem as duas camadas descritas em `docs/arquitetura.md` para
`brief.excluded_families`:

- `tools/plan.py::validate` recusa `plan.elements[]` cuja `role` mapeie
  para uma familia vetada no brief, mesmo quando o `rationale` do
  elemento declara que a IA julgou a familia ausente.
- `tools/render.py::render` repete a mesma barreira para um
  `ArrangementPlan` construido em memoria (sem passar por `plan.load`).

Tambem cobrem o caminho positivo (AC-03/AC-04 de docs/objetivo.md): uma
familia que falta no MIDI de origem e criada dentro do campo harmonico
quando NAO ha veto, e uma familia que a IA nao pediu simplesmente nao
aparece na saida.
"""

from __future__ import annotations

import json
from pathlib import Path

import mido
import pretty_midi
import pytest

from tests.test_plan import _valid_plan
from tests.test_render import _build_plan, _build_synthetic_source
from tools.brief_ref import brief_sha256
from tools.plan import BriefRef, Element, PlanValidationError, validate
from tools.render import RenderError, render
from tools.validators.harmony import has_errors as harmony_has_errors
from tools.validators.placement import has_errors as placement_has_errors

# --- fixtures ----------------------------------------------------------


def _write_brief_excluding(
    tmp_path: Path, families: list[str],
) -> tuple[Path, str]:
    """Grava um brief minimo com `excluded_families` no valor dado.

    Mesma fronteira de `tests/test_plan.py::_write_brief` — so os campos
    que `plan.validate` de fato le entram no arquivo."""
    style_dict = {
        family: {"authorized_techniques": []}
        for family in ("bass", "drums", "guitar", "keys")
    }
    brief = {"style": style_dict, "excluded_families": families}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return brief_path, brief_sha256(brief_path)


def _write_brief_with_raw_excluded_families(
    tmp_path: Path, raw_value: object,
) -> tuple[Path, str]:
    """Mesmo brief minimo de `_write_brief_excluding`, mas grava
    `excluded_families` com o valor bruto dado direto no JSON — para
    testar entradas malformadas que `brief.validate`/`brief_schema.py`
    ja bloqueariam, mas que um brief editado a mao (ou nunca validado)
    pode carregar quando lido direto por `plan.validate`/`render`."""
    style_dict = {
        family: {"authorized_techniques": []}
        for family in ("bass", "drums", "guitar", "keys")
    }
    brief = {"style": style_dict, "excluded_families": raw_value}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return brief_path, brief_sha256(brief_path)


def _build_source_without_bass(
    tmp_path: Path, name: str = "source_no_bass.mid",
) -> Path:
    """MIDI: 8 compassos em 4/4 a 120bpm, so piano com triade C — sem
    nenhuma track/instrumento de baixo. Espelha
    `tests.test_render._build_synthetic_source` menos a track `Bass`."""
    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        # onsets por batida (mesmo motivo do baixo em corcheias na fixture
        # com baixo): `pretty_midi.estimate_tempo` precisa de onsets
        # distintos o bastante para nao explodir com "fewer than two notes".
        for beat in range(4):
            piano.notes.append(pretty_midi.Note(
                velocity=60, pitch=72, start=start + beat * beat_len,
                end=start + beat * beat_len + beat_len * 0.5,
            ))
    pm.instruments.append(piano)
    dest = tmp_path / name
    pm.write(str(dest))
    return dest


def _bass_gap_fill_element() -> Element:
    """Elemento de baixo com `rationale` explicito de que a IA julgou a
    familia ausente no MIDI de origem — a forma que AC-03 pede."""
    return Element(
        id="bass_gap_fill",
        role="bass",
        sections=["MAIN"],
        register=[28, 55],
        layers=1,
        sync_role="kick_support",
        articulation="tight",
        harmony="follow_chords",
        instrument={"plugin": "Trilian", "preset": "Fingered Bass", "verified": True},
        rationale=(
            "O MIDI de origem nao tem nenhuma track de baixo; a familia "
            "esta faltando e foi criada dentro do campo harmonico do piano."
        ),
    )


# --- plan.validate: veto bloqueia mesmo com julgamento da IA ---------------


def test_validate_rejects_element_in_excluded_family(tmp_path):
    """`_valid_plan()` carrega dois elementos (`pad`, `arp`) que mapeiam
    para a familia `keys` — vetar `keys` no brief tem que barrar o plano."""
    plan = _valid_plan()
    brief_path, sha = _write_brief_excluding(tmp_path, ["keys"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "elements[0].role"
    assert "excluded_families" in exc.value.message
    assert "keys" in exc.value.message


def test_validate_allows_element_in_family_not_excluded(tmp_path):
    plan = _valid_plan()
    brief_path, sha = _write_brief_excluding(tmp_path, ["guitar"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    validate(plan)  # nao levanta — "keys" nao esta na lista vetada


@pytest.mark.parametrize(
    "raw_value",
    [
        "guitar",  # string solta em vez de lista
        ["guiter"],  # typo fora do vocabulario fechado
        ["guitar", 5],  # item nao-string na lista
        ["guitar", "guitar"],  # duplicata
        {"guitar": True},  # tipo totalmente errado (dict)
    ],
    ids=["bare-string", "unknown-family", "non-string-item", "duplicate", "dict"],
)
def test_validate_rejects_malformed_excluded_families(tmp_path, raw_value):
    """Achado do Codex na PR #105: `excluded_families` PRESENTE mas
    malformado (tipo errado, item fora do vocabulario fechado, item
    nao-string ou duplicata) nao pode virar silenciosamente "sem veto"
    so porque `brief_ref.sha256` bate — um brief nunca validado por
    `brief.validate` (editado a mao, ou plano em memoria) tem que
    continuar recusando a familia declarada, nao liberar tudo."""
    plan = _valid_plan()
    brief_path, sha = _write_brief_with_raw_excluded_families(tmp_path, raw_value)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "brief_ref.path"


def test_render_rejects_malformed_excluded_families_in_memory(tmp_path):
    """Mesmo achado do Codex, camada do render: plano montado em memoria
    (sem passar por `plan.load`) tambem tem que recusar
    `excluded_families` malformado, convertido em `RenderError` pelo
    `except PlanValidationError` ja existente ao redor da barreira."""
    src = _build_source_without_bass(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _bass_gap_fill_element()
    brief_path, sha = _write_brief_with_raw_excluded_families(tmp_path, "bass")
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    out = tmp_path / "out.mid"
    with pytest.raises(RenderError) as exc:
        render(plan, out, plan_dir=tmp_path)

    assert "excluded_families" in str(exc.value)
    assert not out.exists()


def test_validate_without_brief_ref_does_not_block_creation():
    """Regressao: plano sem `brief_ref` continua sem veto nenhum — a
    criacao de bateria/baixo do zero ja entregue nao pode regredir so
    porque a issue #17 acrescentou o mecanismo de veto."""
    plan = _valid_plan()
    assert plan.brief_ref is None
    validate(plan)  # nao levanta


def test_validate_rejects_excluded_family_even_with_explicit_gap_rationale(tmp_path):
    """AC-03 x restricao: `rationale` julgando a familia ausente nao
    revoga o veto do usuario — a restricao do brief manda."""
    src = _build_source_without_bass(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _bass_gap_fill_element()
    brief_path, sha = _write_brief_excluding(tmp_path, ["bass"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan, plan_dir=tmp_path)

    assert exc.value.path == "elements[0].role"
    assert "bass" in exc.value.message


# --- render: mesma barreira para plano em memoria --------------------------


def test_render_rejects_excluded_family_element_in_memory(tmp_path):
    """Barreira do render (issue #17): plano montado direto em Python,
    sem passar por `plan.load`, ainda assim nao pode gerar familia
    vetada. Nenhum arquivo de saida deve sobrar no disco."""
    src = _build_source_without_bass(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _bass_gap_fill_element()
    brief_path, sha = _write_brief_excluding(tmp_path, ["bass"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    out = tmp_path / "out.mid"
    with pytest.raises(RenderError) as exc:
        render(plan, out, plan_dir=tmp_path)

    assert "excluded_families" in str(exc.value)
    assert not out.exists()


def test_render_malformed_role_surfaces_as_plan_validation_error(tmp_path):
    """Achado do Codex na PR #105: a barreira de exclusao roda ANTES de
    `validate_plan` — `role` nao-string (plano em memoria montado errado,
    sem passar por `plan.load`) nao pode estourar `TypeError` dentro da
    barreira. O contrato de `render()` e que plano malformado sempre vira
    `PlanValidationError`, nunca uma excecao interna do pipeline; a
    barreira so calcula familia pra `role` que ja e string, e deixa
    `validate_plan` reportar o tipo invalido normalmente."""
    src = _build_source_without_bass(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _bass_gap_fill_element()
    plan.elements[0].role = ["bass"]  # type: ignore[assignment]
    brief_path, sha = _write_brief_excluding(tmp_path, ["bass"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    out = tmp_path / "out.mid"
    with pytest.raises(PlanValidationError):
        render(plan, out, plan_dir=tmp_path)

    assert not out.exists()


def test_render_allows_creation_when_brief_declares_no_veto(tmp_path):
    src = _build_source_without_bass(tmp_path)
    plan = _build_plan(src)
    plan.elements[0] = _bass_gap_fill_element()
    brief_path, sha = _write_brief_excluding(tmp_path, [])  # sem veto
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    out = tmp_path / "out.mid"
    report = render(plan, out, plan_dir=tmp_path)
    assert report.elements[0].rendered is True
    assert out.exists()


# --- AC-03: familia ausente e criada dentro do campo harmonico -------------


def test_bass_created_when_missing_stays_in_harmonic_field_and_passes_validators(
    tmp_path,
):
    """AC-03 (docs/objetivo.md): 'Pediu uma familia que nao existe no
    MIDI, ela e criada' — track de baixo dentro do campo harmonico,
    alinhada as secoes declaradas, e sem erro nos validadores
    harmonico/placement."""
    src = _build_source_without_bass(tmp_path)
    src_pm = pretty_midi.PrettyMIDI(str(src))
    assert not any(
        "bass" in (inst.name or "").lower() for inst in src_pm.instruments
    )

    plan = _build_plan(src)
    plan.elements[0] = _bass_gap_fill_element()

    out = tmp_path / "out.mid"
    report = render(plan, out)

    assert not harmony_has_errors(report.harmony_issues), [
        i.message for i in report.harmony_issues if i.severity == "error"
    ]
    assert not placement_has_errors(report.placement_issues), [
        i.message for i in report.placement_issues if i.severity == "error"
    ]

    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(str(out), charset="utf-8")
    emitted_tracks = out_mid.tracks[len(src_mid.tracks):]
    assert emitted_tracks
    assert any(
        any(msg.type == "note_on" and msg.velocity > 0 for msg in track)
        for track in emitted_tracks
    )


# --- AC-04: familia nao pedida nao aparece ---------------------------------


def test_no_guitar_track_when_plan_has_no_guitar_element(tmp_path):
    """AC-04 (docs/objetivo.md): 'Familia que o usuario nao pediu e a IA
    nao julgou faltar nao aparece' — plano sem elemento de guitarra nao
    gera track de guitarra na saida."""
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)  # unico elemento e role="pad" -> familia keys
    out = tmp_path / "out.mid"
    render(plan, out)

    out_mid = mido.MidiFile(str(out), charset="utf-8")
    names = [
        msg.name for track in out_mid.tracks for msg in track
        if msg.type == "track_name"
    ]
    assert not any("guitar" in name.lower() for name in names)
