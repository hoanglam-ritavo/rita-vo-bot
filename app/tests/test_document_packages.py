"""Synthetic extraction/review/approval tests: no internal data or model downloads."""
import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.catalog.inventory import inventory
from app.catalog.storage import atomic_write, json_text
from app.catalog.validation import new_catalog
from app.ingestion import document_packages as p
from app.ingestion.document_preparation import prepare
from app.ingestion.document_extractors import config, extract
from app.paths import assets, workspace


def setup_document(root, name='policy.txt', content='Original policy.\n\nSecond paragraph.'):
    source = assets(root) / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
    catalog = new_catalog(inventory(assets(root)))
    for record in catalog['documents']:
        record.update(indexing_decision='include', review_status='reviewed', lifecycle_status='active',
                      owner='Synthetic owner', applicability='Synthetic scope', access_groups=['staff'],
                      review_notes=['Synthetic approval'])
    record = next(r for r in catalog['documents'] if r['source_path'] == name)
    return p.pending(root, record), source


def edit(directory, change):
    path = directory / 'draft.json'
    draft = p.decode(path.read_bytes())
    change(draft)
    path.write_bytes(p.encoded(draft))


def approved(root):
    record, source = setup_document(root)
    review = prepare(root, record)
    edit(review, lambda d: d['chunks'][0].update(content='Human corrected policy.'))
    approval = p.approve(root, review, 'Synthetic reviewer')
    return record, source, review, approval


def test_corrected_content_and_immutable_snapshot(tmp_path):
    record, source, review, approval = approved(tmp_path)
    _, payload, manifest, _ = p.load_approval(tmp_path, approval)
    assert payload['chunks'][0]['content'] == 'Human corrected policy.'
    assert manifest['reviewer'] == 'Synthetic reviewer'
    assert p.decode((review / 'draft.initial.json').read_bytes())['chunks'][0]['content'] == 'Original policy.'
    edit(review, lambda d: d['chunks'][0].update(content='Later edit.'))
    assert p.load_approval(tmp_path, approval)[1] == payload
    assert source.read_text() == 'Original policy.\n\nSecond paragraph.'
    assert not (tmp_path / 'data').exists() and not (tmp_path / 'app/data').exists()


@pytest.mark.parametrize('change,code', [
    ('duplicate', 'duplicate_chunk'), ('missing', 'uncovered_source'), ('unknown', 'unknown_segment'),
    ('empty', 'empty_content'), ('identity', 'identity_mismatch'), ('range', 'reference_range'),
])
def test_invalid_edits_rejected(tmp_path, change, code):
    record, _ = setup_document(tmp_path)
    directory = prepare(tmp_path, record)
    def mutate(draft):
        if change == 'duplicate': draft['chunks'].append(copy.deepcopy(draft['chunks'][0]))
        if change == 'missing': draft['chunks'].pop()
        if change == 'unknown': draft['chunks'][0]['source_refs'][0]['segment_id'] = 'absent'
        if change == 'empty': draft['chunks'][0]['content'] = ' '
        if change == 'identity': draft['identity']['revision_id'] = str(uuid4())
        if change == 'range': draft['chunks'][0]['source_refs'][0]['end'] = 99999
    edit(directory, mutate)
    with pytest.raises(p.Rejected, match=code):
        p.load_review(directory)


def test_split_merge_and_exclude(tmp_path):
    record, _ = setup_document(tmp_path)
    directory = prepare(tmp_path, record)
    def mutate(draft):
        first, second = draft['chunks']
        ref = first['source_refs'][0]
        first['source_refs'] = [{**ref, 'end': 4}]
        first['content'] = 'Part one'
        split = {**copy.deepcopy(first), 'chunk_id': str(uuid4()), 'content': 'Part two',
                 'source_refs': [{**ref, 'start': 4}]}
        draft['chunks'] = [first, split]
        draft['excluded'] = [{'source_ref': second['source_refs'][0], 'reason': 'Synthetic duplicate paragraph'}]
    edit(directory, mutate)
    p.load_review(directory)
    def merge(draft):
        first, second = draft['chunks']
        first['content'] += second['content']
        first['source_refs'] += second['source_refs']
        draft['chunks'] = [first]
    edit(directory, merge)
    p.approve(tmp_path, directory, 'Reviewer')


