import json
import os
import tempfile
from pathlib import Path
from app.catalog.inventory import CatalogError, reject_links


def atomic_write(path: Path, text: str, *, create_only: bool = False) -> None:
    """Publish a complete file; init uses a hard link for atomic no-clobber."""
    reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_links(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)



def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'



def load_catalog(path: Path) -> object:
    reject_links(path)
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CatalogError('Catalog is not valid UTF-8 JSON.') from exc
