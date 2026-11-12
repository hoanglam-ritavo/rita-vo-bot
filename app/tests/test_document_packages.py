"""V2 structural/provenance checks using only synthetic documents."""
import copy
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
import pytest
from app.ingestion import document_packages as p
from app.cli import prepare_documents as cli
from package_fixtures import ready


def edit(path, change, file='chunks.json'):
    data = p.decode((path / file).read_bytes())
    change(data)
    (path / file).write_bytes(p.encoded(data))


def test_ready_package_and_source_drift(tmp_path):
    path, source = ready(tmp_path)
    record, payload, files = p.load_package(tmp_path, path, eligible=True)
    assert record['owner'] is None and record['evidence_notes'] == []
    assert len(files) == 4 and payload['chunks'][0]['chunk_index'] == 0
    assert payload['chunks'][0]['source_location']['spans'][0]['location']['line_start'] == 1
    source.write_bytes(b'Changed original')
    assert p.load_package(tmp_path, path)[1] == payload
    assert not {'manifest.json', 'payload.json', 'draft.json'} & files.keys()


@pytest.mark.parametrize('mutate,code', [
    (lambda d: d['chunks'].pop(), 'uncovered_source'),
    (lambda d: d['chunks'][0].update(content=' '), 'empty_content'),
    (lambda d: d['chunks'][0]['source_refs'][0].update(segment_id='absent'), 'unknown_segment'),
    (lambda d: d['chunks'][0]['source_refs'][0].update(end=999), 'reference_range'),
    (lambda d: d['chunks'][0]['source_refs'][0].update(start=True), 'reference_range'),
    (lambda d: d.update(identity={}), 'draft_fields'),
    (lambda d: d.update(schema_version=1), 'draft_version'),
    (lambda d: d['chunks'][0].update(chunk_id=str(uuid4())), 'chunk_fields'),
])
def test_invalid_chunks(tmp_path, mutate, code):
    path, _ = ready(tmp_path)
    edit(path, mutate)
    with pytest.raises(p.Rejected, match=code):
        p.load_package(tmp_path, path)


def test_split_exclude_and_overlapping_spans(tmp_path):
    path, _ = ready(tmp_path)
    def split(d):
        first, second = d['chunks']
        extra = copy.deepcopy(first)
        first['source_refs'][0]['end'] = 8
        extra['source_refs'][0]['start'] = 5
        d['chunks'] = [first, extra]
        d['excluded'] = [{'source_ref': second['source_refs'][0], 'reason': 'Synthetic omission'}]
    edit(path, split)
    assert len(p.load_package(tmp_path, path)[1]['chunks']) == 2


def test_warning_resolution(tmp_path):
    path, _ = ready(tmp_path, warnings=[{'warning_id': 'w1', 'code': 'uncertain', 'location': {}}])
    with pytest.raises(p.Rejected, match='unresolved_warnings'):
        p.load_package(tmp_path, path)
    edit(path, lambda d: d['warning_decisions'].update(w1='Compared synthetic source'))
    p.load_package(tmp_path, path)


@pytest.mark.parametrize('file', ['metadata.json', 'extraction.json', 'chunks.json', 'source.txt'])
def test_missing_file(tmp_path, file):
    path, _ = ready(tmp_path)
    (path / file).unlink()
    with pytest.raises(p.Rejected, match='missing|incomplete'):
        p.load_package(tmp_path, path)


def test_identity_snapshot_and_json_safety(tmp_path):
    path, _ = ready(tmp_path)
    edit(path, lambda d: d.update(revision_id=str(uuid4())), 'metadata.json')
    with pytest.raises(p.Rejected, match='identity_changed'):
        p.load_package(tmp_path, path)
    edit(path, lambda d: d.update(revision_id=path.name), 'metadata.json')
    (path / 'source.txt').write_bytes(b'wrong snapshot')
    with pytest.raises(p.Rejected, match='snapshot_mismatch'):
        p.load_package(tmp_path, path)
    for data in (b'{"x":1,"x":2}', b'{"x":NaN}'):
        with pytest.raises(p.Rejected):
            p.decode(data)


@pytest.mark.parametrize('field,value', [('owner', None), ('evidence_notes', [])])
def test_optional_metadata(tmp_path, field, value):
    path, _ = ready(tmp_path)
    edit(path, lambda d: d.update({field: value}), 'metadata.json')
    p.load_package(tmp_path, path, eligible=True)


@pytest.mark.parametrize('field,value', [('applicability', None), ('access_groups', []),
    ('lifecycle_status', 'unknown'), ('effective_from', '2099-01-01'), ('effective_to', '2000-01-01'),
    ('review_status', 'reviewed'), ('schema_version', 1)])
def test_invalid_metadata(tmp_path, field, value):
    path, _ = ready(tmp_path)
    edit(path, lambda d: d.update({field: value}), 'metadata.json')
    with pytest.raises(p.Rejected, match='metadata_invalid'):
        p.load_package(tmp_path, path, eligible=True)


