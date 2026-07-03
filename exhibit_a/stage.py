"""Staging: config contract + throwaway workspaces + environment setup.

The security invariant of the whole tool is enforced by what this module
REFUSES to do: every command exhibit-a ever executes comes from the repo
owner's `.exhibit.toml` (or our documented defaults) — argv lists, no shell.
Text from the bug report has no path into a command line. If you ever find
yourself threading a plan field into `setup` or `test_command`, you are
building the vulnerability this tool exists to avoid (see: Clinejection).
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .runner import LocalRunner
from .schemas import RunResult

CONFIG_NAME = ".exhibit.toml"

# Directories that are never copied into a workspace: caches, envs, VCS
# internals, and — pointedly — any previous exhibit workspace.
COPY_IGNORE = (
    ".git", ".hg", ".svn",
    "__pycache__", "*.egg-info", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", ".exhibit-venv", ".exhibit-work",
    "node_modules", "dist", "build", ".tox", ".nox",
)


class ConfigError(ValueError):
    """The repo's .exhibit.toml is missing something we can't guess safely."""


@dataclass
class ExhibitConfig:
    framework: str = "pytest"
    setup: list[list[str]] = field(default_factory=lambda: [
        ["python", "-m", "pip", "install", "-e", "."],
    ])
    test_command: list[str] = field(default_factory=lambda: [
        "python", "-m", "pytest", "{test_file}", "-q",
    ])
    test_dir: str = "tests"
    timeout_seconds: int = 300
    runs: int = 3
    docker_image: str | None = None

    def validate(self) -> None:
        if self.framework != "pytest":
            raise ConfigError(
                f"framework {self.framework!r} not supported yet; v1 is pytest-only "
                "(vitest is next; see README roadmap)")
        if not any("{test_file}" in part for part in self.test_command):
            raise ConfigError("test_command must contain a '{test_file}' placeholder")
        if not (10 <= self.timeout_seconds <= 3600):
            raise ConfigError("timeout_seconds must be between 10 and 3600")
        if not (1 <= self.runs <= 5):
            raise ConfigError("runs must be between 1 and 5")
        for cmd in self.setup:
            if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) for a in cmd)):
                raise ConfigError("each setup entry must be a non-empty list of strings "
                                  "(argv form, we never invoke a shell)")
        # test_dir stays inside the workspace. "../../etc" is not a test dir.
        if Path(self.test_dir).is_absolute() or ".." in Path(self.test_dir).parts:
            raise ConfigError("test_dir must be a relative path inside the repository")


def load_config(repo_root: Path) -> ExhibitConfig:
    """Read .exhibit.toml if present; otherwise use the pytest defaults.

    TOML because it's in the standard library (tomllib) — config parsing is
    not worth a dependency.
    """
    path = Path(repo_root) / CONFIG_NAME
    cfg = ExhibitConfig()
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("exhibit", {})
        if not isinstance(section, dict):
            raise ConfigError("[exhibit] table missing or malformed")
        for key in ("framework", "test_dir", "docker_image"):
            if key in section:
                setattr(cfg, key, section[key])
        for key in ("timeout_seconds", "runs"):
            if key in section:
                try:
                    setattr(cfg, key, int(section[key]))
                except (TypeError, ValueError):
                    raise ConfigError(f"{key} must be an integer") from None
        if "test_command" in section:
            cfg.test_command = list(map(str, section["test_command"]))
        if "setup" in section:
            cfg.setup = [list(map(str, c)) for c in section["setup"]]
    cfg.validate()
    return cfg


def write_starter_config(repo_root: Path) -> Path:
    """`exhibit init` — drop a commented starter config into the repo."""
    path = Path(repo_root) / CONFIG_NAME
    path.write_text(
        '# exhibit-a configuration. https://github.com/Dhxnx-git/exhibit-a\n'
        '#\n'
        '# Commands are argv LISTS, not shell strings. This is load-bearing:\n'
        '# it means no shell ever parses anything, which is one of the ways\n'
        '# exhibit-a stays safe on public repos.\n'
        '\n'
        '[exhibit]\n'
        'framework = "pytest"\n'
        'test_dir = "tests"\n'
        'setup = [["python", "-m", "pip", "install", "-e", "."]]\n'
        'test_command = ["python", "-m", "pytest", "{test_file}", "-q"]\n'
        'timeout_seconds = 300\n'
        'runs = 3\n'
        '# docker_image = "python:3.12-slim"   # enables the container sandbox\n',
        encoding="utf-8",
    )
    return path


def prepare_workspace(repo_root: Path, work_root: Path) -> Path:
    """Copy the repo into a throwaway workspace.

    We operate on a copy so that (a) generated tests and junit files never
    dirty the real checkout, and (b) a hostile test that shreds its cwd
    shreds a copy. The copy excludes .git — the workspace can't rewrite
    history it doesn't have.
    """
    workspace = Path(work_root) / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(
        repo_root, workspace,
        ignore=shutil.ignore_patterns(*COPY_IGNORE),
        symlinks=False,
    )
    return workspace


def create_venv(workspace: Path, timeout_s: int = 600) -> Path:
    """Local mode: a venv inside the workspace so `pip install -e .` can never
    touch the operator's global site-packages.

    Created with --system-site-packages on purpose: the workspace inherits the
    pytest that exhibit-a runs under (so we don't re-download it every run),
    while the editable install of the TARGET still lands in the venv's own
    writable layer and shadows anything ambient. If the target pins its own
    pytest, that pin wins — venv site-packages take precedence.
    """
    venv_dir = workspace / ".exhibit-venv"
    runner = LocalRunner(workspace)
    result = runner.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
        timeout_s=timeout_s)
    if result.exit_code != 0:
        raise ConfigError(f"could not create workspace venv: {result.stderr[-400:]}")
    ensure_pytest(venv_dir, workspace, timeout_s=timeout_s)
    return venv_dir


def ensure_pytest(venv_dir: Path, workspace: Path, timeout_s: int = 600) -> None:
    """Guarantee `python -m pytest` works in the venv.

    Usually a no-op: --system-site-packages means the ambient pytest is already
    visible. Only when the host has no pytest at all do we install one — better
    a one-time download than a confusing 'No module named pytest' masquerading
    as UNREPRODUCIBLE.
    """
    runner = LocalRunner(workspace, venv_dir=venv_dir)
    check = runner.run(["python", "-c", "import pytest"], timeout_s=60)
    if check.exit_code == 0:
        return
    installed = runner.run(["pip", "install", "pytest>=8"], timeout_s=timeout_s, allow_network=True)
    if installed.exit_code != 0:
        raise ConfigError(
            "no pytest available and could not install one into the workspace venv:\n"
            + installed.stderr[-400:])


def run_setup(runner, config: ExhibitConfig) -> tuple[bool, list[RunResult]]:
    """Run the owner's setup commands (network allowed — pip needs it).

    A setup failure is an ENV_FAILED verdict upstream: we refuse to blame a
    reporter because the build was broken.
    """
    results: list[RunResult] = []
    for cmd in config.setup:
        res = runner.run(cmd, timeout_s=config.timeout_seconds, allow_network=True)
        results.append(res)
        if res.exit_code != 0 or res.timed_out:
            return False, results
    return True, results
