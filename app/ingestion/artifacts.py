"""Atomic publication of extracted JSONL artifacts."""
import json
import os
import tempfile
from pathlib import Path


def save_jsonl(chunks: list[dict], output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                         prefix=".ocr-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            for chunk in chunks:
                file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
