"""Workspace boundaries and relocation use synthetic local files only."""
import json

from app.cli import review, asset_catalog
from app.paths import assets, workspace
from scripts import migrate_indexing_workspace as relocation


def test_default_review_and_catalog_are_independent_of_cwd(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    assets(root).mkdir(parents=True)
    (assets(root) / 'synthetic.pdf').write_bytes(b'synthetic')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(review, 'workspace', lambda: workspace(root))
    monkeypatch.setattr(asset_catalog, 'REPOSITORY_ROOT', root)
    assert review.create_parser().parse_args(['input.pdf']).output == workspace(root) / 'review/chunks.jsonl'
    explicit = review.create_parser().parse_args([
        'input.pdf', '--output', 'custom.jsonl', '--review-dir', 'inspection', '--tessdata', 'ocr'])
    assert not explicit.output.is_absolute()
    assert str(explicit.review_dir) == 'inspection'
    assert str(explicit.tessdata) == 'ocr'
    assert asset_catalog.main(['init']) == 0
    assert (workspace(root) / 'catalog/documents.json').is_file()
    for base in (root, elsewhere):
        assert not (base / 'data').exists()
        assert not (base / 'app/data').exists()
    assert not (workspace(root) / 'approved').exists()


def test_relocation_preserves_bytes_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(relocation, 'ROOT', tmp_path)
    old = tmp_path / 'app/data'
    for name in ('catalog/documents.json', 'extracted/chunks.jsonl', 'reports/asset-sync/run/report.json'):
        path = old / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original synthetic bytes')
    assert relocation.main() == 0
    assert not old.exists()
    assert (workspace(tmp_path) / 'catalog/documents.json').read_bytes() == b'original synthetic bytes'
    archive = workspace(tmp_path) / 'archive/pre-reorganization'
    assert (archive / 'extracted/chunks.jsonl').read_bytes() == b'original synthetic bytes'
    result = json.loads(next((archive / 'relocation').glob('*/result.json')).read_text())
    assert len(result['files']) == 3
    assert all(item['status'] == 'moved' for item in result['files'])
    assert not result['blocked']


def test_relocation_never_overwrites_existing_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(relocation, 'ROOT', tmp_path)
    old = tmp_path / 'app/data/catalog/documents.json'
    new = workspace(tmp_path) / 'catalog/documents.json'
    old.parent.mkdir(parents=True)
    new.parent.mkdir(parents=True)
    old.write_bytes(b'old')
    new.write_bytes(b'new')
    assert relocation.main() == 1
    assert old.read_bytes() == b'old'
    assert new.read_bytes() == b'new'
