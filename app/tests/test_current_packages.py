"""Current-package lifecycle, recovery, migration and Git portability."""
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.ingestion import document_packages as p
from app.ingestion import package_storage as storage
from scripts.migrate_chunk_layout import convert
from package_fixtures import ready, revised_package, set_metadata


def test_revision_requires_intent_and_accumulates_ancestors(tmp_path):
    package, source = ready(tmp_path)
    first = p.metadata(package)['revision_id']
    before = p.snapshot_files(package)
    with pytest.raises(p.Rejected, match='supersedes_current_required'):
        p.revise(tmp_path, package, 'policy.txt')
    with pytest.raises(p.Rejected, match='source_unchanged'):
        p.revise(tmp_path, package, 'policy.txt', supersedes_current=True)
    with pytest.raises(p.Rejected, match='source_path_change_unsupported'):
        p.revise(tmp_path, package, 'renamed.txt', supersedes_current=True)
    assert p.snapshot_files(package) == before
    set_metadata(package, relations=[{'type': 'supplements', 'revision_id': str(uuid4())}])
    assert revised_package(tmp_path, package) == package
    second = p.metadata(package)['revision_id']
    source.write_bytes(b'Third source')
    assert p.revise(tmp_path, package, 'policy.txt', supersedes_current=True) == package
    record = p.metadata(package)
    assert {r['revision_id'] for r in record['relations']} == {first, second}
    assert all(r['type'] == 'supersedes' for r in record['relations'])
    assert set(p.snapshot_files(package)) == {'metadata.json', 'source.txt'}
    assert p.package_paths(tmp_path) == [package]
    assert record['indexing_decision'] == 'hold'


def test_duplicate_source_rejected_even_for_explicit_package(tmp_path):
    first, _ = ready(tmp_path)
    second, _ = ready(tmp_path, name='second.txt')
    set_metadata(second, source_path='POLICY.txt')
    for operation in (lambda: p.package_paths(tmp_path), lambda: p.load_package(tmp_path, first),
                      lambda: p.initialize(tmp_path, 'policy.txt')):
        with pytest.raises(p.Rejected, match='duplicate_source_package'):
            operation()


@pytest.mark.parametrize('crash', [False, True])
def test_swap_failure_and_interrupted_process_recovery(tmp_path, monkeypatch, crash):
    package, source = ready(tmp_path)
    original = p.snapshot_files(package)
    source.write_bytes(b'Changed')
    rename = Path.rename

    def fail_stage(self, target):
        if self.name == 'staged':
            raise KeyboardInterrupt() if crash else OSError('synthetic swap failure')
        return rename(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'rename', fail_stage)
        with pytest.raises(KeyboardInterrupt if crash else OSError):
            p.revise(tmp_path, package, 'policy.txt', supersedes_current=True)
    if crash:
        with pytest.raises(p.Rejected, match='recovery_required'):
            p.snapshot_files(package)
        with pytest.raises(p.Rejected, match='recovery_required'):
            p.initialize(tmp_path, 'policy.txt')
        with pytest.raises(p.Rejected, match='recovery_required'):
            p.package_paths(tmp_path)
        storage.recover(tmp_path, package)
    assert p.snapshot_files(package) == original
    assert not storage.transaction(tmp_path, package.name).exists()


def test_initial_inventory_rejects_case_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'inventory', lambda _: {'policy.txt': {}, 'POLICY.txt': {}})
    with pytest.raises(p.Rejected, match='duplicate_source_path'):
        p.initialize(tmp_path)


def test_other_process_cannot_read_during_replacement(tmp_path):
    package, _ = ready(tmp_path)
    script = '''
import sys
from pathlib import Path
from app.ingestion import document_packages as p
try:
    p.snapshot_files(Path(sys.argv[1]))
except p.Rejected as exc:
    assert str(exc) == 'package_busy'
else:
    raise AssertionError('Concurrent reader was not rejected')
'''
    with storage.lock(tmp_path, package.name):
        result = subprocess.run([sys.executable, '-c', script, str(package)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def old_layout(root):
    package, _ = ready(root)
    original = p.snapshot_files(package)
    revision = p.metadata(package)['revision_id']
    temporary = root / 'relocation'
    package.rename(temporary)
    package.mkdir()
    temporary.rename(package / revision)
    return package, revision, original


def test_layout_preview_conversion_and_backup(tmp_path):
    document, revision, original = old_layout(tmp_path)
    with pytest.raises(p.Rejected, match='legacy_layout_convert_explicitly'):
        p.package_paths(tmp_path)
    assert convert(tmp_path, document)['code'] == 'layout_preview'
    assert not storage.transaction(tmp_path, document.name).exists()
    assert p.snapshot_files(document / revision) == original
    assert convert(tmp_path, document, apply=True)['code'] == 'layout_migrated'
    assert p.snapshot_files(document) == original
    backup, = (tmp_path / '.tmp/package-backups').iterdir()
    assert p.snapshot_files(backup / 'previous' / revision) == original
    assert convert(tmp_path, document, apply=True)['code'] == 'already_current'


def test_multiple_old_revisions_need_explicit_selection(tmp_path):
    document, revision, original = old_layout(tmp_path)
    other = str(uuid4())
    files = dict(original)
    record = p.decode(files['metadata.json'])
    record['revision_id'] = other
    files['metadata.json'] = p.encoded(record)
    p.write_package(document, other, files)
    with pytest.raises(p.Rejected, match='select_revision_explicitly'):
        convert(tmp_path, document, apply=True)
    convert(tmp_path, document, revision_id=other, apply=True)
    assert p.snapshot_files(document) == files
    assert p.metadata(document)['relations'] == []
    backup, = (tmp_path / '.tmp/package-backups').iterdir()
    assert p.snapshot_files(backup / 'previous' / revision) == original


def test_git_allowlist_and_checkout_preserve_bytes(tmp_path):
    if shutil.which('git') is None:
        pytest.skip('Git is unavailable')
    repo = tmp_path / 'repo'
    repo.mkdir()
    project = Path(__file__).resolve().parents[2]
    for filename in ('.gitignore', '.gitattributes'):
        (repo / filename).write_bytes((project / filename).read_bytes())
    package, _ = ready(repo)
    # CRLF in JSON and arbitrary binary bytes must survive core.autocrlf.
    metadata = package / 'metadata.json'
    metadata.write_bytes(metadata.read_bytes().replace(b'\n', b'\r\n'))
    (package / 'source.txt').write_bytes(b'line\r\nline\n\x00\xff')
    original = p.snapshot_files(package)
    extras = [package / 'notes.log', package / 'old-revision/chunks.json',
              repo / 'app/chunks/.staging-test/metadata.json',
              repo / 'app/chunks/approved/metadata.json']
    for extra in extras:
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_bytes(b'ignored')

    def git(*args, cwd=repo):
        result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout

    git('init', '-q')
    git('config', 'core.autocrlf', 'true')
    git('add', '.gitignore', '.gitattributes', 'app/chunks')
    tracked = git('ls-files', 'app/chunks').splitlines()
    assert set(tracked) == {f'app/chunks/{package.name}/{name}' for name in original}
    # Commit only synthetic fixtures in the isolated test repository.
    git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic package')
    clone = tmp_path / 'clone'
    git('-c', 'core.autocrlf=true', 'clone', '-q', '--no-local', 'repo', 'clone', cwd=tmp_path)
    copied = clone / 'app/chunks' / package.name
    assert p.snapshot_files(copied) == original
