"""Explicit, private configuration; never invoke the application's dotenv loader."""
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


class EvaluationError(ValueError):
    """Fixed diagnostic only. No source, credentials or driver exceptions."""


def digest(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str, allow_nan=False).encode()).hexdigest()


def base_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in ('', '/')
                or not ipaddress.ip_address(parsed.hostname).is_loopback):
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise EvaluationError('invalid_loopback_url') from None
    return value.rstrip('/')


def private_path(root, value):
    from src.artifact_authoring.sources import checked
    path = Path(value)
    path = checked(path if path.is_absolute() else Path(root) / path)
    if not path.is_relative_to(Path(root).absolute() / '.tmp'):
        raise EvaluationError('private_path_required')
    return path


def write_private(root, value, data):
    path = private_path(root, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation, including a competing creator between check and open.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


@dataclass(frozen=True)
class Configuration:
    url: str
    service_key: str = field(repr=False)
    hrm_url: str = field(repr=False)
    corpus_url: str = field(repr=False)
    schema: str = 'public'

    def __post_init__(self):
        import re
        base_url(self.url)
        if not self.service_key or not self.service_key.isascii() or any(
                ord(c) < 32 or ord(c) == 127 for c in self.service_key):
            raise EvaluationError('invalid_service_key')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', self.schema):
            raise EvaluationError('invalid_test_schema')
        from src.database.config import validate_url
        from src.type_b.employee_store.config import EmployeeSourceSettings
        hrm = validate_url(self.hrm_url, 'TEST_HRM_DATABASE_URL')
        corpus = validate_url(self.corpus_url, 'DATABASE_URL')
        EmployeeSourceSettings(hrm, self.schema)
        EmployeeSourceSettings(corpus)  # Also rejects startup-option overrides.
        target = lambda u: (u.host.casefold(), u.port or 5432, u.database)
        if target(hrm) == target(corpus):
            raise EvaluationError('separate_hrm_database_required')


def configuration(url='http://127.0.0.1:8000', env_file=None, environ=None):
    # Read only these keys; never load dotenv values into os.environ.
    names = ('CHAT_BOT_SERVICE_KEY', 'TEST_HRM_DATABASE_URL', 'DATABASE_URL',
             'TEST_HRM_DATABASE_SCHEMA', 'TEST_HRM_DISPOSABLE_CONFIRMED')
    values = {}
    if env_file is not None:
        from dotenv import dotenv_values
        from src.artifact_authoring.sources import checked
        values = {k: v for k, v in dotenv_values(checked(Path(env_file)), interpolate=False).items()
                  if k in names}
    source = os.environ if environ is None else environ
    values.update({k: source[k] for k in names if k in source})
    if values.get('TEST_HRM_DISPOSABLE_CONFIRMED') != 'true':
        raise EvaluationError('disposable_confirmation_required')
    if any(not values.get(k) for k in names[:3]):
        raise EvaluationError('configuration_missing')
    return Configuration(base_url(url), values['CHAT_BOT_SERVICE_KEY'],
        values['TEST_HRM_DATABASE_URL'], values['DATABASE_URL'],
        values.get('TEST_HRM_DATABASE_SCHEMA') or 'public')
