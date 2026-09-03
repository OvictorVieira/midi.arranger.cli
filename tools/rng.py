"""Guarda de rastreabilidade de seed (AC-21).

Regra invariavel do projeto: "Nenhum parametro sorteado sem origem
declarada. O componente aleatorio nunca supera a soma das intencoes
deterministicas." Para o motor de humanizacao por profile
(`tools/humanize.py::VelocityEngine`) essa regra ja e checada em runtime por
clamp de amplitude. Para os geradores de paleta (`tools/palette/*.py`), a
seed que alimenta cada `random.Random(...)` sempre chegou como argumento
explicito do chamador (nunca `random` de modulo, relogio ou estado global) —
mas nada verificava isso em runtime; um refactor futuro poderia introduzir
`random.random()` sem seed sem que nenhum teste percebesse ate o build
deixar de ser determinístico.

`assert_traceable_seed` e essa barreira: todo gerador de paleta chama isto
UMA VEZ, na entrada, antes de tocar em qualquer `random.Random`. Falha
imediatamente (nao silenciosamente) se a seed nao for um int explicito —
a unica origem declarada aceita por este maquinario.
"""

from __future__ import annotations


def assert_traceable_seed(seed: object, *, source: str) -> int:
    """Garante que `seed` e uma origem declarada e rastreavel.

    Levanta `AssertionError` (nao `ValueError`) porque isto e uma checagem de
    invariante interno do maquinario — nunca uma validacao de entrada do
    usuario — mesmo espirito de `assert` usado para invariantes de contrato
    em `tools/techniques/engine.py`.

    Args:
      seed: valor recebido como seed pelo chamador.
      source: identificador do gerador/motor que esta pedindo a checagem
        (ex.: "palette.bass.generate_bass") — aparece na mensagem de erro
        para tornar o diagnostico acionavel.

    Returns:
      A propria `seed`, para uso encadeado (`seed = assert_traceable_seed(...)`).
    """
    if not source:
        raise AssertionError(
            "AC-21: assert_traceable_seed chamado sem 'source' — a origem da "
            "checagem tambem precisa ser rastreavel"
        )
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise AssertionError(
            f"AC-21: componente aleatorio sem origem declarada em {source!r} — "
            f"seed precisa ser int explicito, recebido {seed!r} "
            f"({type(seed).__name__})"
        )
    return seed


__all__ = ["assert_traceable_seed"]
