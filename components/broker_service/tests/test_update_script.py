"""update.sh and prepare.sh: the logic of the host scripts, in a temporary git repository.

A fake ``docker`` records each call. No test calls the real Docker engine. The tests that run the
script without ``--dry-run`` use a local bare repository as origin and a fake ``docker``. HOME and the
XDG configuration folder are temporary folders.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
LOOKUP = Path("components/client/src/agent_broker_client/dependencies.py")
SCRIPTS = ("update.sh", "prepare.sh", "compose.yaml")
DEPENDENCIES = """[maya-agent-kit]
../maya
./maya

[vault-agent_broker]
../vault-agent_broker
./vault-agent_broker
"""
# A fake docker. It records its arguments in the file named by FAKE_DOCKER_LOG.
FAKE_DOCKER = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$FAKE_DOCKER_LOG"
"""


@pytest.fixture(autouse=True)
def no_autocrlf(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user configuration of git can convert the line ends (core.autocrlf). A shell script needs LF."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")


def git(folder: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, env=env, check=True)
    return result.stdout.strip()


@dataclass
class Layout:
    base: Path
    project: Path
    docker_log: Path
    socket: Path
    bin: Path
    home: Path

    @property
    def config_dir(self) -> Path:
        return self.home / ".config" / "gustaw" / "agent_broker"

    @property
    def deploy_dir(self) -> Path:
        return self.home / "containers" / "agent_broker"

    def run(self, *args: str, socket_from_env: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(self.docker_log),
               "HOME": str(self.home)}
        for name in ("AGENT_BROKER_DOCKER_SOCKET", "XDG_CONFIG_HOME"):
            env.pop(name, None)
        if socket_from_env:
            env["AGENT_BROKER_DOCKER_SOCKET"] = str(self.socket)
        return subprocess.run(["bash", str(self.project / "update.sh"), *args], capture_output=True, text=True, env=env)

    def docker_calls(self) -> list[str]:
        return self.docker_log.read_text().splitlines() if self.docker_log.exists() else []

    def env_lines(self) -> list[str]:
        return (self.deploy_dir / ".env").read_text().splitlines()


def make_repo(folder: Path, files: dict[str, str]) -> None:
    folder.mkdir(parents=True)
    git(folder, "init", "-q", "-b", "main")
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
        if name.endswith(".sh"):
            (folder / name).chmod(0o755)
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "First")


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    """A clone of the project with its own origin, the dependencies next to it, and a fake docker."""
    base = tmp_path / "work"
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    files = {"dependencies.txt": DEPENDENCIES, str(LOOKUP): (PROJECT / LOOKUP).read_text(),
             **{name: (PROJECT / name).read_text() for name in SCRIPTS}}
    make_repo(seed, files)
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    base.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin), str(base / "project")], check=True)
    make_repo(base / "maya", {"tools/auth/README.md": "tool", "other/x.txt": "x"})
    (base / "vault-agent_broker").mkdir()
    (base / "vault-agent_broker" / "README.md").write_text("vault")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    socket = tmp_path / "docker.sock"
    socket.write_text("")
    home = tmp_path / "home"
    home.mkdir()
    return Layout(base, base / "project", tmp_path / "docker.log", socket, bin_dir, home)


def steps(out: str) -> list[str]:
    return [line.split(": ", 2)[2] for line in out.splitlines() if line.startswith("update: step ")]


def test_script_syntax() -> None:
    for name in ("update.sh", "prepare.sh"):
        assert subprocess.run(["bash", "-n", str(PROJECT / name)]).returncode == 0
        assert os.access(PROJECT / name, os.X_OK)
        if shutil.which("shellcheck"):
            assert subprocess.run(["shellcheck", str(PROJECT / name)]).returncode == 0


