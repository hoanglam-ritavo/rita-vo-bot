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
from app.cli import review as review_cli


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


@pytest.mark.parametrize("failure", ["missing", "ocr", "empty"])
def test_failures_preserve_existing_output(monkeypatch, tmp_path, tessdata, failure, capsys):
    pdf = tmp_path / "input.pdf"
    pdf.touch()
    output = tmp_path / "output.jsonl"
    output.write_text("existing", encoding="utf-8")
    page = Mock()
    page.get_text.return_value = ""
    if failure == "missing":
        (tessdata / "vie.traineddata").unlink()
    elif failure == "ocr":
        page.get_textpage_ocr.side_effect = RuntimeError("PRIVATE CONTENT")
    monkeypatch.setattr(pdf_processing.pymupdf, "open", lambda _: Document([page]))
    assert review_cli.main([str(pdf), "--output", str(output), "--tessdata", str(tessdata), "--skip-embeddings"]) == 1
    assert output.read_text() == "existing"
    errors = capsys.readouterr().err
    assert "PRIVATE CONTENT" not in errors
    if failure == "ocr":
        assert "page 1" in errors


def test_skip_embeddings_writes_unicode_metadata(monkeypatch, tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.touch()
    output = tmp_path / "nested" / "chunks.jsonl"
    monkeypatch.setattr(pdf_processing, "load_pdf", lambda *a, **k: [dict(page=2, text="Nội quy tiếng Việt", source="input.pdf", extraction_method="ocr")])
    monkeypatch.setattr(embeddings, "generate_embeddings", Mock(side_effect=AssertionError))
    assert review_cli.main([str(pdf), "--skip-embeddings", "--output", str(output)]) == 0
    raw = output.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert "Nội quy" in raw
    assert record == dict(chunk_index=0, page=2, content="Nội quy tiếng Việt", source="input.pdf", extraction_method="ocr")


def test_embeddings_use_passage_prefix_and_normalization(monkeypatch):
    vector = Mock()
    vector.tolist.return_value = [0.6, 0.8]
    model = Mock()
    model.encode.return_value = [vector]
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=factory))
    result = embeddings.generate_embeddings([{"content": "Nội quy"}])
    factory.assert_called_once_with(embeddings.MODEL_NAME)
    model.encode.assert_called_once_with(["passage: Nội quy"], batch_size=16, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=True)
    assert result[0]["embedding"] == [0.6, 0.8]


def test_tessdata_cli_precedes_environment(monkeypatch, tessdata):
    monkeypatch.setenv("TESSDATA_PREFIX", "nonexistent")
    assert pdf_processing.resolve_tessdata(tessdata, "vie+eng") == str(tessdata)
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    assert pdf_processing.resolve_tessdata(None, "vie+eng") == str(tessdata)


@pytest.mark.parametrize("dpi", ["0", "-1", "abc"])
def test_invalid_dpi_rejected(dpi):
    with pytest.raises(SystemExit) as exc:
        review_cli.main(["input.pdf", "--ocr-dpi", dpi])
    assert exc.value.code == 2


def test_source_cannot_be_overwritten(tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"source")
    assert review_cli.main([str(pdf), "--output", str(pdf)]) == 1
    assert pdf.read_bytes() == b"source"


@pytest.mark.parametrize("value", ["", "0", "1,", "-1", "3-2", "1.5", "1-2-3"])
def test_invalid_page_selection(value):
    with pytest.raises(SystemExit):
        review_cli.main(["input.pdf", "--pages", value])


def test_selected_pages_forced_ocr(monkeypatch, tessdata):
    pages = [Mock(rotation=0) for _ in range(4)]
    for page in pages:
        page.get_text.return_value = "Recognized text"
    monkeypatch.setattr(pdf_processing.pymupdf, "open", lambda _: Document(pages))
    result = pdf_processing.load_pdf(Path("synthetic.pdf"), tessdata=tessdata,
                              selected_pages=review_cli.parse_pages("4,2-3,2"), ocr_mode="always")
    assert [p["page"] for p in result] == [2, 3, 4]
    pages[0].get_text.assert_not_called()
    for page in pages[1:]:
        assert page.get_text.call_count == 1
        page.get_textpage_ocr.assert_called_once()


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


def test_review_manifest_and_no_content_in_console(monkeypatch, tmp_path, tessdata, capsys):
    pdf = tmp_path / "source.pdf"
    with pdf_processing.pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), "PRIVATE_SYNTHETIC_TEXT")
        doc.save(pdf)
    monkeypatch.setattr(pdf_processing.pymupdf.Page, "get_textpage_ocr", lambda page, **kw: page.get_textpage())
    output, review = tmp_path / "chunks.jsonl", tmp_path / "review"
    command = [str(pdf), "--output", str(output), "--review-dir", str(review),
               "--ocr-mode", "always", "--tessdata", str(tessdata), "--skip-embeddings"]
    assert review_cli.main(command) == 0
    manifest = json.loads((review / "manifest.json").read_text())
    assert manifest["selected_pages"] == [1]
    assert manifest["source_sha256"] == pdf_processing.sha256_file(pdf)
    assert manifest["language_files"]["vie"]["sha256"] == pdf_processing.sha256_file(tessdata / "vie.traineddata")
    assert manifest["chunk_count"] == 1
    assert manifest["ocr_dpi"] == 300
    assert manifest["ocr_region"] is None
    assert manifest["elapsed_seconds"] >= 0
    assert (review / "page-1.png").is_file()
    assert "PRIVATE_SYNTHETIC_TEXT" in (review / "page-1.raw.txt").read_text()
    assert "PRIVATE_SYNTHETIC_TEXT" not in capsys.readouterr().out
    before = output.read_bytes()
    assert review_cli.main(command) == 1
    assert output.read_bytes() == before


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


def test_embedding_failure_preserves_output(monkeypatch, tmp_path):
    pdf, output = tmp_path / "source.pdf", tmp_path / "chunks.jsonl"
    pdf.touch()
    output.write_text("existing")
    monkeypatch.setattr(pdf_processing, "load_pdf", lambda *a, **kw: [
        dict(page=1, text="Synthetic", source="source.pdf", extraction_method="native")])
    monkeypatch.setattr(embeddings, "generate_embeddings", Mock(side_effect=OSError("private")))
    assert review_cli.main([str(pdf), "--output", str(output)]) == 1
    assert output.read_text() == "existing"
