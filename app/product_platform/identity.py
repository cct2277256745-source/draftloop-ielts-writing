"""Password/session identity boundary with server-side token hashing."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import secrets
from typing import Mapping

from .contracts import ErrorCode, PlatformError, Principal, Role, digest
from .persistence import PlatformStore, _iso


IDENTITY_POLICY_VERSION = "c3-identity-pbkdf2-sha256-v1"
_PBKDF2_ITERATIONS = 310_000


def _email_hash(email: str) -> str:
    normalized = email.strip().casefold()
    if "@" not in normalized or len(normalized) > 320:
        raise PlatformError(ErrorCode.INVALID_REQUEST, "A valid email address is required.")
    return digest({"kind": "normalized-email", "value": normalized})


def _password_hash(password: str) -> str:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise PlatformError(ErrorCode.INVALID_REQUEST, "Password must contain at least 12 characters.")
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=32,
    )
    return "$".join((
        IDENTITY_POLICY_VERSION,
        str(_PBKDF2_ITERATIONS),
        salt.hex(),
        derived.hex(),
    ))


def _verify_password(password: str, encoded: str) -> bool:
    try:
        version, iterations, salt_hex, expected_hex = encoded.split("$")
        if version != IDENTITY_POLICY_VERSION:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
            dklen=len(bytes.fromhex(expected_hex)),
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (TypeError, ValueError):
        return False


class IdentityService:
    def __init__(self, store: PlatformStore, *, session_hours: int = 12) -> None:
        if not (1 <= session_hours <= 168):
            raise ValueError("session duration must be between 1 hour and 7 days")
        self._store = store
        self._session_hours = session_hours

    def create_tenant_admin(self, display_name: str, email: str, password: str) -> Principal:
        tenant_id = self._store.create_tenant(display_name)
        user_id = self._store.create_user(
            tenant_id,
            _email_hash(email),
            _password_hash(password),
            Role.TENANT_ADMIN,
        )
        return Principal(tenant_id, user_id, Role.TENANT_ADMIN)

    def create_learner(
        self,
        actor: Principal,
        email: str,
        password: str,
    ) -> Principal:
        if not actor.can_manage_tenant:
            raise PlatformError(ErrorCode.FORBIDDEN, "Tenant administration is required.")
        user_id = self._store.create_user(
            actor.tenant_id,
            _email_hash(email),
            _password_hash(password),
            Role.LEARNER,
        )
        return Principal(actor.tenant_id, user_id, Role.LEARNER)

    def login(self, tenant_id: str, email: str, password: str) -> Mapping[str, str]:
        user = self._store.user_by_email_hash(tenant_id, _email_hash(email))
        if user is None or not _verify_password(password, str(user["password_hash"])):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "Invalid credentials.")
        token = secrets.token_urlsafe(32)
        token_hash = digest({"kind": "session-token", "token": token})
        session_id = f"session_{secrets.token_hex(16)}"
        expires_at = _iso(self._store._clock() + timedelta(hours=self._session_hours))
        self._store.create_session(
            session_id=session_id,
            session_hash=token_hash,
            tenant_id=tenant_id,
            user_id=str(user["user_id"]),
            expires_at=expires_at,
        )
        return {"token": token, "sessionId": session_id, "expiresAt": expires_at}

    def authenticate(self, token: str) -> Principal:
        if not token:
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "Authentication is required.")
        row = self._store.session(digest({"kind": "session-token", "token": token}))
        if (
            row is None
            or row["revoked_at"] is not None
            or row["deleted_at"] is not None
            or str(row["expires_at"]) <= self._store.now()
        ):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "Session is invalid or expired.")
        return Principal(
            str(row["tenant_id"]),
            str(row["user_id"]),
            Role(str(row["role"])),
            str(row["session_id"]),
        )

    def logout(self, principal: Principal) -> None:
        self._store.revoke_session(principal)
