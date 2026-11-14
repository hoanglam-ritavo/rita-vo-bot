"""Synthetic preflight checks; these do not verify real model quality."""
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.cli import preflight_documents as cli
from src.ingestion import document_packages as p, embeddings, preflight
from package_fixtures import ready


@pytest.fixture
def setup(tmp_path, monkeypatch):
    package, _ = ready(tmp_path)
    record = p.metadata(package)
    record.update(indexing_decision='hold', lifecycle_status='unknown', access_groups=[])
    (package / 'metadata.json').write_bytes(p.encoded(record))
    model_path = tmp_path / 'model'
    model_path.mkdir()
    (model_path / 'weights').write_bytes(b'synthetic')
    tokenizer = Mock(return_value={'input_ids': [1] * 8})
    tokenizer.model_max_length = 10
    model = SimpleNamespace(max_seq_length=8, tokenizer=tokenizer,
                            get_sentence_embedding_dimension=lambda: 384,
                            encode=Mock(return_value=[SimpleNamespace(tolist=lambda: [1.0] + [0.0] * 383)
                                                      for _ in range(2)]))
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=factory))
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    return package, model_path, model, factory


def run(tmp_path, setup, **kwargs):
    package, model_path, _, _ = setup
    return preflight.check(tmp_path, package, model_path, 'a' * 40, **kwargs)


def test_hold_package_tokens_only_and_unchanged(tmp_path, setup):
    package, _, model, factory = setup
    before = p.snapshot_files(package)
    result = run(tmp_path, setup)
    assert result['status'] == 'processed'
    assert result['embedding_check'] == 'not_run'
    assert result['database_checked'] is False
    assert result['metadata_blockers'] == ['indexing_decision_requires_include',
                                           'lifecycle_status_requires_active', 'access_groups_required']
    assert result['token_limit'] == 8
    assert result['chunks'] == [{'chunk_index': i, 'token_count': 8, 'status': 'passed'} for i in range(2)]
    model.encode.assert_not_called()
    assert factory.call_args.kwargs == {'local_files_only': True, 'trust_remote_code': False}
    for call in model.tokenizer.call_args_list:
        assert call.args[0].startswith('passage: ')
        assert call.kwargs == {'add_special_tokens': True, 'truncation': False}
    assert p.snapshot_files(package) == before


@pytest.mark.parametrize('damage', ['references', 'snapshot', 'warnings'])
def test_invalid_package_precedes_model(tmp_path, setup, damage):
    package, _, _, factory = setup
    if damage == 'snapshot':
        (package / 'source.txt').write_bytes(b'changed')
    else:
        draft = p.decode((package / 'chunks.json').read_bytes())
        if damage == 'references':
            draft['chunks'][0]['source_refs'][0]['end'] = 999
        else:
            draft['warning_decisions']['nonexistent'] = ''
        (package / 'chunks.json').write_bytes(p.encoded(draft))
    assert run(tmp_path, setup)['status'] == 'rejected'
    factory.assert_not_called()


def test_all_oversize_chunks_reported_without_encode(tmp_path, setup):
    model = setup[2]
    model.tokenizer.return_value = {'input_ids': [1] * 9}
    result = run(tmp_path, setup, check_embeddings=True)
    assert result['code'] == 'chunk_exceeds_model_limit'
    assert [c['status'] for c in result['chunks']] == ['exceeded', 'exceeded']
    assert result['embedding_check'] == 'skipped'
    model.encode.assert_not_called()


def test_embedding_success_does_not_write_or_expose_vectors(tmp_path, setup):
    before = p.snapshot_files(setup[0])
    result = run(tmp_path, setup, check_embeddings=True)
    assert result['embedding_check'] == 'passed'
    assert result['status'] == 'processed'
    assert len(setup[2].encode.call_args.args[0]) == 2
    assert setup[2].encode.call_args.kwargs['normalize_embeddings'] is True
    assert 'embedding' not in result['chunks'][0]
    assert p.snapshot_files(setup[0]) == before


@pytest.mark.parametrize('vector', [[0.0], [float('nan')] * 384, [float('inf')] * 384])
def test_invalid_vectors(tmp_path, setup, vector):
    setup[2].encode.return_value = [SimpleNamespace(tolist=lambda: vector)] * 2
    result = run(tmp_path, setup, check_embeddings=True)
    assert result['code'] == 'invalid_vectors'
    assert result['embedding_check'] == 'failed'


