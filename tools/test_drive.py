"""Caminho guiado de validacao local do MVP (issue #78).

`test-drive` roda o subconjunto do fluxo de 10 passos (docs/arquitetura.md
§3) que hoje e maquinario puro, sem chamar nenhuma IA:

    1. ANALISAR      tool `analyze`
    (2-5, pesquisa e decisao — SUBSTITUIDOS por um perfil MOCKADO fixo,
     nunca pesquisa ao vivo)
    6. VALIDAR PLANO  tool `plan.validate`
    7. CONSTRUIR      tool `render`
    8. VERIFICAR      tool `validate`

Cada chamada passa pelo mesmo `tools.registry.call` que `python -m tools.cli
tool <nome>` usaria — este modulo nao contorna o contrato de tool, ele o
exercita, exatamente como o resto do produto exercitaria.

O fixture default e uma copia de `tests/fixtures/corpus_drums/ENTRE NÓS.mid`
que vive em `tools/fixtures/test_drive/` (ver README la) — citado em
`docs/objetivo.md` §4 como "o fixture mais valioso do conjunto": bateria
100% em velocity 127, zero ghost note, zero desvio de grade. Se o motor de
tecnicas produzir alguma coisa a partir dele, o motor funciona de verdade.
A copia existe porque `install.sh` so instala `tools/` (entre outros) — nao
`tests/` — e o comando instalado (`midi-arranger test-drive`, sem
`--fixture`) precisa do fixture dentro do corpo instalado (AGENTS.md —
"Instalacao"). `--fixture` sobrescreve para qualquer outro MIDI.

## Isolamento

Todo trabalho acontece numa copia do fixture dentro de um workspace
temporario — o arquivo original NUNCA e aberto para escrita (mesma garantia
que `tools/render.py` da para o MIDI de origem de qualquer render real). O
hash do fixture original e conferido antes e depois da execucao como
cinto-de-seguranca. Sem `--keep`, o workspace e apagado ao final;
com `--keep`, ele e preservado e o caminho e impresso.

## Codigo de saida

    0   fluxo completo, sem erro de validacao musical
    1   fluxo rodou, mas algum passo devolveu `ok=false` ou algum validador
        (harmony/placement/artifice/persona/transition) reportou severidade
        `error`
    2   falha de ambiente antes do fluxo rodar — fixture ausente, workspace
        sem escrita, dependencia faltando etc (mesmo codigo de
        `tools.doctor.EX_ENVIRONMENT`)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .doctor import EX_ENVIRONMENT, REQUIRED_DEPENDENCIES
from .plan import DEFAULT_STYLE_RESEARCHED_AT

EX_OK = 0
EX_MUSICAL = 1

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
# Dentro de `tools/` (nao `tests/`) de proposito: `install.sh` copia `tools/`
# inteiro para o corpo instalado, mas nunca `tests/` (AGENTS.md —
# "Instalacao"). Ver `tools/fixtures/test_drive/README.md`.
DEFAULT_FIXTURE = PACKAGE_ROOT / "fixtures" / "test_drive" / "ENTRE NÓS.mid"

MOCKED_STYLE_TECHNIQUE = "drums.ghost_notes"
MOCKED_STYLE_FAMILY = "drums"
TEST_DRIVE_SEED = 424242

_ISSUE_KEYS = (
    "harmony_issues", "placement_issues", "artifice_issues",
    "persona_issues", "transition_issues",
)


class TestDriveError(Exception):
    """Erro estruturado de `test-drive`.

    `category` distingue os dois eixos do codigo de saida: `"environment"`
    (EX_ENVIRONMENT=2, nada musical rodou ainda) vs `"musical"` (EX_MUSICAL=1,
    o fluxo rodou e algum passo/validador reprovou).
    """

    # O nome comeca com `Test` por simetria com `TestDriveResult`/o comando
    # `test-drive`, nao porque e uma classe de teste — diz isso ao pytest
    # para nao tentar coleta-la (ela tem `__init__`, o que geraria warning).
    __test__ = False

    def __init__(self, category: str, message: str, *, detail: Any = None) -> None:
        if category not in ("environment", "musical"):
            raise ValueError(f"categoria invalida: {category!r}")
        self.category = category
        self.detail = detail
        super().__init__(message)


@dataclass
class TestDriveResult:
    __test__ = False  # ver comentario equivalente em `TestDriveError`

    ok: bool
    workspace: Path
    source_copy: Path
    output_path: Path
    plan_path: Path
    report_path: Path
    steps: list[dict[str, Any]] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    error_count: int = 0
    warning_count: int = 0


def _sha256_bytes(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_dependencies() -> None:
    """Preflight de `mido`/`pretty_midi` antes de qualquer import arriscado.

    `install.sh` so AVISA quando essas dependencias faltam (AGENTS.md —
    "Instalacao"), nunca aborta a instalacao. Sem esta checagem, o primeiro
    `import mido`/`import pretty_midi` disparado por `analyze`/`render`/etc
    (via `from . import contract`) levantaria `ModuleNotFoundError` fora de
    qualquer `try/except` deste modulo, virando traceback cru e exit 1 em vez
    do exit 2 (`EX_ENVIRONMENT`) documentado. Usa `find_spec`, nao `import`,
    mesma escolha de `tools.doctor.check_dependencies` — nao importa
    `pretty_midi` (custa numpy/scipy) so para perguntar se ele existe.
    """
    missing = [m for m in REQUIRED_DEPENDENCIES if importlib.util.find_spec(m) is None]
    if missing:
        raise TestDriveError(
            "environment",
            (
                "dependencias faltando: "
                + ", ".join(missing)
                + f"; instale com: {sys.executable} -m pip install -r requirements.txt"
            ),
        )


def _prepare_workspace(*, keep: bool) -> Path:
    prefix = "midi-arranger-test-drive-"
    try:
        workspace = Path(tempfile.mkdtemp(prefix=prefix))
    except OSError as exc:
        raise TestDriveError(
            "environment", f"nao foi possivel criar workspace temporario: {exc}",
        ) from exc
    return workspace


def _copy_fixture(fixture: Path, workspace: Path) -> Path:
    if not fixture.exists():
        raise TestDriveError(
            "environment",
            (
                f"fixture nao encontrado: {fixture}. "
                "Rode a partir de um checkout do repositorio (o fixture vive em "
                "tests/fixtures/), ou passe --fixture apontando para um MIDI seu."
            ),
        )
    if not fixture.is_file():
        raise TestDriveError("environment", f"fixture nao e um arquivo: {fixture}")

    before_hash = _sha256_bytes(fixture)
    dest = workspace / "source.mid"
    try:
        shutil.copy2(fixture, dest)
    except OSError as exc:
        raise TestDriveError(
            "environment", f"nao foi possivel copiar o fixture para o workspace: {exc}",
        ) from exc

    after_hash = _sha256_bytes(fixture)
    if before_hash != after_hash:
        # Isto nunca deveria acontecer — `shutil.copy2` so le o fixture — mas
        # a mesma garantia que `tools/render.py` da para o MIDI de origem vale
        # aqui: o fixture original NUNCA e sobrescrito. Se o hash mudou, algo
        # fora deste processo mexeu no arquivo; falhamos alto em vez de seguir.
        raise TestDriveError(
            "environment",
            f"fixture original mudou durante a copia: {fixture} (isto nao deveria acontecer)",
        )
    return dest


# Nome sintetico deterministico para uma track de bateria sem `track_name` —
# nunca sorteado, sempre o mesmo texto (ver `_ensure_named_drum_track`).
SYNTHETIC_DRUM_TRACK_NAME = "test-drive drums (sem nome na origem)"


def _ensure_named_drum_track(midi_path: Path) -> str | None:
    """Primeira SMF track com nota no canal de bateria (canal 9, 0-indexado).

    `plan.edits[].track` exige string nao-vazia (`tools/plan.py` —
    `_require_nonempty_str`), entao track de bateria sem `track_name`
    (comum em MIDI exportado de DAW) nao pode virar `""` no plano mockado —
    isso faria `plan.validate` rejeitar o proprio plano que este modulo
    monta. Quando a track de bateria encontrada nao tem nome, GRAVA um nome
    sintetico deterministico (`SYNTHETIC_DRUM_TRACK_NAME`) na COPIA de
    trabalho (`midi_path` — nunca o fixture original, que ja foi conferido
    byte-a-byte antes desta chamada) e devolve esse nome. Devolve o nome
    existente quando a track ja tem um. `None` quando nenhuma track usa o
    canal de bateria — nesse caso `test-drive` nao tem o que editar.
    """
    import mido

    mid = mido.MidiFile(str(midi_path))
    for track in mid.tracks:
        name: str | None = None
        is_drum = False
        for msg in track:
            if msg.is_meta and msg.type == "track_name":
                name = msg.name
            if not msg.is_meta and getattr(msg, "channel", None) == 9:
                is_drum = True
        if is_drum:
            if name:
                return name
            track.insert(0, mido.MetaMessage(
                "track_name", name=SYNTHETIC_DRUM_TRACK_NAME, time=0,
            ))
            mid.save(str(midi_path))
            return SYNTHETIC_DRUM_TRACK_NAME
    return None


def _build_mocked_plan(source_copy: Path) -> dict[str, Any]:
    """Monta o plano com um perfil de estilo MOCKADO — nunca pesquisa ao vivo.

    Usa `plan.skeleton` (tool real) para `source_midi`/`version`/`seed`, e
    substitui `sections` por uma unica secao cobrindo a musica inteira: o
    skeleton automatico gera uma secao por compasso quando o MIDI nao tem
    marker musicalmente significativo, o que so gera ruido de transicao
    (AC-14) sem valor nenhum para este smoke test.
    """
    from . import contract  # noqa: F401 — popula o registry
    from .registry import call

    env = call("plan.skeleton", {"midi_path": str(source_copy), "seed": TEST_DRIVE_SEED})
    if not env["ok"]:
        raise TestDriveError(
            "environment", f"plan.skeleton falhou: {env['error']['message']}", detail=env["error"],
        )
    plan = env["data"]["plan"]

    drum_track = _ensure_named_drum_track(source_copy)
    if drum_track is None:
        raise TestDriveError(
            "environment",
            (
                f"nenhuma track de bateria (canal MIDI 10) encontrada em {source_copy.name}; "
                "test-drive precisa de uma track de bateria para exercitar "
                f"'{MOCKED_STYLE_TECHNIQUE}' — use --fixture com um MIDI que tenha bateria."
            ),
        )

    bars = plan["source_midi"]["bars"]
    plan["sections"] = [{
        "label": "FULL",
        "kind": "verse",
        "start_bar": 0,
        "end_bar": bars,
        "source": "marker",
        "protagonist": "drum_groove",
        "energy": {
            "densidade": 5, "impacto": 5, "largura": 5,
            "altura": 5, "instabilidade": 5,
        },
    }]
    plan["transitions"] = []
    plan["elements"] = []
    plan["edits"] = [{"track": drum_track, "profile": "drums", "intensity": 0.0}]
    plan["style"] = {
        MOCKED_STYLE_FAMILY: {
            "reference": "Perfil mockado de test-drive (sem pesquisa ao vivo)",
            # Constante fixa, nunca o relogio (AGENTS.md — "Determinismo nas
            # tools: sem relogio"). Mesma constante que `tools/plan.py` ja usa
            # para este exato tipo de placeholder mockado.
            "researched_at": DEFAULT_STYLE_RESEARCHED_AT,
            "sources": ["mock://midi-arranger-test-drive"],
            "confidence": "default",
            "techniques": [{"name": MOCKED_STYLE_TECHNIQUE}],
            "parameters": {},
        },
    }
    return plan


def _write_mocked_brief(workspace: Path, plan: dict[str, Any]) -> dict[str, str]:
    from .brief_ref import brief_sha256

    authorized: dict[str, dict[str, list[str]]] = {}
    for family, entry in plan.get("style", {}).items():
        names = [t["name"] for t in entry.get("techniques", [])]
        if names:
            authorized[family] = {"authorized_techniques": names}

    brief_path = workspace / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({"style": authorized}, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return {"path": str(brief_path), "sha256": brief_sha256(brief_path)}


def _issue_severity_counts(data: dict[str, Any]) -> tuple[dict[str, int], int, int]:
    counts: dict[str, int] = {}
    errors = 0
    warnings = 0
    for key in _ISSUE_KEYS:
        for issue in data.get(key, []):
            severity = issue.get("severity", "warning")
            counts[severity] = counts.get(severity, 0) + 1
            if severity == "error":
                errors += 1
            else:
                warnings += 1
    return counts, errors, warnings


def run(
    *,
    fixture: Path | None = None,
    keep: bool = False,
    workspace: Path | None = None,
) -> TestDriveResult:
    """Executa o fluxo guiado inteiro. Levanta `TestDriveError` em falha.

    `workspace`, quando passado, e usado como esta (o chamador controla o
    ciclo de vida) — existe para teste. No uso normal via CLI, `main()` cria
    o workspace com `_prepare_workspace` e decide se apaga no final.
    """
    _check_dependencies()

    fixture = fixture or DEFAULT_FIXTURE
    ws = workspace or _prepare_workspace(keep=keep)

    steps: list[dict[str, Any]] = []
    step_warnings: dict[str, list[dict[str, Any]]] = {}

    def record(name: str, env: dict[str, Any]) -> dict[str, Any]:
        steps.append({"step": name, "ok": env["ok"]})
        if not env["ok"]:
            raise TestDriveError(
                "musical" if name in ("plan.validate", "render", "validate") else "environment",
                f"{name} falhou: {env['error']['message']}",
                detail=env["error"],
            )
        step_warnings[name] = env.get("warnings", [])
        return env["data"]

    source_copy = _copy_fixture(fixture, ws)

    from . import contract  # noqa: F401 — popula o registry
    from .registry import call

    analyze_data = record("analyze", call("analyze", {"midi_path": str(source_copy)}))
    steps[-1]["summary"] = {
        "bars": analyze_data["bars"], "tempo": analyze_data["tempo"],
    }

    plan = _build_mocked_plan(source_copy)
    brief_ref = _write_mocked_brief(ws, plan)
    plan["brief_ref"] = brief_ref

    plan_path = ws / "arrangement-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    validate_plan_data = record(
        "plan.validate", call("plan.validate", {"plan": plan, "midi_path": str(source_copy)}),
    )
    if not validate_plan_data["valid"]:
        raise TestDriveError(
            "musical",
            f"plano mockado invalido: {validate_plan_data['errors']}",
            detail=validate_plan_data["errors"],
        )

    output_path = ws / "arranged.mid"
    render_data = record("render", call("render", {
        "midi_path": str(source_copy), "plan": plan, "output_path": str(output_path),
    }))

    validate_data = record("validate", call("validate", {
        "midi_path": str(source_copy), "rendered_path": str(output_path), "plan": plan,
    }))

    issue_counts, error_count, warning_count = _issue_severity_counts(validate_data)

    report = {
        "ok": error_count == 0,
        "fixture": str(fixture),
        "source_copy": str(source_copy),
        "output_path": str(output_path),
        "plan_path": str(plan_path),
        "steps": steps,
        "render_warnings": step_warnings.get("render", []),
        "issue_counts": issue_counts,
        "error_count": error_count,
        "warning_count": warning_count,
        "elements_rendered": len(render_data.get("elements", [])),
        "edits_rendered": len(render_data.get("edits", [])),
    }
    report_path = ws / "test-drive-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if error_count > 0:
        raise TestDriveError(
            "musical",
            f"{error_count} issue(s) de severidade 'error' no relatorio de validacao",
            detail=issue_counts,
        )

    return TestDriveResult(
        ok=True,
        workspace=ws,
        source_copy=source_copy,
        output_path=output_path,
        plan_path=plan_path,
        report_path=report_path,
        steps=steps,
        issue_counts=issue_counts,
        error_count=error_count,
        warning_count=warning_count,
    )


# --- CLI ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.test_drive",
        description=(
            "Roda o fluxo analyze -> plan.validate -> render -> validate sobre um "
            "fixture versionado, com um perfil de estilo MOCKADO (sem pesquisa ao "
            "vivo), num workspace isolado."
        ),
    )
    parser.add_argument(
        "--fixture", default=None,
        help=f"MIDI a usar (default: {DEFAULT_FIXTURE})",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="preserva o workspace temporario em vez de apagar ao final",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fixture = Path(args.fixture).expanduser() if args.fixture else DEFAULT_FIXTURE

    workspace: Path | None = None
    try:
        workspace = _prepare_workspace(keep=args.keep)
        result = run(fixture=fixture, keep=args.keep, workspace=workspace)
    except TestDriveError as exc:
        print(f"test-drive FALHOU ({exc.category}): {exc}", file=sys.stderr)
        if exc.detail is not None:
            print(json.dumps(exc.detail, indent=2, ensure_ascii=False, default=str))
        if not args.keep and workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
        elif workspace is not None:
            print(f"workspace preservado em: {workspace}")
        return EX_MUSICAL if exc.category == "musical" else EX_ENVIRONMENT

    print("test-drive OK")
    print(f"  workspace:      {result.workspace}")
    print(f"  source (copia): {result.source_copy}")
    print(f"  plano:          {result.plan_path}")
    print(f"  midi renderizado: {result.output_path}")
    print(f"  relatorio:      {result.report_path}")
    print(
        f"  issues: {result.error_count} error(s), {result.warning_count} warning(s) "
        f"({result.issue_counts})",
    )
    if not args.keep:
        shutil.rmtree(result.workspace, ignore_errors=True)
        print("  workspace temporario removido (use --keep para preservar)")
    return EX_OK


if __name__ == "__main__":
    raise SystemExit(main())
