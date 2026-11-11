
import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import pymupdf


MODEL_NAME = "intfloat/multilingual-e5-small"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def sha256_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def parse_pages(value: str) -> list[tuple[int, int]]:
    """Keep ranges compact until the document's page count is known."""
    ranges = []
    for item in value.split(","):
        match = re.fullmatch(r"\s*([1-9][0-9]*)(?:-([1-9][0-9]*))?\s*", item)
        if not match:
            raise argparse.ArgumentTypeError("Pages must be positive numbers or ranges, e.g. 1,2-3")
        first = int(match[1])
        last = int(match[2] or match[1])
        if first > last:
            raise argparse.ArgumentTypeError("Page ranges must be ascending")
        ranges.append((first, last))
    return ranges


def validate_ocr_options(mode, region):
    if mode not in ("auto", "always"):
        raise ValueError("OCR mode must be auto or always")
    if region is not None:
        if mode != "always":
            raise ValueError("--ocr-region requires --ocr-mode always")
        if (len(region) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in region)
                or region[0] >= region[2] or region[1] >= region[3]):
            raise ValueError("OCR region must satisfy 0 <= LEFT < RIGHT <= 1 and 0 <= TOP < BOTTOM <= 1")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")).strip()


def ocr_page(page, *, language, dpi, tessdata, region, image_path):
    """Normalize rotation in memory; crop in upright, physical page coordinates."""
    if page.rotation:
        page.remove_rotation()

    def recognize(target):
        if image_path is not None:
            # Match the RGB, opaque full-page raster used by integrated OCR.
            target.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False).save(image_path)
        textpage = target.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=tessdata)
        return target.get_text("text", sort=True, textpage=textpage)

    if region is None:
        return recognize(page)
    rect = page.rect
    left, top, right, bottom = region
    clip = pymupdf.Rect(rect.x0 + left * rect.width, rect.y0 + top * rect.height,
                        rect.x0 + right * rect.width, rect.y0 + bottom * rect.height)
    pixmap = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csRGB, alpha=False)
    with pymupdf.open() as cropped:
        target = cropped.new_page(width=clip.width, height=clip.height)
        target.insert_image(target.rect, pixmap=pixmap, keep_proportion=False)
        return recognize(target)


# STEP 1: PDF Loader
def resolve_tessdata(tessdata: Path | None, language: str) -> str:
    languages = language.split("+")
    if not all(re.fullmatch(r"[A-Za-z0-9_]+", item) for item in languages):
        raise ValueError("OCR languages must be codes separated by '+', e.g. vie+eng")
    configured = str(tessdata) if tessdata is not None else os.environ.get("TESSDATA_PREFIX")
    try:
        directory = Path(pymupdf.get_tessdata(configured))
    except Exception:
        raise RuntimeError("OCR language data unavailable. Set --tessdata or TESSDATA_PREFIX to a tessdata directory.") from None
    missing = [f"{item}.traineddata" for item in languages if not (directory / f"{item}.traineddata").is_file()]
    if missing:
        raise RuntimeError(f"Missing OCR language files: {', '.join(missing)}. Configure --tessdata or TESSDATA_PREFIX.")
    return str(directory)


def load_pdf(pdf_path: Path, *, ocr_language: str = "vie+eng", ocr_dpi: int = 300,
             tessdata: Path | None = None, selected_pages=None, ocr_mode="auto",
             ocr_region=None, review_dir: Path | None = None, manifest=None) -> list[dict]:
    if ocr_dpi <= 0:
        raise ValueError("OCR DPI must be positive")
    validate_ocr_options(ocr_mode, ocr_region)
    pages = []
    counts = {"native": 0, "ocr": 0, "empty": 0}
    resolved_tessdata = None
    source = Path(os.path.relpath(pdf_path.resolve(), Path.cwd())).as_posix()

    with pymupdf.open(pdf_path) as document:
        selection = set()
        for first, last in selected_pages or [(1, len(document))]:
            if first < 1 or first > last or last > len(document):
                raise ValueError("Requested pages are outside the document")
            selection.update(range(first, last + 1))
        if manifest is not None:
            manifest["selected_pages"] = sorted(selection)
            manifest["language_files"] = {}
            manifest["pages"] = []
        if review_dir is not None:
            if review_dir.exists() and (not review_dir.is_dir() or any(review_dir.iterdir())):
                raise ValueError("Review directory must be empty or new")
            review_dir.mkdir(parents=True, exist_ok=True)
        for page_number, page in enumerate(document, start=1):
            if page_number not in selection:
                continue
            started = time.perf_counter()
            text = page.get_text("text", sort=True) if ocr_mode == "auto" else ""
            method = "native"
            if ocr_mode == "always" or not text.strip():
                if resolved_tessdata is None:
                    resolved_tessdata = resolve_tessdata(tessdata, ocr_language)
                    if manifest is not None:
                        manifest["language_files"] = {
                            lang: {"path": str((Path(resolved_tessdata) / f"{lang}.traineddata").resolve()),
                                   "sha256": sha256_file(Path(resolved_tessdata) / f"{lang}.traineddata")}
                            for lang in ocr_language.split("+")
                        }
                print(f"OCR page {page_number}/{len(document)}")
                try:
                    text = ocr_page(page, language=ocr_language, dpi=ocr_dpi,
                                    tessdata=resolved_tessdata, region=ocr_region,
                                    image_path=review_dir / f"page-{page_number}.png" if review_dir else None)
                except Exception:
                    raise RuntimeError(f"OCR failed on page {page_number}. Check language data and PDF rendering.") from None
                method = "ocr"
            counts[method] += 1

            raw_text = text
            text = normalize_text(raw_text)
            if review_dir is not None:
                (review_dir / f"page-{page_number}.raw.txt").write_text(raw_text, encoding="utf-8")
                (review_dir / f"page-{page_number}.normalized.txt").write_text(text, encoding="utf-8")
            if manifest is not None:
                manifest["pages"].append({"page": page_number, "extraction_method": method,
                                          "empty": not bool(text),
                                          "elapsed_seconds": time.perf_counter() - started})

            if not text:
                counts["empty"] += 1
                print(f"Warning: page {page_number} remains empty after OCR", file=sys.stderr)
                continue

            pages.append({
                "page": page_number,
                "text": text,
                "source": source,
                "extraction_method": method,
            })
        print(f"Processed {len(selection)} pages: native={counts['native']}, "
              f"ocr={counts['ocr']}, empty={counts['empty']}")

    return pages


