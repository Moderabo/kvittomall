import requests

from kvittomall.drive import _get_confirm_token, get_drive_file_id


def test_get_drive_file_id_from_open_url():
    assert get_drive_file_id("https://drive.google.com/open?id=abc123XYZ") == "abc123XYZ"


def test_get_drive_file_id_from_share_url():
    url = "https://drive.google.com/file/d/abc123XYZ/view?usp=sharing"
    assert get_drive_file_id(url) == "abc123XYZ"


def test_get_drive_file_id_returns_none_for_unrecognized_url():
    assert get_drive_file_id("https://example.com/not-a-drive-link") is None


def test_get_confirm_token_from_cookie():
    response = requests.Response()
    response.cookies.set("download_warning_12345", "the-token")
    assert _get_confirm_token(response) == "the-token"


def test_get_confirm_token_from_html_body():
    response = requests.Response()
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response._content = b'<a href="?confirm=abc123-XYZ_9\">Download anyway</a>'
    response.encoding = "utf-8"
    assert _get_confirm_token(response) == "abc123-XYZ_9"


def test_get_confirm_token_none_when_neither_present():
    response = requests.Response()
    response.headers["Content-Type"] = "application/octet-stream"
    response._content = b""
    assert _get_confirm_token(response) is None
