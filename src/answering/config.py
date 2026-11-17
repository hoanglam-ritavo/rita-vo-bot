"""Explicit template settings; retired model settings are ignored."""
from dataclasses import dataclass
from pathlib import Path
from src.paths import REPOSITORY_ROOT


@dataclass(frozen=True)
class AnswerSettings:
    enabled: bool = False
    catalog_path: Path = REPOSITORY_ROOT / 'artifacts/answers/v1/catalog.json'

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('invalid_answer_configuration')
        path = Path(self.catalog_path)
        object.__setattr__(self, 'catalog_path', path if path.is_absolute() else REPOSITORY_ROOT / path)


def from_environment(env):
    enabled = env.get('ANSWER_ENABLED', 'false').lower()
    if enabled not in ('true', 'false'):
        raise ValueError('invalid_answer_configuration')
    return AnswerSettings(enabled=enabled == 'true',
        catalog_path=Path(env.get('ANSWER_CATALOG_PATH', 'artifacts/answers/v1/catalog.json')))
