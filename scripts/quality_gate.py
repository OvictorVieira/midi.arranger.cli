#!/usr/bin/env python3
"""Quality gate do repo midi.arranger.cli.

Coleta metricas do maquinario em `tools/`, compara contra
`quality-baseline.json` com ratchet (metrica so pode melhorar) e bloqueia
quando qualquer uma regride.

    python3 scripts/quality_gate.py                  # ratchet (default)
    python3 scripts/quality_gate.py --check          # compara contra origin/main
    python3 scripts/quality_gate.py --report-json quality-report.json

Modos
-----
ratchet (default)
    Compara contra o `quality-baseline.json` do working tree. Quando alguma
    metrica melhora, reescreve o baseline. E o modo do dia a dia.

--check
    Compara contra o baseline do `origin/main`. Nao escreve nada. E o modo do
    pre-push e do CI: garante que a branch nao piorou nada em relacao a main,
    mesmo que o baseline local ja tenha sido atualizado no meio do caminho.

Regra inviolavel: o baseline NUNCA e editado a mao. Quem escreve nele e este
script, e so quando uma metrica melhora. Baixar a regua para o gate passar
derrota o proposito dele.
"""

import sys

# Guarda de versao, antes de qualquer `def` (anotacao `X | Y` e avaliada na
# criacao da funcao e explodiria em 3.9/3.10 antes de chegar aqui).
#
# Cobre Python 3.x abaixo de 3.11. NAO cobre Python 2: o interpretador
# parseia o arquivo inteiro antes de executar a primeira linha, entao as
# f-strings mais abaixo ja sao SyntaxError e este bloco nunca roda. Contra o
# 2.7 a protecao e o shebang: invocar com `python scripts/quality_gate.py`
# numa maquina onde `python` e 2.7 falha de forma feia, e nao ha como evitar
# isso de dentro do proprio arquivo.
#
# Mensagem em ASCII puro de proposito: 2.7 rejeita fonte com byte >127 sem
# declaracao de encoding, o que trocaria este erro por um ainda mais opaco.
# noqa UP036: o ruff considera o bloco morto porque `target-version = py311`
# torna a condicao sempre falsa. A premissa e que o codigo so roda no alvo —
# e este bloco existe exatamente para o caso em que nao roda.
if sys.version_info < (3, 11):  # noqa: UP036
    sys.stderr.write(
        f"FAIL: este gate exige Python >= 3.11, mas foi invocado com "
        f"{sys.version.split()[0]} ({sys.executable}).\n"
        f"      Use: python3 scripts/quality_gate.py\n"
        f"      Para instalar dependencias use `python3 -m pip install <pkg>`;\n"
        f"      `pip` sozinho pode apontar para outro interpretador.\n"
    )
    raise SystemExit(1)

# `from __future__ import annotations` seria obrigatoriamente o primeiro
# statement do arquivo, o que impediria a guarda de versao acima. Nao faz
# falta: o gate exige 3.11+, onde `X | Y` em anotacao ja e nativo.
import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "tools"
TESTS = ROOT / "tests"
BASELINE_PATH = ROOT / "quality-baseline.json"
REQUIREMENTS = ROOT / "requirements.txt"

# Metricas em que MAIOR e melhor. Todas as outras: menor e melhor.
HIGHER_IS_BETTER = frozenset({"test_passed", "coverage_pct"})

# Metricas que precisam ser exatamente zero, sempre, independente do baseline.
# Nao entram no ratchet: nao existe "melhorar de 3 para 2 erros de lint".
HARD_ZERO = frozenset({"test_failed", "lint_errors", "security_issues"})

# Metricas nao-numericas: comparadas por igualdade contra o baseline.
IDENTITY: frozenset[str] = frozenset()

PCT_METRICS = frozenset({"coverage_pct", "duplication_pct"})


# --- infra -------------------------------------------------------------------

class GateError(Exception):
    """Falha que aborta o gate."""


def log(msg: str = "") -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", flush=True)


def run(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    allow_failure: bool = False,
    stream: bool = True,
) -> subprocess.CompletedProcess[str]:
    log(f"> {' '.join(cmd)}")
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=False
    )
    if stream:
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
    if not allow_failure and proc.returncode != 0:
        raise GateError(f"{cmd[0]} exited with {proc.returncode}")
    return proc


