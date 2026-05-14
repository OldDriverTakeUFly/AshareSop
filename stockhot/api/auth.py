"""HTTP Basic Auth dependency for protecting API routes."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from stockhot.api.config import settings

security = HTTPBasic(auto_error=False)


async def verify_credentials(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> bool:
    """Verify HTTP Basic Auth credentials.

    Returns True if credentials are valid.
    Raises 401 if credentials are missing or incorrect.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    username_ok = secrets.compare_digest(
        credentials.username.encode("utf8"),
        b"stockhot",
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf8"),
        settings.API_PASSWORD.encode("utf8"),
    )

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return True
