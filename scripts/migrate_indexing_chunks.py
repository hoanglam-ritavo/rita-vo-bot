"""Retired workspace migration; explicit package conversion is non-destructive."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    print('Workspace migration is retired. Use scripts/convert_chunk_package.py --package <path> to preview selected review/approved packages. Historical indexing_chunks and indexing_workspace are not scanned or removed.')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
