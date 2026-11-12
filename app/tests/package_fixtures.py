"""Synthetic package contracts, not PDF/XLSX parsing or extraction fidelity tests."""
from datetime import datetime, timezone
from uuid import uuid4
from app.ingestion import document_packages as p
from app.paths import assets, workspace


def ready(root, *, warnings=None, name='policy.txt'):
    source = assets(root) / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'Original policy.\n\nSecond paragraph.')
    path, = p.initialize(root, name)
    record = p.metadata(path)
    record.update(indexing_decision='include', lifecycle_status='active',
                  applicability='Synthetic scope', access_groups=['staff'])
    (path / 'metadata.json').write_bytes(p.encoded(record))
    texts = ['Original policy.', 'Second paragraph.']
    segments = [{'segment_id': f's{i}', 'raw': text, 'text': text,
                 'location': {'kind': 'text', 'line_start': i * 2 - 1, 'line_end': i * 2 - 1},
                 'extraction_method': 'native', 'details': {}} for i, text in enumerate(texts, 1)]
    extraction = {'schema_version': 2, 'status': 'complete', 'configuration': {},
                  'parser_metadata': {'method': 'synthetic-agent'}, 'segments': segments,
                  'warnings': warnings or []}
    chunks = {'schema_version': 2, 'chunks': [{'content': s['text'],
              'source_refs': [{'segment_id': s['segment_id'], 'start': 0, 'end': len(s['text'])}]} for s in segments],
              'excluded': [], 'warning_decisions': {w['warning_id']: '' for w in warnings or []}}
    (path / 'extraction.json').write_bytes(p.encoded(extraction))
    (path / 'chunks.json').write_bytes(p.encoded(chunks))
    return path, source


def legacy_package(root, kind='ready', *, unresolved=False, extension='.txt',
                   location=None, with_exclusion=False, content_only=False):
    from app.catalog.legacy_validation import new_record
    from app.catalog.inventory import file_fingerprint
    from app.ingestion import legacy_packages as old
    source = assets(root) / ('legacy' + extension)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'Original policy.')
    record = new_record('legacy' + extension, file_fingerprint(source))
    if kind != 'pending' and not content_only:
        record.update(indexing_decision='include', review_status='reviewed', lifecycle_status='active',
                      owner='Synthetic owner', applicability='Synthetic scope', access_groups=['staff'],
                      review_notes=['Synthetic evidence'])
    source_name = 'source' + extension
    location = location if location is not None else source_location(extension)
    package_id = str(uuid4())
    manifest = {'schema_version': 2, 'kind': 'review', 'state': 'pending' if kind == 'pending' else 'ready',
                'identity': {k: record[k] for k in p.IDENTITY_FIELDS}}
    files = {'metadata.json': p.encoded(record), 'manifest.json': p.encoded(manifest)}
    if kind != 'pending':
        identity = old.identity(record, package_id)
        extraction = {'schema_version': 1, 'status': 'complete', 'identity': identity, 'configuration': {},
                      'parser_metadata': {'method': 'synthetic'}, 'segments': [{
                      'segment_id': 's1', 'raw': 'Original policy.', 'text': 'Original policy.',
                      'location': location,
                      'extraction_method': 'native', 'details': {}}],
                      'warnings': [{'warning_id': 'w1', 'code': 'uncertain', 'location': {}}] if unresolved else []}
        draft = {'schema_version': 1, 'identity': identity, 'chunks': [{
                 'chunk_id': str(uuid4()), 'content': 'Original' if with_exclusion else 'Original policy.',
                 'source_refs': [{'segment_id': 's1', 'start': 0, 'end': 8 if with_exclusion else 16}], 'review_notes': 'Synthetic note'}],
                 'excluded': [{'source_ref': {'segment_id': 's1', 'start': 8, 'end': 16},
                               'reason': 'Synthetic excluded span'}] if with_exclusion else [], 'warning_decisions': {'w1': ''} if unresolved else {}}
        files.update({source_name: source.read_bytes(), 'extraction.json': p.encoded(extraction),
                      'draft.initial.json': p.encoded(draft), 'draft.json': p.encoded(draft)})
        manifest.update(source_file=source_name, files={k: p.sha(files[k]) for k in (source_name, 'extraction.json', 'draft.initial.json')})
        files['manifest.json'] = p.encoded(manifest)
        if kind == 'approved':
            files['review-manifest.json'] = files.pop('manifest.json')
            payload = {'schema_version': 1, 'identity': identity, 'chunks': old.validate_draft(extraction, draft),
                       'excluded': draft['excluded'], 'warning_decisions': draft['warning_decisions']}
            files['payload.json'] = p.encoded(payload)
            manifest = {'schema_version': 2, 'kind': 'approval', 'approval_id': package_id,
                        'reviewer': 'Synthetic reviewer', 'approved_at': datetime.now(timezone.utc).isoformat(),
                        'content_hash': p.canonical_hash(payload), 'files': {k: p.sha(v) for k, v in files.items()}}
            if content_only:
                manifest['approval_scope'] = 'content'
            files['manifest.json'] = p.encoded(manifest)
    path = p.write_package(workspace(root) / ('approved' if kind == 'approved' else 'review') / record['revision_id'], package_id, files)
    return path, source


def source_location(extension):
    if extension == '.pdf':
        return {'kind': 'pdf', 'page_start': 1, 'page_end': 1}
    if extension == '.xlsx':
        return {'kind': 'xlsx', 'sheet': 'Synthetic', 'row': 2, 'column_start': 1,
                'column_end': 2, 'header_row': 1}
    return {'kind': 'csv' if extension == '.csv' else 'text', 'line_start': 1, 'line_end': 1}


def revised_package(root, previous, *, name='replacement.txt'):
    """A source-byte revision with unchanged synthetic non-whitespace text."""
    source = assets(root) / name
    source.write_bytes((previous / 'source.txt').read_bytes() + b'\n')
    target = p.revise(root, previous, name)
    record = p.metadata(target)
    record.update(indexing_decision='include', lifecycle_status='active',
                  applicability='Synthetic scope', access_groups=['staff'])
    (target / 'metadata.json').write_bytes(p.encoded(record))
    for filename in ('extraction.json', 'chunks.json'):
        (target / filename).write_bytes((previous / filename).read_bytes())
    return target


def set_metadata(package, **changes):
    record = p.metadata(package)
    record.update(changes)
    (package / 'metadata.json').write_bytes(p.encoded(record))
    return record


def batch_arguments(paths=None, *, all=False, model_path=None, dry_run=False):
    from types import SimpleNamespace
    return SimpleNamespace(all=all, package=paths, model_revision='a'*40, model_path=model_path,
                           dry_run=dry_run, force=False, update_metadata=False, json=True)
