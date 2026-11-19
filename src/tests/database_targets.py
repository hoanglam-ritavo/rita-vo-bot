"""Select disposable pytest targets without loading runtime configuration."""
import os

from dotenv import dotenv_values
import pytest

from src.database.config import ConfigurationError, ROOT, validate_url


RUNTIME_URLS = ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'HRM_DATABASE_URL')
TEST_URLS = ('TEST_DATABASE_URL', 'TEST_CONVERSATION_DATABASE_URL', 'TEST_HRM_DATABASE_URL')
ROUTING_OPTIONS = {'host', 'hostaddr', 'port', 'dbname', 'service', 'servicefile'}


def _direct_url(value, name):
    try:
        url = validate_url(value, name)
    except ConfigurationError as exc:
        pytest.fail(str(exc), pytrace=False)
    if ROUTING_OPTIONS.intersection(key.casefold() for key in url.query):
        pytest.fail(f'{name} must specify its PostgreSQL target directly in the URL.', pytrace=False)
    return url


def _target(url):
    return url.host.casefold(), url.port or 5432, url.database


def require_disposable_database(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f'Set {name} to a dedicated disposable PostgreSQL database.')
    url = _direct_url(value, name)
    target = _target(url)

    # Compare both sources: HTTP and standalone database commands have different precedence.
    try:
        with (ROOT / '.env').open(encoding='utf-8') as stream:
            runtime_file = dotenv_values(stream=stream)
    except FileNotFoundError:
        runtime_file = {}
    except (OSError, UnicodeError):
        pytest.fail('Cannot inspect runtime .env for PostgreSQL target conflicts.', pytrace=False)

    for source, keys in ((os.environ, (*RUNTIME_URLS, *TEST_URLS)), (runtime_file, RUNTIME_URLS)):
        for other_name in keys:
            other = source.get(other_name)
            if other_name != name and other and _target(_direct_url(other, other_name)) == target:
                pytest.fail(f'{name} must use a separate database from {other_name}.', pytrace=False)
    return url
