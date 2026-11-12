"""Read-only legacy contracts for explicit conversion; never used for publication."""
from datetime import datetime
from pathlib import Path
import re
from app.catalog.legacy_validation import validate_schema
from app.catalog.inventory import reject_links, safe_relative
from app.paths import workspace
from app.ingestion.document_packages import (Rejected, require, uuid, sha, encoded, decode,
    canonical_hash, io_path, read_file, IDENTITY_FIELDS, SUPPORTED)

def validate_metadata(record):
    # Cross-revision relations are checked against the database at publication.
    candidate = dict(record)
    relations = candidate.get('relations')
    require(isinstance(relations, list), 'metadata_relations')
    candidate['relations'] = []
    require(not validate_schema({'schema_version': 1, 'documents': [candidate]}), 'metadata_invalid')
    from app.catalog.legacy_validation import RELATIONS
    for relation in relations:
        require(isinstance(relation, dict) and set(relation) == {'type', 'revision_id'}
                and relation['type'] in RELATIONS, 'metadata_relation')
        uuid(relation['revision_id'])
        require(relation['revision_id'] != record['revision_id'], 'self_relation')
        if relation['type'] != 'duplicate_of':
            require(record['review_status'] == 'reviewed' and bool(record['review_notes']), 'relation_review_required')
    return record

def metadata(directory):
    manifest = decode(read_file(directory, 'manifest.json'))
    require(manifest.get('schema_version') == 2 and manifest.get('kind') == 'review', 'not_review_package')
    record = validate_metadata(decode(read_file(directory, 'metadata.json')))
    require({k: record[k] for k in IDENTITY_FIELDS} == manifest.get('identity'), 'metadata_identity_changed')
    return record

def identity(record, extraction_id):
    return {key: record[key] for key in ('document_id', 'revision_id', 'source_path', 'sha256', 'extension')} | {'extraction_id': extraction_id}

def validate_extraction(extraction):
    require(isinstance(extraction, dict) and set(extraction) == {
        'schema_version', 'status', 'identity', 'configuration', 'parser_metadata', 'segments', 'warnings'}, 'extraction_fields')
    require(type(extraction['schema_version']) is int and extraction['schema_version'] == 1
            and extraction['status'] == 'complete', 'incomplete_extraction')
    ident = extraction['identity']
    require(isinstance(ident, dict) and set(ident) == {
        'document_id', 'revision_id', 'source_path', 'sha256', 'extension', 'extraction_id'}, 'identity_fields')
    for key in ('document_id', 'revision_id', 'extraction_id'):
        uuid(ident[key])
    require(isinstance(ident['sha256'], str) and re.fullmatch('[0-9a-f]{64}', ident['sha256']), 'source_hash')
    require(safe_relative(ident['source_path']) and ident['extension'] in SUPPORTED
            and Path(ident['source_path']).suffix.lower() == ident['extension'], 'source_identity')
    require(isinstance(extraction['configuration'], dict) and isinstance(extraction['parser_metadata'], dict), 'extraction_configuration')
    require(isinstance(extraction['segments'], list) and bool(extraction['segments']), 'extraction_segments')
    for segment in extraction['segments']:
        require(isinstance(segment, dict) and set(segment) == {'segment_id', 'raw', 'text', 'location', 'extraction_method', 'details'}, 'segment_fields')
        require(isinstance(segment['segment_id'], str) and bool(segment['segment_id'])
                and isinstance(segment['raw'], str) and isinstance(segment['text'], str) and bool(segment['text'].strip())
                and segment['extraction_method'] in ('native', 'ocr') and isinstance(segment['details'], dict), 'segment_content')
        location = segment['location']
        require(isinstance(location, dict) and location.get('kind') in ('pdf', 'text', 'csv', 'xlsx'), 'segment_location')
        keys = ('page_start', 'page_end') if location['kind'] == 'pdf' else ('line_start', 'line_end') if location['kind'] in ('text', 'csv') else ('row', 'column_start', 'column_end', 'header_row')
        require(all(type(location.get(key)) is int and location[key] > 0 for key in keys), 'segment_location_range')
    require(isinstance(extraction['warnings'], list), 'warnings_type')
    ids = set()
    for warning in extraction['warnings']:
        require(isinstance(warning, dict) and set(warning) == {'warning_id', 'code', 'location'}
                and isinstance(warning['warning_id'], str) and isinstance(warning['code'], str)
                and isinstance(warning['location'], dict), 'warning_fields')
        require(warning['warning_id'] not in ids, 'duplicate_warning')
        ids.add(warning['warning_id'])

