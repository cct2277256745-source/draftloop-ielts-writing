"""Public/private classification, leak scan, and reproducible candidate builder."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping, Sequence
import zipfile

from .contracts import ErrorCode, PlatformError, canonical_json, digest


COMMUNITY_BOUNDARY_VERSION = "c3-community-boundary-v1"
RELEASE_FORMAT_VERSION = "c3-reproducible-zip-v1"

_PRUNED_DIRECTORIES = frozenset({
    ".git",
    ".venv",
    "__pycache__",
    "corpus",
    "rubrics",
    "synthetic_reference",
    "ielts_band_evidence_corpus",
    "ielts_dataset_discovery",
})
_DENIED_NAMES = frozenset({
    ".env",
    "samples.json",
    "credentials.json",
    "service-account.json",
})
_DENIED_SUFFIXES = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db",
    ".jsonl", ".pickle", ".pkl", ".faiss", ".npy", ".npz",
})
_TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".sh", ".qss", ".html", ".css", ".js", ".ts",
})
_LEAK_PATTERNS = (
    ("PRIVATE_KEY", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("BEARER_TOKEN", re.compile(rb"(?i)bearer\s+[A-Za-z0-9._~+/-]{20,}")),
    ("CREDENTIAL_LITERAL", re.compile(rb"(?i)(?:api[_-]?key|password|secret)\s*[:=]\s*['\"][^'\"\r\n]{12,}['\"]")),
    ("ABSOLUTE_USER_PATH", re.compile(rb"/(?:Users|home)/[^/\s]+/")),
)


@dataclass(frozen=True)
class ReleaseFinding:
    code: str
    path: str


@dataclass(frozen=True)
class CandidateFile:
    path: str
    data: bytes
    sha256: str


class CommunityReleaseBuilder:
    """Build only from explicit public roots; private package trees are never walked."""

    DEFAULT_ROOTS = (
        "app",
        "scripts",
        "docs/adr",
        "docs/contracts",
        "docs/runbooks",
        "docs/community",
        "README.md",
        "requirements.txt",
        "requirements-lock.txt",
        "run.sh",
    )

    def __init__(self, repository_root: str | Path, *, roots: Sequence[str] = DEFAULT_ROOTS) -> None:
        self.root = Path(repository_root).resolve()
        self.roots = tuple(roots)

    @staticmethod
    def _denied(relative: Path) -> bool:
        return (
            relative.name in _DENIED_NAMES
            or relative.suffix.casefold() in _DENIED_SUFFIXES
            or any(part in _PRUNED_DIRECTORIES for part in relative.parts)
            or any(part.startswith(".") for part in relative.parts)
        )

    def _walk_public_root(self, relative_root: str) -> Iterable[Path]:
        base = (self.root / relative_root).resolve()
        if self.root != base and self.root not in base.parents:
            raise PlatformError(ErrorCode.CONFLICT, "Release root escapes the repository.")
        if not base.exists():
            return
        if base.is_file():
            yield base
            return
        # Path.rglob would descend into private ignored trees before filtering.
        # Explicit recursion prunes those directory names before any object below
        # them is enumerated or hashed.
        pending = [base]
        while pending:
            directory = pending.pop()
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                relative = child.relative_to(self.root)
                if self._denied(relative):
                    continue
                if child.is_symlink():
                    raise PlatformError(ErrorCode.CONFLICT, "Symlinks are not allowed in a release candidate.")
                if child.is_dir():
                    pending.append(child)
                elif child.is_file():
                    yield child

    def _is_configured_public_path(self, relative: Path) -> bool:
        for configured in self.roots:
            configured_path = Path(configured)
            if relative == configured_path or configured_path in relative.parents:
                return True
        return False

    def _tracked_public_paths(self) -> tuple[Path, ...] | None:
        if not (self.root / ".git").exists():
            return None
        completed = subprocess.run(
            ["git", "-C", str(self.root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise PlatformError(ErrorCode.CONFLICT, "Tracked release membership is unavailable.")
        paths = []
        for encoded in completed.stdout.split(b"\0"):
            if not encoded:
                continue
            try:
                relative = Path(encoded.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise PlatformError(ErrorCode.CONFLICT, "Tracked release path encoding is invalid.") from exc
            if not self._is_configured_public_path(relative) or self._denied(relative):
                continue
            path = (self.root / relative).resolve()
            if self.root not in path.parents or not path.is_file() or path.is_symlink():
                raise PlatformError(ErrorCode.CONFLICT, "Tracked release path is missing or unsafe.")
            paths.append(path)
        return tuple(sorted(paths, key=lambda item: item.relative_to(self.root).as_posix()))

    def collect(self) -> tuple[CandidateFile, ...]:
        paths: dict[str, Path] = {}
        tracked = self._tracked_public_paths()
        if tracked is not None:
            for path in tracked:
                paths[path.relative_to(self.root).as_posix()] = path
        else:
            for configured in self.roots:
                for path in self._walk_public_root(configured):
                    relative = path.relative_to(self.root)
                    if self._denied(relative):
                        continue
                    paths[relative.as_posix()] = path
        files = []
        for relative, path in sorted(paths.items()):
            data = path.read_bytes()
            files.append(CandidateFile(relative, data, hashlib.sha256(data).hexdigest()))
        if not files:
            raise PlatformError(ErrorCode.CONFLICT, "Release candidate is empty.")
        return tuple(files)

    def scan(self, files: Sequence[CandidateFile]) -> tuple[ReleaseFinding, ...]:
        findings: list[ReleaseFinding] = []
        for item in files:
            relative = Path(item.path)
            if self._denied(relative):
                findings.append(ReleaseFinding("DENIED_PATH", item.path))
                continue
            if relative.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            for code, pattern in _LEAK_PATTERNS:
                if pattern.search(item.data):
                    findings.append(ReleaseFinding(code, item.path))
        return tuple(findings)

    def manifest(self, files: Sequence[CandidateFile]) -> Mapping[str, object]:
        entries = [{"path": item.path, "sha256": item.sha256, "bytes": len(item.data)} for item in files]
        content = {
            "boundaryVersion": COMMUNITY_BOUNDARY_VERSION,
            "releaseFormatVersion": RELEASE_FORMAT_VERSION,
            "privateRagIncluded": False,
            "files": entries,
            "featureMatrix": {
                "rubricOnlyAssessment": "AVAILABLE_WITH_AUTHORISED_RUNTIME_PACKAGE",
                "privateRag": "ABSENT",
                "payments": "ABSENT",
                "teacherPortal": "ABSENT",
                "finalFrontend": "C4_NOT_INCLUDED",
            },
        }
        return {**content, "manifestSha256": digest(content)}

    def build(self, destination: str | Path) -> Mapping[str, object]:
        files = self.collect()
        findings = self.scan(files)
        if findings:
            codes = sorted({finding.code for finding in findings})
            raise PlatformError(ErrorCode.CONFLICT, "Release leak scan failed: " + ", ".join(codes))
        manifest = self.manifest(files)
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for item in files:
                info = zipfile.ZipInfo(item.path, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, item.data)
            manifest_data = canonical_json(manifest).encode("utf-8")
            info = zipfile.ZipInfo("community-manifest.json", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, manifest_data)
        artifact_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "path": str(destination),
            "artifactSha256": artifact_sha,
            "manifest": manifest,
            "fileCount": len(files),
        }