def test_initialize_revise_and_cli(tmp_path, monkeypatch, capsys):
    path, source = ready(tmp_path)
    assert p.initialize(tmp_path, 'policy.txt') == []
    source.write_bytes(b'New source version')
    with pytest.raises(p.Rejected, match='source_changed'):
        p.initialize(tmp_path)
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    assert cli.main(['revise', '--package', str(path), '--source', 'policy.txt', '--json']) == 0
    result = json.loads(capsys.readouterr().out)['results'][0]
    new = tmp_path / result['package']
    assert new.parent == path.parent and new != path
    record = p.metadata(new)
    assert record['relations'] == [] and record['indexing_decision'] == 'hold'
    assert cli.main(['validate', '--package', str(new), '--json']) == 1
    assert 'incomplete_package' in capsys.readouterr().out
    assert cli.main(['validate', '--package', str(path), '--json']) == 0


@pytest.mark.parametrize('args', [['approve', '--review', 'absent', '--reviewer', 'x'],
    ['extract', '--all'], ['validate', '--review=absent'], ['revise', '--review', 'absent']])
def test_retired_cli_no_io(tmp_path, monkeypatch, args):
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Retired command accessed files')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    assert cli.main(args) == 2


def test_paths_and_unknown_files(tmp_path):
    path, _ = ready(tmp_path)
    with pytest.raises(p.Rejected, match='outside_workspace'):
        p.load_package(tmp_path, tmp_path / 'elsewhere')
    with pytest.raises(p.Rejected, match='unsafe_package_path'):
        p.read_file(path, '../metadata.json')
    (path / 'extra.json').write_bytes(b'{}')
    with pytest.raises(p.Rejected, match='package_files'):
        p.load_package(tmp_path, path)


def test_atomic_package_write(tmp_path):
    target = p.write_package(tmp_path, 'existing', {'sample': b'original'})
    with pytest.raises(p.Rejected, match='package_exists'):
        p.write_package(tmp_path, 'existing', {'sample': b'new'})
    assert (target / 'sample').read_bytes() == b'original'
    with pytest.raises(p.Rejected, match='unsafe_package_path'):
        p.write_package(tmp_path, 'failed', {'sample': b'partial', '../escape': b'invalid'})
    assert not (tmp_path / 'failed').exists()


def test_imports_are_database_and_model_free():
    script = 'import sys; import app.cli.prepare_documents, app.cli.publish_documents; assert not any(k in sys.modules for k in ("sqlalchemy", "sentence_transformers", "openpyxl", "pymupdf"))'
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('extension,location', [
    ('.pdf', {'kind': 'pdf', 'page_start': 1, 'page_end': 2}),
    ('.csv', {'kind': 'csv', 'line_start': 1, 'line_end': 2}),
    ('.xlsx', {'kind': 'xlsx', 'sheet': 'Policy', 'row': 2, 'column_start': 1, 'column_end': 3, 'header_row': 1}),
    ('.md', {'kind': 'text', 'line_start': 1, 'line_end': 2}),
])
def test_formats_and_ordered_locations(tmp_path, extension, location):
    path, source = ready(tmp_path)
    record = p.metadata(path)
    record.update(source_path='policy' + extension, extension=extension)
    (path / 'metadata.json').write_bytes(p.encoded(record))
    (path / 'source.txt').rename(path / ('source' + extension))
    def locate(d):
        for segment in d['segments']:
            segment['location'] = dict(location)
            segment['extraction_method'] = 'ocr'
    edit(path, locate, 'extraction.json')
    assert p.load_package(tmp_path, path)[1]['chunks'][0]['extraction_method'] == 'ocr'
    key = 'page_end' if extension == '.pdf' else 'column_end' if extension == '.xlsx' else 'line_end'
    edit(path, lambda d: d['segments'][0]['location'].update({key: 0}), 'extraction.json')
    with pytest.raises(p.Rejected, match='segment_location_range'):
        p.load_package(tmp_path, path)


def test_long_repository_path_and_other_cwd(tmp_path, monkeypatch):
    root = p.io_path(tmp_path / ('long-segment-' * 10) / ('nested-' * 12))
    monkeypatch.chdir(tmp_path.parent)
    path, _ = ready(root)
    assert len(str(path)) > 260
    assert p.load_package(root, path)[1]['chunks']
    assert p.package_paths(root) == [path]


def test_unsupported_format_stays_initialized(tmp_path):
    source = tmp_path / 'app/assets/example.docx'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'synthetic')
    path, = p.initialize(tmp_path, 'example.docx')
    assert (path / 'source.docx').read_bytes() == b'synthetic'
    with pytest.raises(p.Rejected, match='unsupported_format'):
        p.load_package(tmp_path, path)
