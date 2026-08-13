import httplib2
import pytest
import requests
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from kvittomall import google_api


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"denied")


def test_get_access_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("ACCESS_MODE", raising=False)
    assert google_api.get_access_mode() == "auto"


@pytest.mark.parametrize("mode", ["api", "public", "auto", "AUTO", "Api"])
def test_get_access_mode_accepts_valid_modes_case_insensitively(monkeypatch, mode):
    monkeypatch.setenv("ACCESS_MODE", mode)
    assert google_api.get_access_mode() == mode.lower()


def test_get_access_mode_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("ACCESS_MODE", "bogus")
    with pytest.raises(SystemExit):
        google_api.get_access_mode()


def test_describe_api_403_mentions_sharing_and_id():
    reason = google_api.describe(_http_error(403), via="api")
    assert "service account" in reason
    assert "ID in .env" in reason


def test_describe_api_404_is_treated_like_403_not_shared():
    # On the API path, Google returns 404 (not 403) for something that exists but
    # isn't shared with the caller - describe() must not say "wrong ID" outright.
    reason = google_api.describe(_http_error(404), via="api")
    assert "isn't shared" in reason


def test_describe_public_403_mentions_link_sharing():
    reason = google_api.describe(_http_error(403), via="public")
    assert "anyone with the link" in reason


def test_describe_public_404_differs_from_api_404():
    reason = google_api.describe(_http_error(404), via="public")
    assert "double check the ID" in reason


def test_describe_google_auth_error():
    reason = google_api.describe(GoogleAuthError("bad creds"), via="api")
    assert "credentials" in reason


def test_describe_file_not_found():
    reason = google_api.describe(FileNotFoundError("nope"), via="api")
    assert "could not be found" in reason


def test_describe_permission_error():
    reason = google_api.describe(PermissionError("denied"), via="api")
    assert "file permissions" in reason


def test_describe_connection_error():
    reason = google_api.describe(requests.exceptions.ConnectionError(), via="public")
    assert "internet connection" in reason


def test_describe_timeout():
    reason = google_api.describe(requests.exceptions.Timeout(), via="public")
    assert "did not respond in time" in reason


def test_describe_plain_valueerror_uses_message_as_reason():
    reason = google_api.describe(ValueError("GOOGLE_SERVICE_ACCOUNT_FILE must be set"), via="api")
    assert reason == "GOOGLE_SERVICE_ACCOUNT_FILE must be set"


def test_describe_unexpected_exception_has_generic_reason_and_detail():
    reason = google_api.describe(RuntimeError("weird"), via="api")
    assert "unexpected error" in reason
    assert "weird" in reason
