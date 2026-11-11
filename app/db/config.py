"""Configuration without connection side effects."""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[2]


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
    try:
        url = make_url(value)
        if url.drivername != 'postgresql+psycopg' or not url.host or not url.database:
            raise ValueError
        _ = url.port
    except (ArgumentError, ValueError, TypeError):
        raise ConfigurationError(
            f'{name} must be a valid postgresql+psycopg URL with host and database.'
        ) from None
    return url
