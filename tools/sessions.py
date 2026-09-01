"""Persistencia append-only de sessoes de trabalho (issue #96).

O bloco `session` de `ArrangementPlan` recorta uma rodada de trabalho — id,
intent e familias em escopo. Este modulo arquiva uma copia carimbada do plano
sob `.midiarranger/sessions/` para que o historico de sessoes seja auditavel.

## Fronteira intencional

- `tools/sessions.py` NAO e chamado por `tools/render.py`. O render tem uma
  ordem inviolavel de pipeline definida em `AGENTS.md` e nao vai ganhar mais
  um ponto de I/O nesta issue. O consumidor real (harness/CLI) invoca
  `archive_session` explicitamente quando o brief/plano vai para disco.
- O nome do arquivo e DETERMINISTICO: `<id>-<intent>-<familias-com-dash>.json`.
  Sem timestamp — o carimbo de tempo ja vive dentro do plano (`session.created_at`).
- APPEND-ONLY: se o arquivo ja existe, e erro. Colisao aponta bug de id
  duplicado — nunca sobrescreve historico.
"""

from __future__ import annotations

import json
from pathlib import Path

from .plan import ArrangementPlan, PlanSession, to_dict

SESSIONS_DIRNAME = "sessions"
"""Subdiretorio dentro de `.midiarranger/` onde as sessoes ficam arquivadas."""


class SessionArchiveError(RuntimeError):
    """Levantada quando a persistencia da sessao nao pode acontecer.

    Casos:
    - Plano sem `session` (nao ha o que arquivar).
    - Arquivo de destino ja existe (append-only, id duplicado).
    - Falha de I/O ao criar diretorio ou escrever arquivo.
    """


def session_filename(session: PlanSession) -> str:
    """Nome do arquivo de sessao, deterministico.

    Formato: `<id>-<intent>-<familias-joined-com-dash>.json`. Quando
    `families_in_scope` esta vazio, cai para `all` para produzir nome
    legivel — nao ha ambiguidade porque o proprio `id` identifica a
    sessao. A ordem das familias segue a do plano; o consumidor que
    quiser estabilizar pode ordenar antes de construir a sessao.
    """
    families_part = "-".join(session.families_in_scope) or "all"
    return f"{session.id}-{session.intent}-{families_part}.json"


def sessions_dir(base_dir: Path | str) -> Path:
    """Diretorio-alvo de arquivamento: `<base>/.midiarranger/sessions/`."""
    return Path(base_dir) / ".midiarranger" / SESSIONS_DIRNAME


def archive_session(
    plan: ArrangementPlan, base_dir: Path | str,
) -> Path:
    """Arquiva uma copia carimbada do plano em `<base>/.midiarranger/sessions/`.

    - `plan` DEVE ter `session` — plano sem sessao levanta `SessionArchiveError`.
    - Cria `.midiarranger/sessions/` se nao existir.
    - APPEND-ONLY: se o arquivo destino ja existir, `SessionArchiveError`.
    - Devolve o path final absoluto do arquivo escrito.

    O consumidor decide quando chamar; este modulo nao adivinha ponto de
    integracao no pipeline deterministico.
    """
    if plan.session is None:
        raise SessionArchiveError(
            "plan.session is None; nada para arquivar — a persistencia de "
            "sessao so faz sentido quando o brief/plano declara uma."
        )
    target_dir = sessions_dir(base_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SessionArchiveError(
            f"nao consegui criar {target_dir}: {exc}"
        ) from None

    target = target_dir / session_filename(plan.session)
    if target.exists():
        raise SessionArchiveError(
            f"arquivo de sessao ja existe em {target}; sessao append-only, "
            "colisao aponta bug de id duplicado — nunca sobrescreve historico"
        )
    try:
        target.write_text(
            json.dumps(to_dict(plan), indent=2), encoding="utf-8",
        )
    except OSError as exc:
        raise SessionArchiveError(
            f"nao consegui escrever {target}: {exc}"
        ) from None
    return target


__all__ = [
    "SESSIONS_DIRNAME",
    "SessionArchiveError",
    "archive_session",
    "session_filename",
    "sessions_dir",
]
