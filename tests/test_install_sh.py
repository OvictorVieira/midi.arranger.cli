"""Testes do instalador da skill (US-004).

Cobrimos:
- `install.sh` existe e e executavel;
- symlink e criado em cada provider presente (`.claude`, `.opencode`, `.agents`)
  e provider ausente e reportado sem quebrar;
- rodar duas vezes nao duplica e reporta como skip (idempotencia);
- alvo inexistente falha ruidosamente;
- `shellcheck` passa quando disponivel;
- `AGENTS.md` na raiz cita a skill (fallback para agente sem sistema de skill).
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


def _run(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALL_SH), str(target)],
        capture_output=True,
        text=True,
        check=True,
    )


def test_install_sh_exists_and_executable():
    assert INSTALL_SH.is_file()
    assert os.access(INSTALL_SH, os.X_OK), "install.sh precisa ser executavel"


def test_symlinks_only_present_providers(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".opencode").mkdir()
    # .agents intencionalmente ausente.

    result = _run(tmp_path)

    for provider in (".claude", ".opencode"):
        link = tmp_path / provider / "skills" / "midi-brief"
        assert link.is_symlink(), f"{provider} deveria ter symlink"
        assert link.resolve() == SKILL_SOURCE.resolve()
    assert not (tmp_path / ".agents").exists()
    assert ".agents" in result.stdout


def test_idempotent_second_run(tmp_path):
    (tmp_path / ".claude").mkdir()
    first = _run(tmp_path)
    second = _run(tmp_path)
    assert "instalado em" in first.stdout
    assert "skip" in second.stdout.lower()
    entries = list((tmp_path / ".claude" / "skills").iterdir())
    assert [e.name for e in entries] == ["midi-brief"]


def test_missing_target_root_fails(tmp_path):
    inexistente = tmp_path / "nao-existe"
    with pytest.raises(subprocess.CalledProcessError) as exc:
        subprocess.run(
            ["bash", str(INSTALL_SH), str(inexistente)],
            capture_output=True,
            text=True,
            check=True,
        )
    assert "inexistente" in exc.value.stderr


def test_refuses_to_clobber_existing_non_symlink(tmp_path):
    claude = tmp_path / ".claude" / "skills"
    claude.mkdir(parents=True)
    (claude / "midi-brief").mkdir()  # ja existe como diretorio real
    with pytest.raises(subprocess.CalledProcessError) as exc:
        subprocess.run(
            ["bash", str(INSTALL_SH), str(tmp_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    assert "symlink" in exc.value.stderr


def test_replaces_stale_symlink(tmp_path):
    claude = tmp_path / ".claude" / "skills"
    claude.mkdir(parents=True)
    stale_target = tmp_path / "outra-fonte"
    stale_target.mkdir()
    (claude / "midi-brief").symlink_to(stale_target)

    _run(tmp_path)

    link = claude / "midi-brief"
    assert link.is_symlink()
    assert link.resolve() == SKILL_SOURCE.resolve()


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