def test_dry_run_gives_the_steps_in_order_and_runs_nothing(layout: Layout) -> None:
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    names = steps(result.stdout)
    assert [n.split()[0] for n in names] == ["check", "pull", "build", "prepare", "run", "done"]
    lines = result.stdout.splitlines()
    pull = lines.index("update: would run: git -C " + str(layout.project) + " pull --ff-only")
    build = next(i for i, line in enumerate(lines) if "docker build" in line)
    up = next(i for i, line in enumerate(lines) if line.endswith(" up -d"))
    assert pull < build < up
    assert layout.docker_calls() == []            # a dry run calls no docker
    assert not (layout.project / ".local_assets").exists()   # and writes nothing
    assert not layout.deploy_dir.exists() and not layout.config_dir.exists()


def test_no_up_does_all_steps_except_the_start(layout: Layout) -> None:
    result = layout.run("--dry-run", "--no-up")
    assert result.returncode == 0, result.stderr
    assert steps(result.stdout)[-1] == "skip docker compose up -d (--no-up)"
    assert not any(line.endswith(" up -d") for line in result.stdout.splitlines())


def test_dry_run_keeps_the_branch_and_pulls_the_current_one(layout: Layout) -> None:
    git(layout.project, "checkout", "-q", "-b", "release-1")
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert "update: branch release-1" in result.stdout
    assert git(layout.project, "symbolic-ref", "--short", "HEAD") == "release-1"
    assert "checkout" not in result.stdout and "switch" not in result.stdout


def test_detached_head_stops(layout: Layout) -> None:
    git(layout.project, "checkout", "-q", "--detach")
    result = layout.run("--dry-run")
    assert result.returncode != 0
    assert "HEAD is detached" in result.stderr
    assert steps(result.stdout) == [f"check the clone {layout.project}"]


def test_local_changes_stop(layout: Layout) -> None:
    (layout.project / "dependencies.txt").write_text(DEPENDENCIES + "# change\n")
    result = layout.run("--dry-run")
    assert result.returncode != 0
    assert "local changes" in result.stderr
    assert not any("pull" in line for line in result.stdout.splitlines())


def test_compose_variables_without_a_vault(layout: Layout) -> None:
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"  DOCKER_GID={layout.socket.stat().st_gid}" in result.stdout
    assert f"  HOST_UID={os.getuid()}" in result.stdout and f"  HOST_GID={os.getgid()}" in result.stdout
    assert f"  HOST_CONFIG_HOME={layout.home / '.config'}" in result.stdout
    assert "VAULT_DIR" not in result.stdout


def test_override_finds_the_vault_and_the_vault_tool(layout: Layout) -> None:
    override = layout.base / "override.yaml"
    override.write_text("services: {}\n")
    result = layout.run("--dry-run", "--override", str(override))
    assert result.returncode == 0, result.stderr
    assert f"  VAULT_DIR={(layout.base / 'vault-agent_broker').resolve()}" in result.stdout
    assert f"  VAULT_TOOL_DIR={(layout.base / 'maya').resolve()}/tools/auth" in result.stdout


def test_override_with_a_nested_vault_works(layout: Layout) -> None:
    shutil.move(str(layout.base / "vault-agent_broker"), str(layout.project / "vault-agent_broker"))
    shutil.move(str(layout.base / "maya"), str(layout.project / "maya"))
    override = layout.base / "override.yaml"
    override.write_text("services: {}\n")
    result = layout.run("--dry-run", "--override", str(override))
    assert result.returncode == 0, result.stderr
    assert f"  VAULT_DIR={(layout.project / 'vault-agent_broker').resolve()}" in result.stdout


def test_missing_override_file_stops(layout: Layout) -> None:
    result = layout.run("--dry-run", "--override", str(layout.base / "no-such-override.yaml"))
    assert result.returncode != 0
    assert "no-such-override.yaml does not exist" in result.stderr


