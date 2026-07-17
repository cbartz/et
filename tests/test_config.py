"""Tests for et.config, using a temporary directory via ET_CONFIG_DIR."""

from __future__ import annotations

import pytest

from et.config import ConfigError, get_config_path, load_workspace_names


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ET_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_config(config_dir, text: str) -> None:
    (config_dir / "config.yaml").write_text(text)


def test_get_config_path_uses_env_var_override(config_dir):
    assert get_config_path() == config_dir / "config.yaml"


def test_load_workspace_names_parses_list_of_name_mappings(config_dir):
    _write_config(
        config_dir,
        "workspaces:\n"
        "  - name: mails\n"
        "  - name: handson\n"
        "  - name: isd-321\n",
    )
    assert load_workspace_names() == ["mails", "handson", "isd-321"]


def test_load_workspace_names_raises_when_file_missing(config_dir):
    with pytest.raises(ConfigError, match="config file not found"):
        load_workspace_names()


def test_load_workspace_names_raises_on_invalid_yaml(config_dir):
    _write_config(config_dir, "workspaces: [unclosed")
    with pytest.raises(ConfigError, match="could not parse"):
        load_workspace_names()


def test_load_workspace_names_raises_when_top_level_is_not_a_mapping(config_dir):
    _write_config(config_dir, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="must contain a mapping"):
        load_workspace_names()


def test_load_workspace_names_raises_when_workspaces_is_not_a_list(config_dir):
    _write_config(config_dir, "workspaces: mails\n")
    with pytest.raises(ConfigError, match="'workspaces' must be a list"):
        load_workspace_names()


def test_load_workspace_names_raises_when_entry_missing_name_key(config_dir):
    _write_config(config_dir, "workspaces:\n  - not_name: mails\n")
    with pytest.raises(ConfigError, match="must be a mapping with a 'name' key"):
        load_workspace_names()


def test_load_workspace_names_raises_when_name_is_not_a_string(config_dir):
    _write_config(config_dir, "workspaces:\n  - name: 42\n")
    with pytest.raises(ConfigError, match="name must be a string"):
        load_workspace_names()


def test_load_workspace_names_returns_empty_list_for_empty_workspaces_key(config_dir):
    _write_config(config_dir, "workspaces: []\n")
    assert load_workspace_names() == []


def test_load_workspace_names_treats_empty_file_as_no_workspaces(config_dir):
    _write_config(config_dir, "")
    assert load_workspace_names() == []
