"""Testes de arquitetura da fronteira tools/ x bin/.

A fronteira que nao se cruza (AGENTS.md, docs/arquitetura.md): nada em `tools/`
pode importar de `bin/`, e nada em `tools/` pode depender de biblioteca de LLM.
As tools precisam rodar e ser testadas sem modelo nenhum.

Se um novo modulo em `tools/` importar `bin`, `openai`, `anthropic`, `litellm`
ou similar, este teste falha e a migracao volta pra prancheta.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import tools

TOOLS_ROOT = Path(tools.__file__).parent
TECHNIQUES_ROOT = TOOLS_ROOT / "techniques"

FORBIDDEN_ROOTS = frozenset({"bin"})

FORBIDDEN_LLM_LIBS = frozenset({
    "openai",
    "anthropic",
    "litellm",
    "langchain",
    "langchain_core",
    "langchain_community",
    "llama_index",
    "llama_cpp",
    "transformers",
    "cohere",
    "google.generativeai",
    "vertexai",
    "ollama",
    "huggingface_hub",
})


def _iter_tools_py_files() -> list[Path]:
    files: list[Path] = []
    for root, _, filenames in os.walk(TOOLS_ROOT):
        if "__pycache__" in root:
            continue
        for name in filenames:
            if name.endswith(".py"):
                files.append(Path(root) / name)
    return files


def _iter_techniques_py_files() -> list[Path]:
    files: list[Path] = []
    for root, _, filenames in os.walk(TECHNIQUES_ROOT):
        if "__pycache__" in root:
            continue
        for name in filenames:
            if name.endswith(".py"):
                files.append(Path(root) / name)
    return files


def _iter_import_roots(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                yield node.module


def test_tools_never_imports_from_bin():
    offenders: list[tuple[str, str]] = []
    for path in _iter_tools_py_files():
        for module in _iter_import_roots(path):
            root = module.split(".", 1)[0]
            if root in FORBIDDEN_ROOTS:
                offenders.append((str(path.relative_to(TOOLS_ROOT.parent)), module))
    assert offenders == [], f"tools/ nao pode importar de bin/: {offenders}"


def test_techniques_never_imports_from_bin():
    offenders: list[tuple[str, str]] = []
    for path in _iter_techniques_py_files():
        for module in _iter_import_roots(path):
            root = module.split(".", 1)[0]
            if root in FORBIDDEN_ROOTS:
                offenders.append((str(path.relative_to(TOOLS_ROOT.parent)), module))
    assert offenders == [], f"tools/techniques/ nao pode importar de bin/: {offenders}"


def test_techniques_random_and_clock_usage_stays_centralized():
    allowed_random_imports = {TECHNIQUES_ROOT / "engine.py"}
    forbidden_modules = frozenset({"datetime", "time"})
    offenders: list[tuple[str, str]] = []

    for path in _iter_techniques_py_files():
        for module in _iter_import_roots(path):
            if module == "random" and path not in allowed_random_imports:
                offenders.append((str(path.relative_to(TOOLS_ROOT.parent)), module))
            root = module.split(".", 1)[0]
            if root in forbidden_modules:
                offenders.append((str(path.relative_to(TOOLS_ROOT.parent)), module))

    assert offenders == [], (
        "tools/techniques/ deve derivar variacao de TechniqueContext.seed, "
        f"sem RNG global nem relogio: {offenders}"
    )


def test_tools_never_imports_llm_libraries():
    offenders: list[tuple[str, str]] = []
    for path in _iter_tools_py_files():
        for module in _iter_import_roots(path):
            for forbidden in FORBIDDEN_LLM_LIBS:
                if module == forbidden or module.startswith(forbidden + "."):
                    offenders.append((str(path.relative_to(TOOLS_ROOT.parent)), module))
    assert offenders == [], f"tools/ nao pode depender de LLM: {offenders}"


def test_tools_never_imports_from_logicpro():
    offenders: list[tuple[str, str]] = []
    for path in _iter_tools_py_files():
        for module in _iter_import_roots(path):
            root = module.split(".", 1)[0]
            if root == "logicpro":
                offenders.append((str(path.relative_to(TOOLS_ROOT.parent)), module))
    assert offenders == [], f"tools/ nao pode importar de logicpro: {offenders}"
