"""Catalog source identity checks; no business approval or real source fixtures."""
import json
from copy import deepcopy
import pytest
from src.answering.catalog import validate_catalog
from src.evaluation.questions import ExpectedContext, context_failures
from src.tests.test_answering import sample


def test_context_oracle_rejects_stale_routing_and_checks_only_authored_slots():
    expected = ExpectedContext.model_validate({
        'has_request': True, 'slots': {'system': 'eleave', 'days': None, 'sessions': 1,
                                       'granularity': 'session'},
    })
    slots = {'system': 'eleave', 'sessions': 1, 'granularity': 'session', 'subtype': 'annual'}
    assert context_failures(expected, {'request': {'slots': slots}}) == []
    for stale in ({'system': 'workit'}, {'days': 2}, {'sessions': None}, {'sessions': True}):
        assert context_failures(expected, {'request': {'slots': {**slots, **stale}}}) == ['context_slots']


def test_context_oracle_distinguishes_cleared_inputs_from_omitted_assertions():
    entry = {'value': '2026-12-01', 'origin': 'user_input'}
    state = {'request': {'user_inputs': {'as_of': entry['value']}}, 'user_inputs': {'as_of': entry}}
    assert context_failures(ExpectedContext(), state) == []
    assert context_failures(ExpectedContext(user_inputs={'as_of': entry['value']}), state) == []
    assert context_failures(ExpectedContext(user_inputs={'as_of': '2026-12-02'}), state) == [
        'context_user_inputs']
    assert context_failures(ExpectedContext(user_inputs={'as_of': entry['value']}),
                            {**state, 'user_inputs': {'as_of': {**entry, 'origin': 'employee'}}}) == [
        'context_user_inputs']
    cleared = ExpectedContext(user_inputs={'as_of': None})
    assert context_failures(cleared, {'request': {}}) == []
    for stale in (state, {**state, 'user_inputs': {}}, {**state, 'request': {}}):
        assert context_failures(cleared, stale) == ['context_user_inputs']


def test_context_oracle_requires_cancelled_request_and_empty_pending_slots():
    expected = ExpectedContext(has_request=False, missing_slots=[], pending_inputs=[], pending=None)
    assert context_failures(expected, {'request': None}) == []
    assert context_failures(expected, {}) == ['context_unavailable']
    assert context_failures(expected, {'request': {'slots': {}}, 'missing_slots': ['system'],
                                       'pending_inputs': ['as_of'], 'pending': 'inputs'}) == [
        'context_request', 'context_missing_slots', 'context_pending_inputs', 'context_pending']


@pytest.mark.parametrize('failure',[None,'fingerprint','lexicon','quote','revision','hash','coverage','duplicate','metadata'])
def test_structural_catalog_validation_leaves_source_drift_to_local_evidence_gate(sample,tmp_path,failure):
    folder=tmp_path/'chunks'/'doc'
    folder.mkdir(parents=True)
    (folder/'metadata.json').write_text(json.dumps(sample.metadata),encoding='utf-8')
    ref=sample.data['cards'][0]['sections']['amount']['units'][0]['refs'][0]
    snapshot={'package_fingerprint':'packages','packages':[{'document_id':'doc','revision_id':'revision'}],
              'chunks':[{'document_id':'doc','chunk_index':0,'content':sample.result['matches'][0]['content'],
                         'content_sha256':ref['content_sha256']}]}
    data=deepcopy(sample.data)
    target=data['cards'][0]['sections']['amount']['units'][0]['refs'][0]
    if failure=='fingerprint': data['package_fingerprint']='changed'
    elif failure=='lexicon': data['lexicon_sha256']='changed'
    elif failure=='quote': target['quote']='fabricated'
    elif failure=='revision': target['revision_id']='old'
    elif failure=='hash': target['content_sha256']='0'*64
    elif failure=='coverage': data['coverage']=[]
    elif failure=='duplicate': data['cards']*=2
    elif failure=='metadata': target['metadata']['applicability']='everyone'
    if failure in {'lexicon','coverage','duplicate'}:
        with pytest.raises(ValueError): validate_catalog(data,sample.lexicon,'lexicon',snapshot,root=tmp_path)
    else:
        assert len(validate_catalog(data,sample.lexicon,'lexicon',snapshot,root=tmp_path).cards)==1


def test_template_regression_input_is_private_and_bounded(tmp_path,monkeypatch):
    from scripts import benchmark_template_answers as utility
    monkeypatch.setattr(utility,'REPOSITORY_ROOT',tmp_path)
    folder=tmp_path/'.tmp'
    folder.mkdir()
    path=folder/'cases.json'
    path.write_text(json.dumps([{'text':'Fictional question?'}]),encoding='utf-8')
    assert len(utility.load_cases(path))==1
    with pytest.raises(ValueError): utility.private_path(tmp_path/'outside.json')
    path.write_text(json.dumps([{'text':' '}]),encoding='utf-8')
    with pytest.raises(ValueError): utility.load_cases(path)


def test_retired_answer_study_has_no_model_or_database_imports():
    import subprocess
    import sys

    script = '''
import builtins, runpy, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('src.', 'llama_cpp', 'torch', 'transformers', 'sqlalchemy', 'dotenv')):
        raise AssertionError('Retired studies must not initialize runtime dependencies')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.argv = ['scripts.evaluate_answers', 'run', '--cases', 'absent', '--model', 'absent']
runpy.run_module('scripts.evaluate_answers', run_name='__main__')
'''
    process = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
    assert process.returncode == 2 and not process.stderr
    assert 'src.cli.check_answer_catalog' in process.stdout
    assert 'scripts.benchmark_template_answers' in process.stdout
