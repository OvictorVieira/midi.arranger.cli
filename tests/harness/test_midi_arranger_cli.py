import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "bin" / "midi-arranger"
FIXTURE_BIN_DIR = REPO_ROOT / "tests" / "harness" / "fixtures" / "bin"
SUPPORTED_MOCK_BINARIES = [
    "claude",
    "codex",
    "agy",
    "cursor-agent",
    "opencode",
    "amp",
    "gemini",
]


def run_cli(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env is not None:
        run_env.update(env)

    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def install_mock_binary(tmp_path: Path, binary_name: str) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log_path = tmp_path / f"{binary_name}.log"
    binary = bin_dir / binary_name
    shutil.copy2(FIXTURE_BIN_DIR / binary_name, binary)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)

    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "MOCK_LOG": str(log_path),
    }
    return log_path, env


def prepare_run_project(tmp_path: Path, name: str = "project") -> Path:
    project = tmp_path / name
    project.mkdir()
    (project / ".git").mkdir()
    (project / "arrangement-brief.json").write_text('{"input_midi":"song.mid"}\n')
    return project


def brief_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def install_model_discovery_binary(
    tmp_path: Path,
    binary_name: str,
    configured: str,
    models: list[str],
    efforts: list[str],
) -> dict[str, str]:
    bin_dir = tmp_path / "discovery-bin"
    bin_dir.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    binary = bin_dir / binary_name
    models_text = " ".join(models)
    efforts_text = "|".join(efforts)
    binary.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
args="$*"
case "$args" in
  "--help"|"exec --help"|"run --help")
    printf 'Configured model: {configured}\\n'
    printf 'Available models: {models_text}\\n'
    if [[ -n "{efforts_text}" ]]; then
      printf 'Reasoning effort ({efforts_text})\\n'
    fi
    ;;
  "models"|"--list-models")
    printf 'Configured model: {configured}\\n'
    printf 'Available models: {models_text}\\n'
    ;;
  *)
    printf 'unexpected discovery command: %s\\n' "$args" >&2
    exit 2
    ;;
