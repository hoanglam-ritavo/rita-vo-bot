"""Answering is opt-in; the filtered branch requires a reviewed matching study."""
from dataclasses import dataclass
from pathlib import Path

from src.paths import REPOSITORY_ROOT


@dataclass(frozen=True)
class AnswerSettings:
    enabled: bool = False
    model_path: Path = REPOSITORY_ROOT / 'models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf'
    model_sha256: str = ''
    report_path: Path | None = None
    review_path: Path | None = None


def from_environment(env):
    enabled = env.get('ANSWER_ENABLED', 'false').lower()
    if enabled not in ('true', 'false'):
        raise ValueError('Invalid answer configuration.')
    def path(name, default=None):
        value = env.get(name, default)
        if not value:
            return None
        result = Path(value)
        return result if result.is_absolute() else REPOSITORY_ROOT / result
    return AnswerSettings(
        enabled=enabled == 'true',
        model_path=path('ANSWER_MODEL_PATH', 'models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf'),
        model_sha256=env.get('ANSWER_MODEL_SHA256', ''),
        report_path=path('ANSWER_REPORT_PATH'), review_path=path('ANSWER_REVIEW_PATH'))
