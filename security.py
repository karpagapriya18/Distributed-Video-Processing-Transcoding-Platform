
import hashlib
import hmac
import time
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common.config import settings


# ============================================================
# SWAGGER BEARER AUTHENTICATION
# ============================================================

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    description="Enter your API key to access protected endpoints.",
    auto_error=False,
)


# ============================================================
# INTERNAL SERVICE AUTHENTICATION
# ============================================================

from fastapi import Header


def internal(x_internal_token: str = Header(default="")):
    if not hmac.compare_digest(
        x_internal_token,
        settings().internal_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid service credentials"
        )


# ============================================================
# PUBLIC API AUTHENTICATION
# ============================================================

def authorized(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    )
):
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(
            credentials.credentials,
            settings().api_key
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid Bearer API key is required",
            headers={"WWW-Authenticate": "Bearer"}
        )


# ============================================================
# INTERNAL REQUEST HEADERS
# ============================================================

def headers() -> dict:
    return {
        "X-Internal-Token": settings().internal_token
    }


# ============================================================
# SECURE MEDIA PATH
# ============================================================

def media_path(relative: str) -> Path:
    root = Path(settings().media_root).resolve()
    path = (root / relative).resolve()

    if not path.is_relative_to(root):
        raise HTTPException(
            status_code=400,
            detail="Invalid media path"
        )

    return path


# ============================================================
# MEDIA SIGNATURE GENERATION
# ============================================================

def signature(kind: str, item_id: str, expires: int) -> str:
    message = f"{kind}:{item_id}:{expires}".encode()

    return hmac.new(
        settings().signing_secret.encode(),
        message,
        hashlib.sha256
    ).hexdigest()


# ============================================================
# GENERATE SIGNED MEDIA URL
# ============================================================

def signed_url(kind: str, item_id: str) -> str:
    expires = int(time.time()) + 3600

    return (
        f"{settings().public_base_url}"
        f"/media/{kind}/{item_id}"
        f"?expires={expires}"
        f"&signature={signature(kind, item_id, expires)}"
    )


# ============================================================
# VERIFY MEDIA SIGNATURE
# ============================================================

def verify_signature(
    kind: str,
    item_id: str,
    expires: int,
    token: str
):
    if (
        expires < int(time.time())
        or not hmac.compare_digest(
            signature(kind, item_id, expires),
            token
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Media link is invalid or expired"
        )
