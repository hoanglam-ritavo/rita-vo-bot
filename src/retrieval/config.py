"""Explicit transport configuration adapters; no implicit environment loading."""
from pathlib import Path
from .reranking import RerankSettings, MODEL_FILE


def from_environment(values):
    enabled = values.get('RERANK_ENABLED', 'false').lower()
    if enabled not in ('true', 'false'):
        raise ValueError('invalid_reranker_configuration')
    return RerankSettings(enabled=enabled == 'true',
        model_path=Path(values.get('RERANK_MODEL_PATH', 'models')),
        model_file=values.get('RERANK_MODEL_FILE', MODEL_FILE),
        candidate_count=int(values.get('RERANK_CANDIDATE_COUNT', '30')),
        batch_size=int(values.get('RERANK_BATCH_SIZE', '4')),
        profile_path=Path(values['RERANK_PROFILE_PATH']) if values.get('RERANK_PROFILE_PATH') else None)


def add_arguments(parser):
    parser.add_argument('--rerank', action='store_true', help='Enable the pinned local CPU ONNX reranker.')
    parser.add_argument('--rerank-model-path', type=Path, default=Path('models'))
    parser.add_argument('--rerank-candidates', type=int, choices=[30], default=30)
    parser.add_argument('--rerank-batch-size', type=int, choices=range(1, 17), default=4)
    parser.add_argument('--rerank-profile', type=Path)


def from_arguments(args):
    return RerankSettings(enabled=getattr(args, 'rerank', False),
        model_path=getattr(args, 'rerank_model_path', Path('models')),
        candidate_count=getattr(args, 'rerank_candidates', 30),
        batch_size=getattr(args, 'rerank_batch_size', 4),
        profile_path=getattr(args, 'rerank_profile', None))
