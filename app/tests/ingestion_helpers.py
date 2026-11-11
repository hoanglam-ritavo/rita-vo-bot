"""Synthetic ingestion inputs shared by unit and integration tests."""
import json
from types import SimpleNamespace
from uuid import UUID
from app.catalog.inventory import file_fingerprint
from app.catalog.validation import new_record


def setup_source(root):
    source = root / 'app/assets' / 'synthetic.pdf'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'synthetic PDF bytes')
    record = new_record('synthetic.pdf', file_fingerprint(source))
    record.update(indexing_decision='include', review_status='reviewed', lifecycle_status='active',
                  owner='Synthetic owner', applicability='Synthetic scope', access_groups=['staff'],
                  review_notes=['Synthetic approval'])
    catalog = root / 'indexing_workspace/catalog/documents.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({'schema_version': 1, 'documents': [record]}), encoding='utf-8')
    return record, source


def options(record, **kwargs):
    return SimpleNamespace(revision_id=UUID(record['revision_id']), model_revision='a' * 40,
                           ocr_language='vie+eng', ocr_dpi=300, tessdata=None, ocr_mode='auto',
                           force=kwargs.get('force', False))


def chunk():
    return dict(chunk_index=0, page=1, content='Synthetic policy.', source='synthetic.pdf',
                extraction_method='native', embedding=[1.0] + [0.0] * 383)
