"""Prepare a fresh Rita VO Bot database on Linux."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cli.linux_release import launch


if __name__ == '__main__':
    raise SystemExit(launch(first=True))
