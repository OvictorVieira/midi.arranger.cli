"""Testes do filtro `only` de `render()` (issue #24, parte 2)."""

from __future__ import annotations

import pretty_midi
import pytest

from tests.test_render import (
    _build_plan,
    _build_synthetic_source,
    _rhythmic_element,
    _sub_element,
)
from tools.plan import Transition
from tools.render import RenderError, render


def _plan_with_three_families(src):
    """`pad_main` (harmonic), `arp_main` (rhythmic), `sub_main` (electronic),
    todas na mesma secao MAIN — cada uma cobre uma das tres categorias
    nomeadas de `only`, sem cobrir bateria/baixo (fora do vocabulario)."""
    plan = _build_plan(src)
    plan.elements = [
        plan.elements[0],          # pad_main (harmonic: KEYBOARD/STRINGS/DRONE + pad)
        _rhythmic_element(role="arp"),   # arp_main (rhythmic)
        _sub_element(),             # sub_main (electronic)
    ]
    plan.transitions = [
        Transition(
            at_bar=4,
            from_section="MAIN",
            to_section="MAIN",
            dimensions_changed=[],
            elements=["pad_main"],
            technique="entrada do pad",
        ),
    ]
    return plan


def test_only_harmonic_keeps_only_harmonic_family(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    report = render(plan, out, only="harmonic")

    rendered_ids = {e.element_id for e in report.elements}
    assert rendered_ids == {"pad_main"}


def test_only_transitions_keeps_only_boundary_elements(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    report = render(plan, out, only="transitions")

    rendered_ids = {e.element_id for e in report.elements}
    assert rendered_ids == {"pad_main"}


def test_only_accepts_comma_separated_list(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    report = render(plan, out, only="rhythmic,electronic")

    rendered_ids = {e.element_id for e in report.elements}
    assert rendered_ids == {"arp_main", "sub_main"}


def test_only_accepts_list_of_strings(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    report = render(plan, out, only=["rhythmic", "electronic"])

    rendered_ids = {e.element_id for e in report.elements}
    assert rendered_ids == {"arp_main", "sub_main"}


def test_only_none_renders_everything_unchanged(tmp_path):
    """Sem `only`, comportamento identico ao de antes da issue #24."""
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    report = render(plan, out)

    rendered_ids = {e.element_id for e in report.elements}
    assert rendered_ids == {"pad_main", "arp_main", "sub_main"}


def test_filtered_elements_do_not_emit_tracks(tmp_path):
    """'Elementos filtrados nao aparecem no output nem no relatorio.'"""
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    render(plan, out, only="harmonic")

    src_pm = pretty_midi.PrettyMIDI(str(src))
    out_pm = pretty_midi.PrettyMIDI(str(out))
    # So o pad (1 track) foi acrescentado ao source — arp e sub ficaram de fora.
    assert len(out_pm.instruments) == len(src_pm.instruments) + 1


def test_only_unknown_category_raises_render_error(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    out = tmp_path / "out.mid"
    with pytest.raises(RenderError, match="only"):
        render(plan, out, only="bogus_category")


def test_only_does_not_filter_edits(tmp_path):
    """`only` filtra `plan.elements`; `plan.edits` (humanizacao de track do
    usuario) nao e um 'elemento gerado' e continua rodando igual."""
    from tools.plan import PlanEdit

    src = _build_synthetic_source(tmp_path)
    plan = _plan_with_three_families(src)
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5)]
    out = tmp_path / "out.mid"
    report = render(plan, out, only="harmonic")

    assert report.edits
    assert report.edits[0].track == "Bass"
