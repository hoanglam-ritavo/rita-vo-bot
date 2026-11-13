"""Explicit local retrieval API configuration; imports have no side effects."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from src.paths import REPOSITORY_ROOT


@dataclass(frozen=True)
class Settings:
    model_path: Path
    model_revision: str
    host: str = '127.0.0.1'
    port: int = 8000
    top_k: int = 5

    def __post_init__(self):
        if self.host != '127.0.0.1' or type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('API requires loopback and a valid port.')
        if not re.fullmatch('[a-fA-F0-9]{40}', self.model_revision):
            raise ValueError('E5 revision must be a full commit.')
        if type(self.top_k) is not int or not 1 <= self.top_k <= 20:
            raise ValueError('Invalid retrieval configuration.')


def load_settings():
    load_dotenv(REPOSITORY_ROOT / '.env', override=False)
    revision = os.environ.get('E5_MODEL_REVISION', '614241f622f53c4eeff9890bdc4f31cfecc418b3')
    path = Path(os.environ.get('E5_MODEL_PATH', str(REPOSITORY_ROOT / '.tmp/models/multilingual-e5-small' / revision)))
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return Settings(path, revision, os.environ.get('API_HOST', '127.0.0.1'),
                    int(os.environ.get('API_PORT', '8000')),
                    int(os.environ.get('RETRIEVAL_TOP_K', '5')))
