"""O documento de fluxo do harness nao pode ficar para tras do codigo.

`docs/fluxo-harness.md` descreve como o harness executa. Se alguem muda
`bin/midi-arranger` sem passar por la, a doc vira ficcao — e doc de fluxo errada
e pior que doc nenhuma, porque quem le confia.

A trava e um hash do harness gravado no fim do documento. Ela nao verifica que o
texto ficou correto (nada verifica), mas garante que ninguem muda o harness sem
abrir o arquivo e olhar.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "bin" / "midi-arranger"
FLOW_DOC = REPO_ROOT / "docs" / "fluxo-harness.md"
UPDATE_SCRIPT = REPO_ROOT / "scripts" / "update-flow-lock.sh"

LOCK_RE = re.compile(r"^<!-- harness-sha256: (?P<sha>[0-9a-f]{64}|PENDENTE) -->$", re.MULTILINE)


def _harness_sha() -> str:
    return hashlib.sha256(HARNESS.read_bytes()).hexdigest()


def test_flow_doc_exists() -> None:
    assert FLOW_DOC.is_file(), (
        f"{FLOW_DOC.relative_to(REPO_ROOT)} nao existe. O fluxo do harness precisa "
        "estar documentado."
    )


def test_flow_doc_carries_lock_marker() -> None:
    match = LOCK_RE.search(FLOW_DOC.read_text(encoding="utf-8"))
    assert match is not None, (
        f"{FLOW_DOC.relative_to(REPO_ROOT)} precisa terminar com uma linha no formato "
        "'<!-- harness-sha256: ... -->'. Rode scripts/update-flow-lock.sh."
    )


def test_flow_doc_is_in_sync_with_harness() -> None:
    match = LOCK_RE.search(FLOW_DOC.read_text(encoding="utf-8"))
    assert match is not None, "marcador ausente — ver test_flow_doc_carries_lock_marker"

    recorded = match.group("sha")
    current = _harness_sha()

    assert recorded == current, (
        f"\n\nbin/midi-arranger mudou e docs/fluxo-harness.md nao foi revisado.\n\n"
        f"  registrado no doc : {recorded}\n"
        f"  hash atual        : {current}\n\n"
        f"O que fazer:\n"
        f"  1. Abra {FLOW_DOC.relative_to(REPO_ROOT)} e atualize as secoes que descrevem\n"
        f"     o que voce mudou — diagramas, tabela de adaptadores, regras.\n"
        f"  2. Rode {UPDATE_SCRIPT.relative_to(REPO_ROOT)} para gravar o hash novo.\n"
        f"  3. Commite os dois juntos.\n"
    )


def test_update_script_is_executable() -> None:
    assert UPDATE_SCRIPT.is_file(), f"{UPDATE_SCRIPT.relative_to(REPO_ROOT)} nao existe"
    import os

    assert os.access(UPDATE_SCRIPT, os.X_OK), (
        f"{UPDATE_SCRIPT.relative_to(REPO_ROOT)} precisa ser executavel — "
        "sem isso a mensagem de erro do teste manda rodar algo que nao roda."
    )


def test_flow_doc_documents_every_supported_tool() -> None:
    """Ferramenta suportada pelo harness precisa aparecer na doc.

    Acrescentar adaptador sem documentar e o modo mais provavel de a doc ficar
    incompleta sem o hash pegar (o hash pega a mudanca, este teste diz o que falta).
    """
    harness_text = HARNESS.read_text(encoding="utf-8")
    doc_text = FLOW_DOC.read_text(encoding="utf-8")

    match = re.search(r'^SUPPORTED_TOOLS="([^"]+)"', harness_text, re.MULTILINE)
    assert match is not None, "SUPPORTED_TOOLS nao encontrado em bin/midi-arranger"

    missing = [tool for tool in match.group(1).split() if f"`{tool}`" not in doc_text]
    assert not missing, (
        f"ferramentas suportadas ausentes de {FLOW_DOC.relative_to(REPO_ROOT)}: "
        f"{', '.join(missing)}"
    )
