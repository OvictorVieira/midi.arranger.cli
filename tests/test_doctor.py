"""Testes de `tools/doctor.py` (issue #78)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tools import doctor

# --- checagens individuais --------------------------------------------------


def test_check_python_version_reports_ok_on_this_interpreter():
    result = doctor.check_python_version()
    assert result["ok"] is True
    assert result["minimum"] == "3.11"


def test_check_python_version_fails_below_minimum(monkeypatch: pytest.MonkeyPatch):
    class _FakeVersionInfo:
        major, minor, micro = 3, 9, 0

    monkeypatch.setattr(doctor.sys, "version_info", _FakeVersionInfo())
    result = doctor.check_python_version()
    assert result["ok"] is False
    assert "3.9.0" in result["message"]


def test_check_dependencies_ok_when_mido_and_pretty_midi_installed():
    result = doctor.check_dependencies()
    assert result["ok"] is True
    assert result["missing"] == []
    assert set(result["required"]) == {"mido", "pretty_midi"}


def test_check_dependencies_reports_missing_module(monkeypatch: pytest.MonkeyPatch):
    """Simula ambiente com `pretty_midi` faltando — sem exigir desinstalar de
    verdade o pacote do ambiente de teste."""

    real_find_spec = doctor.importlib.util.find_spec

    def _fake_find_spec(name: str):
        if name == "pretty_midi":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(doctor.importlib.util, "find_spec", _fake_find_spec)
    result = doctor.check_dependencies()
    assert result["ok"] is False
    assert result["missing"] == ["pretty_midi"]
    assert "pretty_midi" in result["message"]
    assert "pip install" in result["message"]


def test_check_registry_lists_registered_tools():
    result = doctor.check_registry()
    assert result["ok"] is True
    assert result["tool_count"] > 0
    assert "render" in result["tools"]
    assert "analyze" in result["tools"]


def test_check_registry_reports_import_failure_without_raising(monkeypatch: pytest.MonkeyPatch):
    """`check_registry` nunca deixa vazar stack trace — erro vira relatorio.

    Simula a falha no ponto que `check_registry` realmente chama
    (`tools.registry.list_tools`), sem depender de um bug real do
    interpretador para exercitar o caminho de erro.
    """
    import tools.registry as registry_mod

    def _broken_list_tools():
        raise RuntimeError("simulated broken registry")

    monkeypatch.setattr(registry_mod, "list_tools", _broken_list_tools)

    result = doctor.check_registry()

    assert result["ok"] is False
    assert result["tool_count"] == 0
    assert "RuntimeError" in result["error"]


def test_check_techniques_is_derived_from_the_engine_registry():
    from tools.techniques.engine import SUPPORTED_TECHNIQUES

    result = doctor.check_techniques()
    assert result["ok"] is True
    assert result["total"] == len(SUPPORTED_TECHNIQUES)
    all_reported = {name for names in result["families"].values() for name in names}
    assert all_reported == set(SUPPORTED_TECHNIQUES)
    assert "drums" in result["families"]
    assert "drums.ghost_notes" in result["families"]["drums"]


def test_check_roles_is_derived_from_render_supported_roles():
    from tools.render import SUPPORTED_ROLES

    result = doctor.check_roles()
    assert result["ok"] is True
    assert set(result["roles"]) == set(SUPPORTED_ROLES)


def test_check_provider_ok_when_binary_found_and_executable(tmp_path: Path):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    result = doctor.check_provider("claude", "found", str(binary))
    assert result["ok"] is True
    assert result["tool"] == "claude"


def test_check_provider_reports_absent_with_actionable_hint():
    result = doctor.check_provider("claude", "absent", None)
    assert result["ok"] is False
    assert "claude" in result["message"]
    assert "not found" in result["message"] or "nao encontrado" in result["message"]


def test_check_provider_reports_not_executable(tmp_path: Path):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\n")
    # sem chmod +x

    result = doctor.check_provider("codex", "found", str(binary))
    assert result["ok"] is False
    assert "codex" in result["message"]


def test_check_provider_reports_binary_that_no_longer_exists(tmp_path: Path):
    missing = tmp_path / "gone"
    result = doctor.check_provider("gemini", "found", str(missing))
    assert result["ok"] is False


def test_check_provider_ok_true_when_no_tool_requested():
    result = doctor.check_provider(None, "not_queried", None)
    assert result["ok"] is True
    assert result["tool"] is None


def test_check_write_permissions_ok_for_writable_directory(tmp_path: Path):
    result = doctor.check_write_permissions([tmp_path])
    assert result["ok"] is True
    assert result["checked"][str(tmp_path)]["ok"] is True
    # a checagem nao deixa residuo
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora permissao de escrita do filesystem")
def test_check_write_permissions_fails_for_readonly_directory(tmp_path: Path):
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        result = doctor.check_write_permissions([readonly])
        assert result["ok"] is False
        assert result["checked"][str(readonly)]["ok"] is False
    finally:
        readonly.chmod(0o700)


def test_web_research_note_is_always_ok_and_informational():
    result = doctor.web_research_note()
    assert result["ok"] is True
    assert "web" in result["message"].lower()


# --- run() e agregacao -------------------------------------------------------


def test_run_reports_healthy_environment(tmp_path: Path):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    report = doctor.run(
        tool="claude", provider_status="found", provider_binary=str(binary),
        project_root=tmp_path,
    )
    assert report["ok"] is True
    assert report["python"]["ok"] is True
    assert report["dependencies"]["ok"] is True
    assert report["registry"]["ok"] is True
    assert report["provider"]["ok"] is True
    assert report["write_permissions"]["ok"] is True


def test_run_marks_environment_unhealthy_when_provider_missing(tmp_path: Path):
    report = doctor.run(tool="claude", provider_status="absent", project_root=tmp_path)
    assert report["ok"] is False
    assert report["provider"]["ok"] is False
    # as outras checagens continuam ok — so o provider reprova.
    assert report["python"]["ok"] is True
    assert report["dependencies"]["ok"] is True


def test_run_marks_environment_unhealthy_when_dependency_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    real_find_spec = doctor.importlib.util.find_spec

    def _fake_find_spec(name: str):
        if name == "mido":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(doctor.importlib.util, "find_spec", _fake_find_spec)
    report = doctor.run(project_root=tmp_path)
    assert report["ok"] is False
    assert report["dependencies"]["ok"] is False
    assert report["dependencies"]["missing"] == ["mido"]


def test_format_report_mentions_techniques_and_roles(tmp_path: Path):
    report = doctor.run(project_root=tmp_path)
    text = doctor.format_report(report)
    assert "drums.ghost_notes" in text
    assert "roles renderizaveis" in text


def test_main_returns_ok_exit_code_for_healthy_environment(tmp_path: Path, capsys):
    exit_code = doctor.main(["--project-root", str(tmp_path)])
    assert exit_code == doctor.EX_OK
    out = capsys.readouterr().out
    assert "midi-arranger doctor" in out


def test_main_returns_environment_exit_code_when_provider_absent(tmp_path: Path, capsys):
    exit_code = doctor.main([
        "--project-root", str(tmp_path), "--tool", "claude", "--provider-status", "absent",
    ])
    assert exit_code == doctor.EX_ENVIRONMENT


def test_main_json_output_is_valid_json(tmp_path: Path, capsys):
    import json

    exit_code = doctor.main(["--project-root", str(tmp_path), "--json"])
    assert exit_code == doctor.EX_OK
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ok"] is True
