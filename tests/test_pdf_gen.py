import logging
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from kvittomall import db, pdf_gen

# --- rendering (pure bytes-in, bytes-out - no DB, no filesystem state) ---


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


@pytest.fixture
def fake_jpg(tmp_path) -> str:
    path = tmp_path / "receipt.jpg"
    Image.new("RGB", (200, 300), (255, 255, 255)).save(path, "JPEG")
    return str(path)


@pytest.fixture
def fake_attachment_pdf(tmp_path) -> str:
    """A letter-sized (not A4) single-page PDF with recognizable text, so scaling to
    fit the A4 content area is actually exercised, not a no-op.
    """
    path = tmp_path / "receipt.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(50, 700, "FAKE RECEIPT MARKER")
    c.showPage()
    c.save()
    return str(path)


@pytest.fixture
def tiny_svg_logo(tmp_path) -> str:
    path = tmp_path / "logo.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect width="100" height="100" fill="blue"/></svg>'
    )
    return str(path)


@pytest.fixture
def tiny_png_logo(tmp_path) -> str:
    path = tmp_path / "logo.png"
    Image.new("RGB", (50, 50), (10, 20, 30)).save(path, "PNG")
    return str(path)


def test_draw_page_header_svg_branch(monkeypatch, tiny_svg_logo):
    monkeypatch.setattr(pdf_gen, "LOGO_PATH", tiny_svg_logo)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=pdf_gen.A4)
    pdf_gen._draw_page_header(c, "Test Title")
    c.showPage()
    c.save()
    assert len(PdfReader(BytesIO(buf.getvalue())).pages) == 1


def test_draw_page_header_raster_branch(monkeypatch, tiny_png_logo):
    monkeypatch.setattr(pdf_gen, "LOGO_PATH", tiny_png_logo)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=pdf_gen.A4)
    pdf_gen._draw_page_header(c, "Test Title")
    c.showPage()
    c.save()
    assert len(PdfReader(BytesIO(buf.getvalue())).pages) == 1


def test_image_page_renders_real_jpg(fake_jpg):
    reader = pdf_gen._image_page(fake_jpg, "Kvittomall - Test")
    assert len(reader.pages) == 1


def test_scale_pdf_to_a4_preserves_page_count_and_content(fake_attachment_pdf):
    # The source fixture is letter-sized, not A4 - this must come out A4 regardless,
    # since that's the whole point of the function (and easy to silently break: the
    # merge order determines whose mediabox "wins").
    reader = pdf_gen._scale_pdf_to_a4(fake_attachment_pdf, "Kvittomall - Test")
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(pdf_gen.PAGE_WIDTH)
    assert float(page.mediabox.height) == pytest.approx(pdf_gen.PAGE_HEIGHT)
    assert "FAKE RECEIPT MARKER" in page.extract_text()


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


def test_build_final_pdf_with_jpg_attachment_has_two_pages(sample_row, fake_jpg):
    pdf_bytes = pdf_gen._build_final_pdf(sample_row, attachment_paths=[fake_jpg])
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 2


def test_build_final_pdf_with_pdf_attachment_has_two_pages(sample_row, fake_attachment_pdf):
    pdf_bytes = pdf_gen._build_final_pdf(sample_row, attachment_paths=[fake_attachment_pdf])
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 2
    assert "FAKE RECEIPT MARKER" in reader.pages[1].extract_text()


def test_build_final_pdf_logs_and_skips_attachment_that_fails_to_render(sample_row, tmp_path, caplog):
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"not a real jpeg")

    with caplog.at_level(logging.ERROR, logger=pdf_gen.logger.name):
        pdf_bytes = pdf_gen._build_final_pdf(sample_row, attachment_paths=[str(corrupt)])

    reader = PdfReader(BytesIO(pdf_bytes))
    # Cover page only - the broken attachment is logged and skipped, not fatal to the run.
    assert len(reader.pages) == 1
    assert "Failed to add attachment" in caplog.text


# --- orchestration (real DB + real filesystem, isolated via fixtures) ---


@pytest.fixture
def final_dir(monkeypatch, tmp_path):
    d = tmp_path / "final"
    monkeypatch.setattr(pdf_gen, "FINAL_DIR", str(d))
    return d


