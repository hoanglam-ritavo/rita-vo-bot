"""Page-local text chunking with word-aware overlap."""
import re

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def chunk_spans(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[tuple[int, int]]:
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("Invalid chunk size or overlap")

    chunks = []
    start = 0
    covered_until = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Prefer paragraph, line, then word boundaries, while making progress.
        if end < len(text):
            for pattern in (r"\n\s*\n", r"\n", r"\s+"):
                # Do not repeatedly split at a boundary inside the overlap.
                lower = max(start + 1, covered_until + 1)
                boundaries = list(re.finditer(pattern, text[lower:end]))
                if boundaries:
                    end = lower + boundaries[-1].start()
                    break

        raw = text[start:end]
        chunk = raw.strip()

        if chunk:
            chunks.append((start + len(raw) - len(raw.lstrip()), end - len(raw) + len(raw.rstrip())))
        covered_until = end
        while covered_until < len(text) and text[covered_until].isspace():
            covered_until += 1

        if end >= len(text):
            break

        # A hard split inside an oversized token has no word-safe overlap.
        if not text[end].isspace():
            start = end
            continue
        candidates = [m.start() + start for m in re.finditer(r"(?<!\S)\S+", text[start:end])]
        start = next((pos for pos in candidates if pos > start and pos >= end - overlap), end)
        while start < len(text) and text[start].isspace():
            start += 1

    return chunks


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    return [text[start:end] for start, end in chunk_spans(text, chunk_size, overlap)]


def build_chunks(pages: list[dict]) -> list[dict]:
    result = []

    for page in pages:
        texts = chunk_text(page["text"])

        for text in texts:
            result.append({
                "chunk_index": len(result),
                "page": page["page"],
                "content": text,
                "source": page["source"],
                "extraction_method": page["extraction_method"],
            })

    return result
