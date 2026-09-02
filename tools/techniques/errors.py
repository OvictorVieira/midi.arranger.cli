"""`TechniqueRecipeError`, isolada de `engine.py` por design.

Um aplicador registrado no motor central (`tools/techniques/engine.py`)
precisa ser autocontido (AGENTS.md): nenhuma captura de estado global do
proprio modulo onde o aplicador esta definido. Mas `inspect.getclosurevars`
casa QUALQUER nome que apareca em `co_names` da funcao contra
`func.__globals__` — inclusive o nome-fonte de um `from X import Y` local,
inclusive um nome usado so como atributo. Se `TechniqueRecipeError`
continuasse definida em `engine.py`, qualquer forma de referencia-la de
dentro de um aplicador (import local incluso) seguiria contando como
"global capturado", porque o proprio modulo tambem precisa do nome para o
despacho central (`_resolve_recipe`/`_recipe_for_tool`).

Por isso a classe mora aqui: `engine.py` referencia este modulo por
`from . import errors as _errors` (nunca importa o nome solto), e um
aplicador que precise levantar a excecao faz `from .errors import
TechniqueRecipeError` — import local de um modulo DIFERENTE do seu proprio,
igual ao padrao ja usado para `iter_note_dicts` (`tools/techniques/_helpers.py`).
"""

from __future__ import annotations


class TechniqueRecipeError(ValueError):
    """Falha ao resolver receita MIDI documentada para uma tecnica."""
