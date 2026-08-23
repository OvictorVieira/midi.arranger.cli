"""Subpacote de tecnicas — indice derivado dos manuais em knowledge/tecnicas."""

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
    "Technique",
    "TechniqueError",
    "TechniqueIndex",
    "TechniqueParameter",
    "build_index",
    "parse_manual",
]
