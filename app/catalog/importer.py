"""Metadata field conversion shared by package publication."""
from datetime import date
from uuid import UUID
from app.catalog.validation import NULLABLE_TEXT, STATES

IMPORT_LOCK = 728194031

class ImportRejected(ValueError):
    pass

def revision_values(record):
    values = {key: record[key] for key in NULLABLE_TEXT | set(STATES) |
              {'source_path', 'sha256', 'size_bytes', 'extension', 'review_notes'}}
    values.update(document_id=UUID(record['document_id']),
                  source_path_key=record['source_path'].casefold(), is_catalog_current=True)
    for key in ('effective_from', 'effective_to'):
        values[key] = date.fromisoformat(record[key]) if record[key] else None
    return values


async def synchronize(*args, **kwargs):
    raise ImportRejected('Full catalog synchronization is retired; publish an approved package.')


def prepare_catalog(*args, **kwargs):
    raise ImportRejected('Local catalog is retired; use review package metadata.')
