"""Database-free review orchestration."""
import json
import time
from app.ingestion import pdf, chunking, embeddings, artifacts


def run(args) -> int:
    started = time.perf_counter()
    if not args.pdf.is_file():
        raise ValueError("Input PDF does not exist or is not a file")
    if args.output.resolve() == args.pdf.resolve():
        raise ValueError("Output must not overwrite the source PDF")
    pdf.validate_ocr_options(args.ocr_mode, args.ocr_region)
    if args.review_dir is not None:
        review = args.review_dir.resolve()
        if review == args.output.resolve() or review in args.output.resolve().parents:
            raise ValueError("Output must be outside the review directory")
        if review.exists() and (not review.is_dir() or any(review.iterdir())):
            raise ValueError("Review directory must be empty or new")
    manifest = None if args.review_dir is None else {
        "status": "extraction_complete", "source_sha256": pdf.sha256_file(args.pdf),
        "pymupdf_version": pdf.pymupdf.VersionBind, "ocr_language": args.ocr_language,
        "ocr_dpi": args.ocr_dpi, "ocr_mode": args.ocr_mode,
        "ocr_region": args.ocr_region, "chunk_size": chunking.CHUNK_SIZE, "chunk_overlap": chunking.CHUNK_OVERLAP,
        "skip_embeddings": args.skip_embeddings,
    }

    # 1. Load
    pages = pdf.load_pdf(args.pdf, ocr_language=args.ocr_language,
                     ocr_dpi=args.ocr_dpi, tessdata=args.tessdata, selected_pages=args.pages,
                     ocr_mode=args.ocr_mode, ocr_region=args.ocr_region,
                     review_dir=args.review_dir, manifest=manifest)
    print(f"Loaded {len(pages)} pages containing text")

    # 2. Chunk
    chunks = chunking.build_chunks(pages)
    print(f"Created {len(chunks)} chunks")

    if not chunks:
        raise ValueError("No extractable text found after OCR; output was not written")

    # 3. Embed
    if not args.skip_embeddings:
        try:
            chunks = embeddings.generate_embeddings(chunks)
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
    artifacts.save_jsonl(chunks, args.output)
    print(f"Saved to {args.output}")
    return 0
