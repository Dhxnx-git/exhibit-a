"""Config loading + the guardrails that keep .exhibit.toml from becoming an
injection vector of its own (it's owner-controlled, but bugs happen)."""

import pytest

from exhibit_a.stage import load_config, write_starter_config, ExhibitConfig, ConfigError


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.framework == "pytest"
    assert any("{test_file}" in p for p in cfg.test_command)


def test_reads_overrides(tmp_path):
    (tmp_path / ".exhibit.toml").write_text("""
        [exhibit]
        test_dir = "src/tests"
        timeout_seconds = 120
        runs = 2
        setup = [["python", "-m", "pip", "install", "-e", ".[test]"]]
        test_command = ["python", "-m", "pytest", "{test_file}", "-x"]
    """, encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.test_dir == "src/tests"
    assert cfg.timeout_seconds == 120
    assert cfg.setup[0][-1] == ".[test]"


def test_rejects_test_command_without_placeholder(tmp_path):
    (tmp_path / ".exhibit.toml").write_text("""
        [exhibit]
        test_command = ["pytest", "-q"]
    """, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_rejects_test_dir_escaping_repo():
    cfg = ExhibitConfig(test_dir="../../etc")
    with pytest.raises(ConfigError):
        cfg.validate()


def test_rejects_absurd_timeout():
    with pytest.raises(ConfigError):
        ExhibitConfig(timeout_seconds=99999).validate()


def test_rejects_non_pytest_framework():
    with pytest.raises(ConfigError):
        ExhibitConfig(framework="vitest").validate()


def test_starter_config_is_valid(tmp_path):
    write_starter_config(tmp_path)
    cfg = load_config(tmp_path)  # must round-trip through the validator
    assert cfg.framework == "pytest"
