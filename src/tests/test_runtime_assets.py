"""Asset inventory and real Git policy checks; no model downloads or database IO."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import verify_runtime_assets as assets

REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
E5 = 'models/embeddings/multilingual-e5-small/' + REVISION


def git(root, *args, check=True, input=None):
    return subprocess.run(['git', '-C', str(root), *args], input=input, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def write_manifest(root, data):
    (root / assets.MANIFEST).write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def inventory(tmp_path):
    git(tmp_path, 'init')
    entries, models = [], {}
    for group, directory, names, model_id in [
        ('e5', E5, assets.E5_FILES, 'intfloat/multilingual-e5-small'),
        ('reranker', 'models', assets.RERANK_FILES, 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'),
    ]:
        hashes = {}
        for name in sorted(names):
            path = tmp_path / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = ('fictional ' + group + ' ' + name + '\n').encode()
            path.write_bytes(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
            entries.append({'path': directory + '/' + name, 'group': group,
                            'size_bytes': len(raw), 'sha256': hashes[name]})
        models[group] = {'id': model_id, 'revision': REVISION, 'root': directory,
                         'files_sha256': assets.canonical_hash(hashes)}
    data = {'schema_version': 1, 'models': models, 'assets': entries}
    (tmp_path / 'artifacts').mkdir()
    write_manifest(tmp_path, data)
    return tmp_path, data


def error_codes(result):
    return {entry['code'] for entry in result['errors']}


def test_valid_inventory_is_read_only_and_uses_no_optional_dependencies(inventory):
    root, _ = inventory
    before = {p.relative_to(root): p.read_bytes() for directory in ('models', 'artifacts')
              for p in (root / directory).rglob('*') if p.is_file()}
    result, code = assets.verify(root)
    assert code == 0 and result == {'files': 16, 'errors': []}
    assert all((root / name).read_bytes() == raw for name, raw in before.items())
    script = "import sys; import scripts.verify_runtime_assets; print(any(n in sys.modules for n in ('dotenv', 'numpy', 'torch', 'sqlalchemy', 'transformers', 'src.retrieval')))"
    process = subprocess.run([sys.executable, '-I', '-S', '-c',
        'import sys; sys.path.insert(0, ' + repr(str(assets.ROOT)) + '); ' + script],
        check=True, capture_output=True, text=True)
    assert process.stdout.strip() == 'False'


@pytest.mark.parametrize('change,expected', [
    ('missing', 'file_missing_or_nonregular'), ('size', 'size_mismatch'),
    ('hash', 'sha256_mismatch'), ('pointer', 'lfs_pointer_not_hydrated'),
    ('extra', 'unexpected_e5_file'),
])
def test_file_failures(inventory, change, expected):
    root, data = inventory
    entry = data['assets'][0]
    path = root / entry['path']
    if change == 'missing':
        path.unlink()
    elif change == 'size':
        path.write_bytes(b'changed size')
    elif change == 'hash':
        path.write_bytes(b'x' * entry['size_bytes'])
    elif change == 'pointer':
        path.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:fictional\nsize 9000\n')
    else:
        (root / E5 / 'receipt.json').write_text('private output')
    result, code = assets.verify(root)
    assert code == 1 and expected in error_codes(result)
    assert 'private output' not in json.dumps(result)


@pytest.mark.parametrize('change,expected', [
    ('duplicate', 'duplicate_asset'), ('incomplete', 'incomplete_model_inventory'),
    ('identity', 'model_fingerprint_mismatch'), ('invalid_group', 'invalid_asset_entry'),
    ('bool_size', 'invalid_asset_entry'), ('schema', 'invalid_manifest'),
])
def test_invalid_manifest(inventory, change, expected):
    root, data = inventory
    if change == 'duplicate':
        data['assets'].append(copy.deepcopy(data['assets'][0]))
    elif change == 'incomplete':
        data['assets'].pop()
    elif change == 'identity':
        data['models']['e5']['files_sha256'] = '0' * 64
    elif change == 'invalid_group':
        data['assets'][0]['group'] = 'unknown'
    elif change == 'bool_size':
        data['assets'][0]['size_bytes'] = True
    else:
        data['schema_version'] = True
    write_manifest(root, data)
    result, code = assets.verify(root)
    assert code == 2 and error_codes(result) == {expected}


@pytest.mark.parametrize('path', ['/etc/file', 'C:/secret', '../secret', 'models/../secret',
                                  'models\\secret', 'models//file', 'models/name:stream'])
def test_reject_paths_before_reading(inventory, path):
    root, data = inventory
    data['assets'][0]['path'] = path
    write_manifest(root, data)
    result, code = assets.verify(root)
    assert code == 2 and error_codes(result) == {'invalid_asset_entry'}


def test_link_is_rejected_without_reading_target(inventory, monkeypatch):
    root, data = inventory
    original = Path.lstat
    target = root / data['assets'][0]['path']
    class Reparse:
        st_mode = 0
        st_file_attributes = 0x400
    def lstat(path, *args, **kwargs):
        return Reparse() if path == target else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    result, code = assets.verify(root)
    assert code == 1 and 'linked_path' in error_codes(result)


def test_bad_json_and_duplicate_keys_are_content_safe(inventory):
    root, _ = inventory
    for raw, expected in [('PRIVATE_INVALID_JSON', 'invalid_manifest'),
                          ('{"schema_version":1,"schema_version":2}', 'duplicate_json_key')]:
        (root / assets.MANIFEST).write_text(raw)
        result, code = assets.verify(root)
        assert code == 2 and error_codes(result) == {expected}
        assert 'PRIVATE_INVALID_JSON' not in json.dumps(result)


@pytest.mark.parametrize('group', ['answers', 'retrieval'])
def test_tracked_policy_and_optional_artifact_pair(inventory, group):
    root, data = inventory
    # An ignored root-level cache is harmless until somebody force-adds it.
    (root / '.gitignore').write_bytes((assets.ROOT / '.gitignore').read_bytes())
    cache = root / 'models/.cache/download.metadata'
    cache.parent.mkdir()
    cache.write_text('fictional cache')
    assert assets.verify(root)[1] == 0
    git(root, 'add', '-f', 'models/.cache/download.metadata')
    assert 'tracked_path_not_allowed' in error_codes(assets.verify(root)[0])
    git(root, 'rm', '--cached', 'models/.cache/download.metadata')
    parent = root / 'artifacts' / group
    parent.mkdir(parents=True)
    for name in ('catalog.json', 'manifest.json'):
        path = parent / name
        raw = b'{"fictional": true}\n'
        path.write_bytes(raw)
        git(root, 'add', path.relative_to(root).as_posix())
        assert 'tracked_asset_not_in_manifest' in error_codes(assets.verify(root)[0])
        data['assets'].append({'path': path.relative_to(root).as_posix(), 'size_bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest(), 'group': group})
    write_manifest(root, data)
    assert assets.verify(root)[1] == 0  # Integrity only, not acceptance validation.
    data['assets'].pop()
    write_manifest(root, data)
    assert error_codes(assets.verify(root)[0]) == {'artifact_provenance_missing'}


def test_git_failure_is_an_operational_error(inventory):
    root, _ = inventory
    # Point Git elsewhere without touching this fixture's Git directory.
    from unittest.mock import patch
    with patch.object(assets, 'git_files', side_effect=assets.InventoryError('git_inventory_unavailable')):
        result, code = assets.verify(root)
    assert code == 2 and error_codes(result) == {'git_inventory_unavailable'}


def test_real_allowlist_attributes_and_lfs_checkout(inventory):
    root, _ = inventory
    for name in ('.gitignore', '.gitattributes'):
        (root / name).write_bytes((assets.ROOT / name).read_bytes())
    paths = [E5 + '/' + name for name in assets.E5_FILES] + ['models/' + name for name in assets.RERANK_FILES]
    paths += ['artifacts/answers/catalog.json', 'artifacts/answers/manifest.json', assets.MANIFEST, 'artifacts/rerank/relevance-profile.json',
              'artifacts/rerank/manifest.json', 'artifacts/semantics/lexicon.json',
              'artifacts/semantics/manifest.json', 'artifacts/intent/accepted-artifact.json',
              'artifacts/intent/manifest.json']
    paths += ['artifacts/semantics/source/index.json', 'artifacts/semantics/source/concepts/fictional.json',
              'artifacts/answers/source/index.json', 'artifacts/answers/source/cards/fictional.json',
              'artifacts/answers/source/coverage/fictional.json']
    paths += ['artifacts/retrieval/catalog.json', 'artifacts/retrieval/manifest.json',
              'artifacts/retrieval/source/index.json', 'artifacts/questions/README.md',
              'artifacts/questions/index.json']
    paths += ['artifacts/retrieval/source/' + collection + '/fictional.json'
              for collection in ('families', 'systems', 'subtypes', 'operations', 'bindings', 'routes')]
    paths += ['artifacts/questions/' + group + '/fictional.json' for group in assets.QUESTION_GROUPS]
    for path in paths:
        result = git(root, 'check-ignore', '--no-index', path, check=False)
        assert result.returncode == 1, (path, result.stdout, result.stderr)
        values = git(root, 'check-attr', '-z', 'filter', 'text', 'ident', 'working-tree-encoding', '--', path).stdout.decode().split('\0')
        attrs = {values[i + 1]: values[i + 2] for i in range(0, len(values) - 1, 3)}
        assert attrs['text'] == attrs['ident'] == attrs['working-tree-encoding'] == 'unset'
        large = path.startswith('models/') and (path.endswith(('.safetensors', '.onnx', '.model', '/tokenizer.json')))
        assert attrs['filter'] == ('lfs' if large else 'unset')
    for path in ['.env', '.tmp/report.json', 'env/config', 'models/.cache/tokenizer.json.metadata',
                 'models/other.onnx', E5 + '/receipt.json', E5 + '/1_Pooling/extra.json',
                 'artifacts/storage-preparation-20261114/verification.json',
                 'artifacts/rerank/report.json', 'artifacts/intent/candidate.json',
                 'artifacts/rerank/nested/relevance-profile.json',
                 'artifacts/answers/source/report.json', 'artifacts/semantics/source/concepts/private.txt',
                 'artifacts/answers/v1/catalog.json', 'artifacts/semantics/v2/lexicon.json',
                 'artifacts/rerank/v1/manifest.json', 'artifacts/intent/v1/accepted-artifact.json']:
        assert git(root, 'check-ignore', '--no-index', path, check=False).returncode == 0, path
    for path in ['artifacts/questions/report.json', 'artifacts/questions/report.log',
                 'artifacts/questions/policy/leave.json.bak', 'artifacts/questions/policy/private.txt',
                 'artifacts/questions/policy/nested/leave.json', 'artifacts/questions/unknown/case.json',
                 'artifacts/questions/v1/index.json', 'artifacts/retrieval/report.json',
                 'artifacts/retrieval/catalog.json.bak', 'artifacts/retrieval/source/report.json',
                 'artifacts/retrieval/source/routes/private.txt',
                 'artifacts/retrieval/source/routes/nested/route.json',
                 'artifacts/retrieval/source/unknown/route.json', 'artifacts/retrieval/v1/catalog.json']:
        assert git(root, 'check-ignore', '--no-index', path, check=False).returncode == 0, path
    # Stage and re-checkout tiny fictional files through real LFS and autocrlf.
    git(root, 'lfs', 'install', '--local', '--skip-smudge')
    git(root, 'config', 'core.autocrlf', 'true')
    before = {path: (root / path).read_bytes() for path in paths[:16]}
    git(root, 'add', '.gitattributes', '.gitignore', 'models', assets.MANIFEST)
    pointer = git(root, 'show', ':models/model_quint8_avx2.onnx').stdout
    assert pointer.startswith(b'version https://git-lfs.github.com/spec/v1\n')
    git(root, '-c', 'user.name=Asset fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=.git/no-hooks',
        'commit', '-m', 'Fictional runtime assets')
    for path in before:
        (root / path).unlink()
    git(root, 'checkout-index', '-a', '-f')
    assert 'lfs_pointer_not_hydrated' in error_codes(assets.verify(root)[0])
    git(root, 'lfs', 'checkout')
    assert {path: (root / path).read_bytes() for path in before} == before
    assert assets.verify(root)[1] == 0
    clone = root.parent / 'asset-clone'
    git(root, 'clone', '--no-checkout', '--no-hardlinks', '.', '../asset-clone')
    git(clone, 'lfs', 'install', '--local', '--skip-smudge')
    git(clone, 'config', 'core.autocrlf', 'true')
    git(clone, 'checkout', '-f', 'HEAD')
    assert 'lfs_pointer_not_hydrated' in error_codes(assets.verify(clone)[0])
    git(clone, 'lfs', 'pull')  # Local fixture remote only; no external network.
    assert {path: (clone / path).read_bytes() for path in before} == before
    assert assets.verify(clone)[1] == 0


def test_committed_inventory_matches_runtime_pins_without_loading_models():
    from src.retrieval.reranking import ARTIFACTS, MODEL_ID, MODEL_REVISION
    data = assets.load_manifest(assets.ROOT, assets.MANIFEST)
    model = data['models']['reranker']
    assert (model['id'], model['revision']) == (MODEL_ID, MODEL_REVISION)
    assert {e['path'].removeprefix('models/'): e['sha256'] for e in data['assets']
            if e['group'] == 'reranker'} == ARTIFACTS


@pytest.mark.parametrize('group,collection', [('semantics', 'concepts'), ('answers', 'cards'),
                                            ('retrieval', 'routes')])
def test_exact_authoring_declarations_are_separate_from_runtime_inventory(inventory, group, collection):
    root, data = inventory
    (root / '.gitignore').write_bytes((assets.ROOT / '.gitignore').read_bytes())
    directory = root / 'artifacts' / group / 'source'
    (directory / collection).mkdir(parents=True)
    metadata = dict(schema_version=1, package_fingerprint='fictional')
    if group == 'semantics':
        metadata.update(reviewed=True, review_notes='Fictional review')
    index = dict(schema_version=1, metadata=metadata, **{collection: [collection + '/fictional.json']})
    if group == 'answers':
        index['coverage'] = []
    if group == 'retrieval':
        index.update({key: [] for key in ('families', 'systems', 'subtypes', 'operations', 'bindings')})
    (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    record = directory / collection / 'fictional.json'
    record.write_bytes(b'{"id":"fictional"}')
    git(root, 'add', str(directory.relative_to(root)))
    result, code = assets.verify(root)
    assert code == 0 and result['files'] == len(data['assets'])
    (directory / 'extra.json').write_bytes(b'{}')
    assert 'invalid_artifact_sources' in error_codes(assets.verify(root)[0])
    (directory / 'extra.json').unlink()
    record.write_bytes(b'{"id":"different"}')
    assert 'invalid_artifact_sources' in error_codes(assets.verify(root)[0])


@pytest.mark.parametrize('group,name', list(assets.ARTIFACT_FILES.items()))
def test_versioned_inventory_and_force_added_paths_rejected(inventory, group, name):
    root, data = inventory
    relative = f'artifacts/{group}/v1/{name}'
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{}')
    git(root, 'add', '-f', relative)
    assert 'tracked_path_not_allowed' in error_codes(assets.verify(root)[0])
    data['assets'].append({'path': relative, 'group': group,
        'sha256': hashlib.sha256(b'{}').hexdigest(), 'size_bytes': 2})
    write_manifest(root, data)
    assert 'invalid_asset_entry' in error_codes(assets.verify(root)[0])


@pytest.fixture
def question_tree(inventory):
    root, _ = inventory
    (root / '.gitignore').write_bytes((assets.ROOT / '.gitignore').read_bytes())
    directory = root / assets.QUESTIONS
    (directory / 'policy').mkdir(parents=True)
    (directory / 'README.md').write_text('Fictional question sources.\n', encoding='utf-8')
    index = {'schema_version': 1, 'files': [{'path': 'policy/fictional.json'}]}
    (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    # Content is deliberately not a question schema: this checker enforces paths only.
    (directory / 'policy/fictional.json').write_text('{}\n', encoding='utf-8')
    git(root, 'add', assets.QUESTIONS)
    return root, directory, index


def test_indexed_questions_are_read_only_nonruntime_sources(question_tree):
    root, directory, _ = question_tree
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    manifest = (root / assets.MANIFEST).read_bytes()
    assert assets.verify(root) == ({'files': 16, 'errors': []}, 0)
    assert assets.question_sources(root, assets.git_files(root)) == {
        assets.QUESTIONS + '/README.md', assets.QUESTIONS + '/index.json',
        assets.QUESTIONS + '/policy/fictional.json'}
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert (root / assets.MANIFEST).read_bytes() == manifest


def test_indexed_oracles_are_sources_and_undeclared_oracles_are_rejected(question_tree):
    root, directory, index = question_tree
    (directory / 'oracles').mkdir()
    (directory / 'oracles/fictional.json').write_text('{}\n', encoding='utf-8')
    index['oracles'] = ['oracles/fictional.json']
    (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    git(root, 'add', assets.QUESTIONS)
    assert assets.verify(root)[1] == 0
    index['oracles'] = []
    (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    assert 'tracked_path_not_allowed' in error_codes(assets.verify(root)[0])


@pytest.mark.parametrize('raw', [
    'FICTIONAL_PRIVATE_INVALID_JSON', '{"schema_version":1,"schema_version":1,"files":[]}',
    'null', '[]', '{"schema_version":true,"files":[]}', '{"schema_version":2,"files":[]}',
    '{"schema_version":1}', '{"schema_version":1,"files":{}}',
    '{"schema_version":1,"files":[],"extra":NaN}',
])
def test_question_index_errors_are_content_safe(question_tree, raw):
    root, directory, _ = question_tree
    (directory / 'index.json').write_text(raw, encoding='utf-8')
    result, code = assets.verify(root)
    assert code == 1 and 'invalid_question_index' in error_codes(result)
    assert 'FICTIONAL_PRIVATE_INVALID_JSON' not in json.dumps(result)


@pytest.mark.parametrize('entry', [
    'policy/fictional.json', {}, {'path': None}, {'path': '/private.json'},
    {'path': 'C:/private.json'}, {'path': '../private.json'},
    {'path': 'policy/../private.json'}, {'path': 'policy\\private.json'},
    {'path': 'policy//private.json'}, {'path': 'policy/name:stream.json'},
    {'path': 'unknown/private.json'}, {'path': 'policy/nested/private.json'},
    {'path': 'policy/private.txt'}, {'path': 'policy/.json'},
])
def test_invalid_question_declarations_fail_before_reading(question_tree, entry):
    root, directory, index = question_tree
    index['files'] = [entry]
    (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    result, code = assets.verify(root)
    assert code == 1 and 'invalid_question_path' in error_codes(result)


@pytest.mark.parametrize('path', ['policy/fictional.json', 'policy/FICTIONAL.json'])
def test_duplicate_question_declarations_are_case_insensitive(question_tree, path):
    root, directory, index = question_tree
    index['files'].append({'path': path})
    (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    assert 'duplicate_question_path' in error_codes(assets.verify(root)[0])


@pytest.mark.parametrize('relative', ['index.json', 'README.md', 'policy/fictional.json'])
def test_missing_question_files_cannot_be_hidden_by_unstaged_deletion(question_tree, relative):
    root, directory, _ = question_tree
    (directory / relative).unlink()
    result, code = assets.verify(root)
    assert code == 1 and 'question_file_missing_or_nonregular' in error_codes(result)


def test_missing_tracked_question_tree_is_not_optional(question_tree):
    root, directory, _ = question_tree
    for relative in ['policy/fictional.json', 'index.json', 'README.md']:
        (directory / relative).unlink()
    (directory / 'policy').rmdir()
    directory.rmdir()
    assert 'question_file_missing_or_nonregular' in error_codes(assets.verify(root)[0])


@pytest.mark.parametrize('relative', ['policy/extra.json', 'report.json',
                                     'policy/private.txt', 'policy/nested/private.json'])
def test_force_added_unlisted_question_files_are_rejected(question_tree, relative):
    root, directory, _ = question_tree
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('FICTIONAL_PRIVATE_CONTENT', encoding='utf-8')
    assert assets.verify(root)[1] == 0  # Untracked files do not enter the Git allowance.
    git(root, 'add', '-f', path.relative_to(root).as_posix())
    result, code = assets.verify(root)
    assert code == 1 and {'path': path.relative_to(root).as_posix(),
                         'code': 'tracked_path_not_allowed'} in result['errors']
    assert 'FICTIONAL_PRIVATE_CONTENT' not in json.dumps(result)


@pytest.mark.parametrize('relative', ['', 'index.json', 'README.md', 'policy', 'policy/fictional.json'])
def test_question_reparse_points_are_rejected_before_reading(question_tree, monkeypatch, relative):
    root, directory, _ = question_tree
    target = directory / relative
    original = Path.lstat
    class Reparse:
        st_mode = 0
        st_file_attributes = 0x400
    def lstat(path, *args, **kwargs):
        return Reparse() if path == target else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    result, code = assets.verify(root)
    assert code == 1 and 'linked_path' in error_codes(result)


def test_unreadable_question_index_has_fixed_diagnostic(question_tree, monkeypatch):
    root, directory, _ = question_tree
    target = directory / 'index.json'
    original = Path.read_bytes
    def read(path):
        if path == target:
            raise PermissionError('FICTIONAL_PRIVATE_ERROR')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    result, code = assets.verify(root)
    assert code == 1 and 'question_sources_unavailable' in error_codes(result)
    assert 'FICTIONAL_PRIVATE_ERROR' not in json.dumps(result)
