from __future__ import annotations

import re
from pathlib import Path

from tools import contract  # noqa: F401 - popula o registry por side effect
from tools.registry import list_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"
DRIVER_NAMES = ("CLAUDE", "CODEX", "OPENCODE", "AGY", "CURSOR", "AMP", "GEMINI")
DRIVER_PATHS = {name: PROMPTS_DIR / f"{name}.md" for name in DRIVER_NAMES}
REFERENCE_DRIVER = DRIVER_PATHS["CLAUDE"]
COMPLETION_SENTINEL = "<promise>COMPLETE</promise>"


def _driver_text(name: str) -> str:
    return DRIVER_PATHS[name].read_text(encoding="utf-8")


def _tool_section(text: str) -> str:
    return text.split("Tools disponiveis:", maxsplit=1)[1].split(
        "As tools retornam JSON.", maxsplit=1,
    )[0]


def _without_tool_convention(text: str) -> str:
    return re.sub(
        r"## Convencao da ferramenta\n\n.*?\n\n(?=Voce e um arranjador)",
        "## Convencao da ferramenta\n\n<TOOL CONVENTION>\n\n",
        text,
        count=1,
        flags=re.DOTALL,
    )


def _strip_fenced_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def _strip_ordered_flow(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not re.match(r"^\d+\. ", line)
    )


def _numeric_constants(text: str) -> set[str]:
    stripped = _strip_ordered_flow(_strip_fenced_code(text))
    return set(re.findall(r"(?<![A-Za-z_])\d+(?:[.,]\d+)?(?:[–-]\d+(?:[.,]\d+)?)?%?", stripped))


def _knowledge_numeric_constants() -> set[str]:
    constants: set[str] = set()
    for path in (REPO_ROOT / "knowledge").rglob("*"):
        if path.is_file():
            constants.update(_numeric_constants(path.read_text(encoding="utf-8")))
    return constants


def test_all_run_drivers_exist() -> None:
    for path in DRIVER_PATHS.values():
        assert path.is_file()


def test_claude_driver_exists_as_reference_driver() -> None:
    assert REFERENCE_DRIVER.is_file()


def test_non_reference_drivers_derive_from_claude_except_tool_convention() -> None:
    reference = _without_tool_convention(_driver_text("CLAUDE"))

    for name in DRIVER_NAMES:
        assert _without_tool_convention(_driver_text(name)) == reference


def test_all_drivers_include_completion_sentinel_literal() -> None:
    for name in DRIVER_NAMES:
        assert COMPLETION_SENTINEL in _driver_text(name), f"sentinela ausente em {name}"


def test_all_drivers_reference_only_existing_repo_paths() -> None:
    for name in DRIVER_NAMES:
        text = _driver_text(name)
        repo_paths = re.findall(r"`((?:docs|knowledge|prompts|bin|tests|scripts)/[^`]+)`", text)
        for repo_path in repo_paths:
            assert (REPO_ROOT / repo_path).exists(), f"{name} referencia caminho inexistente: {repo_path}"


def test_all_drivers_do_not_duplicate_numeric_constants_from_knowledge() -> None:
    knowledge_constants = _knowledge_numeric_constants()

    for name in DRIVER_NAMES:
        driver_constants = _numeric_constants(_driver_text(name))
        duplicated = sorted(driver_constants & knowledge_constants)
        assert duplicated == [], f"{name} duplica constantes numericas de knowledge/: {duplicated}"


def test_claude_driver_carries_required_agent_contract() -> None:
    text = _driver_text("CLAUDE")

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


def test_all_drivers_document_the_full_arrangement_flow() -> None:
    for name in DRIVER_NAMES:
        text = _driver_text(name)

        for step in range(1, 11):
            assert re.search(rf"^{step}\. ", text, re.MULTILINE), f"{name}: passo {step} ausente"

        assert "Rode `analyze`" in text
        assert "Consulte `techniques.list` e `techniques.describe`" in text
        assert "Rode `plan.validate`" in text
        assert "Rode `render`" in text
        assert "Leia o relatorio JSON do render" in text


def test_all_drivers_list_registered_tools_by_real_names() -> None:
    registry_names = {tool["name"] for tool in list_tools()}

    for driver_name in DRIVER_NAMES:
        text = _driver_text(driver_name)

        for name in registry_names:
            assert f"`{name}`" in text, f"{driver_name}: tool registrada ausente do driver: {name}"

        documented_names = set(re.findall(r"^- `([^`]+)`:", _tool_section(text), flags=re.MULTILINE))
        assert documented_names == registry_names


def test_all_drivers_show_exact_tool_cli_invocation_shape() -> None:
    for name in DRIVER_NAMES:
        text = _driver_text(name)

        assert "python -m tools.cli tool <nome-da-tool> --input <payload.json>" in text
        assert "python -m tools.cli tool <nome-da-tool> --input -" in text
        assert "python -m tools.cli --list" in text
        assert "python -m tools.cli --schema <nome-da-tool>" in text


def test_all_drivers_discover_preset_libraries_without_user_configuration() -> None:
    for name in DRIVER_NAMES:
        text = _driver_text(name)

        assert "Rode primeiro sem overrides" in text
        assert "Compare plugins instalados com presets" in text
        assert "`searched_roots`, `discovered_roots` e `unresolved_roots`" in text
        assert "inspecione de forma read-only symlinks/aliases e configuracoes locais" in text
        assert "Nao peca ao usuario para definir env var nem path" in text
        assert "volume desmontado/permissao" in text


def test_all_drivers_restrict_completion_sentinel_to_validated_delivery() -> None:
    for name in DRIVER_NAMES:
        text = _driver_text(name)

        assert COMPLETION_SENTINEL in text
        assert text.count(COMPLETION_SENTINEL) >= 2
        assert "so pode aparecer quando o MIDI final existe" in text
        assert "plan.validate` passou" in text
        assert "`render` passou" in text
        assert "relatorio de\nvalidadores foi lido" in text
        assert "Nunca emita a sentinela para encerrar cedo" in text
