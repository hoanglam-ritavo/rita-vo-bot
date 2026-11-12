"""Synthetic checks for the optional local PDF example; no model downloads."""
import json
import unicodedata
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("pymupdf", reason="Install requirements-example.txt for PDF example tests")
from app.ingestion import pdf as pdf_processing, chunking, embeddings, artifacts


class Document(list):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def tessdata(tmp_path):
    for lang in ("vie", "eng"):
        (tmp_path / f"{lang}.traineddata").touch()
    return tmp_path


def test_mixed_pdf_uses_ocr_textpage_and_keeps_page_numbers(monkeypatch, tessdata, capsys):
    native = Mock()
    native.get_text.return_value = "Native text"
    scan = Mock()
    scan.rotation = 270
    scan.get_text.side_effect = [" \n", "Điều 1. Nội quy lao động"]
    empty = Mock()
    empty.get_text.side_effect = ["", " \n"]
    final = Mock()
    final.get_text.return_value = "Final page"
    monkeypatch.setattr(pdf_processing.pymupdf, "open", lambda _: Document([native, scan, empty, final]))
    pages = pdf_processing.load_pdf(Path("synthetic.pdf"), tessdata=tessdata)
    assert [p["page"] for p in pages] == [1, 2, 4]
    assert [p["extraction_method"] for p in pages] == ["native", "ocr", "native"]
    native.get_textpage_ocr.assert_not_called()
    scan.get_textpage_ocr.assert_called_once_with(language="vie+eng", dpi=300, full=True, tessdata=str(tessdata))
    scan.remove_rotation.assert_called_once_with()
    scan.get_text.assert_called_with("text", sort=True, textpage=scan.get_textpage_ocr.return_value)
    assert pages[1]["source"] == "synthetic.pdf"
    assert "page 3 remains empty" in capsys.readouterr().err


def test_native_pdf_needs_no_tessdata(monkeypatch):
    page = Mock()
    page.get_text.return_value = "Native"
    monkeypatch.setattr(pdf_processing.pymupdf, "open", lambda _: Document([page]))
    monkeypatch.setattr(pdf_processing, "resolve_tessdata", Mock(side_effect=AssertionError))
    assert len(pdf_processing.load_pdf(Path("native.pdf"))) == 1








def test_tessdata_cli_precedes_environment(monkeypatch, tessdata):
    monkeypatch.setenv("TESSDATA_PREFIX", "nonexistent")
    assert pdf_processing.resolve_tessdata(tessdata, "vie+eng") == str(tessdata)
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    assert pdf_processing.resolve_tessdata(None, "vie+eng") == str(tessdata)










def test_out_of_range_rejected_before_ocr(monkeypatch):
    page = Mock()
    monkeypatch.setattr(pdf_processing.pymupdf, "open", lambda _: Document([page]))
    with pytest.raises(ValueError, match="outside"):
        pdf_processing.load_pdf(Path("synthetic.pdf"), selected_pages=[(1, 2)])
    page.get_text.assert_not_called()


@pytest.mark.parametrize("mode,region", [
    ("auto", [0, 0, 1, 1]), ("always", [0, 0, 0, 1]),
    ("always", [-0.1, 0, 1, 1]), ("always", [0, 0, float("nan"), 1]),
    ("always", [0, 0, 1, float("inf")]), ("always", [0, 0.8, 1, 0.2]),
])
def test_invalid_region(mode, region):
    with pytest.raises(ValueError):
        pdf_processing.validate_ocr_options(mode, region)


def test_normalization_preserves_vietnamese_and_paragraphs():
    source = unicodedata.normalize("NFD", "Điều 1\t Nội quy\r\n\r\n  Không  bỏ dấu.  ")
    assert pdf_processing.normalize_text(source) == "Điều 1 Nội quy\n\nKhông bỏ dấu."


def test_chunks_preserve_words_order_and_overlap():
    words = [f"mục{i:03d}" for i in range(200)]
    text = "\n\n".join(" ".join(words[i:i+13]) for i in range(0, len(words), 13))
    chunks = chunking.chunk_text(text, chunk_size=110, overlap=25)
    reconstructed = []
    for chunk in chunks:
        assert len(chunk) <= 110
        tokens = chunk.split()
        indices = [words.index(word) for word in tokens]
        assert indices == list(range(indices[0], indices[-1] + 1))
        if reconstructed:
            duplicate_count = max(0, reconstructed[-1] - indices[0] + 1)
            assert len(" ".join(tokens[:duplicate_count])) <= 25
            assert indices[0] <= reconstructed[-1] + 1
        reconstructed.extend(i for i in indices if not reconstructed or i > reconstructed[-1])
    assert reconstructed == list(range(len(words)))
    assert chunking.chunk_text("x" * 123, 50, 10) == ["x" * 50, "x" * 50, "x" * 23]


def test_overlap_does_not_emit_chunks_already_covered():
    text = " ".join(f"word{i}" for i in range(12)) + "\n\n" + " ".join(f"next{i}" for i in range(100))
    chunks = chunking.chunk_text(text)
    assert len(chunks) == 3
    assert "next0" in chunks[1]


def test_rotated_crop_geometry_and_source_preserved(monkeypatch, tmp_path, tessdata):
    pdf = tmp_path / "source.pdf"
    with pdf_processing.pymupdf.open() as doc:
        page = doc.new_page(width=200, height=100)
        page.draw_rect(pdf_processing.pymupdf.Rect(0, 0, 100, 100), color=None, fill=(1, 0, 0))
        page.set_rotation(270)
        doc.save(pdf)
    before = pdf.read_bytes()
    captured = []
    def fake_ocr(page, **kwargs):
        captured.append((tuple(page.rect), page.rotation, kwargs))
        return page.get_textpage()
    monkeypatch.setattr(pdf_processing.pymupdf.Page, "get_textpage_ocr", fake_ocr)
    review = tmp_path / "review"
    manifest = {}
    pdf_processing.load_pdf(pdf, tessdata=tessdata, ocr_mode="always", ocr_dpi=72,
                     ocr_region=[0, 0.5, 1, 1], review_dir=review, manifest=manifest)
    assert captured[0][0] == pytest.approx((0, 0, 100, 100))
    assert captured[0][1] == 0
    pix = pdf_processing.pymupdf.Pixmap(str(review / "page-1.png"))
    assert (pix.width, pix.height) == (100, 100)
    assert pix.pixel(50, 50) == (255, 0, 0)
    assert pdf.read_bytes() == before
    assert manifest["pages"][0]["empty"] is True




@pytest.mark.parametrize("failure", ["serialize", "replace"])
def test_atomic_write_preserves_existing_file(monkeypatch, tmp_path, failure):
    output = tmp_path / "chunks.jsonl"
    output.write_text("existing")
    rows = [{"content": "ok"}, {"content": object()}] if failure == "serialize" else [{"content": "ok"}]
    if failure == "replace":
        monkeypatch.setattr(artifacts.os, "replace", Mock(side_effect=OSError("failed")))
    with pytest.raises((TypeError, OSError)):
        artifacts.save_jsonl(rows, output)
    assert output.read_text() == "existing"
    assert list(tmp_path.glob(".ocr-*.tmp")) == []
