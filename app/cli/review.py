"""PDF review arguments and standalone 0/1 exit-code handling."""
import argparse
import re
import sys
from pathlib import Path
from app.paths import workspace


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
        default=workspace() / "review/chunks.jsonl",
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
        from app.ingestion.review import run
        return run(args)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Processing failed; check the input, dependencies, model availability and output permissions."
        print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
