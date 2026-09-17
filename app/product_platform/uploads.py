"""Owner-scoped upload storage with type, size, and path controls."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets
from typing import Mapping

from .contracts import ErrorCode, PlatformError, Principal, digest
from .persistence import PlatformStore


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}


def _valid_magic(media_type: str, data: bytes) -> bool:
    headers = _MAGIC.get(media_type)
    if headers is None or not any(data.startswith(header) for header in headers):
        return False
    if media_type == "image/webp":
        return len(data) >= 12 and data[8:12] == b"WEBP"
    return True


class UploadStorage:
    def __init__(self, root: str | Path, store: PlatformStore) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self._store = store

    def _owner_root(self, principal: Principal) -> Path:
        tenant = digest({"tenantId": principal.tenant_id})[:24]
        owner = digest({"ownerId": principal.user_id})[:24]
        path = (self.root / tenant / owner).resolve()
        if self.root not in path.parents:
            raise PlatformError(ErrorCode.UNSAFE_UPLOAD, "Upload path is invalid.")
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        return path

    def save(self, principal: Principal, *, media_type: str, data: bytes) -> Mapping[str, object]:
        if not isinstance(data, bytes) or not data or len(data) > MAX_UPLOAD_BYTES:
            raise PlatformError(ErrorCode.UNSAFE_UPLOAD, "Upload size is invalid.")
        if not _valid_magic(media_type, data):
            raise PlatformError(ErrorCode.UNSAFE_UPLOAD, "Upload media signature is invalid.")
        content_sha = hashlib.sha256(data).hexdigest()
        storage_name = f"{secrets.token_hex(24)}.bin"
        path = (self._owner_root(principal) / storage_name).resolve()
        if self.root not in path.parents:
            raise PlatformError(ErrorCode.UNSAFE_UPLOAD, "Upload path is invalid.")
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        storage_key = str(path.relative_to(self.root))
        upload_id = self._store.register_upload(
            principal,
            media_type=media_type,
            byte_size=len(data),
            content_sha256=content_sha,
            storage_key=storage_key,
        )
        return {
            "uploadId": upload_id,
            "mediaType": media_type,
            "byteSize": len(data),
            "contentSha256": content_sha,
        }

    def read(self, principal: Principal, upload_id: str) -> bytes:
        record = self._store.upload(principal, upload_id)
        path = (self.root / str(record["storage_key"])).resolve()
        if self.root not in path.parents or not path.is_file():
            raise PlatformError(ErrorCode.NOT_FOUND, "Upload not found.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["content_sha256"]:
            raise PlatformError(ErrorCode.CONFLICT, "Upload integrity check failed.")
        return data

    def delete_subject_files(self, tenant_id: str, owner_id: str) -> int:
        principal = Principal(tenant_id, owner_id)
        records = self._store.uploads_for_subject(tenant_id, owner_id)
        removed = 0
        for record in records:
            path = (self.root / str(record["storage_key"])).resolve()
            if self.root in path.parents and path.is_file():
                path.unlink()
                removed += 1
        owner_root = self._owner_root(principal)
        for candidate in (owner_root, owner_root.parent):
            try:
                candidate.rmdir()
            except OSError:
                pass
        return removed

    def delete_storage_key(self, storage_key: str | None) -> bool:
        if not storage_key:
            return False
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise PlatformError(ErrorCode.UNSAFE_UPLOAD, "Upload path is invalid.")
        if path.is_file():
            path.unlink()
            return True
        return False
