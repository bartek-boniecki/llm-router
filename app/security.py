"""
Optional Bearer/JWT auth for API calls.
- If REQUIRE_AUTH=false, requests are allowed without Authorization header.
- If true, we verify signature if a public key is configured,
  otherwise we just parse the token for aud/iss (lightweight mode).
"""

from typing import Optional
from fastapi import HTTPException
from jose import jwt, JWTError
from app.config import settings


def verify_bearer_token_if_required(authorization: Optional[str]):
    if not settings.REQUIRE_AUTH:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.split(" ", 1)[1]

    # If a public key is provided, verify signature. Otherwise, do basic claims checks.
    try:
        if settings.JWT_PUBLIC_KEY_PEM:
            jwt.decode(
                token,
                settings.JWT_PUBLIC_KEY_PEM,
                algorithms=["RS256", "ES256"],
                audience=settings.JWT_AUDIENCE,
                issuer=settings.JWT_ISSUER,
            )
        else:
            claims = jwt.get_unverified_claims(token)
            if settings.JWT_AUDIENCE and claims.get("aud") != settings.JWT_AUDIENCE:
                raise HTTPException(status_code=401, detail="Invalid audience")
            if settings.JWT_ISSUER and claims.get("iss") != settings.JWT_ISSUER:
                raise HTTPException(status_code=401, detail="Invalid issuer")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
