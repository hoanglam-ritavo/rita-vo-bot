"""PDF extraction, OCR and text normalization."""
import hashlib
import math
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import pymupdf


def sha256_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


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
             ocr_region=None, review_dir: Path | None = None, manifest=None, raw_pages=None) -> list[dict]:
    if ocr_dpi <= 0:
        raise ValueError("OCR DPI must be positive")
    validate_ocr_options(ocr_mode, ocr_region)
    pages = []
    counts = {"native": 0, "ocr": 0, "empty": 0}
    resolved_tessdata = None
    # Normalize extended Windows spelling before computing provenance paths.
    def ordinary(path):
        value = str(path)
        if value.startswith('\\\\?\\UNC\\'):
            return '\\\\' + value[8:]
        return value[4:] if value.startswith('\\\\?\\') else value
    source = Path(os.path.relpath(ordinary(pdf_path.resolve()), ordinary(Path.cwd()))).as_posix()

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
            if raw_pages is not None:
                raw_pages.append({'page': page_number, 'raw': raw_text, 'extraction_method': method})
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
