"""Testes do registro de tecnicas aplicaveis."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.techniques import (
    SUPPORTED_TECHNIQUES,
    TechniqueRegistrationError,
    TechniqueRegistry,
    UnknownTechniqueError,
    apply_technique,
    build_index,
    get_technique,
    registered_techniques,
    validate_registry_against_index,
)

MANUALS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "tecnicas"


def test_registered_technique_declares_canonical_level_and_apply_function():
    tech = get_technique("drums.ghost_notes")

    assert tech.canonical == "drums.ghost_notes"
    assert tech.level == "technique"
    assert callable(tech.apply)


def test_dispatch_by_canonical_name_calls_registered_function():
    registry = TechniqueRegistry()
    calls = []

    @registry.register("drums.ghost_notes", "technique")
    def apply(payload: dict[str, str], *, intensity: float) -> dict[str, object]:
        calls.append((payload, intensity))
        return {"payload": payload, "intensity": intensity}

    result = registry.apply("drums.ghost_notes", {"track": "Drums"}, intensity=0.5)

    assert result == {"payload": {"track": "Drums"}, "intensity": 0.5}
    assert calls == [({"track": "Drums"}, 0.5)]


def test_unknown_technique_error_names_available_techniques():
    registry = TechniqueRegistry()
    registry.register("drums.microtiming", "humanize")(lambda payload: payload)
    registry.register("drums.ghost_notes", "technique")(lambda payload: payload)

    with pytest.raises(UnknownTechniqueError) as exc:
        registry.apply("drums.flanm", object())

    assert exc.value.available == ("drums.ghost_notes", "drums.microtiming")
    assert "drums.ghost_notes" in str(exc.value)
    assert "drums.microtiming" in str(exc.value)


def test_duplicate_or_malformed_registration_fails():
    registry = TechniqueRegistry()
    registry.register("drums.ghost_notes", "technique")(lambda payload: payload)

    with pytest.raises(TechniqueRegistrationError, match="duplicada"):
        registry.register("drums.ghost_notes", "technique")(lambda payload: payload)
    with pytest.raises(TechniqueRegistrationError, match="<familia>.<nome>"):
        registry.register("ghost_notes", "technique")(lambda payload: payload)
    with pytest.raises(TechniqueRegistrationError, match="nivel"):
        registry.register("drums.microtiming", "ornament")(lambda payload: payload)
    with pytest.raises(TechniqueRegistrationError, match="chamavel"):
        registry.register("drums.microtiming", "humanize")(None)


def test_supported_techniques_is_derived_from_the_registry():
    assert tuple(t.canonical for t in registered_techniques()) == SUPPORTED_TECHNIQUES
    assert tuple(sorted(SUPPORTED_TECHNIQUES)) == SUPPORTED_TECHNIQUES
    assert set(SUPPORTED_TECHNIQUES) == {
        "bass.ghost_notes",
        "drums.ghost_notes",
        "drums.microtiming",
    }


def test_global_dispatch_uses_registered_implementation():
    payload = {"notes": [38]}

    assert apply_technique("drums.ghost_notes", payload) is payload


def test_every_registered_technique_exists_in_manual_index():
    idx = build_index(MANUALS_DIR)

    validate_registry_against_index(idx)
    for canonical in SUPPORTED_TECHNIQUES:
        assert idx.get(canonical) is not None