@pytest.mark.parametrize('file', ['payload.json', 'draft.json', 'extraction.json', 'draft.initial.json', 'source.txt'])
def test_post_approval_tamper(tmp_path, file):
    _, _, _, approval = approved(tmp_path)
    with (approval / file).open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(p.Rejected, match='hash_mismatch'):
        p.load_approval(tmp_path, approval)


def test_source_drift_and_unsealed_package(tmp_path):
    _, source, review, approval = approved(tmp_path)
    with pytest.raises(p.Rejected):
        p.load_approval(tmp_path, review)
    source.write_text('Changed source')
    assert p.load_approval(tmp_path, approval)[1]['chunks']


def test_pending_extract_allowed_approval_requires_metadata(tmp_path):
    record, _ = setup_document(tmp_path)
    metadata_path = record / 'metadata.json'
    metadata = p.decode(metadata_path.read_bytes())
    metadata.update(indexing_decision='hold', review_status='pending')
    metadata_path.write_bytes(p.encoded(metadata))
    directory = prepare(tmp_path, record)
    with pytest.raises(p.Rejected, match='not_approved'):
        p.approve(tmp_path, directory, 'Reviewer')


def test_content_only_approval_preserves_unverified_metadata(tmp_path, monkeypatch, capsys):
    from app.cli import prepare_documents as cli
    pending, source = setup_document(tmp_path)
    record = p.metadata(pending)
    record.update(indexing_decision='hold', review_status='pending', lifecycle_status='unknown',
                  owner=None, applicability=None, access_groups=[])
    (pending / 'metadata.json').write_bytes(p.encoded(record))
    review = prepare(tmp_path, pending)
    before = {f.name: f.read_bytes() for f in review.iterdir()}
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    assert cli.main(['approve', '--review', str(review), '--reviewer', 'Synthetic reviewer',
                     '--content-only', '--json']) == 0
    result = json.loads(capsys.readouterr().out)['results'][0]
    approval = tmp_path / result['package']
    actual, payload, manifest, _ = p.load_approval(tmp_path, approval)
    assert actual == record
    assert manifest['approval_scope'] == 'content'
    assert payload['chunks'] and source.read_bytes() == before['source.txt']
    assert before == {f.name: f.read_bytes() for f in review.iterdir()}
    with pytest.raises(p.Rejected, match='not_approved'):
        p.approve(tmp_path, review, 'Reviewer')
    with (approval / 'metadata.json').open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(p.Rejected, match='hash_mismatch'):
        p.load_approval(tmp_path, approval)


def test_content_only_approval_still_rejects_invalid_spans_and_source_drift(tmp_path):
    pending, source = setup_document(tmp_path)
    review = prepare(tmp_path, pending)
    edit(review, lambda d: d['chunks'][0]['source_refs'][0].update(end=99999))
    with pytest.raises(p.Rejected, match='reference_range'):
        p.approve(tmp_path, review, 'Reviewer', content_only=True)
    review = prepare(tmp_path, pending)
    source.write_text('Changed synthetic source')
    with pytest.raises(p.Rejected, match='source_changed'):
        p.approve(tmp_path, review, 'Reviewer', content_only=True)


def test_markdown_headings_fences_and_unicode(tmp_path):
    raw = '# Policy\n\nTiếng Việt\n\n```python\n  # not a heading\n  x = "001"\n```\n\n## Scope\nApplies here.'
    path = tmp_path / 'sample.md'
    path.write_text(raw, encoding='utf-8')
    segments, warnings, _ = extract(path, config({}))
    code = next(s for s in segments if s['location']['code'])
    assert '  # not a heading' in code['text']
    assert code['location']['headings'] == ['Policy']
    assert segments[-1]['location']['headings'] == ['Policy', 'Scope']
    assert segments[1]['text'] == 'Tiếng Việt'
    assert not warnings


def test_csv_duplicate_headers_multiline_zeroes_and_long_row(tmp_path):
    raw = 'Name,Name,\n"A\nB",001,' + 'x' * 1200 + '\n'
    record, _ = setup_document(tmp_path, 'table.csv', raw)
    directory = prepare(tmp_path, record)
    extraction, _, chunks, _ = p.load_review(directory)
    row = extraction['segments'][1]
    assert row['details']['cells'][1] == '001'
    assert row['location']['line_start'] == 2 and row['location']['line_end'] == 3
    assert 'column_1 [Name]' in row['text'] and 'column_2 [Name]' in row['text']
    assert len(chunks) > 2 and all(c['content'].startswith('Table context: ') for c in chunks)


