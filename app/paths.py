"""Repository-local paths; importing this module performs no filesystem writes."""
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def workspace(root: Path = REPOSITORY_ROOT) -> Path:
    return root / 'app' / 'chunks'


def assets(root: Path = REPOSITORY_ROOT) -> Path:
    return root / 'app/assets'
