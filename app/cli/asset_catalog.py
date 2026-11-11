"""Run local asset catalog operations without importing database infrastructure."""

import argparse
import sys
from pathlib import Path

from app.catalog.inventory import CatalogError, inventory, reject_links
from app.catalog.storage import atomic_write, json_text, load_catalog
from app.catalog.reports import build_report, report_summary, write_report
from app.catalog.validation import new_catalog


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'scan', 'validate'))
    args = parser.parse_args(argv)
    # Repository-root paths do not depend on the caller's working directory.
    root = Path(__file__).resolve().parents[2]
    return run(args.command, root)


def run(command: str, root: Path) -> int:
    catalog_path = root / 'data/catalog/documents.json'
    reports = root / 'data/reports'
    try:
        reject_links(catalog_path)
        reject_links(reports)
        if command == 'init' and catalog_path.exists():
            raise CatalogError('Catalog already exists; init never overwrites it.')
        files = inventory(root / 'assets')
        catalog = new_catalog(files) if command == 'init' else load_catalog(catalog_path)
        report = build_report(command, catalog, files)
        if command == 'init':
            atomic_write(catalog_path, json_text(catalog), create_only=True)
        write_report(reports, report)
        print(report_summary(report))
        return report['exit_code']
    except (OSError, CatalogError) as exc:
        # Do not echo paths or arbitrary filesystem/JSON exception payloads.
        message = str(exc) if isinstance(exc, CatalogError) else 'Filesystem operation failed; check local paths and permissions.'
        print(f'Catalog operation failed: {message}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
