"""Regressoes para validacao de plano in-memory no render."""

from __future__ import annotations

import pytest

from tests.test_persona import _element, _empty_analysis, _plan
from tests.test_render import _build_plan, _build_synthetic_source
from tools.plan import PlanValidationError
from tools.render import render
from tools.validators.persona import validate_persona


def test_render_validates_in_memory_plan_before_pipeline(tmp_path):
    src = _build_synthetic_source(tmp_path)
    plan = _build_plan(src)
    plan.sections[0].energy = None

    with pytest.raises(PlanValidationError) as exc:
        render(plan, tmp_path / "out.mid")

    assert exc.value.path == "sections[0].energy"
    assert "missing energy" in exc.value.message


@pytest.mark.parametrize("energy", [None, {"impacto": 5}])
def test_persona_density_inversion_ignores_missing_energy_defensively(energy):
    plan = _plan([_element("pad_main")])
    plan.sections[0].energy = energy

    assert validate_persona(plan, [], _empty_analysis()) == []
