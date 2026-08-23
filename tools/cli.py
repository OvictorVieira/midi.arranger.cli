"""CLI de invocacao de tools (US-002).

    python -m tools.cli tool <nome> --input arquivo.json
    python -m tools.cli tool <nome> --input -           # le de stdin
    python -m tools.cli --list                          # lista tools
    python -m tools.cli --schema <nome>                 # imprime schemas

Exit code:
    0 quando o envelope retorna `ok=true`.
    != 0 quando `ok=false` — o envelope de falha vai para stdout como JSON,
    igual ao envelope de sucesso, para o consumidor parsear sempre da mesma
    forma. Stack trace NUNCA sai — erros de IO/JSON viram envelope com codigo
    proprio (E_INPUT_FILE, E_INPUT_JSON).

Este modulo NAO vive em `bin/` porque `bin/` e territorio do harness
(nao-deterministico, com LLM). O CLI de tool e deterministico e testavel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Import "eager" do contract para que o registry esteja populado quando o
# CLI rodar. `tools.contract` registra todas as tools no import.
from . import contract  # noqa: F401 — side effect: bootstrap do registry
from .registry import call, failure, get, list_tools


def _read_payload(source: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Le o payload JSON de `source`. Devolve (payload, envelope_de_erro).

    Exatamente um dos dois e None. `source` == "-" le de stdin.
    """
    try:
        if source == "-":
            raw = sys.stdin.read()
        else:
            path = Path(source).expanduser()
            if not path.exists():
                return None, failure(
                    "E_INPUT_FILE",
                    f"arquivo de input nao encontrado: {source}",
                    hint="verifique o caminho passado em --input",
                )
            if not path.is_file():
                return None, failure(
                    "E_INPUT_FILE",
                    f"caminho de input nao e arquivo: {source}",
                )
            try:
                raw = path.read_text(encoding="utf-8")
            except PermissionError:
                return None, failure(
                    "E_INPUT_FILE",
                    f"sem permissao para ler {source}",
                    hint="cheque as permissoes do arquivo",
                )
            except OSError as exc:
                return None, failure(
                    "E_INPUT_FILE",
                    f"erro de IO lendo {source}: {exc}",
                )
    except OSError as exc:
        return None, failure(
            "E_INPUT_FILE",
            f"erro lendo input: {exc}",
        )

    raw = raw.strip()
    if not raw:
        # Payload vazio e legitimo para tools sem entrada — vira {}.
        return {}, None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, failure(
            "E_INPUT_JSON",
            f"input nao e JSON valido: {exc.msg} (linha {exc.lineno}, col {exc.colno})",
            path=f"line {exc.lineno}",
        )

    if not isinstance(data, dict):
        return None, failure(
            "E_INPUT_JSON",
            f"input JSON precisa ser um objeto (dict), recebi {type(data).__name__}",
        )
    return data, None


def _print_envelope(envelope: dict[str, Any]) -> None:
    """Serializa o envelope em JSON estavel para stdout."""
    print(json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True))


def _cmd_list() -> int:
    """`--list`: nome e descricao de todas as tools."""
    for decl in list_tools():
        print(f"{decl['name']}")
        for line in decl["description"].strip().splitlines():
            print(f"  {line}")
        print()
    return 0


def _cmd_schema(name: str) -> int:
    """`--schema <nome>`: schemas de entrada e saida da tool."""
    tool = get(name)
    if tool is None:
        _print_envelope(failure(
            "E_TOOL_NOT_FOUND",
            f"tool desconhecida: {name!r}",
            hint=f"disponiveis: {sorted(d['name'] for d in list_tools())}",
        ))
        return 2
    _print_envelope({
        "ok": True,
        "data": {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        },
        "warnings": [],
    })
    return 0


def _cmd_tool(name: str, input_source: str) -> int:
    payload, err = _read_payload(input_source)
    if err is not None:
        _print_envelope(err)
        return 2
    envelope = call(name, payload)
    _print_envelope(envelope)
    return 0 if envelope["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.cli",
        description="Invoca uma tool do maquinario determinstico do midi.arranger.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="lista as tools registradas")
    group.add_argument("--schema", metavar="NOME",
                       help="imprime o schema de entrada e saida da tool NOME")
    group.add_argument("subcommand", nargs="?", choices=["tool"],
                       help="`tool <nome> --input <arq>` invoca a tool")

    parser.add_argument("name", nargs="?", help="nome da tool a invocar")
    parser.add_argument(
        "--input", dest="input_source",
        help='arquivo JSON com o payload; use "-" para ler de stdin',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return _cmd_list()
    if args.schema:
        return _cmd_schema(args.schema)

    # subcomando `tool`
    if args.subcommand != "tool" or not args.name:
        parser.error("uso: tool <nome> --input <arq|->")
    if not args.input_source:
        parser.error("--input e obrigatorio para o subcomando `tool`")
    return _cmd_tool(args.name, args.input_source)


if __name__ == "__main__":
    raise SystemExit(main())