def test_csv_override_and_warning_decision(tmp_path):
    record, _ = setup_document(tmp_path, 'table.csv', 'Code;Value\n001;Vietnam;extra\n')
    directory = prepare(tmp_path, record, {'delimiter': ';'})
    with pytest.raises(p.Rejected, match='unresolved_warnings'):
        p.load_review(directory)
    edit(directory, lambda d: d['warning_decisions'].update({k: 'Checked extra column' for k in d['warning_decisions']}))
    p.approve(tmp_path, directory, 'Reviewer')


def test_pdf_native_and_blank_page_review(tmp_path, monkeypatch):
    pdf = pytest.importorskip('app.ingestion.pdf')
    record, _ = setup_document(tmp_path, 'sample.pdf', b'synthetic')
    def load(path, *, raw_pages, manifest, **kwargs):
        raw_pages.extend([{'page': 1, 'raw': 'Original\ttext', 'extraction_method': 'native'},
                          {'page': 2, 'raw': '', 'extraction_method': 'ocr'}])
        manifest.update(language_files={}, pages=[])
    monkeypatch.setattr(pdf, 'load_pdf', load)
    directory = prepare(tmp_path, record)
    extraction = p.decode((directory / 'extraction.json').read_bytes())
    assert extraction['segments'][0]['raw'] == 'Original\ttext'
    assert extraction['segments'][0]['text'] == 'Original text'
    with pytest.raises(p.Rejected, match='unresolved_warnings'):
        p.load_review(directory)
    edit(directory, lambda d: d['warning_decisions'].update(w1='Confirmed intentionally blank page'))
    p.approve(tmp_path, directory, 'Reviewer')


def test_xlsx_structure_formula_merge_and_selection(tmp_path):
    openpyxl = pytest.importorskip('openpyxl')
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Main'
    sheet.append(['Title'])
    sheet.merge_cells('A1:B1')
    sheet.append(['Code', 'Code'])
    sheet.append(['001', '=1+1'])
    sheet.row_dimensions[3].hidden = True
    hidden = book.create_sheet('Hidden')
    hidden.sheet_state = 'hidden'
    hidden.append(['A', 'B'])
    hidden.append(['text', 2])
    source = tmp_path / 'book.xlsx'
    book.save(source)
    book.close()
    segments, warnings, _ = extract(source, config({}))
    assert {'hidden_sheet', 'hidden_cell', 'merged_cells', 'formula_without_cached_value'} <= {w['code'] for w in warnings}
    formula_row = next(s for s in segments if s['location']['sheet'] == 'Main' and s['location']['row'] == 3)
    assert formula_row['details']['cells'][0]['value'] == '001'
    assert formula_row['details']['cells'][1]['formula'] == '=1+1'
    assert '[formula result unavailable]' in formula_row['text']
    selected, warnings, _ = extract(source, config({'tables': [{'sheet': 'Main', 'range': 'A2:B3', 'header_row': 2}]}))
    assert len(selected) == 2 and selected[-1]['details']['headers'] == ['Code', 'Code']
    assert any(w['code'] == 'explicit_table_selection' for w in warnings)


def test_cli_batch_partial_and_safe_reporting(tmp_path, monkeypatch, capsys):
    from app.cli import prepare_documents as cli
    setup_document(tmp_path, 'good.txt', 'PRIVATE SYNTHETIC CONTENT')
    setup_document(tmp_path, 'unsupported.doc', b'unsupported')
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    assert cli.main(['extract', '--all']) == 0
    captured = capsys.readouterr().out
    assert 'processed=1' in captured and 'skipped=1' in captured
    assert 'PRIVATE SYNTHETIC CONTENT' not in captured
    with pytest.raises(SystemExit) as exc:
        cli.main(['extract'])
    assert exc.value.code == 2


