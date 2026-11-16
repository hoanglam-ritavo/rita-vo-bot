"""Synthetic authoring/provenance boundaries, not corpus semantic acceptance."""
import copy
import json
import subprocess
import sys

import pytest

from src.semantics import preparation as prep
from src.semantics.corpus import SemanticPreparationError, corpus_snapshot, snapshot_fingerprint
from src.tests.package_fixtures import ready


@pytest.fixture
def authored(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'REPOSITORY_ROOT', tmp_path)
    package, source = ready(tmp_path)
    root = tmp_path / 'chunks'
    data = prep.inventory(root)
    data['concepts'] = [{'id': 'synthetic.policy', 'canonical': 'quy định mẫu',
        'aliases': ['nội quy mẫu'], 'requires_any': [], 'excludes_any': []}]
    for index, chunk in enumerate(data['chunks']):
        unit_id = f'synthetic.unit-{index}'
        chunk.update(disposition='covered', unit_ids=[unit_id])
        data['units'].append({'id': unit_id, 'concept_ids': ['synthetic.policy'],
            'summary': 'Synthetic evidence unit', 'conditions': [],
            'evidence': [{k: chunk[k] for k in ('document_id', 'chunk_index', 'source_refs')}],
            'questions': {'direct': 'Quy định mẫu là gì?', 'paraphrase': 'Nội quy mẫu nói gì?', 'contrast': ''},
            'review_status': 'draft', 'review_notes': ''})
    return root, package, source, data


def test_snapshot_identity_includes_every_source_and_json_byte(authored):
    root, package, source, data = authored
    before = snapshot_fingerprint(root)
    path = package / 'chunks.json'
    path.write_bytes(path.read_bytes() + b'\n')
    after = corpus_snapshot(root)
    assert after['package_fingerprint'] != before
    assert after['package_count'] == 1 and after['chunk_count'] == 2
    snapshot = package / 'source.txt'
    snapshot.write_bytes(b'changed')
    with pytest.raises(SemanticPreparationError, match='source_snapshot_mismatch'):
        snapshot_fingerprint(root)


def test_draft_validity_never_means_review_or_export(authored):
    root, _, _, data = authored
    report = prep.validate_manifest(data, root)
    assert report['status'] == 'valid'
    assert report['inventoried_chunks'] == 2 and report['pending_chunks'] == 0
    assert report['review_required'] is True and report['export_allowed'] is False
    with pytest.raises(SemanticPreparationError, match='human_source_review_required'):
        prep.export_artifact(data, root)


def test_scaffold_marks_every_chunk_pending(authored):
    root, _, _, _ = authored
    data = prep.inventory(root)
    assert data['reviewed'] is False and not data['units']
    assert all(c['disposition'] == 'pending' and not c['unit_ids'] for c in data['chunks'])
    assert all(c['source_excerpts'] for c in data['chunks'])


def test_export_strips_evidence_after_explicit_complete_review(authored):
    root, _, _, data = authored
    data.update(reviewed=True, review_notes='Fictional human review for test only.')
    for unit in data['units']:
        unit.update(review_status='reviewed', review_notes='Fictional source/independent review complete.')
    artifact = prep.export_artifact(data, root)
    assert set(artifact) == {'schema_version', 'reviewed', 'review_notes', 'package_fingerprint', 'concepts'}
    assert 'document_id' not in json.dumps(artifact)


@pytest.mark.parametrize('mutation,expected', [
    (lambda d: d['chunks'].pop(), 'incomplete_chunk_inventory'),
    (lambda d: d['chunks'].append(copy.deepcopy(d['chunks'][0])), 'invalid_chunk_identity'),
    (lambda d: d['chunks'][0].update(content='forged'), 'chunk_drift'),
    (lambda d: d['chunks'][0].update(disposition='administrative', reason=''), 'missing_disposition_reason'),
    (lambda d: d['units'][0]['evidence'][0]['source_refs'][0].update(end=1), 'source_reference_drift'),
    (lambda d: d['units'][0].update(concept_ids=['unknown']), 'invalid_concept_reference'),
    (lambda d: d['chunks'][0].update(unit_ids=['missing']), 'unknown_unit_reference'),
    (lambda d: d.update(reviewed=True, review_notes='Not sufficient'), 'incomplete_review_claim'),
    (lambda d: d['chunks'][0].update(document_id=[]), 'invalid_chunk_identity'),
    (lambda d: d['chunks'][0].update(disposition=[]), 'invalid_disposition'),
    (lambda d: d['units'][0]['evidence'][0].update(document_id={}), 'unknown_evidence'),
    (lambda d: d['chunks'][1]['unit_ids'].append('synthetic.unit-0'), 'missing_forward_evidence_reference'),
])
def test_invalid_coverage_provenance_and_review_claims_fail(authored, mutation, expected):
    root, _, _, original = authored
    data = copy.deepcopy(original)
    mutation(data)
    result = prep.validate_manifest(data, root)
    assert result['status'] == 'invalid'
    assert any(expected in error for error in result['errors'])
    assert result['export_allowed'] is False


