from io import BytesIO

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from kvittomall import pdf_gen


def test_page_title_uses_transaction_type():
    assert pdf_gen._page_title({"Transaktionstyp": "Milersättning"}) == "Kvittomall - Milersättning"


def test_page_title_blank_when_transaction_type_missing():
    assert pdf_gen._page_title({}) == "Kvittomall - "


def _canvas():
    return canvas.Canvas(BytesIO())


def test_wrap_text_keeps_short_text_on_one_line():
    lines = pdf_gen._wrap_text(_canvas(), "kort text", max_width=1000)
    assert lines == ["kort text"]


def test_wrap_text_splits_long_text_into_multiple_lines():
    long_text = " ".join(["ord"] * 50)
    lines = pdf_gen._wrap_text(_canvas(), long_text, max_width=100)
    assert len(lines) > 1
    # No word should have been dropped in the process.
    assert " ".join(lines).split() == long_text.split()


def test_wrap_text_empty_string_returns_no_lines():
    assert pdf_gen._wrap_text(_canvas(), "", max_width=1000) == []


def test_build_final_pdf_cover_page_only_is_a_valid_single_page_pdf(sample_row):
    pdf_bytes = pdf_gen._build_final_pdf(sample_row, attachment_paths=[])
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 1


def test_build_final_pdf_skips_unsupported_attachment_extension(sample_row, tmp_path):
    bogus = tmp_path / "attachment.txt"
    bogus.write_text("not really an attachment")

    pdf_bytes = pdf_gen._build_final_pdf(sample_row, attachment_paths=[str(bogus)])
    reader = PdfReader(BytesIO(pdf_bytes))
    # Cover page only - the unsupported attachment is skipped, not fatal to the run.
    assert len(reader.pages) == 1
