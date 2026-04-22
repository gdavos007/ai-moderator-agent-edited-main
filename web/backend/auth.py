"""Auth dependencies for the admin portal and agent transcript upload.

Two independent guards:

- ``require_admin`` — HTTP Basic with a shared ``ADMIN_PASSWORD`` env var.
  Used on every ``/admin/*`` route and the admin-only report APIs.  Username
  is hard-coded to ``admin``.

- ``require_upload_secret`` — checks the ``X-Upload-Secret`` header against
  ``REPORT_UPLOAD_SECRET`` env var.  Used only on the agent → backend
  transcript upload endpoint.

Both guards use ``secrets.compare_digest`` to avoid timing attacks, return
generic error messages, and fail closed when the env var is unset.
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_basic = HTTPBasic(auto_error=True)

ADMIN_USERNAME = "admin"


def require_admin(creds: HTTPBasicCredentials = Depends(_basic)) -> str:
    """Validate HTTP Basic credentials against ``ADMIN_PASSWORD``.

    Returns the admin username on success, raises 401 otherwise.  Raises
    503 if ``ADMIN_PASSWORD`` is unset so operators can't accidentally
    ship a portal with no auth.
    """
    expected_pw = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_pw:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth not configured (ADMIN_PASSWORD missing)",
        )

    ok_user = secrets.compare_digest(creds.username, ADMIN_USERNAME)
    ok_pw = secrets.compare_digest(creds.password, expected_pw)
    if not (ok_user and ok_pw):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="admin"'},
        )
    return creds.username


def require_upload_secret(x_upload_secret: Optional[str] = Header(default=None)) -> None:
    """Validate the shared secret header used by the agent.

    Returns None on success.  Raises 401 on any failure (missing header,
    wrong value, or server misconfigured).
    """
    expected = os.environ.get("REPORT_UPLOAD_SECRET", "")
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload secret not configured (REPORT_UPLOAD_SECRET missing)",
        )
    if not x_upload_secret or not secrets.compare_digest(x_upload_secret, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid upload secret",
        )