def git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def have(tool: str, module: str | None = None) -> bool:
    """`True` quando a ferramenta esta disponivel por PATH ou como modulo.

    `module` existe porque nome de comando e nome de modulo divergem: o
    comando `pip-audit` e o modulo `pip_audit`. Sem o parametro, uma
    ferramenta instalada mas com o bin fora do PATH — situacao normal num
    `pip install --user`, onde os scripts vao para ~/Library/Python/X/bin —
    seria reportada como ausente e a metrica sumiria em silencio.
    """
    if shutil.which(tool):
        return True
    probe = subprocess.run(
        [sys.executable, "-m", module or tool, "--version"],
        capture_output=True, check=False,
    )
    return probe.returncode == 0


# --- coleta de metricas ------------------------------------------------------

def collect_tests() -> tuple[int, int, float | None]:
    """Roda a suite UMA vez. Devolve (passed, failed, coverage_pct | None).

    Coverage so e coletada quando o pacote `coverage` existe. Sem ele a
    metrica sai como None e o gate decide o que fazer com base no baseline.

    Ate a issue #34 a suite rodava DUAS vezes: uma sob `coverage run` (para
    a metrica de cobertura) e outra, identica, so para reler o resumo
    textual (`N passed`/`N failed`) que a primeira rodada ja tinha
    produzido e descartava. O resumo agora vem da MESMA execucao que roda
    sob coverage — nao ha razao para rodar a suite inteira duas vezes so
    para reler um numero que a primeira rodada ja imprimiu.
    """
    use_cov = have("coverage")
    if use_cov:
        proc = run(
            [sys.executable, "-m", "coverage", "run",
             "--source", "tools",
             "--omit", "tests/*",
             "-m", "pytest", "-q", "--no-header"],
            cwd=ROOT, allow_failure=True, stream=False,
        )
        cov_json = run(
            [sys.executable, "-m", "coverage", "json", "-o", "-", "--quiet"],
            cwd=ROOT, allow_failure=True, stream=False,
        )
        cov = None
        if cov_json.returncode == 0 and cov_json.stdout.strip():
            try:
                cov = round(
                    json.loads(cov_json.stdout)["totals"]["percent_covered"], 2
                )
            except (ValueError, KeyError):
                cov = None
    else:
        warn(
            "pacote `coverage` ausente — metrica coverage_pct nao sera "
            "coletada. Habilite com: python3 -m pip install coverage"
        )
        proc = run(
            [sys.executable, "-m", "pytest", "-q", "--no-header"],
            cwd=ROOT, allow_failure=True, stream=False,
        )
        cov = None

    out = f"{proc.stdout}\n{proc.stderr}"
    sys.stdout.write(out)
    passed = sum(int(n) for n in re.findall(r"(\d+) passed", out))
    failed = sum(int(n) for n in re.findall(r"(\d+) failed", out))
    errors = sum(int(n) for n in re.findall(r"(\d+) error", out))
    if passed == 0 and failed == 0:
        # Skeleton sem testes ainda: rodada legitima, nao runner quebrado.
        return 0, 0, cov
    return passed, failed + errors, cov


def collect_lint() -> int | None:
    """Erros de ruff. None quando ruff nao esta instalado."""
    if not have("ruff"):
        warn("ruff ausente — metrica lint_errors nao sera coletada")
        return None
    # Linta o pacote, testes E o proprio gate. Um gate que nao se mede tem
    # ponto cego exatamente onde mais dói: se ele quebrar, nada mais e medido.
    proc = run(
        ["ruff", "check", "tools", "tests", "scripts",
         "--output-format", "concise"],
        allow_failure=True,
    )
    if proc.returncode == 0:
        return 0
    return len([ln for ln in proc.stdout.splitlines() if ":" in ln])


def collect_duplication() -> tuple[int, float] | None:
    """(clones, pct) via jscpd. None quando npx/jscpd nao rodam."""
    if not shutil.which("npx"):
        warn("npx ausente — metricas de duplicacao nao serao coletadas")
        return None
    report_dir = ROOT / ".jscpd"
    proc = run(
        ["npx", "--yes", "jscpd", "tools",
         "--reporters", "json", "--output", str(report_dir),
         "--ignore", "**/tests/**", "--silent"],
        allow_failure=True, stream=False,
    )
    report = report_dir / "jscpd-report.json"
    if not report.exists():
        warn(f"jscpd nao gerou relatorio (exit {proc.returncode})")
        return None
    try:
        stats = json.loads(report.read_text())["statistics"]["total"]
        return int(stats["clones"]), round(float(stats["percentage"]), 2)
    except (ValueError, KeyError) as exc:
        warn(f"relatorio do jscpd ilegivel: {exc}")
        return None
    finally:
        shutil.rmtree(report_dir, ignore_errors=True)


