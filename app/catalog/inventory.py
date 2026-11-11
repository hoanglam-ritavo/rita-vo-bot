"""Portable source paths, link rejection and content inventory."""
import hashlib
import os
import re
import stat
from pathlib import Path, PureWindowsPath


class CatalogError(ValueError):
    """A safe, operational error message for the CLI."""


def reject_links(path: Path) -> None:
    """Inspect every existing component without resolving away links/junctions."""
    absolute = Path(os.path.abspath(path))
    for component in (*reversed(absolute.parents), absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
        ):
            raise CatalogError('Link or reparse point rejected.')


def safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or '\\' in value:
        return False
    parts = value.split('/')
    return (not PureWindowsPath(value).drive and not value.startswith('/')
            and all(part not in {'', '.', '..'} and not part.endswith((' ', '.'))
                    and not re.search(r'[<>:"|?*\x00-\x1f]', part) for part in parts))


def file_fingerprint(path: Path) -> dict:
    reject_links(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise CatalogError('Source changed while being read; retry after updates stop.')
    return {'sha256': digest, 'size_bytes': after.st_size,
            'extension': path.suffix.lower()}


def inventory(root: Path) -> dict:
    reject_links(root)
    if not root.is_dir():
        raise CatalogError('Assets directory is unavailable.')
    result = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            reject_links(path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                if path.name.lower() == 'thumbs.db':
                    continue
                relative = path.relative_to(root).as_posix()
                if not safe_relative(relative):
                    raise CatalogError('Source filename is not portable; review required.')
                result[relative] = file_fingerprint(path)
            else:
                raise CatalogError('Non-regular source entry rejected.')
    return dict(sorted(result.items()))
