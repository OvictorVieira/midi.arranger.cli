"""Testes do instalador (issue #21).

O instalador escreve em exatamente tres lugares e em nenhum outro:

- `$XDG_BIN_DIR/midi-arranger` — o shim que entra no PATH;
- `$MIDI_ARRANGER_HOME/` — o corpo (bin, prompts, tools, knowledge, skills,
  AGENTS.md, requirements.txt);
- `<provider>/skills/midi-brief` — symlink para o corpo instalado.

Cobrimos: pre-requisito de Python, instalacao do corpo e do shim, o shim
funcionando de fato, idempotencia, respeito a `MIDI_ARRANGER_HOME` e
`XDG_BIN_DIR`, providers ausentes, recusa a sobrescrever o que nao e symlink,
relato preciso de dependencia faltando, e o par `AGENTS.md`/`CLAUDE.md`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
SKILL_SOURCE = REPO_ROOT / "skills" / "midi-brief"

BODY_DIRS = ("bin", "prompts", "tools", "knowledge", "skills")
BODY_FILES = ("AGENTS.md", "requirements.txt")


def _run(
    target: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    # O ambiente do teste nao pode vazar para dentro do instalador: se a
    # maquina que roda a suite ja tiver MIDI_ARRANGER_HOME setado, o teste
    # instalaria na instalacao real do usuario.
    full_env.pop("MIDI_ARRANGER_HOME", None)
    full_env.pop("XDG_BIN_DIR", None)
    full_env.update(env or {})
    return subprocess.run(
        ["bash", str(INSTALL_SH), str(target)],
        capture_output=True,
        text=True,
        check=check,
        env=full_env,
    )


def _home(target: Path) -> Path:
    return target / ".local" / "share" / "midi-arranger"


def _bin(target: Path) -> Path:
    return target / ".local" / "bin"


def _fake_python(tmp_path: Path, version: str, missing: str = "") -> dict[str, str]:
    """PATH com um `python3` falso que responde a sonda do instalador.

    A sonda e um unico `python3 -c` que imprime `<versao>|<modulos faltando>`;
    o falso so ecoa a resposta pedida. Shebang `/bin/sh` absoluto de proposito:
    `#!/usr/bin/env python3` acharia a si mesmo no PATH e recursionaria.
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "python3"
    script.write_text(f"#!/bin/sh\necho '{version}|{missing}'\n", encoding="utf-8")
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def test_install_sh_exists_and_executable():
    assert INSTALL_SH.is_file()
    assert os.access(INSTALL_SH, os.X_OK), "install.sh precisa ser executavel"


# --- corpo e shim ---------------------------------------------------------


def test_installs_body_and_shim(tmp_path):
    result = _run(tmp_path)

    home = _home(tmp_path)
    for item in BODY_DIRS:
        assert (home / item).is_dir(), f"corpo deveria conter {item}/"
    for item in BODY_FILES:
        assert (home / item).is_file(), f"corpo deveria conter {item}"
    assert os.access(home / "bin" / "midi-arranger", os.X_OK)

    shim = _bin(tmp_path) / "midi-arranger"
    assert os.access(shim, os.X_OK)
    assert str(home) in result.stdout
    assert str(shim) in result.stdout


def test_shim_runs_the_installed_harness(tmp_path):
    _run(tmp_path)
    shim = _bin(tmp_path) / "midi-arranger"

    result = subprocess.run([str(shim), "--help"], capture_output=True, text=True, check=True)

    assert "midi-arranger brief" in result.stdout


def test_shim_resolves_prompts_from_installed_body(tmp_path):
    """O shim nao pode ser symlink: o harness deriva PROMPTS_DIR do seu proprio
    diretorio, e um symlink no PATH faria isso apontar para o lugar errado."""
    _run(tmp_path)
    shim = _bin(tmp_path) / "midi-arranger"
    assert not shim.is_symlink()

    project = tmp_path / "projeto"
    project.mkdir()
    result = subprocess.run(
        [str(shim), "--cwd", str(project), "--list-models"],
        capture_output=True,
        text=True,
        check=False,
    )

    # Seja qual for o desfecho, nenhum caminho do checkout pode aparecer:
    # a instalacao tem que ser autocontida.
    assert str(REPO_ROOT / "prompts") not in (result.stdout + result.stderr)


def test_shim_reports_missing_body(tmp_path):
    _run(tmp_path)
    shim = _bin(tmp_path) / "midi-arranger"
    shutil.rmtree(_home(tmp_path))

    result = subprocess.run([str(shim)], capture_output=True, text=True, check=False)

    assert result.returncode == 69
    assert "nao esta instalado" in result.stderr


def test_body_is_free_of_pycache(tmp_path):
    (REPO_ROOT / "tools" / "__pycache__").mkdir(exist_ok=True)
    _run(tmp_path)
    assert list(_home(tmp_path).rglob("__pycache__")) == []


def test_reinstall_drops_files_removed_from_source(tmp_path):
    _run(tmp_path)
    orphan = _home(tmp_path) / "prompts" / "OBSOLETO.md"
    orphan.write_text("sobra de uma versao antiga", encoding="utf-8")

    _run(tmp_path)

    assert not orphan.exists(), "reinstalar tem que limpar o que sumiu da origem"


# --- variaveis de ambiente ------------------------------------------------


def test_respects_midi_arranger_home_and_xdg_bin_dir(tmp_path):
    home = tmp_path / "corpo-custom"
    bin_dir = tmp_path / "bin-custom"

    _run(tmp_path, env={"MIDI_ARRANGER_HOME": str(home), "XDG_BIN_DIR": str(bin_dir)})

    assert (home / "bin" / "midi-arranger").is_file()
    assert (bin_dir / "midi-arranger").is_file()
    assert not _home(tmp_path).exists()
    assert not _bin(tmp_path).exists()


