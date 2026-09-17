"""C3 desktop-independent application platform.

The package is deliberately transport and deployment agnostic.  Presentation
adapters call application services; persistence, identity, jobs, privacy, and
release preparation remain server-side boundaries.
"""

from .contracts import (
    API_VERSION,
    ConsentPurpose,
    ErrorCode,
    JobState,
    PlatformError,
    Principal,
    Role,
    SubmissionState,
)

__all__ = [
    "API_VERSION",
    "ConsentPurpose",
    "ErrorCode",
    "JobState",
    "PlatformError",
    "Principal",
    "Role",
    "SubmissionState",
]
