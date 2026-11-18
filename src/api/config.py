"""Explicit local retrieval API configuration; imports have no side effects."""
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from src.paths import REPOSITORY_ROOT
from src.retrieval.reranking import RerankSettings
from src.retrieval.config import from_environment
from src.retrieval.semantics import SemanticSettings
from src.retrieval.semantic_config import from_environment as semantic_from_environment
from src.answering.config import AnswerSettings, from_environment as answer_from_environment
from src.type_b.config import TypeBSettings, from_environment as type_b_from_environment

logger = logging.getLogger(__name__)
DIAGNOSTIC_KEYS = ('ANSWER_ENABLED', 'SEMANTIC_MODE')
RETIRED_KEYS = ('RELEVANCE_FILTER_ENABLED', 'RERANK_PROFILE_PATH', 'INTENT_MODE', 'INTENT_ARTIFACT_PATH')


@dataclass(frozen=True)
class Settings:
    model_path: Path
    model_revision: str
    host: str = '127.0.0.1'
    port: int = 8000
    top_k: int = 5
    rerank: RerankSettings = RerankSettings()
    semantic: SemanticSettings = SemanticSettings()
    answer: AnswerSettings = AnswerSettings()
    type_b: TypeBSettings = TypeBSettings()

    def __post_init__(self):
        if self.host != '127.0.0.1' or type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('API requires loopback and a valid port.')
        if not re.fullmatch('[a-fA-F0-9]{40}', self.model_revision):
            raise ValueError('E5 revision must be a full commit.')
        if type(self.top_k) is not int or not 1 <= self.top_k <= 20:
            raise ValueError('Invalid retrieval configuration.')


def load_settings():
    env_path = REPOSITORY_ROOT / '.env'
    inherited = {key: os.environ.get(key) for key in DIAGNOSTIC_KEYS}
    file_values = dotenv_values(env_path)
    # HTTP startup owns this precedence, including downstream database settings.
    # Standalone CLI/database entrypoints retain their own loading policy.
    load_dotenv(env_path, override=True)
    for key in RETIRED_KEYS:
        if key in os.environ:
            logger.warning('retired_configuration_ignored key=%s', key)
    revision = os.environ.get('E5_MODEL_REVISION', '614241f622f53c4eeff9890bdc4f31cfecc418b3')
    path = Path(os.environ.get('E5_MODEL_PATH', str(REPOSITORY_ROOT / 'models/embeddings/multilingual-e5-small' / revision)))
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    settings = Settings(path, revision, os.environ.get('API_HOST', '127.0.0.1'),
                    int(os.environ.get('API_PORT', '8000')),
                    int(os.environ.get('RETRIEVAL_TOP_K', '5')), from_environment(os.environ),
                    semantic=semantic_from_environment(os.environ),
                    answer=answer_from_environment(os.environ),
                    type_b=type_b_from_environment(os.environ))
    effective = {
        'ANSWER_ENABLED': str(settings.answer.enabled).lower(),
        'SEMANTIC_MODE': settings.semantic.mode,
    }
    for key, value in effective.items():
        from_file = file_values.get(key) is not None
        source = 'dotenv' if from_file else 'process' if inherited[key] is not None else 'default'
        if from_file and inherited[key] is not None and inherited[key] != file_values[key]:
            logger.warning('http_configuration_conflict key=%s selected=dotenv', key)
        logger.info('http_configuration key=%s source=%s value=%s', key, source, value)
    return settings
