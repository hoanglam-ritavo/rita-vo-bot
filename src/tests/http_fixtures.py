"""Fictional HTTP credentials and isolation from the developer's private .env."""
from pathlib import Path
import pytest

SERVICE_KEY = 'fictional-service-key'
SERVICE_HEADERS = {'X-Rita-Service-Key': SERVICE_KEY}


@pytest.fixture(autouse=True)
def http_service_environment(monkeypatch):
    from src.api import config
    from src.paths import REPOSITORY_ROOT
    private = (REPOSITORY_ROOT / '.env').resolve()
    read_values, load_values = config.dotenv_values, config.load_dotenv
    monkeypatch.setenv('CHAT_BOT_SERVICE_KEY', SERVICE_KEY)
    monkeypatch.setattr(config, 'dotenv_values', lambda path: {} if Path(path).resolve() == private else read_values(path))
    monkeypatch.setattr(config, 'load_dotenv', lambda path, **kwargs:
        False if Path(path).resolve() == private else load_values(path, **kwargs))
