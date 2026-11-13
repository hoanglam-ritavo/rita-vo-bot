"""Local catalog discrepancy reports."""
import json
from pathlib import Path
from uuid import uuid4
from src.catalog.validation import compare, validate_schema
from src.catalog.storage import atomic_write, json_text


def build_report(command: str, catalog: object, files: dict) -> dict:
    errors = validate_schema(catalog)
    report = {'command': command, 'errors': errors, 'source_count': len(files),
              'catalog_count': 0, 'hold_count': 0, 'include_count': 0,
              'exit_code': 1}
    if errors:
        return report
    documents = catalog['documents']
    report.update(compare(catalog, files))
    report.update(catalog_count=len(documents),
                  hold_count=sum(r['indexing_decision'] == 'hold' for r in documents),
                  include_count=sum(r['indexing_decision'] == 'include' for r in documents))
    report['exit_code'] = int(any(report[key] for key in
                                 ('added', 'missing', 'changed', 'unresolved_duplicates', 'unrecognized_formats')))
    return report


def report_summary(report: dict) -> str:
    counts = {key: len(value) if isinstance(value, list) else value
              for key, value in report.items() if key != 'command'}
    return ' '.join(f'{key}={value}' for key, value in counts.items())


def write_report(directory: Path, report: dict) -> None:
    run_id = str(uuid4())
    basename = f'asset-catalog-{report["command"]}-{run_id}'
    atomic_write(directory / f'{basename}.json', json_text(report), create_only=True)
    lines = ['Asset catalog report', report_summary(report), '']
    for key, value in report.items():
        if isinstance(value, list) and value:
            lines.extend([key + ':', json.dumps(value, ensure_ascii=False, indent=2), ''])
    atomic_write(directory / f'{basename}.txt', '\n'.join(lines), create_only=True)
