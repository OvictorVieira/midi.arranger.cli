"""Testes do resolvedor de parametros compartilhado por tecnicas de baixo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tools.techniques._param_range import load_range_resolver
from tools.techniques.engine import TechniqueContext


@dataclass
class _StubParam:
    name: str
    value: Any = None
    range: Any = None


@dataclass
class _StubTechnique:
    canonical: str
    parameters: list[_StubParam]


def _ctx(
    canonical: str,
    *,
    parameters: dict[str, Any] | None = None,
    recipe: dict[str, Any] | None = None,
) -> TechniqueContext:
    return TechniqueContext(
        seed=1,
        canonical=canonical,
        parameters=parameters or {},
        recipe=recipe or {},
    )


def test_raises_when_technique_missing_from_manual_index():
    ctx = _ctx("bass.tecnica_fantasma")
    with pytest.raises(ValueError, match="nao existe no indice dos manuais"):
        load_range_resolver(ctx)


def test_resolve_returns_none_for_unknown_parameter():
    # `bass.let_ring` existe no indice; peca um parametro que ele nao declara.
    ctx = _ctx("bass.let_ring")
    _, resolve = load_range_resolver(ctx)
    assert resolve("parametro_inexistente") is None


def test_resolve_returns_none_when_param_has_no_value_and_no_range(
    monkeypatch: pytest.MonkeyPatch,
):
    stub = _StubTechnique(
        canonical="bass.let_ring",
        parameters=[_StubParam("cc")],  # sem value e sem range
    )

    def fake_build_index():
        class _Idx:
            def get(self, canonical):
                assert canonical == "bass.let_ring"
                return stub

        return _Idx()

    monkeypatch.setattr(
        "tools.techniques.index.build_index",
        fake_build_index,
    )

    _, resolve = load_range_resolver(_ctx("bass.let_ring"))
    assert resolve("cc") is None


def test_resolve_returns_none_for_non_numeric_override_in_parameters():
    ctx = _ctx("bass.let_ring", parameters={"cc": "sessenta e quatro"})
    _, resolve = load_range_resolver(ctx)
    assert resolve("cc") is None
