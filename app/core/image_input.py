"""Fail-closed Task 1 image validation and log-safe image metadata."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtGui import QImage

from .errors import LLMError


MAX_IMAGE_BYTES = 10 * 1024 * 1024
_EXTENSION_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ImageInputFailureCode(str, Enum):
    IMAGE_REQUIRED = "IMAGE_REQUIRED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    MIME_MISMATCH = "MIME_MISMATCH"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    UNREADABLE_IMAGE = "UNREADABLE_IMAGE"
    TASK2_IMAGE_FORBIDDEN = "TASK2_IMAGE_FORBIDDEN"


@dataclass(frozen=True)
class ImageInputFailure:
    code: ImageInputFailureCode
    message: str


class ImageInputError(LLMError):
    """User-facing error that retains the deterministic image failure code."""

    def __init__(self, failure: ImageInputFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True)
class ImageInputMetadata:
    media_type: str
    byte_size: int
    width: int
    height: int

    def as_log_fields(self) -> dict[str, str | int]:
        return {
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class ImageInput:
    metadata: ImageInputMetadata
    content: bytes = field(repr=False, compare=False)

    def as_data_url(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.metadata.media_type};base64,{encoded}"


@dataclass(frozen=True)
class ImageInputResolution:
    image: ImageInput | None = None
    failure: ImageInputFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    def require(self) -> ImageInput | None:
        if self.failure is not None:
            raise ImageInputError(self.failure)
        return self.image


def validate_image_input(task_type: str, path_value: str) -> ImageInputResolution:
    """Validate once and retain immutable bytes without exposing the source path."""
    raw_path = path_value.strip()
    if task_type == "task2":
        if raw_path:
            return _failed(
                ImageInputFailureCode.TASK2_IMAGE_FORBIDDEN,
                "Task 2 不接受图表图片，请清除图片后重试。",
            )
        return ImageInputResolution()
    if task_type != "task1":
        return _failed(
            ImageInputFailureCode.UNREADABLE_IMAGE,
            "无法识别写作任务类型，未读取图表图片。",
        )
    if not raw_path:
        return _failed(
            ImageInputFailureCode.IMAGE_REQUIRED,
            "Task 1 必须上传清晰完整的图表图片后才能开始批改。",
        )

    path = Path(raw_path)
    try:
        if not path.is_file():
            return _failed(
                ImageInputFailureCode.FILE_NOT_FOUND,
                "图表图片不存在或不是普通文件，请重新上传。",
            )
        extension = path.suffix.lower()
        expected_media_type = _EXTENSION_MEDIA_TYPES.get(extension)
        if expected_media_type is None:
            return _failed(
                ImageInputFailureCode.UNSUPPORTED_EXTENSION,
                "目前仅支持 PNG、JPG/JPEG 和 WebP 图片。",
            )
        byte_size = path.stat().st_size
    except OSError:
        return _failed(
            ImageInputFailureCode.FILE_NOT_FOUND,
            "图表图片不存在或无法访问，请重新上传。",
        )

    if byte_size > MAX_IMAGE_BYTES:
        return _failed(
            ImageInputFailureCode.IMAGE_TOO_LARGE,
            "图表图片不能超过 10 MiB，请压缩后重新上传。",
        )
    try:
        content = path.read_bytes()
    except OSError:
        return _failed(
            ImageInputFailureCode.UNREADABLE_IMAGE,
            "无法读取图表图片，请重新上传。",
        )
    if len(content) > MAX_IMAGE_BYTES:
        return _failed(
            ImageInputFailureCode.IMAGE_TOO_LARGE,
            "图表图片不能超过 10 MiB，请压缩后重新上传。",
        )

    return validate_image_bytes(content, expected_media_type=expected_media_type)


def validate_image_bytes(content: bytes, *, expected_media_type: str | None = None) -> ImageInputResolution:
    """Apply the same image gate to owner-scoped browser uploads, without paths."""
    if not isinstance(content, bytes) or not content:
        return _failed(ImageInputFailureCode.UNREADABLE_IMAGE, "图片为空或无法读取。")
    if len(content) > MAX_IMAGE_BYTES:
        return _failed(ImageInputFailureCode.IMAGE_TOO_LARGE, "图表图片不能超过 10 MiB，请压缩后重新上传。")
    detected_media_type = _detect_media_type(content)
    if detected_media_type is None:
        return _failed(
            ImageInputFailureCode.UNREADABLE_IMAGE,
            "图片内容无法识别，请上传有效的 PNG、JPG/JPEG 或 WebP 图片。",
        )
    if expected_media_type is not None and detected_media_type != expected_media_type:
        return _failed(
            ImageInputFailureCode.MIME_MISMATCH,
            "图片扩展名与实际格式不一致，请重新导出后上传。",
        )

    image = QImage.fromData(content)
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        return _failed(
            ImageInputFailureCode.UNREADABLE_IMAGE,
            "图片已损坏或无法完整解码，请重新导出后上传。",
        )
    metadata = ImageInputMetadata(
        media_type=detected_media_type,
        byte_size=len(content),
        width=image.width(),
        height=image.height(),
    )
    return ImageInputResolution(ImageInput(metadata=metadata, content=content))


def _detect_media_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _failed(code: ImageInputFailureCode, message: str) -> ImageInputResolution:
    return ImageInputResolution(failure=ImageInputFailure(code, message))