esac
""",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    if binary_name == "codex":
        # `codex models` nao existe: a CLI trata o argumento como lixo e cai no
        # help interativo, sem anunciar modelo nenhum. O default do usuario vive
        # em ~/.codex/config.toml, e e de la que o harness le.
        codex_home = home / ".codex"
        codex_home.mkdir(exist_ok=True)
        (codex_home / "config.toml").write_text(f'model = "{configured}"\n')

    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
    }


def read_mock_log(log_path: Path) -> dict[str, str]:
    blocks: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in log_path.read_text().splitlines():
        if current_key:
            if line == "END":
                blocks[current_key] = "\n".join(current_lines)
                current_key = ""
                current_lines = []
            else:
                current_lines.append(line)
            continue

        if line.endswith("<<END"):
            current_key = line.removesuffix("<<END")

    return blocks


def logged_args(blocks: dict[str, str]) -> list[str]:
    args: list[str] = []
    index = 0
    while f"ARG_{index}" in blocks:
        args.append(blocks[f"ARG_{index}"])
        index += 1
    return args


def count_mock_invocations(log_path: Path) -> int:
    return log_path.read_text().count("BIN<<END\n")


def test_script_is_executable_bash_with_strict_mode() -> None:
    mode = SCRIPT.stat().st_mode

    assert mode & stat.S_IXUSR
    text = SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text


def test_mock_binary_fixtures_exist_for_all_supported_agent_clis() -> None:
    for binary_name in SUPPORTED_MOCK_BINARIES:
        fixture = FIXTURE_BIN_DIR / binary_name

        assert fixture.is_file()
        assert fixture.stat().st_mode & stat.S_IXUSR
        assert "MOCK_LOG" in fixture.read_text()


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
    assert "max_iterations defaults to 10" in result.stdout
    assert "one iteration per" in result.stdout


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
    tool = run_cli("tool", "--tool", "amp", "analyze", "song.mid")
    list_models = run_cli("--list-models", "--tool", "gemini", env={"PATH": "/usr/bin:/bin"})

    assert tool.returncode == 69
    assert "tool parsed successfully" in tool.stderr
    assert list_models.returncode == 0
    assert "Models" in list_models.stdout


@pytest.mark.parametrize(
    ("tool", "binary", "configured", "models", "efforts"),
    [
        ("claude", "claude", "claude-default", ["claude-a", "claude-b"], ["low", "medium", "high"]),
        # codex nao anuncia lista de modelos: `codex models` nao e subcomando, e o
        # default sai de ~/.codex/config.toml. Esperar lista aqui seria testar
        # comportamento que a CLI nao tem.
        ("codex", "codex", "codex-default", [], ["minimal", "low", "high"]),
        ("agy", "agy", "agy-default", ["agy-a", "agy-b"], ["low", "medium", "high"]),
        ("cursor", "cursor-agent", "cursor-default", ["cursor-a", "cursor-b"], ["medium", "high"]),
        ("opencode", "opencode", "opencode-default", ["open-a", "open-b"], ["minimal", "max"]),
        ("amp", "amp", "amp-default", ["amp-a", "amp-b"], []),
        ("gemini", "gemini", "gemini-default", ["gemini-a", "gemini-b"], []),
    ],
)
def test_list_models_queries_installed_cli_and_prints_what_it_announces(
    tmp_path: Path,
    tool: str,
    binary: str,
    configured: str,
    models: list[str],
    efforts: list[str],
) -> None:
    env = install_model_discovery_binary(tmp_path, binary, configured, models, efforts)

    result = run_cli("--list-models", env=env)

    assert result.returncode == 0, result.stderr
    assert any(line.split() == [tool, "(installed)"] for line in result.stdout.splitlines())
    assert f"configured default : {configured}" in result.stdout
    if models:
        assert f"advertised by CLI  : {' '.join(models)}" in result.stdout
    else:
        assert "advertised by CLI  : no model list announced by CLI" in result.stdout
    if efforts:
        assert f"effort             : {' '.join(efforts)}" in result.stdout
    else:
        assert "effort             : not supported by this CLI" in result.stdout


def test_list_models_marks_missing_tools_absent_without_failing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}

    result = run_cli("--list-models", env=env)

    assert result.returncode == 0, result.stderr
    assert "claude   (absent)" in result.stdout
    assert "codex    (absent)" in result.stdout
    assert "cursor   (absent)" in result.stdout
    assert "configured default : not queried" in result.stdout


@pytest.mark.parametrize(
    ("tool", "binary", "options", "expected_args"),
    [
        (
            "claude",
            "claude",
            ["--model", "test-model", "--effort", "high"],
            ["--print", "--dangerously-skip-permissions", "--model", "test-model", "--effort", "high"],
        ),
        (
            "codex",
            "codex",
            ["--model", "test-model", "--effort", "high"],
            [
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "-C",
                str(REPO_ROOT),
                "--model",
                "test-model",
                "-c",
                'model_reasoning_effort="high"',
                "-",
            ],
        ),
        (
            "agy",
            "agy",
            ["--model", "test-model", "--effort", "high"],
            ["--print", "--dangerously-skip-permissions", "--model", "test-model", "--effort", "high"],
        ),
        (
            "cursor",
            "cursor-agent",
            ["--model", "test-model", "--effort", "high"],
            ["--print", "--force", "--model", "test-model[effort=high]", "<PROMPT>"],
        ),
        (
            "opencode",
            "opencode",
            ["--model", "test-model", "--effort", "high"],
            ["run", "--auto", "--model", "test-model", "--variant", "high", "<PROMPT>"],
        ),
        (
            "amp",
            "amp",
            ["--model", "test-model"],
            ["--dangerously-allow-all", "--model", "test-model"],
        ),
        (
            "gemini",
            "gemini",
            ["--model", "test-model"],
            ["--approval-mode", "yolo", "--model", "test-model", "--prompt", "<PROMPT>"],
        ),
    ],
)
def test_agent_adapter_builds_expected_command_line(
    tmp_path: Path,
    tool: str,
    binary: str,
    options: list[str],
    expected_args: list[str],
) -> None:
    log_path, env = install_mock_binary(tmp_path, binary)
    project = prepare_run_project(tmp_path)
    expected_args = [str(project) if value == str(REPO_ROOT) else value for value in expected_args]

    result = run_cli("run", "--cwd", str(project), "--tool", tool, *options, "1", env=env)

    assert result.returncode == 0, result.stderr
    assert count_mock_invocations(log_path) == 1
    blocks = read_mock_log(log_path)
    assert blocks["BIN"] == binary
    args = logged_args(blocks)

    if "<PROMPT>" in expected_args:
        prompt_index = expected_args.index("<PROMPT>")
        assert args[:prompt_index] == expected_args[:prompt_index]
        assert args[prompt_index].startswith("midi-arranger run")
        assert "iteration=1" in args[prompt_index]
        assert "max_iterations=1" in args[prompt_index]
        assert args[prompt_index + 1 :] == expected_args[prompt_index + 1 :]
        assert blocks["STDIN"].strip() == ""
        assert blocks["PROMPT"].startswith("midi-arranger run")
        assert "iteration=1" in blocks["PROMPT"]
        assert "max_iterations=1" in blocks["PROMPT"]
    else:
        assert args == expected_args
        assert blocks["STDIN"].startswith("midi-arranger run")
        assert "iteration=1" in blocks["STDIN"]
        assert "max_iterations=1" in blocks["STDIN"]
        assert blocks["PROMPT"] == blocks["STDIN"]

    assert f"mock output from {binary}" in result.stdout


@pytest.mark.parametrize(
    ("tool", "binary", "unexpected_args"),
    [
        ("claude", "claude", ["--model", "--effort"]),
        ("codex", "codex", ["--model", "model_reasoning_effort"]),
        ("agy", "agy", ["--model", "--effort"]),
        ("cursor", "cursor-agent", ["--model", "effort="]),
        ("opencode", "opencode", ["--model", "--variant"]),
        ("amp", "amp", ["--model"]),
        ("gemini", "gemini", ["--model"]),
    ],
)
def test_agent_adapter_does_not_pass_model_or_effort_when_not_requested(
    tmp_path: Path,
    tool: str,
    binary: str,
    unexpected_args: list[str],
) -> None:
    log_path, env = install_mock_binary(tmp_path, binary)
    project = prepare_run_project(tmp_path)

    result = run_cli("run", "--cwd", str(project), "--tool", tool, "2", env=env)

    assert result.returncode == 0, result.stderr
    joined_args = "\n".join(logged_args(read_mock_log(log_path)))
    for unexpected in unexpected_args:
        assert unexpected not in joined_args


def test_run_loop_invokes_agent_once_per_requested_iteration(tmp_path: Path) -> None:
    log_path, env = install_mock_binary(tmp_path, "claude")
    project = prepare_run_project(tmp_path)

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", "4", env=env)

    assert result.returncode == 0, result.stderr
    assert count_mock_invocations(log_path) == 4
    raw_log = log_path.read_text()
    for iteration in range(1, 5):
        assert f"iteration={iteration}" in raw_log
    assert "== midi-arranger run iteration 1/4 ==" in result.stdout
    assert "== midi-arranger run iteration 4/4 ==" in result.stdout


def test_run_loop_uses_documented_default_iterations(tmp_path: Path) -> None:
    log_path, env = install_mock_binary(tmp_path, "claude")
    project = prepare_run_project(tmp_path)

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", env=env)

    assert result.returncode == 0, result.stderr
    assert count_mock_invocations(log_path) == 10
    assert "== midi-arranger run iteration 10/10 ==" in result.stdout
    assert "max_iterations=10" in log_path.read_text()


def test_brief_invokes_selected_agent_with_input_midi_prompt(tmp_path: Path) -> None:
    log_path, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("brief", "--tool", "claude", "song.mid", env=env)

    assert result.returncode == 0, result.stderr
    blocks = read_mock_log(log_path)
    assert logged_args(blocks) == ["--print", "--dangerously-skip-permissions"]
    assert blocks["STDIN"].startswith("midi-arranger brief")
    assert "input_midi=song.mid" in blocks["STDIN"]
    assert blocks["PROMPT"] == blocks["STDIN"]


def test_agent_output_is_streamed_and_captured_for_inspection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "arrangement-brief.json").write_text('{"input_midi":"song.mid"}\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    binary = bin_dir / "claude"
    binary.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'stdout before sleep\\n'
printf 'stderr before sleep\\n' >&2
sleep 1
printf 'stdout after sleep\\n'
printf 'stderr after sleep\\n' >&2
""",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    process = subprocess.Popen(
        [str(SCRIPT), "run", "--cwd", str(project), "--tool", "claude", "1"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert process.stdout is not None
    observed_lines: list[str] = []
    while True:
        line = process.stdout.readline()
        assert line != ""
        observed_lines.append(line)
        if line == "stdout before sleep\n":
            break
    assert process.poll() is None

    stdout_remainder, stderr = process.communicate(timeout=5)
    stdout = "".join(observed_lines) + stdout_remainder

    assert process.returncode == 0, stderr
    assert "stderr before sleep" in stdout
    assert "stdout after sleep" in stdout
    assert "stderr after sleep" in stdout

    captured = (project / ".midiarranger" / "last-agent-output.txt").read_text()
    assert "stdout before sleep" in captured
    assert "stderr before sleep" in captured
    assert "stdout after sleep" in captured
    assert "stderr after sleep" in captured


def test_missing_agent_binary_fails_before_invocation(tmp_path: Path) -> None:
    project = prepare_run_project(tmp_path)
    env = {"PATH": "/usr/bin:/bin", "MOCK_LOG": str(tmp_path / "missing.log")}

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", env=env)

    assert result.returncode == 69
    assert "claude was not found in PATH" in result.stderr
    assert "Install the claude CLI" in result.stderr
    assert not (tmp_path / "missing.log").exists()


@pytest.mark.parametrize("tool", ["amp", "gemini"])
def test_effort_for_tools_without_effort_support_fails_clearly(tmp_path: Path, tool: str) -> None:
    log_path, env = install_mock_binary(tmp_path, tool)

    result = run_cli("run", "--tool", tool, "--effort", "high", env=env)

    assert result.returncode == 64
    assert f"--effort is not supported by '{tool}'." in result.stderr
    assert not log_path.exists()


def test_cursor_effort_requires_model(tmp_path: Path) -> None:
    log_path, env = install_mock_binary(tmp_path, "cursor-agent")

    result = run_cli("run", "--tool", "cursor", "--effort", "high", env=env)

    assert result.returncode == 64
    assert "cursor carries effort inside --model" in result.stderr
    assert not log_path.exists()


def test_project_root_detection_climbs_to_git_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "arrangement-brief.json").write_text('{"input_midi":"song.mid"}\n')
    log_path, env = install_mock_binary(tmp_path, "codex")

    result = run_cli("run", "1", cwd=nested, env=env)

    assert result.returncode == 0, result.stderr
    assert f"project_root={project}" in read_mock_log(log_path)["STDIN"]


def test_project_root_detection_climbs_to_state_file(tmp_path: Path) -> None:
    project = tmp_path / "state-project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / "progress.txt").write_text("state\n")
    log_path, env = install_mock_binary(tmp_path, "codex")

    result = run_cli("brief", "song.mid", cwd=nested, env=env)

    assert result.returncode == 0, result.stderr
    assert f"project_root={project}" in read_mock_log(log_path)["STDIN"]


def test_cwd_overrides_auto_detected_root(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    nested = outer / "nested"
    override = tmp_path / "override"
    nested.mkdir(parents=True)
    override.mkdir()
    (outer / ".git").mkdir()
    (override / "arrangement-brief.json").write_text('{"input_midi":"song.mid"}\n')
    log_path, env = install_mock_binary(tmp_path, "codex")

    result = run_cli("run", "--cwd", str(override), "1", cwd=nested, env=env)

    assert result.returncode == 0, result.stderr
    assert f"project_root={override}" in read_mock_log(log_path)["STDIN"]


def test_run_without_brief_fails_before_agent_invocation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    log_path, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", "1", env=env)

    assert result.returncode == 66
    assert "arrangement-brief.json not found" in result.stderr
    assert "midi-arranger brief <input.mid>" in result.stderr
    assert not log_path.exists()
    assert not (project / ".midiarranger").exists()
    assert not (project / "progress.txt").exists()


def test_run_creates_state_directory_and_progress_header(tmp_path: Path) -> None:
    project = prepare_run_project(tmp_path)
    log_path, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", "3", env=env)

    assert result.returncode == 0, result.stderr
    assert log_path.exists()
    assert (project / ".midiarranger").is_dir()
    progress = (project / "progress.txt").read_text()
    assert progress.startswith("# midi-arranger progress\n---\n")
    assert "## run started " in progress
    assert "- tool=claude\n" in progress
    assert "- max_iterations=3\n" in progress


def test_run_preserves_existing_progress_and_appends(tmp_path: Path) -> None:
    project = prepare_run_project(tmp_path)
    existing_progress = "existing progress\n---\n"
    (project / "progress.txt").write_text(existing_progress)
    _, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", "1", env=env)

    assert result.returncode == 0, result.stderr
    progress = (project / "progress.txt").read_text()
    assert progress.startswith(existing_progress)
    assert len(progress) > len(existing_progress)
    assert progress.count("existing progress") == 1
    assert "## run started " in progress


def test_run_archives_plan_and_progress_when_brief_changed(tmp_path: Path) -> None:
    project = prepare_run_project(tmp_path)
    old_brief = '{"input_midi":"old-song.mid"}\n'
    new_brief = '{"input_midi":"new song.mid"}\n'
    (project / "arrangement-brief.json").write_text(new_brief)
    (project / "arrangement-plan.json").write_text('{"old":"plan"}\n')
    (project / "progress.txt").write_text("old progress\n---\n")
    state_dir = project / ".midiarranger"
    state_dir.mkdir()
    (state_dir / "brief.sha256").write_text(f"{brief_hash(old_brief)}\n")
    _, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", "1", env=env)

    assert result.returncode == 0, result.stderr
    archive_root = state_dir / "archive"
    archives = list(archive_root.iterdir())
    assert len(archives) == 1
    assert archives[0].name.endswith("-new-song")
    assert (archives[0] / "arrangement-plan.json").read_text() == '{"old":"plan"}\n'
    assert (archives[0] / "progress.txt").read_text() == "old progress\n---\n"
    assert not (project / "arrangement-plan.json").exists()
    fresh_progress = (project / "progress.txt").read_text()
    assert fresh_progress.startswith("# midi-arranger progress\n---\n")
    assert "old progress" not in fresh_progress
    assert (state_dir / "brief.sha256").read_text() == f"{brief_hash(new_brief)}\n"


def test_run_does_not_archive_when_brief_hash_matches_last_run(tmp_path: Path) -> None:
    project = prepare_run_project(tmp_path)
    brief = (project / "arrangement-brief.json").read_text()
    (project / "arrangement-plan.json").write_text('{"current":"plan"}\n')
    (project / "progress.txt").write_text("current progress\n---\n")
    state_dir = project / ".midiarranger"
    state_dir.mkdir()
    (state_dir / "brief.sha256").write_text(f"{brief_hash(brief)}\n")
    _, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("run", "--cwd", str(project), "--tool", "claude", env=env)

    assert result.returncode == 0, result.stderr
    assert not (state_dir / "archive").exists()
    assert (project / "arrangement-plan.json").read_text() == '{"current":"plan"}\n'
    progress = (project / "progress.txt").read_text()
    assert progress.startswith("current progress\n---\n")
    assert progress.count("current progress") == 1
    assert "## run started " in progress


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
