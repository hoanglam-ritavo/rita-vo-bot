"""Explicit legacy conversion; no private documents or live database."""
from pathlib import Path
import pytest
from app.ingestion import document_packages as p
from package_fixtures import legacy_package
from scripts.convert_chunk_package import convert


@pytest.mark.parametrize('kind', ['pending', 'ready', 'approved'])
def test_preview_apply_repeat_preserves_original(tmp_path, kind):
    old, source = legacy_package(tmp_path, kind)
    before = p.snapshot_files(old)
    result = convert(tmp_path, old)
    target = tmp_path / result['package']
    assert result['code'] == 'conversion_preview' and not target.exists()
    assert convert(tmp_path, old, apply=True)['code'] == 'package_converted'
    assert convert(tmp_path, old, apply=True)['code'] == 'already_converted'
    assert p.snapshot_files(old) == before
    assert (target / 'source.txt').read_bytes() == source.read_bytes()
    record = p.metadata(target)
    assert 'review_status' not in record and 'review_notes' not in record
    if kind != 'pending':
        _, payload, _ = p.load_package(tmp_path, target, eligible=True)
        assert payload['chunks'][0]['content'] == 'Original policy.'
        chunks = p.decode((target / 'chunks.json').read_bytes())
        assert chunks['chunks'][0]['notes'] == 'Synthetic note'
    else:
        with pytest.raises(p.Rejected, match='incomplete_package'):
            p.load_package(tmp_path, target)


@pytest.mark.parametrize('kind', ['ready', 'approved'])
def test_tampered_legacy_hash_rejected(tmp_path, kind):
    old, _ = legacy_package(tmp_path, kind)
    with (old / 'extraction.json').open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(p.Rejected, match='hash_mismatch'):
        convert(tmp_path, old, apply=True)


def test_unresolved_warnings_remain_blocking(tmp_path):
    old, _ = legacy_package(tmp_path, unresolved=True)
    target = tmp_path / convert(tmp_path, old, apply=True)['package']
    with pytest.raises(p.Rejected, match='unresolved_warnings'):
        p.load_package(tmp_path, target)


def test_conflicting_target_never_overwritten(tmp_path):
    old, _ = legacy_package(tmp_path)
    target = tmp_path / convert(tmp_path, old, apply=True)['package']
    (target / 'chunks.json').write_bytes(b'{}')
    with pytest.raises(p.Rejected, match='conversion_target_conflict'):
        convert(tmp_path, old, apply=True)
    assert (target / 'chunks.json').read_bytes() == b'{}'


def test_init_requires_explicit_legacy_conversion(tmp_path):
    old, _ = legacy_package(tmp_path)
    with pytest.raises(p.Rejected, match='legacy_source_convert_explicitly'):
        p.initialize(tmp_path)
    convert(tmp_path, old, apply=True)
    assert p.initialize(tmp_path) == []


def test_pending_missing_original_retains_metadata(tmp_path):
    old, source = legacy_package(tmp_path, 'pending')
    source.unlink()
    target = tmp_path / convert(tmp_path, old, apply=True)['package']
    assert set(p.snapshot_files(target)) == {'metadata.json'}


def test_source_changes_during_conversion_never_finalizes(tmp_path, monkeypatch):
    old, source = legacy_package(tmp_path, 'pending')
    original = p.write_package
    def interrupted(parent, name, files, **kwargs):
        source.write_bytes(b'changed')
        return original(parent, name, files, **kwargs)
    target = tmp_path / convert(tmp_path, old)['package']
    monkeypatch.setattr(p, 'write_package', interrupted)
    with pytest.raises(p.Rejected, match='source_changed'):
        convert(tmp_path, old, apply=True)
    assert not target.exists()


def test_multiple_legacy_versions_require_selection(tmp_path):
    from uuid import uuid4
    old, _ = legacy_package(tmp_path)
    files = p.snapshot_files(old)
    second = p.write_package(old.parent, str(uuid4()), files)
    draft = p.decode((second / 'draft.json').read_bytes())
    draft['chunks'][0]['content'] = 'Another version'
    (second / 'draft.json').write_bytes(p.encoded(draft))
    convert(tmp_path, old, apply=True)
    with pytest.raises(p.Rejected, match='conversion_target_conflict'):
        convert(tmp_path, second, apply=True)


def test_retired_commands_do_not_write(tmp_path, monkeypatch):
    from app.cli import asset_catalog, import_catalog, ingest, review, publish_documents
    from scripts import migrate_indexing_chunks, migrate_indexing_workspace
    monkeypatch.chdir(tmp_path)
    for cli in (asset_catalog, import_catalog, ingest, review, migrate_indexing_chunks, migrate_indexing_workspace):
        assert cli.main(['anything']) == 2
    assert publish_documents.main(['--approved', 'absent']) == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('extension', ['.pdf', '.xlsx', '.csv', '.md', '.txt'])
