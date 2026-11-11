"""Synchronize the local catalog into explicitly migrated PostgreSQL storage."""
import argparse
from pathlib import Path
import sys

from app.catalog.importer import ImportRejected, prepare_catalog, synchronize
from app.db import close_database
from app.db.runtime import run_async


async def run(root: Path, *, dry_run=False):
    try:
        catalog, warnings = prepare_catalog(root)
        result = await synchronize(catalog, dry_run=dry_run, warnings=warnings)
        label = 'Dry run' if dry_run else 'Imported'
        print(f'{label}: documents_added={result.documents_added}, revisions_added={result.revisions_added}, '
              f'revisions_updated={result.revisions_updated}, revisions_retired={result.revisions_retired}, '
              f'warnings={result.warnings}')
        return 0
    except ImportRejected as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print('Catalog import failed; check source access, database configuration and explicit migrations.', file=sys.stderr)
        return 2
    finally:
        await close_database()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    return run_async(run(Path(__file__).resolve().parents[2], dry_run=args.dry_run))


if __name__ == '__main__':
    raise SystemExit(main())
