"""Read-only runtime inventory verification; standard library only, no model/DB IO."""
import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'artifacts/runtime-assets.json'
E5_FILES = frozenset({
    'config.json', 'model.safetensors', 'modules.json', 'README.md',
    'sentencepiece.bpe.model', 'sentence_bert_config.json', 'special_tokens_map.json',
    'tokenizer.json', 'tokenizer_config.json', '1_Pooling/config.json',
})
RERANK_FILES = frozenset({
    'config.json', 'model_quint8_avx2.onnx', 'sentencepiece.bpe.model',
    'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json',
})
ARTIFACT_FILES = {'answers': 'catalog.json', 'rerank': 'relevance-profile.json', 'semantics': 'lexicon.json',
                  'intent': 'accepted-artifact.json'}
HEX = re.compile(r'[0-9a-f]{64}')


class InventoryError(ValueError):
    """Fixed error code; never include file contents or raw exception messages."""


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def safe_path(value):
    return (isinstance(value, str) and bool(value) and not PureWindowsPath(value).drive
        and not value.startswith('/') and '\\' not in value
        and all(part not in ('', '.', '..') and not part.endswith((' ', '.'))
                and not re.search(r'[<>:"|?*\x00-\x1f]', part) for part in value.split('/')))


def checked_path(root, relative):
    if not safe_path(relative):
        raise InventoryError('invalid_path')
    path = root / relative
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InventoryError('linked_path')
    return path


def file_hash(path):
    with path.open('rb') as stream:
        before = path.stat()
        prefix = stream.read(200)
        if prefix.startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise InventoryError('lfs_pointer_not_hydrated')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise InventoryError('file_changed')
        return digest


def group_for(path, e5_root):
    if path in {f'{e5_root}/{name}' for name in E5_FILES}:
        return 'e5'
    if path in {f'models/{name}' for name in RERANK_FILES}:
        return 'reranker'
    parts = path.split('/')
    if (len(parts) == 4 and parts[0] == 'artifacts' and parts[1] in ARTIFACT_FILES
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}', parts[2])
            and parts[3] in (ARTIFACT_FILES[parts[1]], 'manifest.json')):
        return parts[1]
    return None


def load_manifest(root, relative):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InventoryError('duplicate_json_key')
            result[key] = value
        return result
    try:
        data = json.loads(checked_path(root, relative).read_bytes(), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(InventoryError('invalid_manifest')))
    except (UnicodeError, json.JSONDecodeError):
        raise InventoryError('invalid_manifest') from None
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'models', 'assets'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or not isinstance(data['models'], dict) or set(data['models']) != {'e5', 'reranker'}
            or not isinstance(data['assets'], list)):
        raise InventoryError('invalid_manifest')
    for group, model_id in [('e5', 'intfloat/multilingual-e5-small'),
                            ('reranker', 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')]:
        model = data['models'][group]
        if (not isinstance(model, dict) or set(model) != {'id', 'revision', 'root', 'files_sha256'}
                or model['id'] != model_id or not isinstance(model['revision'], str)
                or re.fullmatch(r'[0-9a-f]{40}', model['revision']) is None
                or not isinstance(model['files_sha256'], str) or not HEX.fullmatch(model['files_sha256'])):
            raise InventoryError('invalid_model_identity')
        expected = ('models/embeddings/multilingual-e5-small/' + model['revision']
                    if group == 'e5' else 'models')
        if model['root'] != expected:
            raise InventoryError('invalid_model_identity')
    e5_root = data['models']['e5']['root']
    seen = set()
    for entry in data['assets']:
        if (not isinstance(entry, dict) or set(entry) != {'path', 'size_bytes', 'sha256', 'group'}
                or not safe_path(entry['path']) or type(entry['size_bytes']) is not int
                or entry['size_bytes'] < 0 or not isinstance(entry['sha256'], str)
                or not HEX.fullmatch(entry['sha256']) or entry['group'] is None
                or group_for(entry['path'], e5_root) != entry['group']):
            raise InventoryError('invalid_asset_entry')
        if entry['path'] in seen:
            raise InventoryError('duplicate_asset')
        seen.add(entry['path'])
    required = {f'{e5_root}/{name}' for name in E5_FILES} | {f'models/{name}' for name in RERANK_FILES}
    if not required <= seen:
        raise InventoryError('incomplete_model_inventory')
    for group in ('e5', 'reranker'):
        model = data['models'][group]
        hashes = {entry['path'][len(model['root']) + 1:]: entry['sha256']
                  for entry in data['assets'] if entry['group'] == group}
        if canonical_hash(hashes) != model['files_sha256']:
            raise InventoryError('model_fingerprint_mismatch')
    for entry in data['assets']:
        if entry['group'] in ARTIFACT_FILES:
            parent = entry['path'].rsplit('/', 1)[0]
            if not {parent + '/manifest.json', parent + '/' + ARTIFACT_FILES[entry['group']]} <= seen:
                raise InventoryError('artifact_provenance_missing')
    return data


def git_files(root):
    try:
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--', 'models', 'artifacts'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
        return set(result.stdout.decode('utf-8').rstrip('\0').split('\0')) - {''}
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        raise InventoryError('git_inventory_unavailable') from None


def verify(root=ROOT, manifest=MANIFEST):
    """Return a content-safe result and exit code; root is injectable for fixtures."""
    root = Path(root).absolute()
    try:
        data = load_manifest(root, manifest)
        errors = []
        for entry in data['assets']:
            try:
                path = checked_path(root, entry['path'])
                if not path.is_file():
                    raise InventoryError('file_missing_or_nonregular')
                actual = file_hash(path)
                if path.stat().st_size != entry['size_bytes']:
                    raise InventoryError('size_mismatch')
                if actual != entry['sha256']:
                    raise InventoryError('sha256_mismatch')
            except (InventoryError, OSError) as exc:
                errors.append({'path': entry['path'], 'code': str(exc) if isinstance(exc, InventoryError)
                               else 'file_unavailable'})
        # Every file below E5 affects the runtime fingerprint, even if ignored by Git.
        e5_root = data['models']['e5']['root']
        pending = [e5_root]
        expected = {entry['path'] for entry in data['assets']}
        while pending:
            relative = pending.pop()
            try:
                directory = checked_path(root, relative)
                if not directory.is_dir():
                    continue  # Missing model files were already reported individually.
                for child in directory.iterdir():
                    name = child.relative_to(root).as_posix()
                    checked_path(root, name)
                    if child.is_dir():
                        pending.append(name)
                    elif name not in expected:
                        errors.append({'path': name, 'code': 'unexpected_e5_file'})
            except (InventoryError, OSError) as exc:
                errors.append({'path': relative, 'code': str(exc) if isinstance(exc, InventoryError)
                               else 'directory_unavailable'})
        for path in sorted(git_files(root) - expected - {MANIFEST}):
            code = 'tracked_asset_not_in_manifest' if group_for(path, e5_root) else 'tracked_path_not_allowed'
            errors.append({'path': path, 'code': code})
        return {'files': len(data['assets']), 'errors': errors}, 1 if errors else 0
    except (InventoryError, OSError) as exc:
        # Operational diagnostics deliberately exclude raw exception messages and contents.
        return {'files': 0, 'errors': [{'path': 'manifest', 'code': str(exc)
                if isinstance(exc, InventoryError) else 'inventory_unavailable'}]}, 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default=MANIFEST, help='Repository-relative inventory JSON path.')
    args = parser.parse_args(argv)
    result, code = verify(manifest=args.manifest)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
