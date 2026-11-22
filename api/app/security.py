"""JWT issuing/verification, password hashing and authorization dependencies."""
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .errors import ApiError

_PBKDF2_ROUNDS = 200_000
bearer_scheme = HTTPBearer(auto_error=False)


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ---------- tokens ----------
@dataclass
class Principal:
    username: str
    role: str
    student_no: str | None


def create_access_token(username: str, role: str, student_no: str | None) -> tuple[str, int]:
    expires_in = settings.JWT_EXPIRE_MINUTES * 60
    now = datetime.now(timezone.utc)
    payload = {
        "iss": settings.JWT_ISSUER,
        "sub": username,
        "role": role,
        "student_no": student_no,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256"), expires_in


def decode_token(token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED", "Access token has expired")
    except jwt.InvalidTokenError:
        raise ApiError(401, "INVALID_TOKEN", "Access token is invalid")
    return Principal(claims["sub"], claims["role"], claims.get("student_no"))


# ---------- dependencies ----------
def get_current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    # The gateway validates the JWT first; the API validates it again (defence in depth),
    # so the service is never exposed unauthenticated even if called directly.
    if creds is None or creds.scheme.lower() != "bearer":
        raise ApiError(
            401, "UNAUTHORIZED", "Bearer token required", {"WWW-Authenticate": "Bearer"}
        )
    return decode_token(creds.credentials)


def require_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.role != "admin":
        raise ApiError(403, "FORBIDDEN", "Administrator role required")
    return principal


def ensure_student_access(principal: Principal, student_no: str, allow_admin: bool = True) -> None:
    """A student may only touch their own records; the number in the request (or in an
    LLM prompt) is never trusted on its own - it must match the token's identity."""
    if allow_admin and principal.role == "admin":
        return
    if principal.role != "student" or principal.student_no != student_no:
        raise ApiError(403, "FORBIDDEN", "You can only access your own tuition records")