def collect_security() -> int | None:
    """Vulnerabilidades altas via pip-audit. None quando ausente."""
    if not have("pip-audit", module="pip_audit"):
        warn(
            "pip-audit ausente — metrica security_issues nao sera coletada. "
            "Habilite com: python3 -m pip install --user pip-audit"
        )
        return None
    # Escopo: requirements.txt, NAO o ambiente inteiro.
    #
    # `pip-audit` sem argumento audita tudo que esta instalado — pip, ruff, as
    # deps do proprio pip-audit — e reporta vulnerabilidade que este repo nao
    # introduziu e nao pode corrigir. Uma metrica que falha por motivo alheio
    # ao projeto vira ruido, e metrica ruidosa acaba ignorada, que e o pior
    # destino de um gate.
    if not REQUIREMENTS.exists():
        warn(f"{REQUIREMENTS.name} ausente — metrica security_issues nao sera coletada")
        return None
    proc = run(
        [sys.executable, "-m", "pip_audit", "-r", str(REQUIREMENTS),
         "-f", "json", "--progress-spinner", "off"],
        allow_failure=True, stream=False,
    )
    if not proc.stdout.strip():
        return 0
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        warn("saida do pip-audit ilegivel")
        return None
    deps = data.get("dependencies", data if isinstance(data, list) else [])
    return sum(len(d.get("vulns", [])) for d in deps)


def collect_nonascii_track_names() -> int | None:
    """Simbolos nao-ASCII no vocabulario de nome de track.

    O meta-evento 0x03 do SMF nao carrega encoding: byte >127 fica a merce
    do decoder do DAW. Um `✓` reintroduzido em tracks.py passa em todo teste
    de string e so aparece corrompido dentro do Logic — por isso a checagem
    e do gate, nao so da suite.

    Retorna None enquanto tools.tracks ainda nao existir; sera coletada assim
    que o modulo for migrado.
    """
    tracks_path = PKG / "tracks.py"
    if not tracks_path.exists():
        warn("tools/tracks.py ainda nao existe — metrica sera coletada apos migracao")
        return None
    sys.path.insert(0, str(ROOT))
    try:
        from tools import tracks  # noqa: PLC0415
    except ImportError as exc:
        raise GateError(f"nao consegui importar tools.tracks: {exc}") from exc
    finally:
        sys.path.pop(0)

    offenders = []
    for name in ("VERIFIED_MARK", "UNVERIFIED_MARK", "SEPARATOR",
                 "PRESET_JOINER", "TRUNCATION_MARK"):
        value = getattr(tracks, name, None)
        if value is None:
            continue
        if any(ord(c) >= 0x80 for c in value):
            offenders.append(f"{name}={value!r}")
    for o in offenders:
        warn(f"simbolo nao-ASCII em nome de track: {o}")
    return len(offenders)


# --- baseline e comparacao ---------------------------------------------------

@dataclass
class Row:
    metric: str
    baseline: object
    current: object
    delta: float | None
    status: str


@dataclass
class Comparison:
    rows: list[Row] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    improved: bool = False


def remote_default_ref() -> str:
    for ref in ("origin/main", "origin/master"):
        if git(["rev-parse", "--verify", "--quiet", ref]).returncode == 0:
            return ref
    return "HEAD~1"


def load_baseline(*, ratchet: bool) -> dict:
    if not ratchet:
        ref = remote_default_ref()
        proc = git(["show", f"{ref}:quality-baseline.json"])
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                log(f"baseline lido de {ref}")
                return json.loads(proc.stdout)
            except ValueError:
                warn(f"baseline em {ref} nao e JSON valido")
        warn(f"nao consegui ler baseline de {ref}; usando working tree")
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text())


def save_baseline(baseline: dict) -> None:
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")


