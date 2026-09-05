from enum import Enum
from typing import Optional

from fastapi import Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("auth-middleware", service="api")
settings = get_settings()
security_bearer = HTTPBearer(auto_error=False)


class Role(str, Enum):
    ADMIN = "ADMIN"
    TRADER = "TRADER"
    RISK_ADMIN = "RISK_ADMIN"
    VIEWER = "VIEWER"


async def verify_api_key_or_token(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-KEY"),
    auth_cred: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> Role:
    """
    Verifies API Key or Bearer token for mutating operational routes (AUDIT-09).
    If API_KEY_AUTH_ENABLED is False (development/local test suite), returns Role.ADMIN.
    In production (API_KEY_AUTH_ENABLED=True), rejects missing or invalid credentials with 401/403.
    """
    if not getattr(settings, "API_KEY_AUTH_ENABLED", False):
        return Role.ADMIN

    # 1. Check Bearer token
    if auth_cred and auth_cred.credentials:
        token = auth_cred.credentials.strip()
        if token == settings.API_AUTH_SECRET or token == settings.API_ADMIN_KEY:
            return Role.ADMIN
        logger.warning("Invalid Bearer token received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )

    # 2. Check X-API-KEY header
    if x_api_key:
        clean_key = x_api_key.strip()
        if clean_key == settings.API_ADMIN_KEY or clean_key == settings.API_AUTH_SECRET:
            return Role.ADMIN
        logger.warning("Invalid X-API-KEY header received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    logger.warning(f"Unauthenticated request to protected endpoint: {request.url.path}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required for this operational action. Provide X-API-KEY or Authorization header.",
    )
