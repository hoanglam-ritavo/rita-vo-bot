"""Configuration without connection side effects."""

import os
import re
from src.paths import REPOSITORY_ROOT

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ROOT = REPOSITORY_ROOT


class ConfigurationError(ValueError):
    """Invalid database configuration; messages never contain credentials."""


def database_url(*, migration: bool = False) -> URL:
    load_dotenv(ROOT / '.env', override=False)
    name = 'DATABASE_URL'
    if migration and os.environ.get('MIGRATION_DATABASE_URL'):
        name = 'MIGRATION_DATABASE_URL'
    return validate_url(os.environ.get(name), name)


def validate_url(value: str | None, name: str = 'DATABASE_URL') -> URL:
    if not value or not value.strip():
        raise ConfigurationError(f'{name} is required.')
    authority = re.split(r'[/?#]', value.partition('://')[2], maxsplit=1)[0]
    if authority.count('@') > 1:
        raise ConfigurationError(
            f'{name} contains an unencoded @ in its connection authority. '
            'Encode @ in usernames and passwords as %40; keep the @ before the host.'
        )
    try:
        url = make_url(value)
        if url.drivername != 'postgresql+psycopg' or not url.host or not url.database:
            raise ValueError
        _ = url.port
    except (ArgumentError, ValueError, TypeError):
        raise ConfigurationError(
            f'{name} must be a valid postgresql+psycopg URL with host and database.'
        ) from None
    if '@' in url.host:
        raise ConfigurationError(
            f'{name} contains an invalid @ in its host. '
            'Encode @ in usernames and passwords as %40; keep the @ before the host.'
        )
    return url