def test_attachments_ready_true_with_no_links(conn):
    assert pdf_gen._attachments_ready(conn, "k1", {"Ladda upp bild": ""}) == (True, [])


def test_attachments_ready_false_when_fewer_attachments_recorded_than_links(conn):
    row = {"Ladda upp bild": "https://drive.google.com/open?id=a, https://drive.google.com/open?id=b"}
    db.upsert_attachment(conn, "k1", 0, "a")  # only 1 of 2 links recorded so far
    assert pdf_gen._attachments_ready(conn, "k1", row) == (False, [])


def test_attachments_ready_false_when_not_yet_processed(conn):
    row = {"Ladda upp bild": "https://drive.google.com/open?id=a"}
    db.upsert_attachment(conn, "k1", 0, "a")
    assert pdf_gen._attachments_ready(conn, "k1", row) == (False, [])


def test_attachments_ready_true_with_valid_processed_files(conn, tmp_path):
    processed = tmp_path / "receipt.jpg"
    processed.write_bytes(b"fake-jpeg-bytes")
    row = {"Ladda upp bild": "https://drive.google.com/open?id=a"}
    db.upsert_attachment(conn, "k1", 0, "a")
    db.mark_process_ok(conn, "k1", 0, str(processed), processed.stat().st_size)

    ready, paths = pdf_gen._attachments_ready(conn, "k1", row)
    assert ready is True
    assert paths == [str(processed)]


def test_generate_one_writes_pdf_and_marks_row_generated(conn, final_dir, sample_row):
    row_key = "2026-03-05 12.30.00"
    db.upsert_row(conn, row_key, "base1", "Privat utlägg", "placeholder-hash", "links")
    dest = "privat/base1.pdf"

    pdf_gen._generate_one(conn, row_key, sample_row, dest, attachment_paths=[])

    out_path = final_dir / dest
    assert out_path.exists()
    db_row = db.get_row(conn, row_key)
    assert db_row["status"] == "generated"
    assert db_row["final_pdf_path"] == dest
    assert db_row["final_pdf_size"] == out_path.stat().st_size


def test_toggle_handled_moves_file_and_flips_state(conn, final_dir):
    (final_dir / "privat").mkdir(parents=True)
    (final_dir / "privat" / "row1.pdf").write_bytes(b"pdf-bytes")
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.mark_generated(conn, "k1", "privat/row1.pdf", 9, "h")

    new_state = pdf_gen.toggle_handled(conn, "k1")

    assert new_state is True
    assert not (final_dir / "privat" / "row1.pdf").exists()
    assert (final_dir / "handled" / "privat" / "row1.pdf").exists()
    db_row = db.get_row(conn, "k1")
    assert db_row["status"] == "generated"  # status is untouched by handling
    assert db_row["handled"] == 1
    assert db_row["final_pdf_path"] == "handled/privat/row1.pdf"


def test_toggle_handled_flips_back_to_unhandled(conn, final_dir):
    (final_dir / "privat").mkdir(parents=True)
    (final_dir / "privat" / "row1.pdf").write_bytes(b"pdf-bytes")
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.mark_generated(conn, "k1", "privat/row1.pdf", 9, "h")

    pdf_gen.toggle_handled(conn, "k1")  # -> handled
    new_state = pdf_gen.toggle_handled(conn, "k1")  # -> unhandled again

    assert new_state is False
    assert (final_dir / "privat" / "row1.pdf").exists()
    assert not (final_dir / "handled" / "privat" / "row1.pdf").exists()
    db_row = db.get_row(conn, "k1")
    assert db_row["handled"] == 0
    assert db_row["final_pdf_path"] == "privat/row1.pdf"


def test_toggle_handled_raises_when_never_generated(conn, final_dir):
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    with pytest.raises(SystemExit):
        pdf_gen.toggle_handled(conn, "k1")


def test_toggle_handled_raises_when_file_missing_on_disk(conn, final_dir):
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.mark_generated(conn, "k1", "privat/row1.pdf", 9, "h")  # no file actually written

    with pytest.raises(SystemExit):
        pdf_gen.toggle_handled(conn, "k1")


