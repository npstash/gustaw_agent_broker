"""update.sh: the logic of the host script, in a temporary git repository.

A fake ``docker`` records each call. No test calls the real Docker engine. The tests that run the
script without ``--dry-run`` use a local bare repository as origin and a fake ``docker``.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
LOOKUP = Path("components/client/src/agent_broker_client/dependencies.py")
DEPENDENCIES = """[maya-agent-kit]
../maya
./maya

[vault-agent_broker]
../vault-agent_broker
./vault-agent_broker
"""
# A fake docker. It records its arguments and the compose variables in the file named by FAKE_DOCKER_LOG.
FAKE_DOCKER = """#!/usr/bin/env bash
printf 'docker %s | vault=%s gid=%s commit=%s\\n' "$*" "${AGENT_BROKER_VAULT:-}" "${DOCKER_GID:-}" \\
    "${AGENT_BROKER_COMMIT:-}" >> "$FAKE_DOCKER_LOG"
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
    config_home: Path

    @property
    def config_dir(self) -> Path:
        return self.config_home / "gustaw" / "agent_broker"

    def run(self, *args: str, socket_from_env: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(self.docker_log),
               "XDG_CONFIG_HOME": str(self.config_home)}
        env.pop("AGENT_BROKER_DOCKER_SOCKET", None)
        if socket_from_env:
            env["AGENT_BROKER_DOCKER_SOCKET"] = str(self.socket)
        return subprocess.run(["bash", str(self.project / "update.sh"), *args], capture_output=True, text=True, env=env)

    def docker_calls(self) -> list[str]:
        return self.docker_log.read_text().splitlines() if self.docker_log.exists() else []

    def commit(self, name: str = "file.txt", text: str = "x") -> None:
        (self.project / name).write_text(text)
        git(self.project, "add", name)
        git(self.project, "commit", "-q", "-m", f"Change {name}")


def make_repo(folder: Path, files: dict[str, str]) -> None:
    folder.mkdir(parents=True)
    git(folder, "init", "-q", "-b", "main")
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "First")


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    """A clone of the project with its own origin, the dependencies next to it, and a fake docker."""
    base = tmp_path / "work"
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    files = {"dependencies.txt": DEPENDENCIES, "update.sh": (PROJECT / "update.sh").read_text(),
             str(LOOKUP): (PROJECT / LOOKUP).read_text()}
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
    return Layout(base, base / "project", tmp_path / "docker.log", socket, bin_dir, tmp_path / "config")


def steps(out: str) -> list[str]:
    return [line.split(": ", 2)[2] for line in out.splitlines() if line.startswith("update: step ")]


def test_script_syntax() -> None:
    assert subprocess.run(["bash", "-n", str(PROJECT / "update.sh")]).returncode == 0
    assert os.access(PROJECT / "update.sh", os.X_OK)
    if shutil.which("shellcheck"):
        assert subprocess.run(["shellcheck", str(PROJECT / "update.sh")]).returncode == 0


def test_dry_run_gives_the_steps_in_order_and_runs_nothing(layout: Layout) -> None:
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    names = steps(result.stdout)
    assert len(names) == 6
    assert [n.split()[0] for n in names] == ["check", "pull", "check", "build", "run", "done"]
    lines = result.stdout.splitlines()
    pull = lines.index("update: would run: git -C " + str(layout.project) + " pull --ff-only")
    build = next(i for i, line in enumerate(lines) if line.endswith(" build"))
    up = next(i for i, line in enumerate(lines) if line.endswith(" up -d"))
    assert pull < build < up
    assert layout.docker_calls() == []            # a dry run calls no docker
    assert not (layout.project / ".local_assets").exists()   # and writes nothing
    assert not (layout.project / ".env").exists()
    assert not layout.config_home.exists()


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


def test_missing_dependency_stops_and_names_it(layout: Layout) -> None:
    # The name is not in any other dependencies.txt above this clone, so the lookup cannot find it there.
    text = DEPENDENCIES + "\n[no-such-dependency-for-the-test]\n../no-such-folder\n./no-such-folder\n"
    (layout.project / "dependencies.txt").write_text(text)
    git(layout.project, "commit", "-q", "-am", "Add a dependency")
    result = layout.run("--dry-run")
    assert result.returncode != 0
    assert "the dependency no-such-dependency-for-the-test is missing" in result.stderr
    assert str(layout.base / "no-such-folder") in result.stderr          # the places that the lookup tried
    assert str(layout.project / "no-such-folder") in result.stderr
    assert layout.docker_calls() == []


