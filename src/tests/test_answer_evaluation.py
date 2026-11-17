"""Catalog source identity checks; no business approval or real source fixtures."""
import json
from copy import deepcopy
import pytest
from src.answering.catalog import validate_catalog
from src.tests.test_answering import sample


@pytest.mark.parametrize('failure',[None,'fingerprint','lexicon','quote','revision','hash','coverage','duplicate','metadata'])
def test_catalog_validation_rejects_stale_or_missing_evidence(sample,tmp_path,failure):
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
    if failure:
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