def test_mark_all_handled_moves_generated_rows_and_skips_others(conn, final_dir):
    (final_dir / "privat").mkdir(parents=True)
    (final_dir / "privat" / "row1.pdf").write_bytes(b"pdf-bytes")
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h1", "l")
    db.mark_generated(conn, "k1", "privat/row1.pdf", 9, "h1")

    db.upsert_row(conn, "k2", "row2", "Privat utlägg", "h2", "l")  # never generated

    db.upsert_row(conn, "k3", "row3", "Sektionskort", "h3", "l")
    db.set_handled(conn, "k3", True, "handled/sektionskort/row3.pdf")  # already handled

    handled = pdf_gen.mark_all_handled(conn)

    assert handled == 1
    assert (final_dir / "handled" / "privat" / "row1.pdf").exists()
    assert db.get_row(conn, "k1")["handled"] == 1
    assert db.get_row(conn, "k2")["status"] == "new"  # untouched - never generated
    assert db.get_row(conn, "k3")["final_pdf_path"] == "handled/sektionskort/row3.pdf"  # untouched


def test_mark_all_handled_logs_and_skips_row_missing_on_disk(conn, final_dir, caplog):
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.mark_generated(conn, "k1", "privat/row1.pdf", 9, "h")  # no file actually written

    with caplog.at_level(logging.WARNING, logger=pdf_gen.logger.name):
        handled = pdf_gen.mark_all_handled(conn)

    assert handled == 0
    assert db.get_row(conn, "k1")["handled"] == 0  # left alone, not silently marked


def test_remove_final_deletes_file_leaves_db_untouched(conn, final_dir):
    (final_dir / "privat").mkdir(parents=True)
    (final_dir / "privat" / "row1.pdf").write_bytes(b"pdf-bytes")
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")
    db.mark_generated(conn, "k1", "privat/row1.pdf", 9, "h")
    conn.commit()  # remove_final() opens its own connection - mustn't see an open write lock

    removed = pdf_gen.remove_final("k1")

    assert removed is True
    assert not (final_dir / "privat" / "row1.pdf").exists()
    db_row = db.get_row(conn, "k1")
    assert db_row["status"] == "generated"  # DB is untouched - only the file is gone
    assert db_row["final_pdf_path"] == "privat/row1.pdf"


def test_remove_final_is_a_no_op_when_nothing_to_remove(conn, final_dir):
    db.upsert_row(conn, "k1", "row1", "Privat utlägg", "h", "l")  # never generated
    conn.commit()  # remove_final() opens its own connection - mustn't see an open write lock
    assert pdf_gen.remove_final("k1") is False
    assert pdf_gen.remove_final("unknown-row") is False


def test_remove_final_then_generate_restores_it_in_place(monkeypatch, conn, final_dir):
    rows = [_row("2026-01-01 10.00.00", "Anna Andersson")]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)
    pdf_gen.run()
    dest = final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf"
    original_bytes = dest.read_bytes()

    assert pdf_gen.remove_final("2026-01-01 10.00.00") is True
    assert not dest.exists()

    pdf_gen.run()  # fail-safe "repair" path, same as any other accidental deletion

    assert dest.exists()
    assert dest.read_bytes() == original_bytes


def _row(timestamp: str, name: str, transaction_type: str = "Privat utlägg", summa: str = "100.00") -> dict:
    """A minimal row with no receipt attachments - keeps these tests focused on run()'s
    own planning/generate orchestration, not attachment readiness (covered separately
    above).
    """
    return {
        "Tidstämpel": timestamp,
        "Transaktionstyp": transaction_type,
        "Datum för händelsen": "2026-01-01",
        "Utskott": "Styrelsen",
        "Arrangemang": "",
        "Specificering": "Test",
        "Namn": name,
        "Kontonummer": "1234-5",
        "Summa": summa,
        "Körda mil": "",
        "Sträcka": "",
        "Ladda upp bild": "",
        "Övrigt": "",
        "Godkännande": "Ja",
    }


def test_run_generates_new_rows_into_correct_categories(monkeypatch, conn, final_dir):
    rows = [
        _row("2026-01-01 10.00.00", "Anna Andersson", transaction_type="Privat utlägg"),
        _row("2026-01-02 10.00.00", "Bertil Bengtsson", transaction_type="Sektionskort"),
    ]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)

    pdf_gen.run()

    assert (final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf").exists()
    assert (final_dir / "sektionskort" / "2026-01-02_10.00.00_Bertil-Bengtsson.pdf").exists()
    db_rows = db.list_rows(conn)
    assert len(db_rows) == 2
    assert all(r["status"] == "generated" for r in db_rows)


def test_run_is_a_no_op_when_nothing_changed(monkeypatch, conn, final_dir):
    rows = [_row("2026-01-01 10.00.00", "Anna Andersson")]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)
    dest = final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf"

    pdf_gen.run()
    first_bytes = dest.read_bytes()

    pdf_gen.run()

    assert dest.read_bytes() == first_bytes


