from __future__ import annotations

import re
from pathlib import Path

from tools import contract  # noqa: F401 - popula o registry por side effect
from tools.registry import list_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_DRIVER = REPO_ROOT / "prompts" / "CLAUDE.md"
COMPLETION_SENTINEL = "<promise>COMPLETE</promise>"


def _claude_driver_text() -> str:
    return CLAUDE_DRIVER.read_text(encoding="utf-8")


def test_claude_driver_exists_as_reference_driver() -> None:
    assert CLAUDE_DRIVER.is_file()


def test_claude_driver_carries_required_agent_contract() -> None:
    text = _claude_driver_text()

    assert "Voce nao e assistente generico" in text
    assert "knowledge/persona/" in text
    assert "knowledge/tecnicas/" in text
    assert "autoridade" in text
    assert "brief e contrato" in text
    assert "somente leitura" in text
    assert "Nunca\nreescreva `arrangement-brief.json`" in text
    assert "rationale" in text
    assert "nao vazio" in text
    assert "Nunca extraia, transcreva, copie ou recrie conteudo musical" in text
    assert "Escreva em `progress_file` antes de encerrar" in text
    assert "Uma iteracao e uma unidade de trabalho" in text


def test_claude_driver_documents_the_full_arrangement_flow() -> None:
    text = _claude_driver_text()

    for step in range(1, 11):
        assert re.search(rf"^{step}\. ", text, re.MULTILINE), f"passo {step} ausente"

    assert "Rode `analyze`" in text
    assert "Consulte `techniques.list` e `techniques.describe`" in text
    assert "Rode `plan.validate`" in text
    assert "Rode `render`" in text
    assert "Leia o relatorio JSON do render" in text


def test_claude_driver_lists_registered_tools_by_real_names() -> None:
    text = _claude_driver_text()
    registry_names = {tool["name"] for tool in list_tools()}

    for name in registry_names:
        assert f"`{name}`" in text, f"tool registrada ausente do driver: {name}"

    tools_section = text.split("Tools disponiveis:", maxsplit=1)[1].split(
        "As tools retornam JSON.", maxsplit=1,
    )[0]
    documented_names = set(re.findall(r"^- `([^`]+)`:", tools_section, flags=re.MULTILINE))
    assert documented_names == registry_names


def test_claude_driver_shows_exact_tool_cli_invocation_shape() -> None:
    text = _claude_driver_text()

    assert "python -m tools.cli tool <nome-da-tool> --input <payload.json>" in text
    assert "python -m tools.cli tool <nome-da-tool> --input -" in text
    assert "python -m tools.cli --list" in text
    assert "python -m tools.cli --schema <nome-da-tool>" in text


def test_claude_driver_restricts_completion_sentinel_to_validated_delivery() -> None:
    text = _claude_driver_text()

    assert COMPLETION_SENTINEL in text
    assert text.count(COMPLETION_SENTINEL) >= 2
    assert "so pode aparecer quando o MIDI final existe" in text
    assert "plan.validate` passou" in text
    assert "`render` passou" in text
    assert "relatorio de\nvalidadores foi lido" in text
    assert "Nunca emita a sentinela para encerrar cedo" in text
