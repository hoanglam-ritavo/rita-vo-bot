"""Synthetic v3 schema, conversion and provenance boundaries."""

import pytest
from package_fixtures import ready
from src.ingestion import document_packages as p


def structured_package(root):
    path, source = ready(root)
    for name in ('metadata.json', 'extraction.json', 'chunks.json'):
        data = p.decode((path / name).read_bytes())
        data['schema_version'] = 3
        if name == 'chunks.json':
            for i, c in enumerate(data['chunks']):
                c.update(id=f'c{i}', role='table' if i == 0 else 'detail')
            refs = data['chunks'][0]['source_refs']
            data['items'] = [dict(id=f'i{i}', label=str(i+1), name=f'Item {i}', parent_id=None,
                order=i, chunk_ids=[f'c{i}'], source_refs=refs) for i in range(2)]
            data['scopes'] = [dict(id='all', name='Synthetic table', description='Listed cash per case',
                item_ids=['i0', 'i1'], chunk_ids=['c0'], coverage='complete', metric='cash',
                operations=['list', 'count', 'min', 'max', 'compare'], comparison='same_basis', source_refs=refs)]
            data['values'] = [dict(id=f'v{i}', item_id=f'i{i}', name='cash', type='decimal',
                value='100.01', unit='USD', basis='case', original='100.01 USD per case',
                conditions='Eligible staff', ambiguous=False, source_refs=refs) for i in range(2)]
        (path / name).write_bytes(p.encoded(data))
    return path, source


def test_v3_load_preserves_identity_and_structure(tmp_path):
    path, source = structured_package(tmp_path)
    record, payload, files = p.load_package(tmp_path, path)
    assert record['schema_version'] == 3
    assert files['source.txt'] == source.read_bytes()
    assert payload['chunks'][0]['author_id'] == 'c0'
    assert payload['structured']['values'][0]['value'] == '100.01'
    before = p.canonical_hash(payload)
    data = p.decode(files['chunks.json'])
    data['values'][0]['value'] = '100.02'
    (path / 'chunks.json').write_bytes(p.encoded(data))
    assert p.canonical_hash(p.load_package(tmp_path, path)[1]) != before


@pytest.mark.parametrize('change', [
    lambda d: d['chunks'][1].update(id='c0'),
    lambda d: d['chunks'][0].update(role=[]),
    lambda d: d['items'][0].update(parent_id='i1'),
    lambda d: d['scopes'][0].update(item_ids=['missing']),
    lambda d: d['values'][0].update(item_id=[]),
    lambda d: d['values'][0].update(value=100),
    lambda d: d['values'][0].update(value='NaN'),
    lambda d: d['values'][0].update(unit=None),
    lambda d: d['values'][0]['source_refs'][0].update(end=999),
])
def test_v3_invalid_data(tmp_path, change):
    path, _ = structured_package(tmp_path)
    data = p.decode((path / 'chunks.json').read_bytes())
    change(data)
    if data['items'][0]['parent_id'] == 'i1':
        data['items'][1]['parent_id'] = 'i0'
    (path / 'chunks.json').write_bytes(p.encoded(data))
    with pytest.raises(p.Rejected):
        p.load_package(tmp_path, path)


def test_converter_preview_and_apply_preserve_source_and_revision(tmp_path):
    from scripts.convert_structured_package import convert
    path, source = ready(tmp_path)
    original = p.snapshot_files(path)
    assert convert(tmp_path, path)['code'] == 'preview_scaffold'
    assert p.snapshot_files(path) == original
    assert convert(tmp_path, path, True)['code'] == 'converted_scaffold'
    record, payload, files = p.load_package(tmp_path, path)
    assert record['revision_id'] == p.decode(original['metadata.json'])['revision_id']
    assert files['source.txt'] == original['source.txt'] == source.read_bytes()
    assert payload['structured'] == dict(scopes=[], items=[], values=[])
    assert convert(tmp_path, path, True)['code'] == 'already_v3'


@pytest.mark.parametrize('role', ['definition', 'rule', 'procedure', 'inventory', 'detail', 'table', 'administrative'])
def test_all_roles_and_typed_values(tmp_path, role):
    path, _ = structured_package(tmp_path)
    data = p.decode((path / 'chunks.json').read_bytes())
    data['chunks'][0]['role'] = role
    for kind, value in [('boolean', True), ('date', '2026-11-13'), ('text', 'Synthetic value')]:
        data['values'][0].update(type=kind, value=value)
        (path / 'chunks.json').write_bytes(p.encoded(data))
        assert p.load_package(tmp_path, path)[1]['structured']['values'][0]['value'] == value
