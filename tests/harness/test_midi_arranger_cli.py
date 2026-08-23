import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "bin" / "midi-arranger"


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
    binary.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
{
  printf 'BIN<<END\\n%s\\nEND\\n' "$(basename "$0")"
  index=0
  for arg in "$@"; do
    printf 'ARG_%s<<END\\n%s\\nEND\\n' "$index" "$arg"
    index=$((index + 1))
  done
  printf 'STDIN<<END\\n'
  cat
  printf '\\nEND\\n'
} > "$MOCK_LOG"
printf 'mock output from %s\\n' "$(basename "$0")"
""",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MOCK_LOG": str(log_path),
    }
    return log_path, env


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
        ("codex", "codex", "codex-default", ["codex-a", "codex-b"], ["minimal", "low", "high"]),
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
    assert f"advertised by CLI  : {' '.join(models)}" in result.stdout
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

    result = run_cli("run", "--tool", tool, *options, "12", env=env)

    assert result.returncode == 0, result.stderr
    blocks = read_mock_log(log_path)
    assert blocks["BIN"] == binary
    args = logged_args(blocks)

    if "<PROMPT>" in expected_args:
        prompt_index = expected_args.index("<PROMPT>")
        assert args[:prompt_index] == expected_args[:prompt_index]
        assert args[prompt_index].startswith("midi-arranger run")
        assert "max_iterations=12" in args[prompt_index]
        assert args[prompt_index + 1 :] == expected_args[prompt_index + 1 :]
        assert blocks["STDIN"].strip() == ""
    else:
        assert args == expected_args
        assert blocks["STDIN"].startswith("midi-arranger run")
        assert "max_iterations=12" in blocks["STDIN"]

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

    result = run_cli("run", "--tool", tool, "2", env=env)

    assert result.returncode == 0, result.stderr
    joined_args = "\n".join(logged_args(read_mock_log(log_path)))
    for unexpected in unexpected_args:
        assert unexpected not in joined_args


def test_brief_invokes_selected_agent_with_input_midi_prompt(tmp_path: Path) -> None:
    log_path, env = install_mock_binary(tmp_path, "claude")

    result = run_cli("brief", "--tool", "claude", "song.mid", env=env)

    assert result.returncode == 0, result.stderr
    blocks = read_mock_log(log_path)
    assert logged_args(blocks) == ["--print", "--dangerously-skip-permissions"]
    assert blocks["STDIN"].startswith("midi-arranger brief")
    assert "input_midi=song.mid" in blocks["STDIN"]


def test_agent_output_is_streamed_and_captured_for_inspection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
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
        [str(SCRIPT), "run", "--cwd", str(project), "--tool", "claude"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert process.stdout is not None
    first_line = process.stdout.readline()
    assert first_line == "stdout before sleep\n"
    assert process.poll() is None

    stdout_remainder, stderr = process.communicate(timeout=5)
    stdout = first_line + stdout_remainder

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
    env = {"PATH": "/usr/bin:/bin", "MOCK_LOG": str(tmp_path / "missing.log")}

    result = run_cli("run", "--tool", "claude", env=env)

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
    log_path, env = install_mock_binary(tmp_path, "codex")

    result = run_cli("run", cwd=nested, env=env)

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
    log_path, env = install_mock_binary(tmp_path, "codex")

    result = run_cli("run", "--cwd", str(override), cwd=nested, env=env)

    assert result.returncode == 0, result.stderr
    assert f"project_root={override}" in read_mock_log(log_path)["STDIN"]


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