def test_shim_points_at_the_home_it_was_installed_with(tmp_path):
    home = tmp_path / "corpo-custom"
    bin_dir = tmp_path / "bin-custom"
    _run(tmp_path, env={"MIDI_ARRANGER_HOME": str(home), "XDG_BIN_DIR": str(bin_dir)})

    shim_text = (bin_dir / "midi-arranger").read_text(encoding="utf-8")

    assert str(home) in shim_text


# --- Python ---------------------------------------------------------------


@pytest.mark.parametrize("version", ["3.10.14", "2.7.18"])
def test_fails_clearly_on_old_python(tmp_path, version):
    result = _run(tmp_path, env=_fake_python(tmp_path, version), check=False)

    assert result.returncode == 69
    assert version in result.stderr
    assert ">= 3.11" in result.stderr
    assert not _home(tmp_path).exists(), "nada pode ser instalado se o Python nao serve"


@pytest.mark.parametrize("version", ["3.11.0", "3.13.2", "4.0.0"])
def test_accepts_supported_python(tmp_path, version):
    result = _run(tmp_path, env=_fake_python(tmp_path, version))

    assert (_home(tmp_path) / "bin" / "midi-arranger").is_file()
    assert version in result.stdout


def test_reports_missing_dependency_with_exact_command(tmp_path):
    env = _fake_python(tmp_path, "3.12.4", missing="mido pretty_midi")

    result = _run(tmp_path, env=env)

    assert "FALTANDO: mido pretty_midi" in result.stdout
    assert f"pip install -r {_home(tmp_path)}/requirements.txt" in result.stdout
    # Dependencia faltando avisa, nao aborta: o harness roda sem as tools.
    assert (_home(tmp_path) / "bin" / "midi-arranger").is_file()


def test_reports_dependencies_ok_when_present(tmp_path):
    result = _run(tmp_path)
    assert "dependencias python: ok" in result.stdout


# --- skill ----------------------------------------------------------------


def test_symlinks_only_present_providers(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".opencode").mkdir()
    # .agents intencionalmente ausente.

    result = _run(tmp_path)

    for provider in (".claude", ".opencode"):
        link = tmp_path / provider / "skills" / "midi-brief"
        assert link.is_symlink(), f"{provider} deveria ter symlink"
        # Aponta para o corpo instalado, nao para o checkout: harness e skill
        # tem que ser sempre a mesma versao.
        assert link.resolve() == (_home(tmp_path) / "skills" / "midi-brief").resolve()
        assert (link / "SKILL.md").read_text(encoding="utf-8") == (
            SKILL_SOURCE / "SKILL.md"
        ).read_text(encoding="utf-8")
    assert not (tmp_path / ".agents").exists()
    assert ".agents" in result.stdout


def test_idempotent_second_run(tmp_path):
    (tmp_path / ".claude").mkdir()
    first = _run(tmp_path)
    second = _run(tmp_path)
    assert "instalada em" in first.stdout
    assert "skip" in second.stdout.lower()
    entries = list((tmp_path / ".claude" / "skills").iterdir())
    assert [e.name for e in entries] == ["midi-brief"]


def test_missing_target_root_fails(tmp_path):
    inexistente = tmp_path / "nao-existe"
    result = _run(inexistente, check=False)
    assert result.returncode != 0
    assert "inexistente" in result.stderr


def test_refuses_to_clobber_existing_non_symlink(tmp_path):
    claude = tmp_path / ".claude" / "skills"
    claude.mkdir(parents=True)
    (claude / "midi-brief").mkdir()  # ja existe como diretorio real

    result = _run(tmp_path, check=False)

    assert result.returncode != 0
    assert "symlink" in result.stderr


def test_replaces_stale_symlink(tmp_path):
    claude = tmp_path / ".claude" / "skills"
    claude.mkdir(parents=True)
    stale_target = tmp_path / "outra-fonte"
    stale_target.mkdir()
    (claude / "midi-brief").symlink_to(stale_target)

    _run(tmp_path)

    link = claude / "midi-brief"
    assert link.is_symlink()
    assert link.resolve() == (_home(tmp_path) / "skills" / "midi-brief").resolve()


# --- nada fora dos diretorios declarados ----------------------------------


def test_writes_nothing_outside_declared_directories(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".claude").mkdir()
    (root / "intocado.txt").write_text("nao mexa", encoding="utf-8")

    _run(root)

    top_level = sorted(p.name for p in root.iterdir())
    assert top_level == [".claude", ".local", "intocado.txt"]
    assert (root / "intocado.txt").read_text(encoding="utf-8") == "nao mexa"
    assert sorted(p.name for p in (root / ".claude").iterdir()) == ["skills"]


# --- shellcheck e instrucoes portateis ------------------------------------


def test_shellcheck_passes():
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck nao instalado no ambiente")
    result = subprocess.run(
        [shellcheck, str(INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_agents_md_points_to_skill():
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "skills/midi-brief" in text, (
        "AGENTS.md precisa citar `skills/midi-brief` para o agente sem sistema de skill"
    )


def test_claude_md_imports_agents_md():
    """Claude Code nao le AGENTS.md; importa em vez de duplicar a instrucao."""
    claude_md = REPO_ROOT / "CLAUDE.md"
    assert claude_md.is_file()
    assert claude_md.read_text(encoding="utf-8").strip() == "@AGENTS.md"
