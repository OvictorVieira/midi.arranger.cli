"""Validadores agrupados.

Reexporta a API publica de cada modulo do subpacote para que consumidores
possam importar `from tools.validators import validate_harmony` sem precisar
alcancar o modulo interno.
"""

from .anticopy import (
    AntiCopyIssue,
    ReferenceSequence,
    load_reference_sequences,
    validate_anticopy,
)
from .anticopy import (
    format_issues as format_anticopy_issues,
)
from .anticopy import (
    has_errors as anticopy_has_errors,
)
from .artifice import (
    ArtificeIssue,
    validate_artifice,
)
from .artifice import (
    format_issues as format_artifice_issues,
)
from .artifice import (
    has_errors as artifice_has_errors,
)
from .collision import (
    CollisionRelocation,
    CollisionReport,
    CollisionWarning,
    validate_collisions,
)
from .harmony import (
    HarmonyIssue,
    RenderedNote,
    RenderedTrack,
    chord_pcs,
    degrees_pcs,
    key_scale_pcs,
    pitch_name,
    validate_harmony,
)
from .harmony import (
    format_issues as format_harmony_issues,
)
from .harmony import (
    has_errors as harmony_has_errors,
)
from .persona import (
    PersonaIssue,
    validate_persona,
)
from .persona import (
    format_issues as format_persona_issues,
)
from .persona import (
    has_errors as persona_has_errors,
)
from .placement import (
    PlacementIssue,
    validate_placement,
)
from .placement import (
    format_issues as format_placement_issues,
)
from .placement import (
    has_errors as placement_has_errors,
)
from .transitions import (
    TRANSITION_DIMENSIONS,
    TransitionIssue,
    validate_transitions,
)
from .transitions import (
    format_issues as format_transition_issues,
)
from .transitions import (
    has_errors as transition_has_errors,
)

__all__ = [
    "AntiCopyIssue",
    "ArtificeIssue",
    "CollisionRelocation",
    "CollisionReport",
    "CollisionWarning",
    "HarmonyIssue",
    "PersonaIssue",
    "PlacementIssue",
    "ReferenceSequence",
    "RenderedNote",
    "RenderedTrack",
    "TRANSITION_DIMENSIONS",
    "TransitionIssue",
    "anticopy_has_errors",
    "artifice_has_errors",
    "chord_pcs",
    "degrees_pcs",
    "format_anticopy_issues",
    "format_artifice_issues",
    "format_harmony_issues",
    "format_persona_issues",
    "format_placement_issues",
    "format_transition_issues",
    "harmony_has_errors",
    "key_scale_pcs",
    "load_reference_sequences",
    "persona_has_errors",
    "pitch_name",
    "placement_has_errors",
    "transition_has_errors",
    "validate_anticopy",
    "validate_artifice",
    "validate_collisions",
    "validate_harmony",
    "validate_persona",
    "validate_placement",
    "validate_transitions",
]
