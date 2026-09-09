import secrets
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
    Verifies API Key or Bearer token for mutating operational routes (P0-002).
    Default: API_KEY_AUTH_ENABLED=True.
    Bypass is strictly allowed ONLY when APP_ENV == "development" AND API_KEY_AUTH_BYPASS_DEV == True.
    In all other cases, requests without valid credentials return 401 Unauthorized.
    """
    is_dev = getattr(settings, "APP_ENV", "production").lower() == "development"
    dev_bypass = getattr(settings, "API_KEY_AUTH_BYPASS_DEV", False) is True
    auth_enabled = getattr(settings, "API_KEY_AUTH_ENABLED", True)

    if not auth_enabled and is_dev and dev_bypass:
        return Role.ADMIN

    # 1. Check Bearer token
    if auth_cred and auth_cred.credentials:
        token = auth_cred.credentials.strip()
        if any(secrets.compare_digest(token, expected) for expected in (settings.API_AUTH_SECRET, settings.API_ADMIN_KEY)):
            return Role.ADMIN
        logger.warning("Invalid Bearer token received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )

    # 2. Check X-API-KEY header
    if x_api_key:
        clean_key = x_api_key.strip()
        if any(secrets.compare_digest(clean_key, expected) for expected in (settings.API_ADMIN_KEY, settings.API_AUTH_SECRET)):
            return Role.ADMIN
        logger.warning("Invalid X-API-KEY header received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    # 3. Check Cookie (for authenticated web dashboard sessions)
    cookie_token = request.cookies.get("kripto_admin_token")
    if cookie_token:
        clean_cookie = cookie_token.strip()
        if any(secrets.compare_digest(clean_cookie, expected) for expected in (settings.API_ADMIN_KEY, settings.API_AUTH_SECRET)):
            return Role.ADMIN
        logger.warning("Invalid admin cookie token received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token.",
        )

    logger.warning(f"Unauthenticated request to protected endpoint: {request.url.path}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required for this operational action. Provide X-API-KEY or Authorization header.",
    )