def compare(baseline: dict, current: dict) -> Comparison:
    result = Comparison()
    for metric, value in current.items():
        old = baseline.get(metric)
        has_baseline = old is not None

        if metric in HARD_ZERO:
            worsened, better = value != 0, False
        elif metric in IDENTITY:
            worsened = has_baseline and value != old
            better = False
        elif not has_baseline:
            worsened, better = False, False
        elif metric in HIGHER_IS_BETTER:
            worsened, better = value < old, value > old
        else:
            worsened, better = value > old, value < old

        delta = None
        if has_baseline and isinstance(value, (int, float)) and isinstance(old, (int, float)):
            delta = round(value - old, 2 if metric in PCT_METRICS else 0)

        status = (
            "failed" if worsened
            else "improved" if better
            else "new" if not has_baseline
            else "ok"
        )
        result.rows.append(Row(metric, old, value, delta, status))

        if worsened:
            result.violations.append(
                f"{metric} current={value} baseline={old}"
                if metric not in HARD_ZERO
                else f"{metric}={value} (tem que ser 0)"
            )
        if better or not has_baseline:
            result.improved = True
            baseline[metric] = value
    return result


def write_report(path: Path | None, status: str, rows: list[Row], error: str | None) -> None:
    if path is None:
        return
    path.write_text(json.dumps({
        "project": "midi.arranger.cli",
        "status": status,
        "error": error,
        "metrics": [vars(r) for r in rows],
    }, indent=2) + "\n")


# --- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Quality gate do repo midi.arranger.cli")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="compara contra o baseline de origin/main; nao escreve")
    mode.add_argument("--no-ratchet", action="store_true",
                      help="compara contra o working tree; nao escreve")
    ap.add_argument("--report-json", metavar="PATH",
                    help="grava relatorio JSON")
    args = ap.parse_args()

    ratchet = not (args.check or args.no_ratchet)
    report_path = Path(args.report_json).resolve() if args.report_json else None

    baseline = load_baseline(ratchet=ratchet)
    rows: list[Row] = []
    try:
        log("midi.arranger.cli quality gate\n")

        log("--- Nome de track: ASCII-safety ---")
        nonascii = collect_nonascii_track_names()
        log(f"simbolos nao-ASCII = {nonascii}\n")

        log("--- Lint ---")
        lint = collect_lint()
        log("")

        log("--- Testes + cobertura ---")
        passed, failed, cov = collect_tests()
        if failed:
            raise GateError(f"{failed} teste(s) falharam")
        if cov is None and baseline.get("coverage_pct") is not None:
            raise GateError(
                "baseline exige coverage_pct mas a coleta falhou — "
                "instale `coverage` ou investigue; o gate nao passa as cegas"
            )
        log("")

        log("--- Duplicacao ---")
        dup = collect_duplication()
        if dup is None and baseline.get("duplication_pct") is not None:
            raise GateError("baseline exige metricas de duplicacao mas jscpd falhou")
        log("")

        log("--- Seguranca ---")
        sec = collect_security()
        if sec is None and baseline.get("security_issues") is not None:
            raise GateError("baseline exige security_issues mas pip-audit falhou")
        log("")

        current: dict = {
            "test_passed": passed,
            "test_failed": failed,
        }
        if nonascii is not None:
            current["nonascii_track_symbols"] = nonascii
        if lint is not None:
            current["lint_errors"] = lint
        if cov is not None:
            current["coverage_pct"] = cov
        if dup is not None:
            current["duplication_clones"], current["duplication_pct"] = dup
        if sec is not None:
            current["security_issues"] = sec

        log("--- Comparacao ---")
        result = compare(baseline, current)
        width = max(len(r.metric) for r in result.rows)
        for r in result.rows:
            d = "" if r.delta is None else f"  (delta {r.delta:+})"
            log(f"  {r.status:9s} {r.metric:{width}s}  baseline={r.baseline}  current={r.current}{d}")

        if result.violations:
            write_report(report_path, "failed", result.rows, "; ".join(result.violations))
            raise GateError("gate bloqueado: " + "; ".join(result.violations))

        if ratchet and result.improved:
            save_baseline(baseline)
            log("\nquality-baseline.json atualizado")

        write_report(report_path, "passed", result.rows, None)
        log("\nmidi.arranger.cli quality gate passou")
        return 0

    except GateError as exc:
        write_report(report_path, "failed", rows, str(exc))
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
