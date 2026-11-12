"""Process locks and recoverable directory replacement for current packages."""
from contextlib import contextmanager
import os
from uuid import uuid4

from app.ingestion import document_packages as p


def transaction(root, document_id):
    p.uuid(document_id)
    return p.io_path(root / '.tmp/package-transactions' / document_id)


@contextmanager
def lock(root, key):
    """Nonblocking OS lock, released even when the process exits unexpectedly."""
    if key != 'workspace':
        p.uuid(key)
    path = p.io_path(root / '.tmp/package-locks' / (key + '.lock'))
    p.reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise p.Rejected('package_busy') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def available(root, document_id):
    p.require(not transaction(root, document_id).exists(), 'package_recovery_required')


def workspace_available(root):
    # A crash can leave the current directory absent. Never initialize another
    # identity for its source just because the displaced package is not scanned.
    pending = p.io_path(root / '.tmp/package-transactions')
    p.reject_links(pending)
    p.require(not pending.exists() or not any(pending.iterdir()), 'package_recovery_required')


@contextmanager
def reading(directory):
    """Guard current packages; legacy converter inputs have other layouts."""
    directory = p.io_path(directory)
    if directory.parent.name.casefold() != 'chunks':
        yield
        return
    root = directory.parent.parent.parent
    with lock(root, directory.name):
        available(root, directory.name)
        yield


def archive(root, document_id):
    dest = p.io_path(root / '.tmp/package-backups' / (document_id + '-' + str(uuid4())))
    p.reject_links(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    transaction(root, document_id).rename(dest)
    return dest


def rollback(root, document_id):
    """Caller holds the document lock. Retain all displaced bytes in backups."""
    txn = transaction(root, document_id)
    p.reject_links(txn)
    p.require(txn.exists(), 'no_package_transaction')
    target = p.io_path(p.workspace(root) / document_id)
    previous = txn / 'previous'
    for path in (target, previous, txn / 'failed'):
        p.reject_links(path)
    if previous.exists():
        if target.exists():
            target.rename(txn / 'failed')
        previous.rename(target)
    return archive(root, document_id)


def replace(root, document_id, files, *, before_finalize):
    """Caller holds document lock; retain old data and recover interrupted swaps."""
    available(root, document_id)
    txn = transaction(root, document_id)
    p.reject_links(txn)
    txn.mkdir(parents=True)
    target = p.io_path(p.workspace(root) / document_id)
    p.reject_links(target)
    try:
        stage = p.write_package(txn, 'staged', files)
        p.require(p.snapshot_files(stage) == files, 'staging_changed')
        before_finalize()
        target.rename(txn / 'previous')
        stage.rename(target)
        # Read directly while holding the lock and transaction marker.
        p.require(p._snapshot_files(target) == files, 'replacement_changed')
        archive(root, document_id)
    except Exception:
        rollback(root, document_id)
        raise
    return p.workspace(root) / document_id


def recover(root, directory):
    directory = p.package_directory(root, directory)
    with lock(root, 'workspace'), lock(root, directory.name):
        return rollback(root, directory.name)