def test_dependency_as_a_nested_folder_works(layout: Layout) -> None:
    shutil.move(str(layout.base / "vault-agent_broker"), str(layout.project / "vault-agent_broker"))
    shutil.move(str(layout.base / "maya"), str(layout.project / "maya"))
    git(layout.project, "status", "--short")
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"update: dependency vault-agent_broker: {(layout.project / 'vault-agent_broker').resolve()}" in result.stdout


def test_vault_path_for_compose_is_the_found_one(layout: Layout) -> None:
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    vault = (layout.base / "vault-agent_broker").resolve()
    assert f"update: compose variable AGENT_BROKER_VAULT={vault}" in result.stdout
    assert f"update: compose variable DOCKER_GID={layout.socket.stat().st_gid}" in result.stdout
    assert f"update: compose variable HOST_UID={os.getuid()}" in result.stdout
    assert f"update: compose variable HOST_GID={os.getgid()}" in result.stdout
    assert f"update: compose variable AGENT_BROKER_CONFIG_DIR={layout.config_dir}" in result.stdout


def test_socket_comes_from_config_env_without_the_variable(layout: Layout) -> None:
    layout.config_dir.mkdir(parents=True)
    (layout.config_dir / "config.env").write_text(f"AGENT_BROKER_DOCKER_SOCKET={layout.socket}\n")
    result = layout.run("--dry-run", socket_from_env=False)
    assert result.returncode == 0, result.stderr
    assert f"update: compose variable AGENT_BROKER_DOCKER_SOCKET={layout.socket}" in result.stdout


def test_variable_replaces_the_socket_of_config_env(layout: Layout) -> None:
    layout.config_dir.mkdir(parents=True)
    (layout.config_dir / "config.env").write_text("AGENT_BROKER_DOCKER_SOCKET=/no/such/socket\n")
    result = layout.run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"update: compose variable AGENT_BROKER_DOCKER_SOCKET={layout.socket}" in result.stdout


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
    result = layout.run()
    assert result.returncode == 0, result.stderr
    context = layout.project / ".local_assets" / "build"
    project_files = context / "workspace" / "projects" / "gustaw" / "gustaw_agent_broker"
    assert (project_files / "new.txt").read_text() == "from origin"
    assert not (project_files / "untracked.txt").exists()
    assert (context / "workspace" / "maya" / "tools" / "auth" / "README.md").is_file()
    assert not (context / "workspace" / "maya" / "other").exists()    # only tools/auth of maya-agent-kit
    commit = git(layout.project, "rev-parse", "--short=12", "HEAD")
    vault = (layout.base / "vault-agent_broker").resolve()
    gid = layout.socket.stat().st_gid
    calls = layout.docker_calls()
    assert [c.split(" | ")[0].split()[-1] for c in calls] == ["build", "-d"]
    assert all(f"vault={vault} gid={gid} commit={commit}" in c for c in calls)
    assert layout.config_dir.is_dir()
    assert not (layout.project / "components" / "broker_service" / ".local_assets").exists()
    env_lines = (layout.project / ".env").read_text().splitlines()
    assert f"HOST_UID={os.getuid()}" in env_lines and f"HOST_GID={os.getgid()}" in env_lines
    assert f"DOCKER_GID={gid}" in env_lines and f"AGENT_BROKER_VAULT={vault}" in env_lines
    assert f"AGENT_BROKER_CONFIG_DIR={layout.config_dir}" in env_lines
    assert f"AGENT_BROKER_DOCKER_SOCKET={layout.socket}" in env_lines
    assert stat.S_ISDIR(context.stat().st_mode)


def test_real_run_stops_at_the_first_error(layout: Layout) -> None:
    git(layout.project, "remote", "set-url", "origin", str(layout.base / "no-such-origin"))
    result = layout.run()
    assert result.returncode != 0
    assert layout.docker_calls() == []