def test_socket_comes_from_config_env_without_the_variable(layout: Layout) -> None:
    layout.config_dir.mkdir(parents=True)
    (layout.config_dir / "config.env").write_text(f"AGENT_BROKER_DOCKER_SOCKET={layout.socket}\n")
    result = layout.run("--dry-run", socket_from_env=False)
    assert result.returncode == 0, result.stderr
    assert f"  AGENT_BROKER_DOCKER_SOCKET={layout.socket}" in result.stdout


def test_variable_replaces_the_socket_of_config_env(layout: Layout) -> None:
    layout.config_dir.mkdir(parents=True)
    (layout.config_dir / "config.env").write_text("AGENT_BROKER_DOCKER_SOCKET=/no/such/socket\n")
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"  AGENT_BROKER_DOCKER_SOCKET={layout.socket}" in result.stdout


def test_missing_docker_socket_stops(layout: Layout) -> None:
    layout.socket.unlink()
    result = layout.run("--dry-run")
    assert result.returncode != 0 and "Docker socket" in result.stderr


def test_unknown_option_is_a_usage_error(layout: Layout) -> None:
    result = layout.run("--other")
    assert result.returncode == 2 and "usage" in result.stderr


def test_real_run_pulls_builds_from_the_commit_and_starts(layout: Layout) -> None:
    # A new commit in origin: the script must pull it and build from it.
    other = layout.base.parent / "other"
    subprocess.run(["git", "clone", "-q", str(layout.base.parent / "origin.git"), str(other)], check=True)
    (other / "new.txt").write_text("from origin")
    git(other, "add", "new.txt")
    git(other, "commit", "-q", "-m", "New file")
    git(other, "push", "-q", "origin", "main")
    (layout.project / "untracked.txt").write_text("not committed")   # not in the image
    override = layout.base / "override.yaml"
    override.write_text("services: {}\n")
    result = layout.run("--override", str(override))
    assert result.returncode == 0, result.stderr
    context = layout.project / ".local_assets" / "build"
    project_files = context / "opt" / "gustaw" / "agent_broker"
    assert (project_files / "new.txt").read_text() == "from origin"
    assert not (project_files / "untracked.txt").exists()
    assert not (context / "opt" / "gustaw" / "maya").exists()       # the image holds no part of maya-agent-kit
    commit = git(layout.project, "rev-parse", "--short=12", "HEAD")
    calls = layout.docker_calls()
    assert len(calls) == 2
    assert calls[0].startswith(f"docker build -t agent_broker:{commit} -t agent_broker:latest")
    assert calls[1] == f"docker compose --project-directory {layout.deploy_dir} up -d"
    assert layout.config_dir.is_dir()
    assert (layout.deploy_dir / "compose.yaml").read_text() == (PROJECT / "compose.yaml").read_text()
    assert (layout.deploy_dir / "compose.override.yaml").read_text() == "services: {}\n"
    env_lines = layout.env_lines()
    assert f"HOST_UID={os.getuid()}" in env_lines and f"HOST_GID={os.getgid()}" in env_lines
    assert f"DOCKER_GID={layout.socket.stat().st_gid}" in env_lines
    assert f"AGENT_BROKER_DOCKER_SOCKET={layout.socket}" in env_lines
    assert f"VAULT_DIR={(layout.base / 'vault-agent_broker').resolve()}" in env_lines


def test_real_run_without_override_removes_an_old_override(layout: Layout) -> None:
    layout.deploy_dir.mkdir(parents=True)
    (layout.deploy_dir / "compose.override.yaml").write_text("old")
    result = layout.run("--no-up")
    assert result.returncode == 0, result.stderr
    assert not (layout.deploy_dir / "compose.override.yaml").exists()
    assert [c.split()[1] for c in layout.docker_calls()] == ["build"]
    assert not any(line.startswith("VAULT_DIR=") for line in layout.env_lines())


def test_real_run_stops_at_the_first_error(layout: Layout) -> None:
    git(layout.project, "remote", "set-url", "origin", str(layout.base / "no-such-origin"))
    result = layout.run()
    assert result.returncode != 0
    assert layout.docker_calls() == []
