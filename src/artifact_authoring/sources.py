"""Standard-library source layout and deterministic assembly, without runtime imports."""
import hashlib
import json
from pathlib import Path
import re
import stat


GROUPS = {'semantics': ('lexicon.json', ('concepts',)),
          'answers': ('catalog.json', ('cards', 'coverage')),
          'retrieval': ('catalog.json', ('families','systems','subtypes','operations','bindings','routes'))}
METADATA = {'semantics': {'schema_version', 'reviewed', 'review_notes', 'package_fingerprint'},
            'answers': {'schema_version', 'package_fingerprint'},
            'retrieval': {'schema_version', 'package_fingerprint'}}
ID = re.compile(r'[A-Za-z0-9._-]{1,100}')


class SourceError(ValueError):
    """Fixed content-safe error code."""


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SourceError('duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(SourceError('nonfinite_json')))
    except (UnicodeError, json.JSONDecodeError):
        raise SourceError('invalid_source_json') from None


def encode(value, *, crlf=False):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    return (text.replace('\n', '\r\n') if crlf else text).encode('utf-8')


def checked(path):
    path = Path(path).absolute()
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise SourceError('linked_source_path')
    return path


def identifier(value):
    if (not isinstance(value, str) or not ID.fullmatch(value)
            or value in ('.', '..') or value.endswith('.')
            or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
        raise SourceError('invalid_source_id')
    return value


def files(directory):
    pending, result = [checked(directory)], set()
    while pending:
        for child in pending.pop().iterdir():
            checked(child)
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                result.add(child)
            else:
                raise SourceError('nonregular_source_file')
    return result


def read_group(root, group):
    directory = checked(Path(root) / 'artifacts' / group / 'source')
    observed = {}

    def read(path):
        path = checked(path)
        if not path.is_file():
            raise SourceError('source_file_missing_or_nonregular')
        raw = path.read_bytes()
        observed[path] = raw
        return decode(raw)

    index = read(directory / 'index.json')
    collections = GROUPS[group][1]
    if (not isinstance(index, dict) or set(index) != {'schema_version', 'metadata', *collections}
            or type(index['schema_version']) is not int or index['schema_version'] != 1
            or not isinstance(index['metadata'], dict) or set(index['metadata']) != METADATA[group]):
        raise SourceError('invalid_source_index')
    result = dict(index['metadata'])
    seen_paths = set()
    for collection in collections:
        refs = index[collection]
        if not isinstance(refs, list):
            raise SourceError('invalid_source_references')
        rows, ids = [], set()
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith(collection + '/') or not ref.endswith('.json'):
                raise SourceError('invalid_source_reference')
            name = identifier(ref[len(collection) + 1:-5])
            if ref.casefold() in seen_paths:
                raise SourceError('duplicate_source_reference')
            seen_paths.add(ref.casefold())
            value = read(directory / ref)
            values = value if collection == 'coverage' else [value]
            if not isinstance(values, list) or (collection == 'coverage' and not values):
                raise SourceError('invalid_source_record')
            for row in values:
                if not isinstance(row, dict):
                    raise SourceError('invalid_source_record')
                key = row.get('document_id') if collection == 'coverage' else row.get('id')
                if key != name:
                    raise SourceError('source_id_filename_mismatch')
                identity = (key, row.get('chunk_index')) if collection == 'coverage' else key
                if collection == 'coverage' and (type(identity[1]) is not int or identity[1] < 0):
                    raise SourceError('invalid_coverage_index')
                if identity in ids:
                    raise SourceError('duplicate_source_id')
                ids.add(identity)
                rows.append(row)
        result[collection] = rows
    if files(directory) != set(observed):
        raise SourceError('undeclared_source_file')
    return result, observed


def recheck(observed):
    for path, raw in observed.items():
        if checked(path).read_bytes() != raw:
            raise SourceError('source_changed')
    directories = {p.parent for p in observed if p.name == 'index.json'}
    if set().union(*(files(d) for d in directories)) != set(observed):
        raise SourceError('source_changed')


def active_groups(root):
    return [group for group in GROUPS if group != 'retrieval'
            or (Path(root) / 'artifacts' / group / 'source').exists()]


def assemble(root):
    outputs, observed = [], {}
    for group in active_groups(root):
        data, captured = read_group(root, group)
        observed.update(captured)
        if group != 'semantics':
            collections = GROUPS[group][1]
            data = {**{key: data[key] for key in data if key not in collections},
                    'lexicon_sha256': hashlib.sha256(outputs[0]).hexdigest(),
                    **{key: data[key] for key in collections}}
        outputs.append(encode(data, crlf=group=='answers'))
    return tuple(outputs), observed


def declared_sources(root):
    """Inventory exception only for exact, validated source declarations."""
    paths = set()
    for group in GROUPS:
        source = checked(Path(root) / 'artifacts' / group / 'source')
        if source.exists():
            _, observed = read_group(root, group)
            recheck(observed)
            paths.update(path.relative_to(Path(root).absolute()).as_posix() for path in observed)
    return paths
