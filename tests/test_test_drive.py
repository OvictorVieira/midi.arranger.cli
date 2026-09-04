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


def test_run_names_drum_track_that_has_no_track_name_in_source(tmp_path: Path):
    """Regressao (review PR #111, achado 4): track de bateria SEM `track_name`
    (comum em export de DAW) nao pode virar `plan.edits[0].track == ""` — isso
    faz `plan.validate` rejeitar o proprio plano que este modulo monta."""
    import mido
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    bar_len = 2.0
    for bar in range(8):
        start = bar * bar_len
        drums.notes.append(pretty_midi.Note(
            velocity=100, pitch=36, start=start, end=start + 0.1,
        ))
        drums.notes.append(pretty_midi.Note(
            velocity=90, pitch=38, start=start + 1.0, end=start + 1.1,
        ))
    pm.instruments.append(drums)
    fixture = tmp_path / "unnamed_drums.mid"
    pm.write(str(fixture))

    # Remove o `track_name` que pretty_midi escreveu, simulando o export de
    # DAW que motivou o achado: track de bateria fisicamente presente, sem
    # nome nenhum.
    mid = mido.MidiFile(str(fixture))
    for track in mid.tracks:
        is_drum = any(
            not msg.is_meta and getattr(msg, "channel", None) == 9 for msg in track
        )
        if is_drum:
            track[:] = [
                msg for msg in track if not (msg.is_meta and msg.type == "track_name")
            ]
    mid.save(str(fixture))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = test_drive.run(fixture=fixture, workspace=workspace)

    assert result.ok is True
    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    assert plan["edits"][0]["track"] == test_drive.SYNTHETIC_DRUM_TRACK_NAME
    assert plan["edits"][0]["track"] != ""


def test_run_fails_with_environment_category_when_a_dependency_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Regressao (review PR #111, achado 2): `install.sh` so AVISA quando
    `mido`/`pretty_midi` faltam, nunca aborta. Sem preflight, o primeiro
    `import pretty_midi` disparado indiretamente por `contract` levantaria
    `ModuleNotFoundError` cru (traceback, exit 1), nao o `TestDriveError`
    de ambiente (exit 2) documentado."""
    fixture = _require_default_fixture()
    real_find_spec = test_drive.importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "pretty_midi":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(test_drive.importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(test_drive.TestDriveError) as exc_info:
        test_drive.run(fixture=fixture, workspace=tmp_path)

    assert exc_info.value.category == "environment"
    assert "pretty_midi" in str(exc_info.value)


def test_main_exit_code_environment_when_a_dependency_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    fixture = _require_default_fixture()
    real_find_spec = test_drive.importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "mido":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(test_drive.importlib.util, "find_spec", fake_find_spec)

    exit_code = test_drive.main(["--fixture", str(fixture)])

    assert exit_code == test_drive.EX_ENVIRONMENT


def test_style_researched_at_is_a_fixed_constant_never_the_wall_clock(tmp_path: Path):
    """Regressao (review PR #111, achado 5, AGENTS.md: 'Determinismo nas
    tools: sem relogio'). O mesmo fixture/seed rodado em datas diferentes
    tinha que escrever `researched_at` diferente antes desta correcao."""
    from datetime import date

    fixture = _require_default_fixture()
    result = test_drive.run(fixture=fixture, workspace=tmp_path)

    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    researched_at = plan["style"]["drums"]["researched_at"]

    assert researched_at == test_drive.DEFAULT_STYLE_RESEARCHED_AT
    assert researched_at != date.today().isoformat()


def test_run_output_is_byte_identical_across_separate_runs(tmp_path: Path):
    """Mesmo fixture/seed, mesmo workspace (recriado entre as duas rodadas):
    `arrangement-plan.json` tem que sair byte-identico (AGENTS.md: 'Mesmo
    plano, mesma origem, mesma seed: arquivo byte-identico') — o que so vale
    se nada consultar o relogio real. Reusa o mesmo caminho de workspace nas
    duas rodadas para que o unico jeito de o conteudo divergir seja algo
    nao-deterministico (relogio, `random` sem seed) — caminhos de workspace
    diferentes legitimamente apareceriam no JSON e mascarariam a regressao."""
    import shutil

    fixture = _require_default_fixture()
    workspace = tmp_path / "ws"

    workspace.mkdir()
    result_a = test_drive.run(fixture=fixture, workspace=workspace)
    plan_a = result_a.plan_path.read_bytes()
    shutil.rmtree(workspace)

    workspace.mkdir()
    result_b = test_drive.run(fixture=fixture, workspace=workspace)
    plan_b = result_b.plan_path.read_bytes()

    assert plan_a == plan_b


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