def test_wrong_vector_count(tmp_path, setup):
    setup[2].encode.return_value = []
    assert run(tmp_path, setup, check_embeddings=True)['code'] == 'embedding_count'


@pytest.mark.parametrize('target', ['package', 'model'])
def test_input_changes_during_check(tmp_path, setup, target):
    package, model_path, model, _ = setup
    def tokenize(*args, **kwargs):
        path = package / 'chunks.json' if target == 'package' else model_path / 'weights'
        path.write_bytes(path.read_bytes() + b' ')
        return {'input_ids': [1]}
    model.tokenizer.side_effect = tokenize
    result = run(tmp_path, setup)
    assert result['code'] == target + '_changed'
    assert result['status'] == 'rejected'


@pytest.mark.parametrize('mode,exit_code', [('success', 0), ('tokens', 1), ('runtime', 2), ('missing', 2)])
def test_cli_safe_json_and_exit_codes(tmp_path, setup, capsys, mode, exit_code):
    package, model_path, model, factory = setup
    if mode == 'tokens':
        model.tokenizer.return_value = {'input_ids': [1] * 99}
    elif mode == 'runtime':
        def fail(*args, **kwargs):
            print('private content')
            print('private content', file=sys.stderr)
            raise RuntimeError('private content')
        factory.side_effect = fail
    elif mode == 'missing':
        model_path = tmp_path / 'absent'
    assert cli.main(['--package', str(package), '--model-path', str(model_path),
                     '--model-revision', 'A' * 40, '--json']) == exit_code
    captured = capsys.readouterr()
    assert 'private content' not in captured.out + captured.err
    report = json.loads(captured.out)
    assert report['operation'] == 'preflight-documents'
    assert 'Original policy.' not in captured.out


def test_import_is_database_and_model_free():
    script = '''
import sys
import src.cli.preflight_documents
assert not any(k in sys.modules for k in ('sqlalchemy', 'src.database', 'sentence_transformers'))
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_execution_does_not_import_database(tmp_path, setup, monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith(('sqlalchemy', 'src.database', 'psycopg')):
            pytest.fail('Preflight must not use the database runtime')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    assert run(tmp_path, setup, check_embeddings=True)['status'] == 'processed'


@pytest.mark.parametrize('target', ['package', 'model'])
def test_input_disappearing_during_failed_model_load(tmp_path, setup, target):
    package, model_path, _, factory = setup
    def fail(*args, **kwargs):
        path = package / 'chunks.json' if target == 'package' else model_path / 'weights'
        path.unlink()
        raise RuntimeError('synthetic failure')
    factory.side_effect = fail
    result = run(tmp_path, setup)
    assert result['status'] == 'rejected'
    assert result['code'] == target + '_changed'


def test_metadata_dates_and_scope_are_separate(tmp_path, setup):
    record = p.metadata(setup[0])
    record.update(applicability=None, effective_from='9999-01-01')
    assert 'effective_date_not_started' in preflight.metadata_blockers(record)
    assert 'applicability_required' in preflight.metadata_blockers(record)
    record.update(effective_from=None, effective_to='2000-01-01')
    assert 'effective_date_expired' in preflight.metadata_blockers(record)


@pytest.mark.parametrize('revision', ['a' * 40, 'ABCDEF' * 6 + 'ABCD'])
def test_shared_model_revision_normalizes_and_preserves_publication_adapter(revision):
    from src.ingestion.model_identity import parse_model_revision
    from src.cli.publish_documents import model_commit
    assert parse_model_revision(revision) == revision.lower()
    assert model_commit(revision) == revision.lower()


@pytest.mark.parametrize('revision', ['', 'a' * 39, 'a' * 41, 'g' * 40, ' ' + 'a' * 40, None])
def test_shared_model_revision_rejects_invalid_identity(revision):
    import argparse
    from src.ingestion.model_identity import parse_model_revision
    from src.cli.publish_documents import model_commit
    with pytest.raises(ValueError, match='Expected full 40-hex model commit'):
        parse_model_revision(revision)
    with pytest.raises(argparse.ArgumentTypeError, match='Expected full 40-hex model commit'):
        model_commit(revision)
