"""Local source inventory and catalog validation. No extraction or database access."""

import hashlib
import json
import os
import re
import stat
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4


KNOWN_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xlsx'}
RELATIONS = {'duplicate_of', 'supplements', 'supersedes', 'form_for'}
NULLABLE_TEXT = {'title', 'category', 'document_type', 'owner', 'applicability',
                 'version_label'}
STATES = {
    'lifecycle_status': {'unknown', 'active', 'superseded', 'withdrawn', 'expired'},
    'review_status': {'pending', 'reviewed'},
    'indexing_decision': {'hold', 'include', 'exclude'},
}
FIELDS = (NULLABLE_TEXT | set(STATES) | {
    'document_id', 'revision_id', 'source_path', 'sha256', 'size_bytes',
    'extension', 'effective_from', 'effective_to', 'access_groups',
    'review_notes', 'relations',
})


class CatalogError(ValueError):
    """A safe, operational error message for the CLI."""


def reject_links(path: Path) -> None:
    """Inspect every existing component without resolving away links/junctions."""
    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
        ):
            raise CatalogError('Link or reparse point rejected.')


def safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or '\\' in value:
        return False
    parts = value.split('/')
    return (not PureWindowsPath(value).drive and not value.startswith('/')
            and all(part not in {'', '.', '..'} and not part.endswith((' ', '.'))
                    and not re.search(r'[<>:"|?*\x00-\x1f]', part) for part in parts))


def file_fingerprint(path: Path) -> dict:
    reject_links(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise CatalogError('Source changed while being read; retry after updates stop.')
    return {'sha256': digest, 'size_bytes': after.st_size,
            'extension': path.suffix.lower()}


def inventory(root: Path) -> dict:
    reject_links(root)
    if not root.is_dir():
        raise CatalogError('Assets directory is unavailable.')
    result = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            reject_links(path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                if path.name.lower() == 'thumbs.db':
                    continue
                relative = path.relative_to(root).as_posix()
                if not safe_relative(relative):
                    raise CatalogError('Source filename is not portable; review required.')
                result[relative] = file_fingerprint(path)
            else:
                raise CatalogError('Non-regular source entry rejected.')
    return dict(sorted(result.items()))


def new_record(path: str, fingerprint: dict) -> dict:
    source = PurePosixPath(path)
    return {
        'document_id': str(uuid4()), 'revision_id': str(uuid4()),
        'source_path': path, **fingerprint,
        'title': source.stem, 'category': source.parts[0] if len(source.parts) > 1 else None,
        'document_type': None, 'owner': None, 'applicability': None,
        'effective_from': None, 'effective_to': None, 'version_label': None,
        'lifecycle_status': 'unknown', 'access_groups': [],
        'review_status': 'pending', 'review_notes': [],
        'indexing_decision': 'hold', 'relations': [],
    }


def new_catalog(files: dict) -> dict:
    return {'schema_version': 1,
            'documents': [new_record(path, value) for path, value in files.items()]}


def atomic_write(path: Path, text: str, *, create_only: bool = False) -> None:
    """Publish a complete file; init uses a hard link for atomic no-clobber."""
    reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_links(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def load_catalog(path: Path) -> object:
    reject_links(path)
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CatalogError('Catalog is not valid UTF-8 JSON.') from exc


def valid_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def text_list(value: object) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, str) and item.strip() for item in value))


