"""Composition root for the local C3 platform runtime."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .api import C3ApiRouter, C3WSGIApplication
from .identity import IdentityService
from .persistence import PlatformStore
from .privacy import PrivacyService
from .service import ProductPlatformService
from .uploads import UploadStorage


@dataclass
class PlatformRuntime:
    data_root: Path
    store: PlatformStore
    identity: IdentityService
    uploads: UploadStorage
    service: ProductPlatformService
    privacy: PrivacyService
    router: C3ApiRouter
    wsgi: C3WSGIApplication

    def close(self) -> None:
        self.store.close()


def create_runtime(data_root: str | Path) -> PlatformRuntime:
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    store = PlatformStore(root / "platform.sqlite3")
    identity = IdentityService(store)
    uploads = UploadStorage(root / "uploads", store)
    service = ProductPlatformService(store, uploads=uploads)
    privacy = PrivacyService(store, uploads=uploads)
    router = C3ApiRouter(service, identity, privacy, uploads=uploads)
    return PlatformRuntime(
        root,
        store,
        identity,
        uploads,
        service,
        privacy,
        router,
        C3WSGIApplication(router),
    )
