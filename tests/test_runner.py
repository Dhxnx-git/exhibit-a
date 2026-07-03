"""Runner safety properties. We don't need a docker daemon to assert the
command we WOULD run is shaped safely — that's the point of splitting
docker_argv out."""

from pathlib import Path

from exhibit_a.runner import DockerRunner, LocalRunner


def test_docker_argv_turns_network_off_for_tests(tmp_path):
    r = DockerRunner(tmp_path, "python:3.12-slim")
    argv = r.docker_argv(["python", "-m", "pytest"], allow_network=False)
    assert "--network" in argv and "none" in argv


def test_docker_argv_allows_network_for_setup(tmp_path):
    r = DockerRunner(tmp_path, "python:3.12-slim")
    argv = r.docker_argv(["pip", "install", "-e", "."], allow_network=True)
    assert "--network" not in argv


def test_docker_argv_never_passes_host_env(tmp_path):
    # No --env / -e flags: secrets in the host environment (API keys!) must
    # not leak into the container where hostile test code runs.
    r = DockerRunner(tmp_path, "img")
    argv = r.docker_argv(["python", "-m", "pytest"], allow_network=False)
    assert "--env" not in argv
    assert not any(a == "-e" for a in argv)  # -e would be env-passthrough here


def test_docker_argv_has_resource_caps(tmp_path):
    r = DockerRunner(tmp_path, "img")
    argv = r.docker_argv(["true"], allow_network=True)
    assert "--memory" in argv and "--cpus" in argv


def test_local_runner_remaps_bare_python_to_venv(tmp_path):
    # Simulate a venv layout so the remap has something to find.
    import sys
    sub = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"
    (tmp_path / "venv" / sub).mkdir(parents=True)
    (tmp_path / "venv" / sub / exe).write_text("")
    r = LocalRunner(tmp_path, venv_dir=tmp_path / "venv")
    mapped = r._remap(["python", "-c", "1"])
    assert mapped[0].endswith(exe)
    assert str(tmp_path / "venv") in mapped[0]


def test_local_runner_leaves_unknown_commands_alone(tmp_path):
    r = LocalRunner(tmp_path, venv_dir=tmp_path / "venv")
    assert r._remap(["git", "status"]) == ["git", "status"]


def test_missing_binary_is_captured_not_raised(tmp_path):
    r = LocalRunner(tmp_path)
    res = r.run(["this-command-does-not-exist-xyz"], timeout_s=10)
    assert res.exit_code == 127
