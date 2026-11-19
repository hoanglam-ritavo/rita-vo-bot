"""Select disposable pytest targets without loading runtime configuration."""
import os

from dotenv.main import DotEnv
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
    if ',' in url.host or ROUTING_OPTIONS.intersection(key.casefold() for key in url.query):
        pytest.fail(f'{name} must specify its PostgreSQL target directly in the URL.', pytrace=False)
    return url


def _target(url):
    return url.host.casefold(), url.port or 5432, url.database


def _runtime_file_values():
    # Resolve interpolation the same way as HTTP and standalone DB commands.
    try:
        with (ROOT / '.env').open(encoding='utf-8') as stream:
            http_values = DotEnv(None, stream=stream, override=True).dict()
            stream.seek(0)
            cli_values = DotEnv(None, stream=stream, override=False).dict()
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeError):
        pytest.fail('Cannot inspect runtime .env for PostgreSQL target conflicts.', pytrace=False)
    return http_values, cli_values


def require_disposable_database(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f'Set {name} to a dedicated disposable PostgreSQL database.')
    url = _direct_url(value, name)
    target = _target(url)

    sources = [(os.environ, (*RUNTIME_URLS, *TEST_URLS))]
    sources.extend((values, RUNTIME_URLS) for values in _runtime_file_values())
    for source, keys in sources:
        for other_name in keys:
            other = source.get(other_name)
            if other_name != name and other and _target(_direct_url(other, other_name)) == target:
                pytest.fail(f'{name} must use a separate database from {other_name}.', pytrace=False)
    return url