def test_run_regenerates_in_place_without_moving_anything(monkeypatch, conn, final_dir):
    # No more automatic archiving on regeneration - a changed row is just rewritten
    # where it already sits; only the explicit `handled` command ever moves a PDF out
    # of its category folder.
    rows = [_row("2026-01-01 10.00.00", "Anna Andersson", summa="100.00")]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)
    pdf_gen.run()

    rows[0]["Summa"] = "999.00"
    pdf_gen.run()

    dest = final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf"
    assert dest.exists()
    assert not (final_dir / "handled").exists()


def test_run_does_not_move_unrelated_rows_in_same_category_when_one_changes(monkeypatch, conn, final_dir):
    rows = [
        _row("2026-01-01 10.00.00", "Anna Andersson", summa="100.00"),
        _row("2026-01-02 10.00.00", "Bertil Bengtsson", summa="200.00"),
    ]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)
    pdf_gen.run()
    dest_b = final_dir / "privat" / "2026-01-02_10.00.00_Bertil-Bengtsson.pdf"
    first_bytes_b = dest_b.read_bytes()

    rows[0]["Summa"] = "999.00"
    pdf_gen.run()

    # Bertil's row didn't change and must be left exactly as it was - no more automatic
    # "sweep the whole category" side effect from Anna's row changing.
    assert dest_b.read_bytes() == first_bytes_b
    assert not (final_dir / "handled").exists()


def test_run_repairs_missing_file_without_treating_it_as_new(monkeypatch, conn, final_dir):
    rows = [_row("2026-01-01 10.00.00", "Anna Andersson")]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)
    pdf_gen.run()

    dest = final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf"
    dest.unlink()

    pdf_gen.run()

    assert dest.exists()  # restored to the same place
    assert not (final_dir / "handled").exists()


def test_run_regenerates_a_handled_row_back_into_active_folder_when_content_changes(monkeypatch, conn, final_dir):
    rows = [_row("2026-01-01 10.00.00", "Anna Andersson", summa="100.00")]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)
    pdf_gen.run()
    row_key = "2026-01-01 10.00.00"
    pdf_gen.toggle_handled(conn, row_key)
    conn.commit()  # run() below opens its own connection - mustn't see an open write lock
    handled_path = final_dir / "handled" / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf"
    assert handled_path.exists()

    rows[0]["Summa"] = "999.00"
    pdf_gen.run()

    active_path = final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf"
    assert active_path.exists()  # moved back out of handled/ for re-review
    assert not handled_path.exists()
    db_row = db.get_row(conn, row_key)
    assert db_row["status"] == "generated"
    assert db_row["handled"] == 0


def test_run_only_filters_to_one_row(monkeypatch, conn, final_dir):
    key1, key2 = "2026-01-01 10.00.00", "2026-01-02 10.00.00"
    rows = [
        _row(key1, "Anna Andersson"),
        _row(key2, "Bertil Bengtsson"),
    ]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)

    pdf_gen.run(only=key1)

    assert (final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf").exists()
    assert not (final_dir / "privat" / "2026-01-02_10.00.00_Bertil-Bengtsson.pdf").exists()
    # Both rows are still synced (cheap, no side effects) - only the targeted one generated.
    statuses = {r["row_key"]: r["status"] for r in db.list_rows(conn)}
    assert statuses == {key1: "generated", key2: "new"}


def test_run_skips_row_missing_identity_without_crashing(monkeypatch, conn, final_dir):
    rows = [
        {"Tidstämpel": "", "Namn": "", "Transaktionstyp": "Privat utlägg"},
        _row("2026-01-01 10.00.00", "Anna Andersson"),
    ]
    monkeypatch.setattr(pdf_gen, "read_rows", lambda: rows)

    pdf_gen.run()

    assert (final_dir / "privat" / "2026-01-01_10.00.00_Anna-Andersson.pdf").exists()
    assert len(db.list_rows(conn)) == 1
