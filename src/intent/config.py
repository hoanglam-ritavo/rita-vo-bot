"""Explicit environment/CLI adapters; never read environment implicitly."""
from .runtime import IntentSettings


def from_environment(values):
    return IntentSettings(values.get('INTENT_MODE', 'off'), values.get('INTENT_ARTIFACT_PATH') or None)


def add_arguments(parser):
    parser.add_argument('--intent-mode', choices=('off', 'observe'), default='off')
    parser.add_argument('--intent-artifact-path')


def from_arguments(args):
    return IntentSettings(getattr(args, 'intent_mode', 'off'), getattr(args, 'intent_artifact_path', None))