def test_imports_do_not_load_database_ocr_or_models():
    script = '''import sys
import app.cli.prepare_documents, app.cli.publish_documents
assert not any(k in sys.modules for k in ('sqlalchemy', 'pymupdf', 'sentence_transformers', 'openpyxl'))
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_repeated_extract_never_overwrites_and_other_cwd(tmp_path, monkeypatch):
    record, _ = setup_document(tmp_path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    first = prepare(tmp_path, record)
    original = (first / 'draft.json').read_bytes()
    second = prepare(tmp_path, record)
    assert first != second and (first / 'draft.json').read_bytes() == original
    assert not (elsewhere / 'indexing_workspace').exists()


def test_ocr_failure_never_creates_complete_review(tmp_path, monkeypatch):
    pdf = pytest.importorskip('app.ingestion.pdf')
    record, _ = setup_document(tmp_path, 'sample.pdf', b'synthetic')
    monkeypatch.setattr(pdf, 'load_pdf', Mock(side_effect=RuntimeError('OCR failed')))
    with pytest.raises(RuntimeError):
        prepare(tmp_path, record)
    directories = list((workspace(tmp_path) / 'review' / p.metadata(record)['revision_id']).iterdir())
    directories = [d for d in directories if d.name.startswith('.extracting-')]
    assert directories
    with pytest.raises(p.Rejected, match='incomplete'):
        p.load_review(directories[0])


def test_package_long_paths_and_manifest_path_escape(tmp_path):
    record, _ = setup_document(tmp_path)
    review = prepare(tmp_path, record)
    files = {f.name: f.read_bytes() for f in review.iterdir()}
    parent = workspace(tmp_path) / 'review' / ('long-segment-' * 12) / ('nested-' * 10)
    long_review = p.write_package(parent, str(uuid4()), files)
    assert len(str(long_review)) > 260
    assert p.load_review(long_review)[0]['identity']['revision_id'] == p.metadata(record)['revision_id']
    manifest_path = p.io_path(long_review / 'manifest.json')
    manifest = p.decode(manifest_path.read_bytes())
    manifest['source_file'] = '../source.txt'
    manifest['files']['../source.txt'] = manifest['files'].pop('source.txt')
    manifest_path.write_bytes(p.encoded(manifest))
    with pytest.raises(p.Rejected, match='unsafe_package_path'):
        p.load_review(long_review)


def test_batch_failure_continues_and_validates_known_extensions(tmp_path, monkeypatch, capsys):
    from app.cli import prepare_documents as cli
    from app.ingestion import document_preparation
    from app.catalog.validation import KNOWN_EXTENSIONS
    first, _ = setup_document(tmp_path, 'a.txt', 'Synthetic A')
    setup_document(tmp_path, 'b.txt', 'Synthetic B')
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    original = document_preparation.prepare
    def failing(root, revision, settings):
        if revision == first:
            raise RuntimeError('private content must not enter reports')
        return original(root, revision, settings)
    # setup_document regenerates catalog IDs; use the current first record.
    monkeypatch.setattr(document_preparation, 'prepare', failing)
    assert cli.main(['extract', '--all', '--json']) == 2
    report = json.loads(capsys.readouterr().out)
    assert {r['status'] for r in report['results']} == {'failed', 'processed'}
    assert 'private content' not in str(report)
    assert {'.csv', '.txt', '.md'} <= KNOWN_EXTENSIONS


def test_native_pdf_real_geometry_package(tmp_path):
    pdf = pytest.importorskip('pymupdf')
    source = tmp_path / 'synthetic.pdf'
    with pdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), 'Synthetic native policy')
        document.save(source)
    record, _ = setup_document(tmp_path, 'native.pdf', source.read_bytes())
    review = prepare(tmp_path, record)
    extraction, _, chunks, _ = p.load_review(review)
    assert extraction['segments'][0]['extraction_method'] == 'native'
    assert chunks[0]['source_location']['spans'][0]['location']['page_start'] == 1


def test_review_json_duplicate_keys_and_type_rejected(tmp_path):
    record, _ = setup_document(tmp_path)
    review = prepare(tmp_path, record)
    (review / 'draft.json').write_bytes(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(p.Rejected, match='duplicate_json_key'):
        p.load_review(review)


def test_xlsx_pipeline_approval_after_warning_review(tmp_path):
    openpyxl = pytest.importorskip('openpyxl')
    book = openpyxl.Workbook()
    book.active.append(['Code', 'Value'])
    book.active.append(['001', '=1+1'])
    source = tmp_path / 'synthetic.xlsx'
    book.save(source)
    book.close()
    record, _ = setup_document(tmp_path, 'table.xlsx', source.read_bytes())
    review = prepare(tmp_path, record)
    edit(review, lambda d: d['warning_decisions'].update({k: 'Formula remains explicitly unavailable' for k in d['warning_decisions']}))
    approval = p.approve(tmp_path, review, 'Reviewer')
    payload = p.load_approval(tmp_path, approval)[1]
    assert any('001' in chunk['content'] for chunk in payload['chunks'])