def validate_draft(extraction, draft, *, allow_unresolved=False):
    validate_extraction(extraction)
    require(isinstance(draft, dict) and set(draft) == {
        'schema_version', 'identity', 'chunks', 'excluded', 'warning_decisions'}, 'draft_fields')
    require(type(draft['schema_version']) is int and draft['schema_version'] == 1, 'draft_version')
    require(draft['identity'] == extraction['identity'], 'identity_mismatch')
    segments = {s['segment_id']: s for s in extraction['segments']}
    require(len(segments) == len(extraction['segments']), 'duplicate_segment')
    coverage = {key: [] for key in segments}

    def reference(ref):
        require(isinstance(ref, dict) and set(ref) == {'segment_id', 'start', 'end'}, 'reference_fields')
        key, start, end = ref['segment_id'], ref['start'], ref['end']
        require(isinstance(key, str) and key in segments, 'unknown_segment')
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(segments[key]['text']), 'reference_range')
        coverage[key].append((start, end))
        return {'segment_id': key, 'start': start, 'end': end,
                'location': segments[key]['location'], 'extraction_method': segments[key]['extraction_method']}

    require(isinstance(draft['chunks'], list) and bool(draft['chunks']), 'empty_chunks')
    output, ids = [], set()
    for item in draft['chunks']:
        require(isinstance(item, dict) and set(item) == {'chunk_id', 'content', 'source_refs', 'review_notes'}, 'chunk_fields')
        uuid(item['chunk_id'])
        require(item['chunk_id'] not in ids, 'duplicate_chunk')
        ids.add(item['chunk_id'])
        require(isinstance(item['content'], str) and bool(item['content'].strip()), 'empty_content')
        require(isinstance(item['review_notes'], str), 'review_notes_type')
        require(isinstance(item['source_refs'], list) and bool(item['source_refs']), 'empty_references')
        spans = [reference(ref) for ref in item['source_refs']]
        output.append({'chunk_index': len(output), 'content': item['content'],
            'source_location': {'review_chunk_id': item['chunk_id'], 'spans': spans},
            'extraction_method': 'ocr' if any(s['extraction_method'] == 'ocr' for s in spans) else 'native'})
    require(isinstance(draft['excluded'], list), 'excluded_type')
    for item in draft['excluded']:
        require(isinstance(item, dict) and set(item) == {'source_ref', 'reason'}
                and isinstance(item['reason'], str) and bool(item['reason'].strip()), 'exclusion_reason_required')
        reference(item['source_ref'])
    for key, intervals in coverage.items():
        text = segments[key]['text']
        end = 0
        for first, last in sorted(intervals):
            require(not text[end:first].strip(), 'uncovered_source')
            end = max(end, last)
        require(not text[end:].strip(), 'uncovered_source')
    decisions = draft['warning_decisions']
    require(isinstance(decisions, dict), 'warning_decisions_type')
    warning_ids = {w['warning_id'] for w in extraction['warnings']}
    require(set(decisions) == warning_ids and all(isinstance(v, str) for v in decisions.values()), 'warning_decisions_type')
    if not allow_unresolved:
        require(all(v.strip() for v in decisions.values()), 'unresolved_warnings')
    return output

def load_review(directory, *, allow_unresolved=False):
    directory = io_path(directory)
    reject_links(directory)
    require(not directory.name.startswith('.'), 'incomplete_package')
    manifest = decode(read_file(directory, 'manifest.json'))
    require(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int
            and manifest.get('schema_version') == 2 and manifest.get('kind') == 'review', 'not_review_package')
    require(isinstance(manifest.get('source_file'), str) and isinstance(manifest.get('files'), dict), 'review_manifest_fields')
    require(manifest.get('state') == 'ready', 'incomplete_review')
    record = metadata(directory)
    expected = {'extraction.json', 'draft.initial.json', manifest.get('source_file')}
    require(set(manifest.get('files', {})) == expected, 'review_manifest_files')
    files = {}
    for name, fingerprint in manifest['files'].items():
        files[name] = read_file(directory, name)
        require(sha(files[name]) == fingerprint, 'review_hash_mismatch')
    extraction = decode(files['extraction.json'])
    validate_extraction(extraction)
    require(identity(record, extraction['identity']['extraction_id']) == extraction['identity'], 'metadata_identity_changed')
    require(len(files[manifest['source_file']]) == record['size_bytes'], 'snapshot_size')
    require(extraction.get('schema_version') == 1 and extraction.get('status') == 'complete', 'incomplete_extraction')
    require(manifest['source_file'] == 'source' + extraction['identity']['extension'], 'source_filename')
    require(sha(files[manifest['source_file']]) == extraction['identity']['sha256'], 'snapshot_mismatch')
    draft_bytes = read_file(directory, 'draft.json')
    draft = decode(draft_bytes)
    chunks = validate_draft(extraction, draft, allow_unresolved=allow_unresolved)
    require(decode(files['draft.initial.json'])['identity'] == extraction['identity'], 'initial_identity_mismatch')
    files['draft.json'] = draft_bytes
    files['manifest.json'] = encoded(manifest)
    files['metadata.json'] = encoded(record)
    return extraction, draft, chunks, files