def validate_schema(catalog: object) -> list[str]:
    errors = []
    if (not isinstance(catalog, dict) or set(catalog) != {'schema_version', 'documents'}
            or type(catalog.get('schema_version')) is not int
            or catalog['schema_version'] != 1 or not isinstance(catalog['documents'], list)):
        return ['Expected schema_version=1 and documents array.']
    seen = {key: set() for key in ('document_id', 'revision_id', 'source_path')}
    for index, record in enumerate(catalog['documents']):
        prefix = f'documents[{index}]'
        if not isinstance(record, dict) or set(record) != FIELDS:
            errors.append(f'{prefix}: incorrect fields.')
            continue
        for key in ('document_id', 'revision_id'):
            value = record[key]
            if not valid_uuid(value):
                errors.append(f'{prefix}: invalid {key}.')
            elif value in seen[key]:
                errors.append(f'{prefix}: duplicate {key}.')
            else:
                seen[key].add(value)
        path = record['source_path']
        if not safe_relative(path):
            errors.append(f'{prefix}: invalid source_path.')
        elif path.casefold() in seen['source_path']:
            errors.append(f'{prefix}: duplicate source_path.')
        else:
            seen['source_path'].add(path.casefold())
            if record['extension'] != PurePosixPath(path).suffix.lower():
                errors.append(f'{prefix}: extension does not match path.')
        if not isinstance(record['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', record['sha256']):
            errors.append(f'{prefix}: invalid sha256.')
        if type(record['size_bytes']) is not int or record['size_bytes'] < 0:
            errors.append(f'{prefix}: invalid size_bytes.')
        if not isinstance(record['extension'], str):
            errors.append(f'{prefix}: invalid extension.')
        for key in NULLABLE_TEXT:
            if record[key] is not None and (not isinstance(record[key], str) or not record[key].strip()):
                errors.append(f'{prefix}: invalid {key}.')
        for key, choices in STATES.items():
            if not isinstance(record[key], str) or record[key] not in choices:
                errors.append(f'{prefix}: invalid {key}.')
        for key in ('access_groups', 'review_notes'):
            if not text_list(record[key]):
                errors.append(f'{prefix}: invalid {key}.')
        dates = {}
        for key in ('effective_from', 'effective_to'):
            value = record[key]
            if value is not None:
                try:
                    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                        raise ValueError
                    dates[key] = date.fromisoformat(value)
                except ValueError:
                    errors.append(f'{prefix}: invalid {key}.')
        if len(dates) == 2 and dates['effective_from'] > dates['effective_to']:
            errors.append(f'{prefix}: reversed effective dates.')
        relations = record['relations']
        if not isinstance(relations, list):
            errors.append(f'{prefix}: invalid relations.')
        else:
            for relation in relations:
                if (not isinstance(relation, dict) or set(relation) != {'type', 'revision_id'}
                        or not isinstance(relation['type'], str) or relation['type'] not in RELATIONS
                        or not valid_uuid(relation['revision_id'])):
                    errors.append(f'{prefix}: invalid relation.')
        if record['indexing_decision'] == 'include':
            if (record['review_status'] != 'reviewed' or record['lifecycle_status'] != 'active'
                    or not record['owner'] or not record['applicability']
                    or not record['access_groups'] or not record['review_notes']):
                errors.append(f'{prefix}: include requires reviewed active source, owner, applicability, access groups and evidence notes.')
            today = date.today()
            if (dates.get('effective_from', today) > today
                    or dates.get('effective_to', today) < today):
                errors.append(f'{prefix}: include is outside effective dates.')
    if errors:
        return errors
    records = {r['revision_id']: r for r in catalog['documents']}
    graph = {key: [] for key in records}
    for index, record in enumerate(catalog['documents']):
        for relation in record['relations']:
            target_id = relation['revision_id']
            target = records.get(target_id)
            if target is None or target_id == record['revision_id']:
                errors.append(f'documents[{index}]: missing or self-referencing relation target.')
                continue
            if relation['type'] == 'duplicate_of' and record['sha256'] != target['sha256']:
                errors.append(f'documents[{index}]: duplicate_of hashes differ.')
            if relation['type'] in {'supplements', 'supersedes', 'form_for'}:
                if record['review_status'] != 'reviewed' or not record['review_notes']:
                    errors.append(f'documents[{index}]: business relation requires review and evidence notes.')
            if relation['type'] == 'supersedes':
                graph[record['revision_id']].append(target_id)
    # Kahn's algorithm avoids recursion limits on large catalogs.
    indegree = dict.fromkeys(graph, 0)
    for targets in graph.values():
        for target in targets:
            indegree[target] += 1
    ready = [key for key, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for target in graph[key]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(graph):
        errors.append('Cycle in supersedes relations.')
    return errors


def compare(catalog: dict, files: dict) -> dict:
    stored = {r['source_path']: r for r in catalog['documents']}
    added = sorted(files.keys() - stored.keys())
    missing = sorted(stored.keys() - files.keys())
    changed = sorted(path for path in files.keys() & stored.keys()
                     if any(files[path][key] != stored[path][key]
                            for key in ('sha256', 'size_bytes', 'extension')))
    groups = defaultdict(list)
    for path, fingerprint in files.items():
        groups[fingerprint['sha256']].append(path)
    duplicates = [paths for paths in groups.values() if len(paths) > 1]
    # Duplicate bytes never imply equal access rights or automatic logical identity.
    unresolved = []
    for paths in duplicates:
        members = [stored.get(path) for path in paths]
        if any(r is None or r['review_status'] != 'reviewed' or r['indexing_decision'] == 'hold'
               for r in members):
            unresolved.append(paths)
    return {
        'added': added, 'missing': missing, 'changed': changed,
        'duplicates': duplicates, 'unresolved_duplicates': unresolved,
        'possible_moves': [{'old': old, 'new': new} for old in missing for new in added
                           if stored[old]['sha256'] == files[new]['sha256']],
        'unrecognized_formats': [p for p, f in files.items() if f['extension'] not in KNOWN_EXTENSIONS],
    }


def build_report(command: str, catalog: object, files: dict) -> dict:
    errors = validate_schema(catalog)
    report = {'command': command, 'errors': errors, 'source_count': len(files),
              'catalog_count': 0, 'hold_count': 0, 'include_count': 0,
              'reviewed_count': 0, 'exit_code': 1}
    if errors:
        return report
    documents = catalog['documents']
    report.update(compare(catalog, files))
    report.update(catalog_count=len(documents),
                  hold_count=sum(r['indexing_decision'] == 'hold' for r in documents),
                  include_count=sum(r['indexing_decision'] == 'include' for r in documents),
                  reviewed_count=sum(r['review_status'] == 'reviewed' for r in documents))
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