# STEP 2: Text Chunking
def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
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

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)
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


# STEP 3: Embedding
def generate_embeddings(chunks: list[dict], *, model_revision=None, cache_folder=None) -> list[dict]:
    from sentence_transformers import SentenceTransformer

    options = {}
    if model_revision is not None:
        options["revision"] = model_revision
    if cache_folder is not None:
        options["cache_folder"] = str(cache_folder)
    model = SentenceTransformer(MODEL_NAME, **options)

    # E5 models use passage: for documents
    texts = [
        f"passage: {chunk['content']}"
        for chunk in chunks
    ]

    embeddings = model.encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )

    if len(embeddings) != len(chunks):
        raise RuntimeError("Embedding count mismatch")
    for chunk, vector in zip(chunks, embeddings):
        chunk["embedding"] = vector.tolist()

    return chunks


# STEP 4: Save to JSONL
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


def positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/extracted/chunks.jsonl"),
    )
    parser.add_argument("--skip-embeddings", action="store_true")
    parser.add_argument("--ocr-language", default="vie+eng")
    parser.add_argument("--ocr-dpi", type=positive_int, default=300)
    parser.add_argument("--tessdata", type=Path)
    parser.add_argument("--pages", type=parse_pages, help="One-based pages/ranges, e.g. 1,2-3,15")
    parser.add_argument("--ocr-mode", choices=("auto", "always"), default="auto")
    parser.add_argument("--ocr-region", type=float, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="Upright page fractions (0..1); requires --ocr-mode always")
    parser.add_argument("--review-dir", type=Path, help="New or empty directory for private review artifacts")
    return parser


def main(argv=None) -> int:
    args = create_parser().parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Processing failed; check the input, dependencies, model availability and output permissions."
        print(f"Error: {message}", file=sys.stderr)
        return 1


def run(args) -> int:
    started = time.perf_counter()
    if not args.pdf.is_file():
        raise ValueError("Input PDF does not exist or is not a file")
    if args.output.resolve() == args.pdf.resolve():
        raise ValueError("Output must not overwrite the source PDF")
    validate_ocr_options(args.ocr_mode, args.ocr_region)
    if args.review_dir is not None:
        review = args.review_dir.resolve()
        if review == args.output.resolve() or review in args.output.resolve().parents:
            raise ValueError("Output must be outside the review directory")
        if review.exists() and (not review.is_dir() or any(review.iterdir())):
            raise ValueError("Review directory must be empty or new")
    manifest = None if args.review_dir is None else {
        "status": "extraction_complete", "source_sha256": sha256_file(args.pdf),
        "pymupdf_version": pymupdf.VersionBind, "ocr_language": args.ocr_language,
        "ocr_dpi": args.ocr_dpi, "ocr_mode": args.ocr_mode,
        "ocr_region": args.ocr_region, "chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP,
        "skip_embeddings": args.skip_embeddings,
    }

    # 1. Load
    pages = load_pdf(args.pdf, ocr_language=args.ocr_language,
                     ocr_dpi=args.ocr_dpi, tessdata=args.tessdata, selected_pages=args.pages,
                     ocr_mode=args.ocr_mode, ocr_region=args.ocr_region,
                     review_dir=args.review_dir, manifest=manifest)
    print(f"Loaded {len(pages)} pages containing text")

    # 2. Chunk
    chunks = build_chunks(pages)
    print(f"Created {len(chunks)} chunks")

    if not chunks:
        raise ValueError("No extractable text found after OCR; output was not written")

    # 3. Embed
    if not args.skip_embeddings:
        try:
            chunks = generate_embeddings(chunks)
        except Exception:
            raise RuntimeError("Embedding failed; check the model, dependencies and available memory.") from None
        print(f"Embedding dimensions: {len(chunks[0]['embedding'])}")

    # 4. Save
    if manifest is not None:
        manifest["chunk_count"] = len(chunks)
        manifest["elapsed_seconds"] = time.perf_counter() - started
        manifest["output"] = str(args.output.resolve())
        (args.review_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_jsonl(chunks, args.output)
    print(f"Saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
