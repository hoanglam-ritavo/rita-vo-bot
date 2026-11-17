"""Explicit HRM configuration; importing this module has no environment IO."""
from dataclasses import dataclass, field
import os
import re

from sqlalchemy.engine import URL

from src.database.config import ConfigurationError, validate_url


@dataclass(frozen=True)
class EmployeeSourceSettings:
    url: URL = field(repr=False)
    schema: str = 'public'

    def __post_init__(self):
        if not isinstance(self.url, URL):
            raise ConfigurationError('HRM_DATABASE_URL must be a parsed PostgreSQL URL.')
        validate_url(self.url.render_as_string(hide_password=False), 'HRM_DATABASE_URL')
        if not isinstance(self.schema, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', self.schema):
            raise ConfigurationError('HRM_DATABASE_SCHEMA must be a simple PostgreSQL identifier.')
        if self.url.drivername != 'postgresql+psycopg':
            raise ConfigurationError('HRM_DATABASE_URL requires postgresql+psycopg.')
        # Connection options must not override the source's readonly startup settings.
        if any(key.lower() in {'options', 'service', 'servicefile'} for key in self.url.query):
            raise ConfigurationError('HRM_DATABASE_URL must not override connection startup options.')

    @classmethod
    def from_environment(cls):
        return cls(validate_url(os.environ.get('HRM_DATABASE_URL'), 'HRM_DATABASE_URL'),
                   os.environ.get('HRM_DATABASE_SCHEMA', 'public'))
