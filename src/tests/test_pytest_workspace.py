"""Configuration checks that do not require a writable pytest temp directory."""
import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.tests import conftest as configuration


def make_config(basetemp=None):
    return SimpleNamespace(
        option=SimpleNamespace(basetemp=basetemp), add_cleanup=Mock(),
    )


def test_default_root_and_environment_cleanup(monkeypatch):
    monkeypatch.delenv('PYTEST_DEBUG_TEMPROOT', raising=False)
    mkdir = Mock()
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    config = make_config()
    configuration.pytest_configure(config)
    try:
        expected = configuration.ROOT / '.tmp' / 'pytest-work'
        assert expected.is_absolute()
        assert os.environ['PYTEST_DEBUG_TEMPROOT'] == str(expected)
        mkdir.assert_called_once_with(parents=True, exist_ok=True)
        config.add_cleanup.assert_called_once()
    finally:
        config.add_cleanup.call_args.args[0]()
    assert 'PYTEST_DEBUG_TEMPROOT' not in os.environ


@pytest.mark.parametrize('override', ['custom-root', ''])
def test_explicit_environment_is_preserved(monkeypatch, override):
    monkeypatch.setenv('PYTEST_DEBUG_TEMPROOT', override)
    mkdir = Mock()
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    config = make_config()
    configuration.pytest_configure(config)
    assert os.environ['PYTEST_DEBUG_TEMPROOT'] == override
    mkdir.assert_not_called()
    config.add_cleanup.assert_not_called()


def test_explicit_basetemp_is_preserved(monkeypatch):
    monkeypatch.delenv('PYTEST_DEBUG_TEMPROOT', raising=False)
    mkdir = Mock()
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    config = make_config('.tmp/explicit-run')
    configuration.pytest_configure(config)
    assert config.option.basetemp == '.tmp/explicit-run'
    assert 'PYTEST_DEBUG_TEMPROOT' not in os.environ
    mkdir.assert_not_called()
    config.add_cleanup.assert_not_called()


def test_permission_failure_has_no_fallback(monkeypatch):
    monkeypatch.delenv('PYTEST_DEBUG_TEMPROOT', raising=False)
    mkdir = Mock(side_effect=PermissionError('Access denied'))
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    config = make_config()
    with pytest.raises(pytest.UsageError, match='Cannot create pytest temporary root'):
        configuration.pytest_configure(config)
    assert 'PYTEST_DEBUG_TEMPROOT' not in os.environ
    assert mkdir.call_count == 1
    config.add_cleanup.assert_not_called()


def test_loading_fixtures_does_not_create_directories(monkeypatch):
    mkdir = Mock()
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    before = os.environ.get('PYTEST_DEBUG_TEMPROOT')
    runpy.run_path(configuration.__file__)
    mkdir.assert_not_called()
    assert os.environ.get('PYTEST_DEBUG_TEMPROOT') == before
