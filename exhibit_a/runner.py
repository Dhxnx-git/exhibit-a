"""Subprocess execution, the only place in the codebase that runs anything.

Two runners, one contract:

  LocalRunner  — runs commands directly, inside a venv we created in the
                 workspace copy. Fine for repos you own; the CLI warns loudly.
  DockerRunner — wraps every command in `docker run` with the network OFF for
                 test execution. This is the grown-up mode for strangers' code.

Design rules enforced here:
  * No shell=True. Ever. Commands are argv lists from the repo owner's config.
  * Everything gets a timeout. A hang is data (see FailureSignature), not a
    stuck CI job.
  * Output is captured and capped — attacker-influenced stdout should never
    be able to balloon a receipt into a 200 MB comment.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .schemas import RunResult

MAX_CAPTURE = 200_000  # chars of stdout/stderr we keep per run


class LocalRunner:
    """Run argv lists in a working directory, optionally via a workspace venv.

    Why the venv remap: the repo's setup step is usually `pip install -e .`.
    Doing that against the user's global Python would make exhibit-a a tool
    that mutates your machine as a side effect — absolutely not. stage.py
    creates `.exhibit-venv` in the throwaway workspace; we transparently remap
    bare `python` / `pip` / `pytest` argv heads to that venv's binaries.
    """

    def __init__(self, cwd: Path, venv_dir: Path | None = None):
        self.cwd = Path(cwd)
        self.venv_dir = Path(venv_dir) if venv_dir else None

    def _venv_bin(self, name: str) -> str | None:
        if not self.venv_dir:
            return None
        sub = "Scripts" if sys.platform == "win32" else "bin"
        exe = name + (".exe" if sys.platform == "win32" else "")
        p = self.venv_dir / sub / exe
        return str(p) if p.exists() else None

    def _remap(self, argv: list[str]) -> list[str]:
        head = argv[0].lower()
        if head in ("python", "python3", "pip", "pytest"):
            mapped = self._venv_bin("python" if head.startswith("python") else head)
            if mapped:
                if head == "pip":
                    # `pip` as a bare exe inside a venv can be flaky on Windows;
                    # `python -m pip` is the boring, reliable spelling.
                    py = self._venv_bin("python")
                    return [py or "python", "-m", "pip", *argv[1:]]
                return [mapped, *argv[1:]]
        return argv

    def run(self, argv: list[str], timeout_s: int, allow_network: bool = True) -> RunResult:
        # LocalRunner cannot actually cut the network — only Docker can.
        # The flag exists so both runners share a signature and the caller's
        # intent is visible at every call site.
        argv = self._remap(list(argv))
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
            return RunResult(
                argv=argv,
                exit_code=proc.returncode,
                stdout=proc.stdout[:MAX_CAPTURE],
                stderr=proc.stderr[:MAX_CAPTURE],
                duration_s=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(
                argv=argv,
                exit_code=-1,
                stdout=(e.stdout or "")[:MAX_CAPTURE] if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "")[:MAX_CAPTURE] if isinstance(e.stderr, str) else "",
                duration_s=time.monotonic() - start,
                timed_out=True,
            )
        except FileNotFoundError as e:
            return RunResult(
                argv=argv, exit_code=127, stdout="", stderr=str(e),
                duration_s=time.monotonic() - start,
            )


class DockerRunner:
    """Same contract, but every command runs inside a fresh container.

    The two-phase network story:
      setup phase  -> network ON  (pip install needs the internet)
      test phase   -> network OFF (`--network none`) — generated test code
                      executes with no route out, so even a maliciously
                      steered test has nowhere to send anything.

    The workspace is bind-mounted at /work. No environment variables are
    passed through, so secrets in the host env (API keys!) never enter the
    container. That is not an accident; do not "fix" it.
    """

    def __init__(self, cwd: Path, image: str):
        self.cwd = Path(cwd)
        self.image = image

    def docker_argv(self, argv: list[str], allow_network: bool) -> list[str]:
        """Build the docker run command. Split out for unit testing, since CI
        for this repo can't assume a docker daemon."""
        cmd = [
            "docker", "run", "--rm",
            "--memory", "2g", "--cpus", "2",
            "--workdir", "/work",
            "--volume", f"{self.cwd.resolve()}:/work",
        ]
        if not allow_network:
            cmd += ["--network", "none"]
        cmd += [self.image, *argv]
        return cmd

    def run(self, argv: list[str], timeout_s: int, allow_network: bool = True) -> RunResult:
        wrapped = self.docker_argv(list(argv), allow_network)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                wrapped, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_s,
            )
            return RunResult(
                argv=wrapped, exit_code=proc.returncode,
                stdout=proc.stdout[:MAX_CAPTURE], stderr=proc.stderr[:MAX_CAPTURE],
                duration_s=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(
                argv=wrapped, exit_code=-1,
                stdout=(e.stdout or "")[:MAX_CAPTURE] if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "")[:MAX_CAPTURE] if isinstance(e.stderr, str) else "",
                duration_s=time.monotonic() - start, timed_out=True,
            )
