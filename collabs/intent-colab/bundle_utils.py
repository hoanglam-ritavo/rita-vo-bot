"""Repository-side kit maintenance helpers; not needed by the exported trainer."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SNAPSHOT_ALGORITHM = 'sha256-utf8-lf-v1'


def text_hash(path):
    """Source identity independent of checkout CRLF translation (not model identity)."""
    return hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def source_files():
    paths = sorted(p for p in HERE.rglob('*') if p.is_file()
                   and '__pycache__' not in p.parts
                   and p.suffix in ('.py', '.part', '.md', '.txt', '.json', '.ipynb'))
    paths.append(ROOT / 'src/tests/test_intent_colab.py')
    return paths


def verification_identity():
    # Include upstream source as well, so stale receipts cannot bless source drift.
    paths = source_files() + [ROOT / p for p in (
        'src/intent/runtime.py', 'src/intent/dataset.py', 'src/intent/training.py',
        'src/intent/preparation.py', 'src/retrieval/reranking.py', 'src/ingestion/embeddings.py')]
    return {p.relative_to(ROOT).as_posix(): text_hash(p) for p in paths}


def private_output(path):
    path = Path(path)
    # Refuse links before resolving; no changes to user directory permissions.
    from stat import S_ISLNK
    for part in (*reversed(path.absolute().parents), path.absolute()):
        if part.exists():
            info = part.lstat()
            if S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('linked_output_rejected')
    result = path.resolve()
    private = (ROOT / '.tmp').resolve()
    if result == private or not result.is_relative_to(private):
        raise ValueError('output_must_be_under_repository_tmp')
    return result


def check_or_write(outputs, write):
    mismatches = [p.relative_to(ROOT).as_posix() for p, text in outputs.items()
                  if not p.is_file() or p.read_text(encoding='utf-8') != text]
    if write:
        for path, text in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='\n')
    return {'status': 'written' if write else 'drift' if mismatches else 'current',
            'files': [p.relative_to(ROOT).as_posix() for p in outputs],
            'mismatches': [] if write else mismatches}, 0 if write or not mismatches else 1


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
