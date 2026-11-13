"""Agent-authored v2 chunk packages; no database, model or extraction runtime."""
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from src.catalog.inventory import reject_links, file_fingerprint, inventory, safe_relative
from src.catalog.storage import json_text
from src.catalog.validation import validate_schema, new_record, RELATIONS
from src.paths import assets, workspace

SUPPORTED = {'.pdf', '.xlsx', '.csv', '.md', '.txt'}
IDENTITY_FIELDS = ('document_id', 'revision_id', 'source_path', 'sha256', 'size_bytes', 'extension')

class Rejected(ValueError):
    """Fixed credential/content-safe rejection codes."""

def io_path(path):
    """Extended Windows paths for package IO, without changing public path values."""
    path = Path(path).absolute()
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        value = str(path)
        path = Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)
    return path

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

def read_file(directory, name):
    require(isinstance(name, str) and Path(name).name == name and name not in ('', '.', '..')
            and '/' not in name and '\\' not in name and ':' not in name, 'unsafe_package_path')
    path = io_path(directory / name)
    reject_links(path)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise Rejected('package_file_missing') from None

def write_package(parent, package_id, files, *, before_finalize=None, staging_parent=None):
    """A staged directory becomes visible only after every file is complete."""
    require(safe_relative(package_id) and '/' not in package_id, 'unsafe_package_path')
    public_target = parent / package_id
    parent = io_path(parent)
    reject_links(parent)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / package_id
    require(not target.exists(), 'package_exists')
    staging_parent = io_path(staging_parent) if staging_parent is not None else parent
    reject_links(staging_parent)
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = staging_parent / ('.staging-' + str(uuid4()))
    staging.mkdir()
    for name, data in files.items():
        require(safe_relative(name) and '/' not in name, 'unsafe_package_path')
        with (staging / name).open('xb') as stream:
            stream.write(data)
    # Failure leaves an explicitly incomplete staging directory, never a valid package.
    if before_finalize is not None:
        before_finalize()
    staging.rename(target)
    return public_target

def report(root, operation, results, *, as_json=False):
    counts = {s: sum(r['status'] == s for r in results) for s in ('processed', 'skipped', 'rejected', 'failed')}
    incomplete = sum(r.get('code') in ('incomplete_package', 'package_file_missing') for r in results)
    if as_json:
        print(json.dumps({'operation': operation, 'counts': counts, 'incomplete_count': incomplete,
                          'results': results}, ensure_ascii=True))
    else:
        print(' '.join(f'{key}={value}' for key, value in counts.items()) + f' incomplete={incomplete}')
        for result in results:
            print(json.dumps(result, ensure_ascii=True))
    return 2 if counts['failed'] else 1 if counts['rejected'] else 0

