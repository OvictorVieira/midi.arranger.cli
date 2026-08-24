"""Subpacote de tecnicas — indice dos manuais e motor aplicavel."""

from .engine import (
    SUPPORTED_TECHNIQUES,
    RegisteredTechnique,
    TechniqueLevel,
    TechniqueRegistrationError,
    TechniqueRegistry,
    UnknownTechniqueError,
    apply_technique,
    get_technique,
    register_technique,
    registered_techniques,
    validate_registry_against_index,
)
from .index import (
    DEFAULT_MANUALS_DIR,
    Technique,
    TechniqueError,
    TechniqueIndex,
    TechniqueParameter,
    build_index,
    parse_manual,
)

__all__ = [
    "DEFAULT_MANUALS_DIR",
    "RegisteredTechnique",
    "SUPPORTED_TECHNIQUES",
    "Technique",
    "TechniqueError",
    "TechniqueIndex",
    "TechniqueLevel",
    "TechniqueParameter",
    "TechniqueRegistrationError",
    "TechniqueRegistry",
    "UnknownTechniqueError",
    "apply_technique",
    "build_index",
    "get_technique",
    "parse_manual",
    "register_technique",
    "registered_techniques",
    "validate_registry_against_index",
]