def load_approval(root, directory):
    directory = io_path(directory)
    reject_links(directory)
    require(directory.resolve().is_relative_to(io_path(workspace(root) / 'approved').resolve()), 'approval_outside_workspace')
    require(not directory.name.startswith('.'), 'incomplete_package')
    manifest = decode(read_file(directory, 'manifest.json'))
    require(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int
            and manifest.get('schema_version') == 2 and manifest.get('kind') == 'approval', 'not_approved')
    scope = manifest.get('approval_scope', 'publication')
    require(scope in ('content', 'publication'), 'approval_scope')
    uuid(manifest.get('approval_id'))
    require(isinstance(manifest.get('reviewer'), str) and bool(manifest['reviewer'].strip()), 'reviewer_required')
    try:
        require(datetime.fromisoformat(manifest['approved_at']).utcoffset() is not None, 'approval_time')
    except (KeyError, ValueError, TypeError):
        raise Rejected('approval_time') from None
    files = {}
    require(isinstance(manifest.get('files'), dict), 'approval_files')
    for name, fingerprint in manifest['files'].items():
        files[name] = read_file(directory, name)
        require(sha(files[name]) == fingerprint, 'approval_hash_mismatch')
    require({'payload.json', 'extraction.json', 'draft.initial.json', 'draft.json', 'review-manifest.json'} <= files.keys(), 'approval_files')
    payload = decode(files['payload.json'])
    extraction = decode(files['extraction.json'])
    validate_extraction(extraction)
    draft = decode(files['draft.json'])
    require(isinstance(payload, dict) and type(payload.get('schema_version')) is int
            and payload.get('schema_version') == 1 and extraction.get('schema_version') == 1
            and extraction.get('status') == 'complete', 'package_version')
    source_file = 'source' + extraction['identity']['extension']
    require(set(files) == {'payload.json', 'extraction.json', 'draft.initial.json', 'draft.json', 'review-manifest.json', 'metadata.json', source_file}, 'approval_files')
    review_manifest = decode(files['review-manifest.json'])
    require(review_manifest.get('kind') == 'review' and review_manifest.get('schema_version') == 2
            and set(review_manifest.get('files', {})) == {'extraction.json', 'draft.initial.json', source_file}, 'review_manifest_files')
    require(all(sha(files[k]) == v for k, v in review_manifest['files'].items()), 'review_hash_mismatch')
    expected = {'schema_version': 1, 'identity': extraction['identity'], 'chunks': validate_draft(extraction, draft),
                'excluded': draft['excluded'], 'warning_decisions': draft['warning_decisions']}
    require(payload == expected and canonical_hash(payload) == manifest.get('content_hash'), 'payload_mismatch')
    require(sha(files[source_file]) == payload['identity']['sha256'], 'snapshot_mismatch')
    record = validate_metadata(decode(files['metadata.json']))
    require({k: record[k] for k in IDENTITY_FIELDS} == review_manifest.get('identity'), 'metadata_identity_changed')
    if scope == 'content':
        require(record['indexing_decision'] != 'exclude'
                and record['lifecycle_status'] in ('unknown', 'active'), 'source_ineligible')
    else:
        require(record['indexing_decision'] == 'include', 'source_not_approved')
    require(len(files[source_file]) == record['size_bytes'], 'snapshot_size')
    require(identity(record, payload['identity']['extraction_id']) == payload['identity'], 'metadata_identity_mismatch')
    require(directory.name == manifest['approval_id'] and directory.parent.name == record['revision_id'], 'approval_location')
    files['manifest.json'] = encoded(manifest)
    return record, payload, manifest, files

def inspect_legacy_approval(directory):
    sidecar = decode(read_file(directory, 'migration.json'))
    require(sidecar.get('requires_reapproval') is True, 'not_legacy_approval')
    for name, digest in sidecar['original_files'].items():
        require(sha(read_file(directory, name)) == digest, 'legacy_hash_mismatch')
    return {'requires_reapproval': True, 'metadata': validate_metadata(sidecar['metadata'])}