def validate_extraction(extraction):
    require(isinstance(extraction, dict) and set(extraction) == {
        'schema_version', 'status', 'configuration', 'parser_metadata', 'segments', 'warnings'}, 'extraction_fields')
    require(type(extraction['schema_version']) is int and extraction['schema_version'] == 2
            and extraction['status'] == 'complete', 'incomplete_extraction')
    require(isinstance(extraction['configuration'], dict) and isinstance(extraction['parser_metadata'], dict), 'extraction_configuration')
    require(isinstance(extraction['segments'], list) and bool(extraction['segments']), 'extraction_segments')
    for segment in extraction['segments']:
        require(isinstance(segment, dict) and set(segment) == {'segment_id', 'raw', 'text', 'location', 'extraction_method', 'details'}, 'segment_fields')
        require(isinstance(segment['segment_id'], str) and bool(segment['segment_id'])
                and isinstance(segment['raw'], str) and isinstance(segment['text'], str) and bool(segment['text'].strip())
                and isinstance(segment['extraction_method'], str)
                and segment['extraction_method'] in ('native', 'ocr') and isinstance(segment['details'], dict), 'segment_content')
        location = segment['location']
        require(isinstance(location, dict) and isinstance(location.get('kind'), str)
                and location['kind'] in ('pdf', 'text', 'csv', 'xlsx'), 'segment_location')
        keys = ('page_start', 'page_end') if location['kind'] == 'pdf' else ('line_start', 'line_end') if location['kind'] in ('text', 'csv') else ('row', 'column_start', 'column_end', 'header_row')
        require(set(location) == {'kind', *keys, *(['sheet'] if location['kind'] == 'xlsx' else [])}, 'segment_location_fields')
        require(all(type(location.get(key)) is int and location[key] > 0 for key in keys), 'segment_location_range')
        if location['kind'] == 'pdf':
            require(location['page_start'] <= location['page_end'], 'segment_location_range')
        elif location['kind'] in ('text', 'csv'):
            require(location['line_start'] <= location['line_end'], 'segment_location_range')
        else:
            require(isinstance(location.get('sheet'), str) and bool(location['sheet'].strip())
                    and location['column_start'] <= location['column_end'], 'segment_location_range')
    require(isinstance(extraction['warnings'], list), 'warnings_type')
    ids = set()
    for warning in extraction['warnings']:
        require(isinstance(warning, dict) and set(warning) == {'warning_id', 'code', 'location'}
                and isinstance(warning['warning_id'], str) and bool(warning['warning_id'].strip())
                and isinstance(warning['code'], str) and bool(warning['code'].strip())
                and isinstance(warning['location'], dict), 'warning_fields')
        require(warning['warning_id'] not in ids, 'duplicate_warning')
        ids.add(warning['warning_id'])