def test_package_change_requires_reauthoring(authored):
    root, package, _, data = authored
    path = package / 'metadata.json'
    path.write_bytes(path.read_bytes() + b'\n')
    result = prep.validate_manifest(data, root)
    assert 'package_drift' in result['errors']


def test_duplicate_source_segment_ids_cannot_replace_provenance(authored):
    root, package, _, _ = authored
    path = package / 'extraction.json'
    extraction = json.loads(path.read_text())
    extraction['segments'].append(copy.deepcopy(extraction['segments'][0]))
    path.write_text(json.dumps(extraction), encoding='utf-8')
    with pytest.raises(SemanticPreparationError, match='invalid_chunk_provenance'):
        corpus_snapshot(root)


def test_ambiguous_alias_warning_and_structural_coverage_limit(authored):
    root, _, _, data = authored
    data['concepts'].append({**copy.deepcopy(data['concepts'][0]), 'id': 'different', 'canonical': 'quy định khác'})
    result = prep.validate_manifest(data, root)
    assert result['ambiguous_alias_count'] == 1
    assert 'ambiguous_aliases_suppressed_at_runtime' in result['warnings']
    assert result['coverage_kind'] == 'structural_inventory_only'
    assert result['semantic_completeness_assessed'] is False


def test_reviewed_questions_must_differ_after_normalization(authored):
    root, _, _, data = authored
    unit = data['units'][0]
    unit.update(review_status='reviewed', review_notes='Synthetic review')
    unit['questions']['paraphrase'] = unit['questions']['direct'].upper() + '  '
    report = prep.validate_manifest(data, root)
    assert any('duplicate_question_wording' in error for error in report['errors'])


def test_unit_may_cite_subset_but_not_invent_source_offsets(authored):
    root, package, _, _ = authored
    path = package / 'chunks.json'
    content = json.loads(path.read_text())
    content['chunks'][0]['source_refs'].append(content['chunks'][1]['source_refs'][0])
    path.write_text(json.dumps(content), encoding='utf-8')
    data = prep.inventory(root)
    data['concepts'] = [{'id': 'topic', 'canonical': 'chủ đề', 'aliases': ['vấn đề'], 'requires_any': [], 'excludes_any': []}]
    chunk = data['chunks'][0]
    chunk.update(disposition='covered', unit_ids=['subunit'])
    data['units'] = [{'id': 'subunit', 'concept_ids': ['topic'], 'summary': 'Selected supported clause',
        'conditions': [], 'evidence': [{'document_id': chunk['document_id'], 'chunk_index': 0,
            'source_refs': [copy.deepcopy(chunk['source_refs'][0])]}], 'questions': {'direct': '', 'paraphrase': '', 'contrast': ''},
        'review_status': 'draft', 'review_notes': ''}]
    assert prep.validate_manifest(data, root)['errors'] == []
    data['units'][0]['evidence'][0]['source_refs'][0]['end'] = 1
    assert any('source_reference_drift' in e for e in prep.validate_manifest(data, root)['errors'])


def test_private_outputs_are_new_only_and_no_model_or_database_import(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'REPOSITORY_ROOT', tmp_path)
    output = '.tmp/draft.json'
    prep.write_new(output, {'draft': True})
    with pytest.raises(SemanticPreparationError, match='output_exists'):
        prep.write_new(output, {'changed': True})
    assert json.loads((tmp_path / output).read_text()) == {'draft': True}
    with pytest.raises(SemanticPreparationError, match='private_path_required'):
        prep.write_new('outside.json', {})
    code = "import sys; import src.cli.prepare_semantics; assert not any(k in sys.modules for k in ('numpy','sklearn','sqlalchemy','sentence_transformers','onnxruntime','src.database'))"
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)


def test_cli_draft_and_review_gate_report_only_safe_metadata(authored, capsys):
    root, _, _, _ = authored
    assert prep.main(['inventory', '--chunks-root', str(root), '--output', '.tmp/draft.json', '--json']) == 0
    created = json.loads(capsys.readouterr().out)
    assert created['review_required'] is True and created['chunk_count'] == 2
    assert 'Original policy' not in json.dumps(created)
    assert prep.main(['export', '--chunks-root', str(root), '--manifest', '.tmp/draft.json', '--output', '.tmp/runtime.json']) == 2
    assert json.loads(capsys.readouterr().out)['error'] in {'human_source_review_required', 'invalid_semantic_manifest'}