@pytest.mark.parametrize('kind', ['pending', 'ready', 'approved'])
def test_multiformat_conversion_preserves_contract_evidence(tmp_path, extension, kind):
    """Opaque synthetic source bytes exercise contracts, not actual format parsing."""
    old, source = legacy_package(tmp_path, kind, extension=extension, with_exclusion=True)
    before = p.snapshot_files(old)
    preview = convert(tmp_path, old)
    target = tmp_path / preview['package']
    assert not target.exists() and p.snapshot_files(old) == before
    convert(tmp_path, old, apply=True)
    assert p.snapshot_files(old) == before
    assert (target / ('source' + extension)).read_bytes() == source.read_bytes()
    if kind == 'pending':
        with pytest.raises(p.Rejected, match='incomplete_package'):
            p.load_package(tmp_path, target)
        return
    record, payload, _ = p.load_package(tmp_path, target, eligible=True)
    extraction = p.decode(before['extraction.json'])
    draft = p.decode(before['draft.json'])
    actual_extraction = p.decode((target / 'extraction.json').read_bytes())
    actual_chunks = p.decode((target / 'chunks.json').read_bytes())
    assert actual_extraction['segments'] == extraction['segments']
    assert actual_extraction['parser_metadata'] == extraction['parser_metadata']
    assert actual_chunks['chunks'][0]['content'] == draft['chunks'][0]['content']
    assert actual_chunks['chunks'][0]['source_refs'] == draft['chunks'][0]['source_refs']
    assert actual_chunks['chunks'][0]['notes'] == draft['chunks'][0]['review_notes']
    assert actual_chunks['excluded'] == draft['excluded']
    assert actual_chunks['warning_decisions'] == draft['warning_decisions']
    assert record['evidence_notes'] == p.decode(before['metadata.json'])['review_notes']
    assert payload['chunks'][0]['source_location']['spans'][0]['location'] == extraction['segments'][0]['location']


@pytest.mark.parametrize('kind', ['ready', 'approved'])
@pytest.mark.parametrize('location', [
    {'kind': 'xlsx', 'row': 2, 'column_start': 1, 'column_end': 2, 'header_row': 1},
    {'kind': 'xlsx', 'sheet': 'Synthetic', 'row': 2, 'column_start': 1, 'column_end': 2,
     'header_row': 1, 'legacy_extra': 'not-supported-in-v2'},
])
def test_legacy_location_validity_does_not_guarantee_v2_compatibility(tmp_path, kind, location):
    from app.ingestion import legacy_packages as legacy
    old, _ = legacy_package(tmp_path, kind, extension='.xlsx', location=location)
    before = p.snapshot_files(old)
    if kind == 'approved':
        legacy.load_approval(tmp_path, old)
    else:
        legacy.load_review(old)
    record = p.decode(before['metadata.json'])
    target = p.workspace(tmp_path) / record['document_id'] / record['revision_id']
    for apply in (False, True):
        with pytest.raises(p.Rejected, match='segment_location_fields'):
            convert(tmp_path, old, apply=apply)
        assert not target.exists()
        assert p.snapshot_files(old) == before


@pytest.mark.parametrize('dry_run', [False, True])
def test_content_only_conversion_does_not_invent_publication_metadata(tmp_path, monkeypatch, dry_run):
    from unittest.mock import Mock
    from app.ingestion import approved_publication as service
    from app.database.runtime import run_async
    old, _ = legacy_package(tmp_path, 'approved', content_only=True)
    before = p.snapshot_files(old)
    target = tmp_path / convert(tmp_path, old, apply=True)['package']
    record, _, _ = p.load_package(tmp_path, target)
    assert record['owner'] is None and record['applicability'] is None
    assert record['access_groups'] == [] and record['indexing_decision'] == 'hold'
    assert record['lifecycle_status'] == 'unknown' and record['evidence_notes'] == []
    model, database = Mock(), Mock()
    monkeypatch.setattr(service, 'model_fingerprint', model)
    monkeypatch.setattr(service, 'session_scope', database)
    with pytest.raises(p.Rejected, match='source_ineligible'):
        run_async(service.publish(tmp_path, target, 'a'*40, model_path=tmp_path, dry_run=dry_run))
    model.assert_not_called()
    database.assert_not_called()
    assert p.snapshot_files(old) == before
