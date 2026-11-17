"""Fictional offline source/build contracts; no model, database or business acceptance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from src.artifact_authoring import sources, workflow
from src.cli import build_artifacts as cli
from src.semantics.corpus import corpus_snapshot
from src.tests.package_fixtures import ready


@pytest.fixture
def authored(tmp_path):
    package, _ = ready(tmp_path)
    snapshot = corpus_snapshot(tmp_path / 'chunks')
    doc = snapshot['packages'][0]
    metadata = json.loads((package / 'metadata.json').read_bytes())
    chunk = snapshot['chunks'][0]
    lexicon = dict(schema_version=1, reviewed=True, review_notes='Fictional source review.',
        package_fingerprint=snapshot['package_fingerprint'], concepts=[
            dict(id=name, canonical=name, aliases=[name], requires_any=[], excludes_any=[])
            for name in ('z', 'a')])
    ref = dict(document_id=doc['document_id'], revision_id=doc['revision_id'], chunk_index=0,
               content_sha256=chunk['content_sha256'], quote=chunk['content'],
               metadata={key: metadata[key] for key in ('title', 'applicability', 'effective_from',
                                                        'effective_to', 'version_label', 'source_path')})
    lexical = sources.encode(lexicon)
    catalog = dict(schema_version=1, package_fingerprint=snapshot['package_fingerprint'],
        lexicon_sha256=hashlib.sha256(lexical).hexdigest(), cards=[
            dict(id=name, label='Fictional ' + name, concept_ids=[name], aliases=[name],
                 requires_any=[], excludes_any=[], sections={'overview': {'complete': False,
                     'units': [{'text': chunk['content'], 'refs': [deepcopy(ref)]}]}})
            for name in ('z', 'a')], coverage=[
                dict(document_id=doc['document_id'], chunk_index=i,
                     disposition='template' if i == 0 else 'excerpt', note='Fictional coverage.')
                for i in range(2)])
    raw = (lexical, sources.encode(catalog, crlf=True))
    inputs = tmp_path / '.tmp' / 'inputs'
    inputs.mkdir(parents=True)
    paths = [inputs / 'lexicon.json', inputs / 'catalog.json']
    for path, value in zip(paths, raw):
        path.write_bytes(value)
    imported = tmp_path / '.tmp' / 'imported'
    workflow.import_pair(*paths, imported, tmp_path)
    shutil.copytree(imported / 'artifacts', tmp_path / 'artifacts')
    for group, value in zip(sources.GROUPS, raw):
        folder = tmp_path / 'artifacts' / group
        (folder / sources.GROUPS[group][0]).write_bytes(value)
        (folder / 'manifest.json').write_bytes(b'{"runtime_release_accepted": false}\n')
    (tmp_path / 'artifacts' / 'runtime-assets.json').write_bytes(b'fictional inventory\n')
    return tmp_path, paths, raw


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (root / 'artifacts').rglob('*') if p.is_file()}


def edit(path, change):
    value = json.loads(path.read_bytes())
    change(value)
    path.write_bytes(sources.encode(value))


def test_import_build_preserves_order_bytes_and_noop_mtimes(authored):
    root, paths, raw = authored
    before = snapshot(root)
    assert workflow.build(root, check=True) == {'status': 'current', 'changed_files': 0}
    assert workflow.build(root)['changed_files'] == 0
    assert snapshot(root) == before
    assembled, _ = sources.assemble(root)
    assert assembled == raw
    assert [row['id'] for row in sources.decode(assembled[1])['cards']] == ['z', 'a']
    assert set(sources.declared_sources(root)) == {
        p.relative_to(root).as_posix() for p in before if '/source/' in p.as_posix()}
    with pytest.raises(sources.SourceError, match='new_private_output_required'):
        workflow.import_pair(*paths, root / '.tmp' / 'imported', root)
    with pytest.raises(sources.SourceError, match='new_private_output_required'):
        workflow.import_pair(*paths, root / 'outside', root)
    with pytest.raises(sources.SourceError, match='new_private_output_required'):
        workflow.import_pair(*paths, root / '.tmp' / '..' / 'outside', root)


@pytest.mark.parametrize('collection', ['concepts', 'cards', 'coverage'])
def test_build_changes_only_bound_outputs_and_retains_provenance(authored, collection):
    root, _, _ = authored
    before = snapshot(root)
    group = 'semantics' if collection == 'concepts' else 'answers'
    folder = root / 'artifacts' / group / 'source' / collection
    path = next(folder.glob('*.json'))
    if collection == 'concepts':
        edit(path, lambda v: v['aliases'].append('Fictional extra alias'))
    elif collection == 'cards':
        edit(path, lambda v: v.update(label='Changed fictional label'))
    else:
        edit(path, lambda v: v[0].update(note='Changed fictional coverage note'))
    after_edit = snapshot(root)
    with pytest.raises(sources.SourceError, match='generated_artifact_outdated'):
        workflow.build(root, check=True)
    assert snapshot(root) == after_edit
    assert workflow.build(root)['changed_files'] == (2 if collection == 'concepts' else 1)
    assert workflow.build(root, check=True)['status'] == 'current'
    lexical = (root / 'artifacts/semantics/lexicon.json').read_bytes()
    catalog = sources.decode((root / 'artifacts/answers/catalog.json').read_bytes())
    assert catalog['lexicon_sha256'] == hashlib.sha256(lexical).hexdigest()
    for p, value in before.items():
        if p.name in ('manifest.json', 'runtime-assets.json'):
            assert (p.read_bytes(), p.stat().st_mtime_ns) == value
    if collection != 'concepts':
        assert lexical == before[root / 'artifacts/semantics/lexicon.json'][0]


@pytest.mark.parametrize('failure', ['missing', 'extra', 'duplicate_key', 'duplicate_ref',
    'unsafe_ref', 'case_collision', 'filename', 'schema', 'coverage', 'nonfinite', 'index_schema'])
def test_invalid_sources_never_modify_outputs(authored, failure):
    root, _, _ = authored
    folder = root / 'artifacts/semantics/source'
    path = folder / 'concepts/z.json'
    index = folder / 'index.json'
    if failure == 'missing':
        path.unlink()
    elif failure == 'extra':
        (folder / 'extra.json').write_bytes(b'{}')
    elif failure == 'duplicate_key':
        path.write_bytes(b'{"id":"z","id":"z"}')
    elif failure == 'duplicate_ref':
        edit(index, lambda v: v['concepts'].append(v['concepts'][0]))
    elif failure == 'unsafe_ref':
        edit(index, lambda v: v['concepts'].append('concepts/../../outside.json'))
    elif failure == 'case_collision':
        edit(index, lambda v: v['concepts'].append('concepts/Z.json'))
    elif failure == 'filename':
        edit(path, lambda v: v.update(id='other'))
    elif failure == 'schema':
        edit(path, lambda v: v.update(aliases=[]))
    elif failure == 'coverage':
        coverage = next((root / 'artifacts/answers/source/coverage').glob('*.json'))
        edit(coverage, lambda v: v.append(v[0]))
    elif failure == 'nonfinite':
        path.write_bytes(b'{"id":NaN}')
    else:
        edit(index, lambda v: v.update(schema_version=True))
    outputs = {p: p.read_bytes() for p in (root / 'artifacts').rglob('*.json') if '/source/' not in p.as_posix()}
    with pytest.raises((ValueError, OSError)):
        workflow.build(root)
    assert all(p.read_bytes() == raw for p, raw in outputs.items())


def test_validation_and_concurrent_source_changes_precede_replacement(authored, monkeypatch):
    root, _, _ = authored
    source = root / 'artifacts/semantics/source/concepts/z.json'
    edit(source, lambda v: v['aliases'].append('Fictional pending edit'))
    outputs = {p: p.read_bytes() for p in (root / 'artifacts').glob('*/*.json')}
    original = workflow.validate_pair

    def changed(raw, root):
        original(raw, root)
        edit(source, lambda v: v['aliases'].append('Concurrent edit'))

    monkeypatch.setattr(workflow, 'validate_pair', changed)
    with pytest.raises(sources.SourceError, match='source_changed'):
        workflow.build(root)
    assert all(p.read_bytes() == value for p, value in outputs.items())


def test_partial_replacement_is_repaired_by_explicit_rebuild(authored, monkeypatch):
    root, _, _ = authored
    source = root / 'artifacts/semantics/source/concepts/z.json'
    edit(source, lambda v: v['aliases'].append('Fictional edit'))
    original = workflow.os.replace
    calls = []

    def interrupted(src, dest):
        calls.append(dest)
        if len(calls) == 2:
            raise OSError('Fictional interruption')
        original(src, dest)

    monkeypatch.setattr(workflow.os, 'replace', interrupted)
    with pytest.raises(OSError):
        workflow.build(root)
    monkeypatch.setattr(workflow.os, 'replace', original)
    with pytest.raises(sources.SourceError, match='generated_artifact_outdated'):
        workflow.build(root, check=True)
    assert workflow.build(root)['changed_files'] == 1
    assert workflow.build(root, check=True)['status'] == 'current'


def test_linked_source_is_rejected(authored, monkeypatch):
    root, _, _ = authored
    # Emulate Windows junction metadata without requiring symlink privileges.
    original = Path.lstat
    from types import SimpleNamespace

    def linked(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400) if path.name == 'concepts' else value

    monkeypatch.setattr(Path, 'lstat', linked)
    with pytest.raises(sources.SourceError, match='linked_source_path'):
        sources.assemble(root)


def test_import_validates_input_hash_and_normalizes_only_formatting(authored):
    root, paths, raw = authored
    output = root / '.tmp' / 'stale-import'
    edit(paths[1], lambda v: v.update(lexicon_sha256='0' * 64))
    with pytest.raises(ValueError):
        workflow.import_pair(*paths, output, root)
    assert not output.exists()
    # A valid pair can use different formatting; assembly owns output bytes/hash.
    lexical = json.dumps(sources.decode(raw[0]), ensure_ascii=True).encode()
    catalog = sources.decode(raw[1])
    catalog['lexicon_sha256'] = hashlib.sha256(lexical).hexdigest()
    paths[0].write_bytes(lexical)
    paths[1].write_bytes(json.dumps(catalog).encode())
    workflow.import_pair(*paths, output, root)
    assert sources.assemble(output)[0] == raw


def test_membership_change_after_staging_does_not_replace_outputs(authored, monkeypatch):
    root, _, _ = authored
    source = root / 'artifacts/semantics/source'
    edit(source / 'concepts/z.json', lambda v: v['aliases'].append('Fictional edit'))
    outputs = {p: p.read_bytes() for p in (root / 'artifacts').glob('*/*.json')}
    original = workflow.recheck
    calls = []

    def changed(observed):
        calls.append(True)
        if len(calls) == 2:
            (source / 'extra.json').write_bytes(b'{}')
        original(observed)

    monkeypatch.setattr(workflow, 'recheck', changed)
    with pytest.raises(sources.SourceError, match='source_changed'):
        workflow.build(root)
    assert all(p.read_bytes() == value for p, value in outputs.items())


def test_cli_redacts_validator_text_and_dispatches(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, 'build', lambda **kw: calls.append(kw) or {'status': 'current'})
    assert cli.main(['check']) == 0
    assert calls == [{'check': True}]
    assert cli.main(['build']) == 0
    assert calls[-1] == {'check': False}

    def invalid(**kwargs):
        raise ValueError('Fictional private source content')

    monkeypatch.setattr(cli, 'build', invalid)
    assert cli.main(['check']) == 1
    output = capsys.readouterr().out
    assert 'artifact_validation_or_io_failed' in output and 'private source' not in output