def validate_chunks(extraction, draft, *, allow_unresolved=False):
    validate_extraction(extraction)
    require(isinstance(draft, dict) and set(draft) == {
        'schema_version', 'chunks', 'excluded', 'warning_decisions'}, 'draft_fields')
    require(type(draft['schema_version']) is int and draft['schema_version'] == 2, 'draft_version')
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
    output = []
    for item in draft['chunks']:
        require(isinstance(item, dict) and {'content', 'source_refs'} <= set(item)
                and set(item) <= {'content', 'source_refs', 'notes'}, 'chunk_fields')
        require(isinstance(item['content'], str) and bool(item['content'].strip()), 'empty_content')
        require(isinstance(item.get('notes', ''), str), 'chunk_notes_type')
        require(isinstance(item['source_refs'], list) and bool(item['source_refs']), 'empty_references')
        spans = [reference(ref) for ref in item['source_refs']]
        output.append({'chunk_index': len(output), 'content': item['content'],
            'source_location': {'spans': spans},
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


def validate_metadata(record, *, eligible=False):
    require(isinstance(record, dict), 'metadata_invalid')
    candidate = dict(record)
    relations = candidate.get('relations')
    require(isinstance(relations, list), 'metadata_relations')
    candidate['relations'] = []
    require(not validate_schema({'schema_version': 1, 'documents': [candidate]}), 'metadata_invalid')
    for relation in relations:
        require(isinstance(relation, dict) and set(relation) == {'type', 'revision_id'}
                and isinstance(relation['type'], str) and relation['type'] in RELATIONS, 'metadata_relation')
        uuid(relation['revision_id'])
        require(relation['revision_id'] != record['revision_id'], 'self_relation')
    if eligible:
        require(record['indexing_decision'] == 'include' and record['lifecycle_status'] == 'active',
                'source_ineligible')
    return record


def package_directory(root, directory):
    directory = io_path(directory)
    reject_links(directory)
    base = io_path(workspace(root))
    require(directory.resolve().is_relative_to(base.resolve()), 'package_outside_workspace')
    relative = directory.resolve().relative_to(base.resolve())
    require(len(relative.parts) != 2, 'legacy_layout_convert_explicitly')
    require(len(relative.parts) == 1 and not any(p.startswith('.') for p in relative.parts), 'package_location')
    for part in relative.parts:
        uuid(part)
    return directory


def metadata(directory):
    directory = io_path(directory)
    from src.ingestion.package_storage import reading
    with reading(directory):
        record = validate_metadata(decode(read_file(directory, 'metadata.json')))
    require(directory.name == record['document_id'], 'metadata_identity_changed')
    return record


def package_paths(root):
    from src.ingestion.package_storage import lock
    with lock(root, 'workspace'):
        return _package_paths(root)


def _package_paths(root):
    from src.ingestion.package_storage import workspace_available
    workspace_available(root)
    base = io_path(workspace(root))
    reject_links(base)
    result = []
    if not base.exists():
        return result
    known = set()
    for document in sorted(base.iterdir()):
        if document.name in ('review', 'approved') or document.name.startswith('.'):
            continue
        reject_links(document)
        uuid(document.name)
        require(document.is_dir(), 'unexpected_package_entry')
        if not (document / 'metadata.json').exists():
            from src.ingestion.package_storage import available
            available(root, document.name)
            if any(child.is_dir() and not child.name.startswith('.') for child in document.iterdir()):
                raise Rejected('legacy_layout_convert_explicitly')
            raise Rejected('package_file_missing')
        record = metadata(document)
        key = record['source_path'].casefold()
        require(key not in known, 'duplicate_source_package')
        known.add(key)
        result.append(workspace(root) / document.name)
    return result


def snapshot_files(directory):
    from src.ingestion.package_storage import reading
    with reading(directory):
        return _snapshot_files(directory)


def _snapshot_files(directory):
    directory = io_path(directory)
    reject_links(directory)
    result = {}
    for path in sorted(directory.iterdir()):
        reject_links(path)
        require(path.is_file(), 'unexpected_package_entry')
        result[path.name] = read_file(directory, path.name)
    return result


def load_package(root, directory, *, eligible=False, allow_unresolved=False):
    directory = package_directory(root, directory)
    package_paths(root)
    files = snapshot_files(directory)
    require('metadata.json' in files, 'package_file_missing')
    record = validate_metadata(decode(files['metadata.json']), eligible=eligible)
    require(directory.name == record['document_id'], 'metadata_identity_changed')
    require(record['extension'] in SUPPORTED, 'unsupported_format')
    source_file = 'source' + record['extension']
    require(source_file in files, 'package_file_missing')
    require(sha(files[source_file]) == record['sha256'] and len(files[source_file]) == record['size_bytes'], 'snapshot_mismatch')
    require({'extraction.json', 'chunks.json'} <= files.keys(), 'incomplete_package')
    require(set(files) == {'metadata.json', 'extraction.json', 'chunks.json', source_file}, 'package_files')
    extraction, draft = decode(files['extraction.json']), decode(files['chunks.json'])
    chunks = validate_chunks(extraction, draft, allow_unresolved=allow_unresolved)
    require(all(s['location']['kind'] == {'.pdf': 'pdf', '.xlsx': 'xlsx', '.csv': 'csv', '.txt': 'text', '.md': 'text'}[record['extension']]
                for s in extraction['segments']), 'source_location_kind')
    payload = {'identity': {k: record[k] for k in IDENTITY_FIELDS}, 'chunks': chunks,
               'excluded': draft['excluded'], 'warning_decisions': draft['warning_decisions']}
    return record, payload, files


def files_hash(files):
    return canonical_hash({name: sha(data) for name, data in files.items()})


def source_for(root, record):
    validate_metadata(record)
    source = assets(root) / record['source_path']
    reject_links(source)
    require(source.resolve().is_relative_to(assets(root).resolve()), 'source_outside_assets')
    require(file_fingerprint(source) == {k: record[k] for k in ('sha256', 'size_bytes', 'extension')}, 'source_changed')
    return source


def pending(root, record):
    validate_metadata(record)
    source = source_for(root, record)
    data = source.read_bytes()
    require(sha(data) == record['sha256'] and len(data) == record['size_bytes'], 'source_changed')
    files = {'metadata.json': encoded(record), 'source' + record['extension']: data}
    source_for(root, record)
    from src.ingestion.package_storage import lock, available
    with lock(root, record['document_id']):
        available(root, record['document_id'])
        return write_package(workspace(root), record['document_id'], files,
                             before_finalize=lambda: source_for(root, record),
                             staging_parent=root / '.tmp/package-staging')


def initialize(root, source=None):
    from src.ingestion.package_storage import lock
    with lock(root, 'workspace'):
        return _initialize(root, source)


def _initialize(root, source=None):
    known = {}
    for path in _package_paths(root):
        record = metadata(path)
        known.setdefault(record['source_path'].casefold(), []).append(record)
    if source is None:
        incoming = inventory(assets(root))
    else:
        require(safe_relative(source), 'unsafe_source')
        incoming = {source: file_fingerprint(assets(root) / source)}
    require(len({name.casefold() for name in incoming}) == len(incoming), 'duplicate_source_path')
    # Existing legacy identities require explicit conversion instead of reinitialization.
    legacy = {}
    for kind in ('review', 'approved'):
        base = io_path(workspace(root) / kind)
        reject_links(base)
        if base.exists():
            for revision in base.iterdir():
                reject_links(revision)
                if not revision.is_dir() or revision.name.startswith('.'):
                    continue
                for package in revision.iterdir():
                    reject_links(package)
                    if package.is_dir() and not package.name.startswith('.'):
                        record = decode(read_file(package, 'metadata.json'))
                        legacy.setdefault(record['source_path'].casefold(), set()).add(record['document_id'])
    for name, fingerprint in incoming.items():
        candidates = known.get(name.casefold(), [])
        ids = {r['document_id'] for r in candidates} | legacy.get(name.casefold(), set())
        require(len(ids) <= 1, 'source_identity_conflict')
        require(candidates or not ids, 'legacy_source_convert_explicitly')
        require(not candidates or any(all(r[k] == v for k, v in fingerprint.items()) for r in candidates),
                'source_changed_explicit_revision_required')
    return [pending(root, new_record(name, fp)) for name, fp in incoming.items() if name.casefold() not in known]


def revise(root, directory, source, *, supersedes_current=False):
    from src.ingestion.package_storage import lock, available, replace
    require(supersedes_current, 'supersedes_current_required')
    directory = package_directory(root, directory)
    with lock(root, 'workspace'):
        _package_paths(root)  # Reject ambiguous workspace identities before changing anything.
        with lock(root, directory.name):
            available(root, directory.name)
            original = _snapshot_files(directory)
            record = dict(validate_metadata(decode(original['metadata.json'])))
            require(record['document_id'] == directory.name, 'metadata_identity_changed')
            require(safe_relative(source), 'unsafe_source')
            require(source.casefold() == record['source_path'].casefold(), 'source_path_change_unsupported')
            fingerprint = file_fingerprint(assets(root) / source)
            require(fingerprint['sha256'] != record['sha256'], 'source_unchanged')
            ancestors = {r['revision_id'] for r in record['relations'] if r['type'] == 'supersedes'}
            ancestors.add(record['revision_id'])
            record.update(fingerprint)
            record.update(revision_id=str(uuid4()), indexing_decision='hold',
                          lifecycle_status='unknown', evidence_notes=[],
                          relations=[{'type': 'supersedes', 'revision_id': r} for r in sorted(ancestors)])
            snapshot = source_for(root, record).read_bytes()
            require(sha(snapshot) == record['sha256'] and len(snapshot) == record['size_bytes'], 'source_changed')
            files = {'metadata.json': encoded(record), 'source' + record['extension']: snapshot}

            def unchanged():
                require(_snapshot_files(directory) == original, 'package_changed')
                source_for(root, record)

            return replace(root, directory.name, files, before_finalize=unchanged)
