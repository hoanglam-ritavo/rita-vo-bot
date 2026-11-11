"""Versioned local review contracts. No database, OCR or model imports."""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from app.catalog.inventory import reject_links, file_fingerprint, inventory
from app.catalog.inventory import safe_relative
from app.catalog.storage import load_catalog, atomic_write, json_text
from app.catalog.validation import validate_schema, compare
from app.paths import assets, workspace

SUPPORTED = {'.pdf', '.xlsx', '.csv', '.md', '.txt'}


def io_path(path):
    """Extended Windows paths for package IO, without changing public path values."""
    path = Path(path).absolute()
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        value = str(path)
        path = Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)
    return path


class Rejected(ValueError):
    """Messages contain fixed error codes, never document content."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json_text(value).encode('utf-8')


def canonical_hash(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8'))


def decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique,
                          parse_constant=lambda _: require(False, 'nonfinite_json'))
    except (UnicodeError, json.JSONDecodeError):
        raise Rejected('invalid_json') from None


def uuid(value):
    try:
        require(isinstance(value, str) and str(UUID(value)) == value, 'invalid_uuid')
    except (ValueError, TypeError, AttributeError):
        raise Rejected('invalid_uuid') from None


def catalog(root):
    value = load_catalog(workspace(root) / 'catalog/documents.json')
    require(not validate_schema(value), 'catalog_invalid')
    differences = compare(value, inventory(assets(root)))
    require(not any(differences[k] for k in ('added', 'missing', 'changed')), 'catalog_source_drift')
    return value['documents']


def selected(root, revision_id, *, eligible=False):
    record = next((r for r in catalog(root) if r['revision_id'] == str(revision_id)), None)
    require(record is not None, 'revision_missing')
    require(record['extension'] in SUPPORTED, 'unsupported_format')
    require(record['indexing_decision'] != 'exclude' and record['lifecycle_status'] in ('unknown', 'active'), 'source_ineligible')
    if eligible:
        require(record['indexing_decision'] == 'include' and record['review_status'] == 'reviewed'
                and record['lifecycle_status'] == 'active', 'source_not_approved')
    source = assets(root) / record['source_path']
    reject_links(source)
    require(source.resolve().is_relative_to(assets(root).resolve()), 'source_outside_assets')
    require(file_fingerprint(source)['sha256'] == record['sha256'], 'source_changed')
    return record, source


def identity(record, extraction_id):
    return {key: record[key] for key in ('document_id', 'revision_id', 'source_path', 'sha256', 'extension')} | {'extraction_id': extraction_id}


def read_file(directory, name):
    require(isinstance(name, str) and Path(name).name == name and name not in ('', '.', '..')
            and '/' not in name and '\\' not in name and ':' not in name, 'unsafe_package_path')
    path = io_path(directory / name)
    reject_links(path)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise Rejected('package_file_missing') from None


def write_package(parent, package_id, files):
    """A staged directory becomes visible only after every file is complete."""
    public_target = parent / package_id
    parent = io_path(parent)
    reject_links(parent)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / package_id
    require(not target.exists(), 'package_exists')
    staging = parent / ('.staging-' + str(uuid4()))
    staging.mkdir()
    for name, data in files.items():
        require(Path(name).name == name, 'unsafe_package_path')
        with (staging / name).open('xb') as stream:
            stream.write(data)
    # Failure leaves an explicitly incomplete staging directory, never a valid package.
    staging.rename(target)
    return public_target


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


def validate_draft(extraction, draft):
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
    require(set(decisions) == warning_ids and all(isinstance(v, str) and v.strip() for v in decisions.values()),
            'unresolved_warnings')
    return output


def load_review(directory):
    directory = io_path(directory)
    reject_links(directory)
    require(not directory.name.startswith('.'), 'incomplete_package')
    manifest = decode(read_file(directory, 'manifest.json'))
    require(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int
            and manifest.get('schema_version') == 1 and manifest.get('kind') == 'review', 'not_review_package')
    require(isinstance(manifest.get('source_file'), str) and isinstance(manifest.get('files'), dict), 'review_manifest_fields')
    expected = {'extraction.json', 'draft.initial.json', manifest.get('source_file')}
    require(set(manifest.get('files', {})) == expected, 'review_manifest_files')
    files = {}
    for name, fingerprint in manifest['files'].items():
        files[name] = read_file(directory, name)
        require(sha(files[name]) == fingerprint, 'review_hash_mismatch')
    extraction = decode(files['extraction.json'])
    validate_extraction(extraction)
    require(extraction.get('schema_version') == 1 and extraction.get('status') == 'complete', 'incomplete_extraction')
    require(manifest['source_file'] == 'source' + extraction['identity']['extension'], 'source_filename')
    require(sha(files[manifest['source_file']]) == extraction['identity']['sha256'], 'snapshot_mismatch')
    draft_bytes = read_file(directory, 'draft.json')
    draft = decode(draft_bytes)
    chunks = validate_draft(extraction, draft)
    require(decode(files['draft.initial.json'])['identity'] == extraction['identity'], 'initial_identity_mismatch')
    files['draft.json'] = draft_bytes
    files['manifest.json'] = encoded(manifest)
    return extraction, draft, chunks, files


def approve(root, directory, reviewer):
    require(isinstance(reviewer, str) and reviewer.strip(), 'reviewer_required')
    extraction, draft, chunks, files = load_review(directory)
    record, _ = selected(root, extraction['identity']['revision_id'], eligible=True)
    require(identity(record, extraction['identity']['extraction_id']) == extraction['identity'], 'catalog_identity_mismatch')
    approval_id = str(uuid4())
    files['review-manifest.json'] = files.pop('manifest.json')
    payload = {'schema_version': 1, 'identity': extraction['identity'], 'chunks': chunks,
               'excluded': draft['excluded'], 'warning_decisions': draft['warning_decisions']}
    files['payload.json'] = encoded(payload)
    manifest = {'schema_version': 1, 'kind': 'approval', 'approval_id': approval_id,
                'reviewer': reviewer, 'approved_at': datetime.now(timezone.utc).isoformat(),
                'content_hash': canonical_hash(payload), 'files': {k: sha(v) for k, v in files.items()}}
    files['manifest.json'] = encoded(manifest)
    return write_package(workspace(root) / 'approved' / record['revision_id'], approval_id, files)


def load_approval(root, directory):
    directory = io_path(directory)
    reject_links(directory)
    require(directory.resolve().is_relative_to(io_path(workspace(root) / 'approved').resolve()), 'approval_outside_workspace')
    require(not directory.name.startswith('.'), 'incomplete_package')
    manifest = decode(read_file(directory, 'manifest.json'))
    require(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int
            and manifest.get('schema_version') == 1 and manifest.get('kind') == 'approval', 'not_approved')
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
    require(set(files) == {'payload.json', 'extraction.json', 'draft.initial.json', 'draft.json', 'review-manifest.json', source_file}, 'approval_files')
    review_manifest = decode(files['review-manifest.json'])
    require(review_manifest.get('kind') == 'review' and review_manifest.get('schema_version') == 1
            and set(review_manifest.get('files', {})) == {'extraction.json', 'draft.initial.json', source_file}, 'review_manifest_files')
    require(all(sha(files[k]) == v for k, v in review_manifest['files'].items()), 'review_hash_mismatch')
    expected = {'schema_version': 1, 'identity': extraction['identity'], 'chunks': validate_draft(extraction, draft),
                'excluded': draft['excluded'], 'warning_decisions': draft['warning_decisions']}
    require(payload == expected and canonical_hash(payload) == manifest.get('content_hash'), 'payload_mismatch')
    require(sha(files[source_file]) == payload['identity']['sha256'], 'snapshot_mismatch')
    record, _ = selected(root, payload['identity']['revision_id'], eligible=True)
    require(identity(record, payload['identity']['extraction_id']) == payload['identity'], 'catalog_identity_mismatch')
    require(directory.name == manifest['approval_id'] and directory.parent.name == record['revision_id'], 'approval_location')
    files['manifest.json'] = encoded(manifest)
    return record, payload, manifest, files


def report(root, operation, results):
    run_id = str(uuid4())
    path = workspace(root) / 'reports' / f'{operation}-{run_id}.json'
    atomic_write(path, json_text({'operation': operation, 'results': results}), create_only=True)
    counts = {s: sum(r['status'] == s for r in results) for s in ('processed', 'skipped', 'rejected', 'failed')}
    print(' '.join(f'{key}={value}' for key, value in counts.items()))
    print(f'Report: {path}')
    return 2 if counts['failed'] else 1 if counts['rejected'] else 0
