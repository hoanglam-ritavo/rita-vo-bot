"""Explicit semantic expansion configuration; no environment reads at import."""
from .semantics import SemanticSettings


def from_environment(values):
    return SemanticSettings(values.get('SEMANTIC_MODE', 'off'), values.get('SEMANTIC_ARTIFACT_PATH') or None)


def add_arguments(parser):
    parser.add_argument('--semantic-mode', choices=('off', 'expand'), default='off')
    parser.add_argument('--semantic-artifact-path')


def from_arguments(args):
    return SemanticSettings(getattr(args, 'semantic_mode', 'off'), getattr(args, 'semantic_artifact_path', None))
