"""Testes de `tools/test_drive.py` (issue #78)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import test_drive


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_default_fixture() -> Path:
    if not test_drive.DEFAULT_FIXTURE.exists():
        pytest.skip(f"fixture nao presente: {test_drive.DEFAULT_FIXTURE}")
    return test_drive.DEFAULT_FIXTURE


# --- happy path ---------------------------------------------------------


def test_run_completes_the_full_flow_and_produces_artifacts(tmp_path: Path):
    fixture = _require_default_fixture()
    result = test_drive.run(fixture=fixture, workspace=tmp_path)

    assert result.ok is True
    assert result.error_count == 0
    assert result.output_path.exists()
    assert result.plan_path.exists()
    assert result.report_path.exists()
    assert [s["step"] for s in result.steps] == [
        "analyze", "plan.validate", "render", "validate",
    ]
    assert all(s["ok"] for s in result.steps)


def test_run_never_touches_the_original_fixture(tmp_path: Path):
    fixture = _require_default_fixture()
    before = _sha256(fixture)

    test_drive.run(fixture=fixture, workspace=tmp_path)

    after = _sha256(fixture)
    assert before == after, "o fixture original nao pode ser sobrescrito"


def test_run_source_copy_is_isolated_from_the_fixture(tmp_path: Path):
    fixture = _require_default_fixture()
    result = test_drive.run(fixture=fixture, workspace=tmp_path)

    assert result.source_copy != fixture
    assert result.source_copy.parent == tmp_path


def test_report_json_is_well_formed(tmp_path: Path):
    fixture = _require_default_fixture()
    result = test_drive.run(fixture=fixture, workspace=tmp_path)

    data = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["error_count"] == 0
    assert "steps" in data


# --- ambiente -------------------------------------------------------------


def test_run_fails_with_environment_category_for_missing_fixture(tmp_path: Path):
    missing = tmp_path / "does-not-exist.mid"

    with pytest.raises(test_drive.TestDriveError) as exc_info:
        test_drive.run(fixture=missing, workspace=tmp_path)

    assert exc_info.value.category == "environment"


def test_run_fails_with_environment_category_when_fixture_is_a_directory(tmp_path: Path):
    a_directory = tmp_path / "not-a-file"
    a_directory.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(test_drive.TestDriveError) as exc_info:
        test_drive.run(fixture=a_directory, workspace=workspace)

    assert exc_info.value.category == "environment"


def test_run_fails_with_environment_category_when_no_drum_track(tmp_path: Path):
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    bass = pretty_midi.Instrument(program=32, name="Bass")
    bar_len = 2.0
    beat_len = bar_len / 4
    for bar in range(8):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            bass.notes.append(pretty_midi.Note(
                velocity=90, pitch=36,
                start=start + beat * beat_len,
                end=start + (beat + 1) * beat_len,
            ))
    pm.instruments.extend([piano, bass])
    fixture = tmp_path / "no_drums.mid"
    pm.write(str(fixture))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(test_drive.TestDriveError) as exc_info:
        test_drive.run(fixture=fixture, workspace=workspace)

    assert exc_info.value.category == "environment"
    assert "bateria" in str(exc_info.value)


# --- CLI ----------------------------------------------------------------


def test_main_exit_code_ok_on_happy_path(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch):
    fixture = _require_default_fixture()
    monkeypatch.chdir(tmp_path)

    exit_code = test_drive.main(["--fixture", str(fixture)])

    assert exit_code == test_drive.EX_OK
    out = capsys.readouterr().out
    assert "test-drive OK" in out
    assert "workspace" in out
    assert "workspace temporario removido" in out


def test_main_exit_code_environment_for_missing_fixture(tmp_path: Path, capsys):
    missing = tmp_path / "nope.mid"

    exit_code = test_drive.main(["--fixture", str(missing)])

    assert exit_code == test_drive.EX_ENVIRONMENT
    err = capsys.readouterr().err
    assert "environment" in err


def test_main_without_keep_removes_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _require_default_fixture()
    monkeypatch.chdir(tmp_path)

    captured: dict[str, Path] = {}
    real_run = test_drive.run

    def _spy_run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        captured["workspace"] = result.workspace
        return result

    monkeypatch.setattr(test_drive, "run", _spy_run)

    exit_code = test_drive.main(["--fixture", str(fixture)])

    assert exit_code == test_drive.EX_OK
    assert "workspace" in captured
    assert not captured["workspace"].exists()


def test_main_with_keep_preserves_the_workspace(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch,
):
    fixture = _require_default_fixture()
    monkeypatch.chdir(tmp_path)

    exit_code = test_drive.main(["--fixture", str(fixture), "--keep"])

    assert exit_code == test_drive.EX_OK
    out = capsys.readouterr().out
    workspace_line = next(line for line in out.splitlines() if "workspace:" in line)
    workspace_path = Path(workspace_line.split("workspace:")[1].strip())
    assert workspace_path.exists()
    import shutil
    shutil.rmtree(workspace_path, ignore_errors=True)
