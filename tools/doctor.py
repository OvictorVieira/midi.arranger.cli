"""Diagnostico de ambiente local (issue #78).

`doctor` responde uma unica pergunta: **este ambiente esta pronto para rodar
midi-arranger?** Ele nunca invoca a CLI de IA do usuario (nao ha nada de
nao-deterministico aqui) — so confere Python, dependencias, o registro de
tools, o inventario real de tecnicas/roles do motor, permissao de escrita e
se o binario do provider escolhido esta em PATH e e executavel. Mora em
`tools/` porque cada checagem individual e determinista: mesma maquina, mesmo
resultado, sem rede, sem relogio no calculo (so le `sys.version_info`,
`PATH` e o filesystem local) — o mesmo espirito de `tools/presets.py` e
`tools/plugins.py`, que tambem leem o disco local sem depender de LLM.

`bin/midi-arranger` resolve o binario do provider (mesma logica de
`tool_binary_path` em bash) e repassa o resultado por flag — este modulo
NUNCA reimplementa aquela resolucao, so reporta o que recebeu. Isso evita
duas fontes de verdade para "onde fica o binario de cada CLI".

## Codigo de saida

    0   ambiente saudavel
    2   pelo menos uma checagem de ambiente falhou

`doctor` nunca produz o codigo 1 (falha musical/validacao) — ele nao roda
nenhum passo musical, so diagnostica o ambiente. Ver `tools/test_drive.py`
para o companheiro que executa o fluxo real e usa os tres codigos.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

EX_OK = 0
EX_ENVIRONMENT = 2

MIN_PYTHON = (3, 11)

# Unica lista de dependencias que `tools/` pode ter (AGENTS.md — "Qualidade").
# Mesmo par que `install.sh` sonda com `importlib.util.find_spec`.
REQUIRED_DEPENDENCIES: tuple[str, ...] = ("mido", "pretty_midi")


# --- checagens individuais --------------------------------------------------


def check_python_version() -> dict[str, Any]:
    """Python >= 3.11, mesmo piso que `install.sh` exige antes de instalar."""
    info = sys.version_info
    version = f"{info.major}.{info.minor}.{info.micro}"
    minimum = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    ok = (info.major, info.minor) >= MIN_PYTHON
    return {
        "ok": ok,
        "version": version,
        "minimum": minimum,
        "message": (
            f"Python {version} ok (>= {minimum})"
            if ok
            else f"Python {version} < {minimum}; instale Python >= {minimum} e rode de novo"
        ),
    }


def check_dependencies() -> dict[str, Any]:
    """`mido` e `pretty_midi` — as unicas dependencias que `tools/` pode ter.

    Usa `find_spec` em vez de `import` de proposito: importar `pretty_midi`
    carrega numpy/scipy e custa segundos, e a pergunta aqui e "esta
    instalado?", nao "funciona?" — mesma escolha de `install.sh`.
    """
    missing = [m for m in REQUIRED_DEPENDENCIES if importlib.util.find_spec(m) is None]
    ok = not missing
    return {
        "ok": ok,
        "required": list(REQUIRED_DEPENDENCIES),
        "missing": missing,
        "message": (
            "dependencias ok: " + ", ".join(REQUIRED_DEPENDENCIES)
            if ok
            else (
                "dependencias faltando: "
                + ", ".join(missing)
                + f"; instale com: {sys.executable} -m pip install -r requirements.txt"
            )
        ),
    }


def check_registry() -> dict[str, Any]:
    """As tools registradas importam e se registram sem erro.

    Import "eager" de `tools.contract` — o mesmo efeito colateral que
    `tools/cli.py` usa para popular o registry antes de listar.
    """
    try:
        from . import contract  # noqa: F401 — efeito colateral: popula o registry
        from .registry import list_tools

        names = sorted(decl["name"] for decl in list_tools())
        return {
            "ok": True,
            "tool_count": len(names),
            "tools": names,
            "message": f"{len(names)} tool(s) registradas e importadas sem erro",
        }
    except Exception as exc:  # noqa: BLE001 — diagnostico, nunca deve vazar stack
        return {
            "ok": False,
            "tool_count": 0,
            "tools": [],
            "error": f"{type(exc).__name__}: {exc}",
            "message": f"falha ao importar/registrar tools: {type(exc).__name__}: {exc}",
        }


def check_techniques() -> dict[str, Any]:
    """Tecnicas CURRENTLY executaveis, por familia — nunca hardcoded.

    Deriva de `tools.techniques.engine.SUPPORTED_TECHNIQUES`, a mesma tupla
    que `plan.validate` usa como fonte de verdade (AGENTS.md: "SUPPORTED_TECHNIQUES
    deve ser derivado desse registro"). Nunca duplica a lista aqui.
    """
    try:
        from .techniques.engine import SUPPORTED_TECHNIQUES
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "families": {},
            "total": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    families: dict[str, list[str]] = {}
    for canonical in SUPPORTED_TECHNIQUES:
        family, _, _ = canonical.partition(".")
        families.setdefault(family, []).append(canonical)
    for names in families.values():
        names.sort()

    return {
        "ok": True,
        "families": families,
        "total": len(SUPPORTED_TECHNIQUES),
    }


def check_roles() -> dict[str, Any]:
    """Roles renderiaveis pelo motor — derivado de `tools.render.SUPPORTED_ROLES`.

    Mesma regra de "derivado, nunca hardcoded" que `_ROLE_RENDERERS` ja
    garante em `tools/render.py`.
    """
    try:
        from .render import SUPPORTED_ROLES
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "roles": [], "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": True, "roles": sorted(SUPPORTED_ROLES)}


def check_provider(
    tool: str | None, status: str, binary: str | None,
) -> dict[str, Any]:
    """Reporta o que `bin/midi-arranger` ja resolveu sobre o binario do provider.

    `status` vem de fora (`"found"`, `"absent"`, `"not_queried"`) — este
    modulo nao reimplementa `tool_binary_path` do bash, so audita o
    resultado: quando `status == "found"`, confere se o caminho recebido
    ainda existe e e executavel (o binario pode ter sumido entre a resolucao
    em bash e esta checagem, ex.: symlink quebrado).
    """
    if not tool:
        return {
            "ok": True,
            "tool": None,
            "message": "nenhum provider selecionado (--tool); rode com --tool <nome> para checar um.",
        }

    if status == "absent":
        return {
            "ok": False,
            "tool": tool,
            "binary": None,
            "message": (
                f"'{tool}' nao encontrado em PATH. Instale a CLI do {tool} e "
                "rode de novo, ou escolha outro provider com --tool."
            ),
        }

    if status != "found" or not binary:
        return {
            "ok": False,
            "tool": tool,
            "binary": None,
            "message": f"nao foi possivel confirmar o binario de '{tool}' (status={status!r}).",
        }

    path = Path(binary)
    exists = path.exists()
    executable = exists and os.access(path, os.X_OK)
    if executable:
        return {
            "ok": True,
            "tool": tool,
            "binary": str(path),
            "message": f"'{tool}' encontrado e executavel em {path}",
        }
    if not exists:
        message = f"'{tool}' foi resolvido para {path}, mas o caminho nao existe mais"
    else:
        message = f"'{tool}' foi resolvido para {path}, mas o arquivo nao e executavel"
    return {"ok": False, "tool": tool, "binary": str(path), "message": message}


def check_write_permissions(paths: list[Path]) -> dict[str, Any]:
    """Confere escrita nos diretorios relevantes (raiz do projeto, `.midiarranger/`).

    Cria e apaga um arquivo de sonda; nunca deixa residuo quando a checagem
    passa.
    """
    checked: dict[str, dict[str, Any]] = {}
    ok = True
    for path in paths:
        key = str(path)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".midi-arranger-doctor-{os.getpid()}.tmp"
            probe.write_text("doctor probe\n", encoding="utf-8")
            probe.unlink()
            checked[key] = {"ok": True}
        except OSError as exc:
            ok = False
            checked[key] = {"ok": False, "error": str(exc)}
    return {"ok": ok, "checked": checked}


def web_research_note() -> dict[str, Any]:
    """Nota informativa — `doctor` nunca testa acesso web de verdade.

    `tools/` nunca faz chamada de rede (AGENTS.md — "Determinismo nas tools:
    sem relogio, sem random sem seed, sem rede"), e `doctor` segue a mesma
    regra: a capacidade de pesquisa ao vivo durante `brief` depende
    inteiramente da propria CLI de IA do usuario (se ela tem ferramenta de
    busca web habilitada e permissao pra usar). Este check e sempre `ok`;
    existe so para deixar isso explicito no relatorio.
    """
    return {
        "ok": True,
        "message": (
            "midi-arranger nao testa acesso web. A pesquisa de referencia "
            "durante 'brief' depende das ferramentas e permissoes da sua "
            "propria CLI de IA (--tool); tools/ nunca faz chamada de rede."
        ),
    }


# --- agregacao ---------------------------------------------------------------


def default_write_check_paths(project_root: Path) -> list[Path]:
    return [project_root, project_root / ".midiarranger"]


def run(
    *,
    tool: str | None = None,
    provider_status: str = "not_queried",
    provider_binary: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    write_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Roda todas as checagens e devolve um relatorio agregado.

    `ok` no topo e a verdade sobre o AMBIENTE: python, dependencias,
    registry das tools e permissao de escrita ok, e (quando um `tool` foi
    passado) o binario do provider encontrado e executavel. `techniques` e
    `roles` sao inventario informativo — nunca derrubam `ok` sozinhos, a nao
    ser que o import do motor tenha falhado (nesse caso `registry` ja
    reportou o mesmo erro).
    """
    root = project_root or Path.cwd()
    paths = write_paths if write_paths is not None else default_write_check_paths(root)

    python_check = check_python_version()
    deps_check = check_dependencies()
    registry_check = check_registry()
    techniques_check = check_techniques()
    roles_check = check_roles()
    provider_check = check_provider(tool, provider_status, provider_binary)
    write_check = check_write_permissions(paths)
    research_note = web_research_note()

    environment_ok = all([
        python_check["ok"],
        deps_check["ok"],
        registry_check["ok"],
        provider_check["ok"],
        write_check["ok"],
    ])

    return {
        "ok": environment_ok,
        "project_root": str(root),
        "python": python_check,
        "dependencies": deps_check,
        "registry": registry_check,
        "techniques": techniques_check,
        "roles": roles_check,
        "provider": provider_check,
        "model": {
            "configured": model,
            "message": (
                f"modelo configurado via --model: {model}"
                if model
                else "nenhum --model informado; a CLI do provider usa o default dela"
            ),
        },
        "write_permissions": write_check,
        "web_research": research_note,
    }


# --- formatacao humana -------------------------------------------------------


def _mark(ok: bool) -> str:
    return "OK" if ok else "FALHA"


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("midi-arranger doctor")
    lines.append(f"project_root: {report['project_root']}")
    lines.append("")

    lines.append(f"[{_mark(report['python']['ok'])}] {report['python']['message']}")
    lines.append(f"[{_mark(report['dependencies']['ok'])}] {report['dependencies']['message']}")
    lines.append(f"[{_mark(report['registry']['ok'])}] {report['registry']['message']}")
    lines.append(f"[{_mark(report['provider']['ok'])}] provider: {report['provider']['message']}")
    lines.append(f"[{_mark(True)}] model: {report['model']['message']}")
    lines.append(f"[{_mark(report['write_permissions']['ok'])}] write permissions:")
    for path, status in report["write_permissions"]["checked"].items():
        lines.append(f"    [{_mark(status['ok'])}] {path}" + (f" — {status.get('error')}" if not status["ok"] else ""))
    lines.append(f"[{_mark(True)}] {report['web_research']['message']}")

    lines.append("")
    if report["techniques"]["ok"]:
        lines.append(f"tecnicas executaveis pelo motor ({report['techniques']['total']} no total):")
        for family, names in sorted(report["techniques"]["families"].items()):
            lines.append(f"  {family}: {', '.join(names)}")
    else:
        lines.append(f"[FALHA] nao foi possivel introspectar as tecnicas: {report['techniques'].get('error')}")

    lines.append("")
    if report["roles"]["ok"]:
        lines.append(f"roles renderizaveis: {', '.join(report['roles']['roles'])}")
    else:
        lines.append(f"[FALHA] nao foi possivel introspectar os roles: {report['roles'].get('error')}")

    lines.append("")
    lines.append(
        "ambiente saudavel — pronto para 'midi-arranger brief' e 'midi-arranger run'."
        if report["ok"]
        else "ambiente com problema(s) acima — resolva antes de rodar 'brief'/'run'."
    )
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.doctor",
        description="Diagnostico do ambiente local do midi-arranger.",
    )
    parser.add_argument("--tool", default=None, help="provider configurado (--tool do harness)")
    parser.add_argument(
        "--provider-status",
        default="not_queried",
        choices=["found", "absent", "not_queried"],
        help="resultado que bin/midi-arranger ja obteve ao resolver o binario do provider",
    )
    parser.add_argument("--provider-binary", default=None, help="caminho resolvido do binario do provider")
    parser.add_argument("--model", default=None, help="modelo configurado (--model do harness)")
    parser.add_argument("--project-root", default=None, help="raiz do projeto (default: cwd)")
    parser.add_argument("--json", action="store_true", help="imprime o relatorio como JSON em vez de texto")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser() if args.project_root else Path.cwd()
    report = run(
        tool=args.tool,
        provider_status=args.provider_status,
        provider_binary=args.provider_binary,
        model=args.model,
        project_root=project_root,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(format_report(report))

    return EX_OK if report["ok"] else EX_ENVIRONMENT


if __name__ == "__main__":
    raise SystemExit(main())
