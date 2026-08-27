"""Resolucao de parametro numerico com precedencia `parameters` > `recipe` > manual.

Usado pelas tecnicas registradas em `engine.py` para evitar duplicacao do padrao
"le range do parametro atravessando plano, receita e manual". Cada aplicador
importa localmente `load_range_resolver` — o binding fica em variavel local do
aplicador (via `import` no corpo), sem virar captura de global, e por isso o
teste `test_registered_techniques_do_not_capture_global_or_nonlocal_state`
continua verde.
"""

from __future__ import annotations

from collections.abc import Callable


def load_range_resolver(
    context,
) -> tuple[object, Callable[[str], tuple[float, float] | None]]:
    """Devolve `(technique, resolve)`.

    `technique` e a entrada do manual para `context.canonical`. `resolve(name)`
    devolve o range `(lo, hi)` do parametro `name` seguindo a precedencia
    obrigatoria `context.parameters` > `context.recipe` > `range`/`value` do
    manual. Retorna `None` quando nao ha valor conhecido, para que o aplicador
    decida o fallback local.
    """

    from .index import build_index

    technique = build_index().get(context.canonical)
    if technique is None:
        raise ValueError(
            f"tecnica {context.canonical!r} nao existe no indice dos manuais"
        )

    params_by_name = {p.name: p for p in technique.parameters}

    def resolve(name: str) -> tuple[float, float] | None:
        if name in context.parameters:
            value = context.parameters[name]
        elif name in context.recipe:
            value = context.recipe[name]
        else:
            param = params_by_name.get(name)
            if param is None:
                return None
            if param.range is not None:
                value = param.range
            elif param.value is not None:
                value = (param.value, param.value)
            else:
                return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value), float(value))
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool)
                for v in value
            )
        ):
            return (float(value[0]), float(value[1]))
        return None

    return technique, resolve
