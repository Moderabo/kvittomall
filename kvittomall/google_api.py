"""Shared setup for the optional service-account API path used by sheet.py and drive.py.

ACCESS_MODE in .env controls how the Sheet and Drive are reached:
  - "api": service account only, no fallback if it fails
  - "public": the original anonymous link-sharing method only
  - unset, or "auto" (default): try the API first, warn and fall back to public on failure

GOOGLE_SERVICE_ACCOUNT_FILE must point at the service account's JSON key file when
ACCESS_MODE is "api" or "auto".
"""

import os

import requests
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Failures that mean "the API path didn't work" (bad/missing key, no network, no
# access) as opposed to a bug in this code - callers catch this tuple to decide
# whether to fall back to the public method.
API_ERRORS = (HttpError, GoogleAuthError, OSError, ValueError)

# Same idea, but for the original public/anonymous path, which raises requests'
# exception hierarchy instead.
PUBLIC_ERRORS = (requests.exceptions.RequestException,)

VALID_MODES = {"auto", "api", "public"}

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def get_access_mode() -> str:
    mode = os.getenv("ACCESS_MODE", "auto").lower()
    if mode not in VALID_MODES:
        raise SystemExit(f"ACCESS_MODE must be one of {sorted(VALID_MODES)}, got '{mode}'")
    return mode


def _build_service(name: str, version: str, scopes: list[str]):
    key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not key_path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE must be set in .env to use the API")
    credentials = service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
    return build(name, version, credentials=credentials)


def build_sheets_service():
    return _build_service("sheets", "v4", SHEETS_SCOPES)


def build_drive_service():
    return _build_service("drive", "v3", DRIVE_SCOPES)


def describe(e: Exception, *, via: str) -> str:
    """Turns a raw exception from reaching Google into a short, plain-language reason a
    non-technical user can understand, with the original error kept alongside it (unless
    that original text already *is* the plain-language reason, e.g. our own config errors).

    via is "api" or "public": the same HTTP status means something different depending on
    which method hit it (a 403 means "not shared with the service account" via the API,
    but "no longer publicly shared" via the public method).
    """
    status = None
    if isinstance(e, HttpError):
        status = e.resp.status
    elif isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status = e.response.status_code

    if via == "api" and status in (401, 403, 404):
        # Drive/Sheets deliberately return 404 (not 403) for something that exists but
        # isn't shared with the caller, to avoid confirming it exists to someone who
        # can't see it - so on the API path, even a 404 could mean "not shared" rather
        # than "wrong ID", and there's no way to tell those apart from the response.
        reason = (
            "access was denied - either it isn't shared with the service account's "
            "email address, or the ID in .env is wrong (Google returns the same error "
            "for both, to avoid confirming whether a file exists)"
        )
    elif status in (401, 403):
        reason = 'access was denied - it may no longer be shared as "anyone with the link"'
    elif status == 404:
        reason = "nothing was found there - double check the ID is correct in .env"
    elif status is not None:
        reason = f"Google rejected the request (HTTP {status})"
    elif isinstance(e, GoogleAuthError):
        reason = "the service account credentials could not be verified"
    elif isinstance(e, FileNotFoundError):
        reason = "the service account key file could not be found at the configured path"
    elif isinstance(e, PermissionError):
        reason = "the service account key file exists but couldn't be read (check its file permissions)"
    elif isinstance(e, requests.exceptions.ConnectionError):
        reason = "could not reach Google - check your internet connection"
    elif isinstance(e, requests.exceptions.Timeout):
        reason = "Google did not respond in time"
    elif isinstance(e, ValueError):
        reason = str(e)
    else:
        reason = "an unexpected error occurred"

    detail = str(e)
    return reason if reason == detail else f"{reason} (details: {detail})"
