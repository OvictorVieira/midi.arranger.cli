import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "bin" / "midi-arranger"


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_script_is_executable_bash_with_strict_mode() -> None:
    mode = SCRIPT.stat().st_mode

    assert mode & stat.S_IXUSR
    text = SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text


def test_help_documents_subcommands_options_and_examples() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "brief" in result.stdout
    assert "run" in result.stdout
    assert "tool" in result.stdout
    assert "--list-models" in result.stdout
    assert "--tool TOOL" in result.stdout
    assert "--model MODEL" in result.stdout
    assert "--effort LEVEL" in result.stdout
    assert "--cwd DIR" in result.stdout
    assert "Examples:" in result.stdout


def test_unknown_argument_fails_with_clear_usage_error() -> None:
    result = run_cli("--wat")

    assert result.returncode == 64
    assert "Unknown argument '--wat'." in result.stderr
    assert "unbound variable" not in result.stderr


def test_missing_option_value_fails_with_clear_usage_error() -> None:
    result = run_cli("run", "--tool")

    assert result.returncode == 64
    assert "--tool requires a value." in result.stderr


def test_invalid_tool_fails_before_dispatch() -> None:
    result = run_cli("run", "--tool", "not-a-tool")

    assert result.returncode == 64
    assert "Invalid tool 'not-a-tool'." in result.stderr


def test_recognizes_subcommands_and_common_options() -> None:
    brief = run_cli("brief", "--tool", "claude", "--model", "custom", "--effort", "high", "song.mid")
    run = run_cli("run", "--tool=codex", "--model=custom", "--effort=medium", "12")
    tool = run_cli("tool", "--tool", "amp", "analyze", "song.mid")
    list_models = run_cli("--list-models", "--tool", "gemini")

    assert brief.returncode == 69
    assert "brief parsed successfully" in brief.stderr
    assert run.returncode == 69
    assert "run parsed successfully" in run.stderr
    assert "max_iterations=12" in run.stderr
    assert tool.returncode == 69
    assert "tool parsed successfully" in tool.stderr
    assert list_models.returncode == 69
    assert "--list-models parsed successfully" in list_models.stderr


def test_project_root_detection_climbs_to_git_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()

    result = run_cli("run", cwd=nested)

    assert result.returncode == 69
    assert f"project '{project}'" in result.stderr


def test_project_root_detection_climbs_to_state_file(tmp_path: Path) -> None:
    project = tmp_path / "state-project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / "progress.txt").write_text("state\n")

    result = run_cli("brief", "song.mid", cwd=nested)

    assert result.returncode == 69
    assert f"project '{project}'" in result.stderr


def test_cwd_overrides_auto_detected_root(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    nested = outer / "nested"
    override = tmp_path / "override"
    nested.mkdir(parents=True)
    override.mkdir()
    (outer / ".git").mkdir()

    result = run_cli("run", "--cwd", str(override), cwd=nested)

    assert result.returncode == 69
    assert f"project '{override}'" in result.stderr


def test_shellcheck_passes_for_bin_scripts() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is required by the project but is not installed in PATH")

    result = subprocess.run(
        [shellcheck, str(SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
